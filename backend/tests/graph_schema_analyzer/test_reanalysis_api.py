"""`POST /drafts/{id}/reanalysis` over the real HTTP surface.

A feature nobody can invoke is not done, so this covers the route rather than
the proposer (`test_reanalysis.py` owns that): that a re-analysis refreshes the
*evidence* and never the *design*, that it refuses rather than reporting no
drift it could not look for, and that accepting a proposal is the ordinary
mutations call and nothing new.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from return_platform.graph_schema_analyzer.api import router
from return_platform.graph_schema_analyzer.domain.source_snapshot import (
    DatasetMetadata,
    FieldMetadata,
    SampleClassification,
    SourceSchemaSnapshot,
)
from return_platform.graph_schema_analyzer.ports.source_port import DiscoveredDataset
from tests.governance_doubles import attach_governance
from tests.graph_schema_analyzer.test_api_routes import InMemoryPersistence
from tests.graph_schema_analyzer.test_draft_api import PassingTarget

NOW = datetime(2026, 8, 12, 9, 0, tzinfo=UTC)

ORIGINAL = SourceSchemaSnapshot.create(
    snapshot_id="snap-1",
    analysis_id="a1",
    datasets=(
        DatasetMetadata(
            source_id="mongo_main",
            dataset_name="orders",
            fields=(FieldMetadata(field_name="order_id", declared_type="string"),),
        ),
    ),
    sample_classification=SampleClassification.NONE,
    captured_at=NOW,
)


class RediscoveringSources:
    """Structurally a `SourceDiscoveryPort`, returning whatever the test set.

    Deliberately not a mock: `resolve_source_discovery` isinstance-checks the
    runtime protocol, so a double that drifts from the port 503s the route
    instead of passing with a method nobody calls.
    """

    def __init__(self, fields: Mapping[str, str], *, dataset: str = "orders") -> None:
        self.fields = dict(fields)
        self.dataset = dataset
        self.sample_limits: list[int] = []

    async def list_source_ids(self) -> Sequence[str]:
        return ("mongo_main",)

    async def discover(self, *, source_id: str, sample_limit: int) -> Sequence[DiscoveredDataset]:
        self.sample_limits.append(sample_limit)
        return (
            DiscoveredDataset(
                source_id=source_id,
                dataset_name=self.dataset,
                fields=tuple(
                    {"field_name": name, "declared_type": declared}
                    for name, declared in self.fields.items()
                ),
            ),
        )


@pytest.fixture
def persistence() -> InMemoryPersistence:
    return InMemoryPersistence()


def _client(persistence: InMemoryPersistence, sources: object | None) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    target = PassingTarget()
    app.state.graph_schema_analyzer_persistence = persistence
    app.state.graph_schema_analyzer_graph_target = target
    attach_governance(app, target)
    if sources is not None:
        app.state.graph_schema_analyzer_source_discovery = sources
    return TestClient(app)


def _draft(client: TestClient, persistence: InMemoryPersistence) -> str:
    created = client.post("/api/graph-schema/analyses", json={"source_refs": ["mongo_main"]}).json()
    analysis_id = created["analysis_id"]
    persistence.snapshots["snap-1"] = ORIGINAL
    persistence.sessions[analysis_id] = persistence.sessions[analysis_id].with_snapshot(
        "snap-1", occurred_at=NOW
    )
    draft_id = str(
        client.post(f"/api/graph-schema/analyses/{analysis_id}/drafts").json()["draft_id"]
    )
    response = client.post(
        f"/api/graph-schema/drafts/{draft_id}/mutations",
        json={
            "mutations": [
                {"kind": "AddEntity", "label": "Order", "source_dataset": "orders"},
                {
                    "kind": "AddProperty",
                    "label": "Order",
                    "property_name": "order_id",
                    "property_type": "STRING",
                    "source_field": "orders.order_id",
                },
                {
                    "kind": "ChangeIdentifier",
                    "label": "Order",
                    "identifier_properties": ["order_id"],
                },
            ]
        },
    )
    assert response.status_code == 200, response.text
    return draft_id


def test_a_drifted_source_comes_back_as_commands_and_changes_no_draft(
    persistence: InMemoryPersistence,
) -> None:
    """The whole contract in one test: proposed, not applied."""
    sources = RediscoveringSources({"order_id": "string", "status": "string"})
    client = _client(persistence, sources)
    draft_id = _draft(client, persistence)
    before = client.get(f"/api/graph-schema/drafts/{draft_id}/shape").json()

    response = client.post(f"/api/graph-schema/drafts/{draft_id}/reanalysis")

    assert response.status_code == 200, response.text
    body = response.json()
    (change,) = body["changes"]
    assert change["drift"] == "FIELD_ADDED"
    assert change["mutations"][0]["kind"] == "AddProperty"
    assert client.get(f"/api/graph-schema/drafts/{draft_id}/shape").json() == before
    assert client.get(f"/api/graph-schema/drafts/{draft_id}").json()["current_revision"] == 1


def test_the_proposal_is_accepted_through_the_ordinary_mutations_endpoint(
    persistence: InMemoryPersistence,
) -> None:
    """There is no second apply path. Accepting is the same call a hand-written
    change makes, which is what keeps the revision history honest about it."""
    client = _client(persistence, RediscoveringSources({"order_id": "string", "status": "string"}))
    draft_id = _draft(client, persistence)
    proposal = client.post(f"/api/graph-schema/drafts/{draft_id}/reanalysis").json()

    applied = client.post(
        f"/api/graph-schema/drafts/{draft_id}/mutations",
        json={"mutations": [m for c in proposal["changes"] for m in c["mutations"]]},
    )

    assert applied.status_code == 200, applied.text
    shape = client.get(f"/api/graph-schema/drafts/{draft_id}/shape").json()
    assert "status" in shape["entities"]["Order"]["properties"]


def test_re_analysis_refreshes_the_evidence_the_draft_is_judged_against(
    persistence: InMemoryPersistence,
) -> None:
    """Validation compares a draft to a snapshot. Leaving the analysis on last
    month's reading would keep it passing against a source that moved on."""
    client = _client(persistence, RediscoveringSources({"order_id": "string", "status": "string"}))
    draft_id = _draft(client, persistence)
    analysis_id = client.get(f"/api/graph-schema/drafts/{draft_id}").json()["analysis_id"]

    client.post(f"/api/graph-schema/drafts/{draft_id}/reanalysis")

    snapshot = client.get(f"/api/graph-schema/analyses/{analysis_id}/snapshot").json()
    assert snapshot["snapshot_id"] != "snap-1"
    assert snapshot["dataset_count"] == 1


def test_a_run_that_finds_nothing_stores_nothing(persistence: InMemoryPersistence) -> None:
    """Two captures of the same shape share a content address. Writing a second
    copy under a new id would grow the collection with every poll."""
    client = _client(persistence, RediscoveringSources({"order_id": "string"}))
    draft_id = _draft(client, persistence)

    body = client.post(f"/api/graph-schema/drafts/{draft_id}/reanalysis").json()

    assert body["changes"] == []
    assert body["from_content_hash"] == body["to_content_hash"]
    assert list(persistence.snapshots) == ["snap-1"]


def test_re_analysis_reads_no_sample_rows(persistence: InMemoryPersistence) -> None:
    """Drift is a question about shape. A re-analysis must not become a way to
    sample a source the original analysis was not permitted to sample."""
    sources = RediscoveringSources({"order_id": "string", "status": "string"})
    client = _client(persistence, sources)
    draft_id = _draft(client, persistence)

    client.post(f"/api/graph-schema/drafts/{draft_id}/reanalysis")

    assert sources.sample_limits == [0]
    assert all(
        snapshot.sample_classification is SampleClassification.NONE
        for snapshot in persistence.snapshots.values()
    )


def test_a_relocated_dataset_proposes_a_rebinding_and_no_reshaping(
    persistence: InMemoryPersistence,
) -> None:
    """The W2.2 split, end to end: infrastructure moved, so the answer is a
    binding change, not an edit to a schema someone designed."""
    client = _client(
        persistence, RediscoveringSources({"order_id": "string"}, dataset="orders_restored")
    )
    draft_id = _draft(client, persistence)

    body = client.post(f"/api/graph-schema/drafts/{draft_id}/reanalysis").json()

    assert body["changes"] == []
    (rebinding,) = body["rebindings"]
    assert rebinding["dataset"] == "orders"
    assert rebinding["to_dataset"] == "orders_restored"


def test_re_analysis_is_refused_when_the_source_cannot_be_read(
    persistence: InMemoryPersistence,
) -> None:
    """Reporting "nothing drifted" for a source nobody looked at is the one
    answer that must never be guessed."""
    client = _client(persistence, None)
    draft_id = _draft(
        _client(persistence, RediscoveringSources({"order_id": "string"})), persistence
    )

    response = client.post(f"/api/graph-schema/drafts/{draft_id}/reanalysis")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "SOURCE_DISCOVERY_UNAVAILABLE"


def test_re_analysing_before_any_discovery_is_a_conflict(
    persistence: InMemoryPersistence,
) -> None:
    client = _client(persistence, RediscoveringSources({"order_id": "string"}))
    created = client.post("/api/graph-schema/analyses", json={"source_refs": ["mongo_main"]}).json()
    draft_id = client.post(f"/api/graph-schema/analyses/{created['analysis_id']}/drafts").json()[
        "draft_id"
    ]

    response = client.post(f"/api/graph-schema/drafts/{draft_id}/reanalysis")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "NO_SNAPSHOT"


def test_re_analysing_an_unknown_draft_is_a_404(persistence: InMemoryPersistence) -> None:
    client = _client(persistence, RediscoveringSources({"order_id": "string"}))

    assert client.post("/api/graph-schema/drafts/no-such-draft/reanalysis").status_code == 404
