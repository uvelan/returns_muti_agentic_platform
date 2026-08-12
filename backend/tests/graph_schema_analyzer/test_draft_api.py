"""The mutate -> validate -> approve flow through the real HTTP surface.

Extends the in-memory `PersistencePort` double from `test_api_routes.py` rather
than defining a second one, so both files exercise the same contract.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from return_platform.graph_schema_analyzer.api import router
from return_platform.graph_schema_analyzer.domain.schema_draft import (
    DraftStatus,
)
from return_platform.graph_schema_analyzer.domain.source_snapshot import (
    DatasetMetadata,
    FieldMetadata,
    SampleClassification,
    SourceSchemaSnapshot,
)
from return_platform.graph_schema_analyzer.ports.graph_target_port import BuildHandle
from return_platform.graph_schema_analyzer.ports.system_store_port import PersistencePort
from tests.graph_schema_analyzer.test_api_routes import InMemoryPersistence

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)

SNAPSHOT = SourceSchemaSnapshot.create(
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


class PassingTarget:
    """Structurally a `GraphTargetPort`. Missing a method makes it a 503.

    Deliberately not a mock: `resolve_graph_target` isinstance-checks the
    runtime protocol, so a stub that drifts from the port fails the routes that
    use it rather than passing with a method nobody calls.
    """

    def __init__(self) -> None:
        self.published: list[tuple[str, str, bool]] = []

    async def compile_schema(self, *, draft: Mapping[str, object]) -> Sequence[str]:
        return ()

    async def validate_schema(
        self, *, draft: Mapping[str, object]
    ) -> Sequence[Mapping[str, object]]:
        return ()

    async def request_build(self, *, schema_id: str, activate: bool) -> object:
        raise AssertionError("the analyzer API must never trigger a build")

    async def publish_release(
        self, *, draft: Mapping[str, object], draft_id: str, approver: str, activate: bool
    ) -> object:
        self.published.append((draft_id, approver, activate))
        return BuildHandle(generation_id=f"release_{draft_id}", accepted=True, detail="published")


@pytest.fixture
def persistence() -> InMemoryPersistence:
    return InMemoryPersistence()


@pytest.fixture
def target() -> PassingTarget:
    return PassingTarget()


@pytest.fixture
def client(persistence: InMemoryPersistence, target: PassingTarget) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.graph_schema_analyzer_persistence = persistence
    app.state.graph_schema_analyzer_graph_target = target
    return TestClient(app)


def _analysis(client: TestClient, persistence: InMemoryPersistence) -> str:
    created = client.post("/api/graph-schema/analyses", json={"source_refs": ["mongo_main"]}).json()
    analysis_id = created["analysis_id"]
    persistence.snapshots["snap-1"] = SNAPSHOT
    persistence.sessions[analysis_id] = persistence.sessions[analysis_id].with_snapshot(
        "snap-1", occurred_at=NOW
    )
    return analysis_id


def _build_order_schema(client: TestClient, draft_id: str) -> None:
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
                    "source_field": "order_id",
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


def test_the_double_still_satisfies_the_extended_port() -> None:
    assert isinstance(InMemoryPersistence(), PersistencePort)


def test_full_mutate_validate_approve_flow(
    client: TestClient, persistence: InMemoryPersistence
) -> None:
    analysis_id = _analysis(client, persistence)
    draft = client.post(f"/api/graph-schema/analyses/{analysis_id}/drafts").json()
    draft_id = draft["draft_id"]

    _build_order_schema(client, draft_id)
    state = client.get(f"/api/graph-schema/drafts/{draft_id}").json()
    assert state["entity_count"] == 1
    assert state["status"] == DraftStatus.DRAFT
    assert state["current_revision"] == 1

    validated = client.post(f"/api/graph-schema/drafts/{draft_id}/validate").json()
    assert validated["passed"] is True
    assert validated["missing_checks"] == []
    assert len(validated["checks_run"]) == 14

    approved = client.post(
        f"/api/graph-schema/drafts/{draft_id}/approve", json={"note": "looks right"}
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == DraftStatus.APPROVED
    assert persistence.approvals[0].note == "looks right"


def test_a_mutation_after_validation_returns_the_draft_to_draft(
    client: TestClient, persistence: InMemoryPersistence
) -> None:
    """The 10.4 rule, end to end: editing withdraws the validation."""
    analysis_id = _analysis(client, persistence)
    draft_id = client.post(f"/api/graph-schema/analyses/{analysis_id}/drafts").json()["draft_id"]
    _build_order_schema(client, draft_id)
    assert client.post(f"/api/graph-schema/drafts/{draft_id}/validate").json()["passed"]

    client.post(
        f"/api/graph-schema/drafts/{draft_id}/mutations",
        json={
            "mutations": [{"kind": "AddEntity", "label": "Customer", "source_dataset": "orders"}]
        },
    )
    state = client.get(f"/api/graph-schema/drafts/{draft_id}").json()
    assert state["status"] == DraftStatus.DRAFT
    assert state["validation_result_id"] is None

    # ...and approval is now refused.
    refused = client.post(f"/api/graph-schema/drafts/{draft_id}/approve", json={})
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "NOT_APPROVABLE"


def test_an_untyped_mutation_is_rejected_by_the_parser(
    client: TestClient, persistence: InMemoryPersistence
) -> None:
    """The narrowest enforcement point: a smuggled statement never reaches
    analyzer code at all."""
    analysis_id = _analysis(client, persistence)
    draft_id = client.post(f"/api/graph-schema/analyses/{analysis_id}/drafts").json()["draft_id"]

    for payload in (
        {"kind": "ExecuteCypher", "statement": "MATCH (n) DETACH DELETE n"},
        {"kind": "AddEntity", "label": "Order", "source_dataset": "orders", "cypher": "DROP"},
        {"kind": "AddEntity", "label": "Order) DETACH DELETE n //", "source_dataset": "orders"},
    ):
        response = client.post(
            f"/api/graph-schema/drafts/{draft_id}/mutations", json={"mutations": [payload]}
        )
        assert response.status_code == 422, payload


def test_a_rejected_mutation_batch_changes_nothing(
    client: TestClient, persistence: InMemoryPersistence
) -> None:
    analysis_id = _analysis(client, persistence)
    draft_id = client.post(f"/api/graph-schema/analyses/{analysis_id}/drafts").json()["draft_id"]
    _build_order_schema(client, draft_id)

    response = client.post(
        f"/api/graph-schema/drafts/{draft_id}/mutations",
        json={
            "mutations": [
                {"kind": "AddEntity", "label": "Customer", "source_dataset": "orders"},
                {
                    "kind": "AddProperty",
                    "label": "Ghost",
                    "property_name": "x",
                    "property_type": "STRING",
                    "source_field": "order_id",
                },
            ]
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "MUTATION_REJECTED"
    state = client.get(f"/api/graph-schema/drafts/{draft_id}").json()
    assert state["entity_count"] == 1
    assert state["current_revision"] == 1


def test_validation_is_refused_when_the_graph_target_is_missing(
    persistence: InMemoryPersistence,
) -> None:
    """A partial validation reporting "passed" is worse than none."""
    app = FastAPI()
    app.include_router(router)
    app.state.graph_schema_analyzer_persistence = persistence
    unconfigured = TestClient(app)
    response = unconfigured.post("/api/graph-schema/drafts/whatever/validate")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "GRAPH_TARGET_UNAVAILABLE"


def test_validating_before_discovery_is_a_conflict(
    client: TestClient, persistence: InMemoryPersistence
) -> None:
    created = client.post("/api/graph-schema/analyses", json={"source_refs": ["mongo_main"]}).json()
    analysis_id = created["analysis_id"]  # no snapshot attached
    draft_id = client.post(f"/api/graph-schema/analyses/{analysis_id}/drafts").json()["draft_id"]
    _build_order_schema(client, draft_id)

    response = client.post(f"/api/graph-schema/drafts/{draft_id}/validate")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "NO_SNAPSHOT"


def test_revisions_and_diff_reconstruct_history(
    client: TestClient, persistence: InMemoryPersistence
) -> None:
    analysis_id = _analysis(client, persistence)
    draft_id = client.post(f"/api/graph-schema/analyses/{analysis_id}/drafts").json()["draft_id"]
    _build_order_schema(client, draft_id)
    client.post(
        f"/api/graph-schema/drafts/{draft_id}/mutations",
        json={
            "mutations": [{"kind": "AddEntity", "label": "Customer", "source_dataset": "orders"}]
        },
    )

    revisions = client.get(f"/api/graph-schema/drafts/{draft_id}/revisions").json()
    assert [r["sequence"] for r in revisions] == [1, 2]
    assert all(r["authored_by_model"] is False for r in revisions)

    diff = client.get(f"/api/graph-schema/drafts/{draft_id}/revisions/2/diff").json()
    assert {(e["change_type"], e["element"]) for e in diff["entries"]} == {("ADDED", "Customer")}


def test_one_analysis_gets_one_draft(client: TestClient, persistence: InMemoryPersistence) -> None:
    analysis_id = _analysis(client, persistence)
    assert client.post(f"/api/graph-schema/analyses/{analysis_id}/drafts").status_code == 201
    second = client.post(f"/api/graph-schema/analyses/{analysis_id}/drafts")
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "DRAFT_ALREADY_EXISTS"


def test_publishing_needs_an_approval_first(
    client: TestClient, persistence: InMemoryPersistence
) -> None:
    """A validated shape is one someone might accept, not one to run.

    The three states exist for the human act in the middle; publishing from
    VALIDATED would make approval optional and the state machine decorative.
    """
    analysis_id = _analysis(client, persistence)
    draft_id = client.post(f"/api/graph-schema/analyses/{analysis_id}/drafts").json()["draft_id"]
    _build_order_schema(client, draft_id)
    client.post(f"/api/graph-schema/drafts/{draft_id}/validate")

    response = client.post(f"/api/graph-schema/drafts/{draft_id}/publish", json={})

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "NOT_APPROVED"


def test_an_approved_draft_publishes_to_the_runtime(
    client: TestClient, persistence: InMemoryPersistence, target: PassingTarget
) -> None:
    """The step that closes the loop.

    Before this the analyzer's approval changed a document and nothing else --
    the runtime went on reading a file from the repository.
    """
    analysis_id = _analysis(client, persistence)
    draft_id = client.post(f"/api/graph-schema/analyses/{analysis_id}/drafts").json()["draft_id"]
    _build_order_schema(client, draft_id)
    client.post(f"/api/graph-schema/drafts/{draft_id}/validate")
    client.post(f"/api/graph-schema/drafts/{draft_id}/approve", json={})

    response = client.post(f"/api/graph-schema/drafts/{draft_id}/publish", json={"activate": True})

    assert response.status_code == 200, response.text
    assert response.json()["accepted"] is True
    # Recorded and made live are separate decisions, and the caller's choice is
    # what travels -- not a default the adapter picked.
    assert target.published == [(draft_id, "anonymous", True)]


def test_publishing_defaults_to_recorded_not_live(
    client: TestClient, persistence: InMemoryPersistence, target: PassingTarget
) -> None:
    """The safe default. Cutting a release is reviewable; activating one is not."""
    analysis_id = _analysis(client, persistence)
    draft_id = client.post(f"/api/graph-schema/analyses/{analysis_id}/drafts").json()["draft_id"]
    _build_order_schema(client, draft_id)
    client.post(f"/api/graph-schema/drafts/{draft_id}/validate")
    client.post(f"/api/graph-schema/drafts/{draft_id}/approve", json={})

    client.post(f"/api/graph-schema/drafts/{draft_id}/publish", json={})

    assert target.published == [(draft_id, "anonymous", False)]


def test_publishing_an_unknown_draft_is_a_404(client: TestClient) -> None:
    response = client.post("/api/graph-schema/drafts/no-such-draft/publish", json={})

    assert response.status_code == 404, response.text
