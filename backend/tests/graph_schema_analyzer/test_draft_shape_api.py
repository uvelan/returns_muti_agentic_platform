"""`GET /drafts/{id}/shape` — the entities and relationships a canvas draws.

Wave E's fourth blocker. The analyzer serialised `entity_count` and
`relationship_count` and nothing else, so a consumer could learn that a draft had
seven entities and never learn what they were. E4's canvas is what that blocked,
and the screen said so rather than inventing data.

Two design points these tests hold:

* **The shape is typed at the API boundary, not in the domain.**
  `GraphSchemaShape` is deliberately plain `Mapping[str, Any]` -- it is the
  *result* of applying typed mutation commands, never an editing surface. Typing
  it at the domain would add a second place every command has to satisfy.
* **It is a separate endpoint from `GET /drafts/{id}`.** Counts are O(1) and are
  what a draft *listing* needs; a real source's schema is unbounded, and putting
  it inline would make every listing pay for a payload only the canvas reads.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from return_platform.graph_schema_analyzer.api import router
from tests.governance_doubles import attach_governance
from tests.graph_schema_analyzer.test_draft_api import (
    InMemoryPersistence,
    PassingTarget,
    _analysis,
    _build_order_schema,
)


# Importing the sibling's `client`/`persistence` fixtures by name collides with the
# parameter names every test below binds them to. Both other modules in this package
# declare their own, so this one does too.
@pytest.fixture
def persistence() -> InMemoryPersistence:
    return InMemoryPersistence()


@pytest.fixture
def client(persistence: InMemoryPersistence) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    target = PassingTarget()
    app.state.graph_schema_analyzer_persistence = persistence
    app.state.graph_schema_analyzer_graph_target = target
    attach_governance(app, target)
    return TestClient(app)


def _drafted(client: TestClient, persistence: InMemoryPersistence) -> str:
    analysis_id = _analysis(client, persistence)
    created = client.post(f"/api/graph-schema/analyses/{analysis_id}/drafts").json()
    draft_id: str = created["draft_id"]
    _build_order_schema(client, draft_id)
    return draft_id


def test_the_shape_carries_the_entities_the_counts_only_counted(
    client: TestClient, persistence: InMemoryPersistence
) -> None:
    draft_id = _drafted(client, persistence)

    counted = client.get(f"/api/graph-schema/drafts/{draft_id}").json()
    shaped = client.get(f"/api/graph-schema/drafts/{draft_id}/shape").json()

    assert counted["entity_count"] == 1
    # The thing the count could not tell you.
    assert set(shaped["entities"]) == {"Order"}
    assert shaped["entities"]["Order"]["source_dataset"] == "orders"
    assert shaped["entities"]["Order"]["identifier_properties"] == ["order_id"]


def test_property_detail_survives_the_projection(
    client: TestClient, persistence: InMemoryPersistence
) -> None:
    """A canvas needs the property's type and where it came from, not just its
    name -- that is most of what makes the drawing worth looking at."""
    draft_id = _drafted(client, persistence)

    shaped = client.get(f"/api/graph-schema/drafts/{draft_id}/shape").json()

    assert shaped["entities"]["Order"]["properties"]["order_id"] == {
        "type": "STRING",
        "source_field": "order_id",
        "transformation": "NONE",
    }


def test_relationships_carry_both_endpoints_and_cardinality(
    client: TestClient, persistence: InMemoryPersistence
) -> None:
    """Edges are the half a count hides completely: two drafts with the same
    `relationship_count` can describe entirely different graphs."""
    draft_id = _drafted(client, persistence)
    response = client.post(
        f"/api/graph-schema/drafts/{draft_id}/mutations",
        json={
            "mutations": [
                {"kind": "AddEntity", "label": "Customer", "source_dataset": "customers"},
                {
                    "kind": "AddRelationship",
                    "relationship_type": "PLACED_BY",
                    "from_label": "Order",
                    "to_label": "Customer",
                    "cardinality": "MANY_TO_ONE",
                },
            ]
        },
    )
    assert response.status_code == 200, response.text

    shaped = client.get(f"/api/graph-schema/drafts/{draft_id}/shape").json()

    assert shaped["relationships"] == [
        {
            "relationship_type": "PLACED_BY",
            "from_label": "Order",
            "to_label": "Customer",
            "cardinality": "MANY_TO_ONE",
        }
    ]


def test_an_empty_draft_shape_is_empty_rather_than_absent(
    client: TestClient, persistence: InMemoryPersistence
) -> None:
    """A new draft has a shape with nothing in it. Returning 404 would make
    "no entities yet" indistinguishable from "no such draft"."""
    analysis_id = _analysis(client, persistence)
    draft_id = client.post(f"/api/graph-schema/analyses/{analysis_id}/drafts").json()["draft_id"]

    shaped = client.get(f"/api/graph-schema/drafts/{draft_id}/shape").json()

    assert shaped == {
        "entities": {},
        "relationships": [],
        "graph_indexes": [],
        "graph_constraints": [],
    }


def test_an_unknown_draft_is_404(client: TestClient) -> None:
    assert client.get("/api/graph-schema/drafts/nope/shape").status_code == 404


def test_the_counts_endpoint_still_does_not_carry_the_shape(
    client: TestClient, persistence: InMemoryPersistence
) -> None:
    """The separation is the point. If the shape ever appears on `GET
    /drafts/{id}`, every draft listing starts paying for a payload only the
    canvas reads."""
    draft_id = _drafted(client, persistence)

    counted = client.get(f"/api/graph-schema/drafts/{draft_id}").json()

    assert "entities" not in counted
    assert "relationships" not in counted


def test_an_unrecognised_shape_key_fails_loudly(
    client: TestClient, persistence: InMemoryPersistence
) -> None:
    """The guard that makes typing-at-the-boundary safe.

    The domain shape is untyped, so a mutation command that started writing a
    differently-named key would otherwise serialise as a silently missing field
    and the canvas would quietly stop drawing an attribute. `extra="forbid"`
    turns it into a loud failure instead. Simulated by writing the stray key
    directly, because no current command can produce one -- which is exactly why
    the guard has to be tested rather than assumed.
    """
    draft_id = _drafted(client, persistence)
    stored = persistence.drafts[draft_id]
    entities = {
        label: {**dict(entity), "unexpected_key": True}
        for label, entity in stored.shape.entities.items()
    }
    persistence.drafts[draft_id] = stored.model_copy(
        update={"shape": stored.shape.model_copy(update={"entities": entities})}
    )

    with pytest.raises(Exception, match="unexpected_key"):
        client.get(f"/api/graph-schema/drafts/{draft_id}/shape")
