"""`/api/graph-schema` behaviour, exercised through a real FastAPI app.

Uses an in-memory `PersistencePort` rather than real Mongo: everything asserted
here is routing, DI, status-code mapping, and the guards the API itself owns --
none of which Mongo participates in. The repositories' own real-infra proof is
separate.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from return_platform.graph_schema_analyzer.api import router
from return_platform.graph_schema_analyzer.domain import (
    AnalysisSession,
    Clarification,
    ClarificationStatus,
    ConcurrentModification,
    DatasetMetadata,
    SampleClassification,
    SessionStatus,
    SourceSchemaSnapshot,
    UnknownAnalysis,
)
from return_platform.graph_schema_analyzer.domain.approval import Approval
from return_platform.graph_schema_analyzer.domain.schema_draft import GraphSchemaDraft
from return_platform.graph_schema_analyzer.domain.schema_revision import SchemaRevision
from return_platform.graph_schema_analyzer.domain.validation_result import ValidationResult
from return_platform.graph_schema_analyzer.ports.system_store_port import PersistencePort

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


class InMemoryPersistence:
    """Structurally satisfies the whole PersistencePort.

    One complete double rather than several partial ones: `resolve_persistence`
    guards with a runtime `isinstance` against the Protocol, so a double missing
    any method makes every route 503 -- which is correct fail-closed behaviour,
    and exactly how extending the port was caught here.
    """

    def __init__(self) -> None:
        self.sessions: dict[str, AnalysisSession] = {}
        self.snapshots: dict[str, SourceSchemaSnapshot] = {}
        self.clarifications: dict[str, Clarification] = {}
        self.drafts: dict[str, GraphSchemaDraft] = {}
        self.revisions: list[SchemaRevision] = []
        self.validation_results: dict[str, ValidationResult] = {}
        self.approvals: list[Approval] = []
        self.fail_next_save_with_conflict = False

    async def create_session(self, session: AnalysisSession) -> None:
        self.sessions[session.analysis_id] = session

    async def save_session(self, session: AnalysisSession, *, expected_version: int) -> None:
        if self.fail_next_save_with_conflict:
            raise ConcurrentModification("someone else wrote first")
        stored = self.sessions.get(session.analysis_id)
        if stored is None or stored.version != expected_version:
            raise ConcurrentModification("version moved")
        self.sessions[session.analysis_id] = session

    async def load_session(self, analysis_id: str) -> AnalysisSession:
        try:
            return self.sessions[analysis_id]
        except KeyError as exc:
            raise UnknownAnalysis(analysis_id) from exc

    async def list_sessions(
        self, *, status: SessionStatus | None = None, limit: int = 50
    ) -> Sequence[AnalysisSession]:
        found = [s for s in self.sessions.values() if status is None or s.status is status]
        return found[:limit]

    async def save_snapshot(self, snapshot: SourceSchemaSnapshot) -> None:
        self.snapshots[snapshot.snapshot_id] = snapshot

    async def load_snapshot(self, snapshot_id: str) -> SourceSchemaSnapshot:
        try:
            return self.snapshots[snapshot_id]
        except KeyError as exc:
            raise UnknownAnalysis(snapshot_id) from exc

    async def save_clarification(self, clarification: Clarification) -> None:
        self.clarifications[clarification.clarification_id] = clarification

    async def load_clarification(self, clarification_id: str) -> Clarification:
        try:
            return self.clarifications[clarification_id]
        except KeyError as exc:
            raise UnknownAnalysis(clarification_id) from exc

    async def list_clarifications(self, analysis_id: str) -> Sequence[Clarification]:
        return [c for c in self.clarifications.values() if c.analysis_id == analysis_id]

    # --- drafts / revisions / validation / approval ------------------------
    async def create_draft(self, draft: GraphSchemaDraft) -> None:
        self.drafts[draft.draft_id] = draft

    async def save_draft(self, draft: GraphSchemaDraft, *, expected_version: int) -> None:
        stored = self.drafts.get(draft.draft_id)
        if stored is None or stored.version != expected_version:
            raise ConcurrentModification(
                f"draft {draft.draft_id} expected v{expected_version}, "
                f"stored v{stored.version if stored else None}"
            )
        self.drafts[draft.draft_id] = draft

    async def load_draft(self, draft_id: str) -> GraphSchemaDraft:
        try:
            return self.drafts[draft_id]
        except KeyError as exc:
            raise UnknownAnalysis(draft_id) from exc

    async def load_draft_for_analysis(self, analysis_id: str) -> GraphSchemaDraft | None:
        return next((d for d in self.drafts.values() if d.analysis_id == analysis_id), None)

    async def append_revision(self, revision: SchemaRevision) -> None:
        if any(
            r.draft_id == revision.draft_id and r.sequence == revision.sequence
            for r in self.revisions
        ):
            raise ConcurrentModification("duplicate (draft_id, sequence)")
        self.revisions.append(revision)

    async def list_revisions(self, draft_id: str) -> Sequence[SchemaRevision]:
        return sorted(
            (r for r in self.revisions if r.draft_id == draft_id), key=lambda r: r.sequence
        )

    async def save_validation_result(self, result: ValidationResult) -> None:
        self.validation_results[result.result_id] = result

    async def load_validation_result(self, result_id: str) -> ValidationResult:
        return self.validation_results[result_id]

    async def save_approval(self, approval: Approval) -> None:
        self.approvals.append(approval)

    async def list_approvals(self, draft_id: str) -> Sequence[Approval]:
        return [a for a in self.approvals if a.draft_id == draft_id]


@pytest.fixture
def persistence() -> InMemoryPersistence:
    return InMemoryPersistence()


@pytest.fixture
def client(persistence: InMemoryPersistence) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.graph_schema_analyzer_persistence = persistence
    return TestClient(app)


def test_the_in_memory_double_satisfies_the_port() -> None:
    """If this drifts from the real port, every test below is testing a fiction."""
    assert isinstance(InMemoryPersistence(), PersistencePort)


def test_unavailable_when_startup_did_not_attach_persistence() -> None:
    """A misconfigured deployment must say so, not 500 on a None."""
    app = FastAPI()
    app.include_router(router)
    response = TestClient(app).get("/api/graph-schema/analyses/anything")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "GRAPH_SCHEMA_ANALYZER_UNAVAILABLE"


def test_create_then_read_an_analysis(client: TestClient) -> None:
    created = client.post("/api/graph-schema/analyses", json={"source_refs": ["mongo_main"]})
    assert created.status_code == 201
    body = created.json()
    assert body["status"] == SessionStatus.DRAFT
    assert body["version"] == 0

    fetched = client.get(f"/api/graph-schema/analyses/{body['analysis_id']}")
    assert fetched.status_code == 200
    assert fetched.json()["analysis_id"] == body["analysis_id"]


def test_create_rejects_an_empty_source_list(client: TestClient) -> None:
    """Analyzing nothing is a client error, not an empty analysis."""
    assert client.post("/api/graph-schema/analyses", json={"source_refs": []}).status_code == 422


def test_unknown_analysis_is_404_not_500(client: TestClient) -> None:
    response = client.get("/api/graph-schema/analyses/missing")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "UNKNOWN_ANALYSIS"


def test_abandon_moves_to_terminal_and_a_second_attempt_conflicts(
    client: TestClient, persistence: InMemoryPersistence
) -> None:
    created = client.post("/api/graph-schema/analyses", json={"source_refs": ["mongo_main"]}).json()
    analysis_id = created["analysis_id"]

    abandoned = client.post(f"/api/graph-schema/analyses/{analysis_id}/abandon")
    assert abandoned.status_code == 200
    assert abandoned.json()["status"] == SessionStatus.ABANDONED

    # ABANDONED is terminal, so the domain refuses the second transition and the
    # API reports it as a conflict rather than silently succeeding.
    again = client.post(f"/api/graph-schema/analyses/{analysis_id}/abandon")
    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "INVALID_SESSION_TRANSITION"


def test_a_lost_update_surfaces_as_409(
    client: TestClient, persistence: InMemoryPersistence
) -> None:
    created = client.post("/api/graph-schema/analyses", json={"source_refs": ["mongo_main"]}).json()
    persistence.fail_next_save_with_conflict = True
    response = client.post(f"/api/graph-schema/analyses/{created['analysis_id']}/abandon")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "CONCURRENT_MODIFICATION"


def test_snapshot_is_404_before_discovery_has_run(client: TestClient) -> None:
    created = client.post("/api/graph-schema/analyses", json={"source_refs": ["mongo_main"]}).json()
    response = client.get(f"/api/graph-schema/analyses/{created['analysis_id']}/snapshot")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "NO_SNAPSHOT"


def test_snapshot_view_never_exposes_the_samples_reference(
    client: TestClient, persistence: InMemoryPersistence
) -> None:
    """samples_ref points into the encrypted source_samples structure; the API
    surfaces how samples were classified, never how to reach them."""
    created = client.post("/api/graph-schema/analyses", json={"source_refs": ["mongo_main"]}).json()
    analysis_id = created["analysis_id"]
    snapshot = SourceSchemaSnapshot.create(
        snapshot_id="snap-1",
        analysis_id=analysis_id,
        datasets=(DatasetMetadata(source_id="mongo_main", dataset_name="orders", fields=()),),
        sample_classification=SampleClassification.REDACTED,
        captured_at=NOW,
        samples_ref="secret-pointer",
    )
    persistence.snapshots[snapshot.snapshot_id] = snapshot
    persistence.sessions[analysis_id] = persistence.sessions[analysis_id].with_snapshot(
        "snap-1", occurred_at=NOW
    )

    body = client.get(f"/api/graph-schema/analyses/{analysis_id}/snapshot").json()
    assert body["sample_classification"] == SampleClassification.REDACTED
    assert body["dataset_count"] == 1
    assert "samples_ref" not in body
    assert "secret-pointer" not in str(body)


def test_answering_a_clarification_through_the_wrong_analysis_is_rejected(
    client: TestClient, persistence: InMemoryPersistence
) -> None:
    """Clarification ids are globally unique, so without this guard an answer
    could be attributed to a session it does not belong to."""
    persistence.clarifications["c1"] = Clarification(
        clarification_id="c1", analysis_id="owner", question="Which key?", asked_at=NOW
    )
    response = client.post(
        "/api/graph-schema/analyses/impostor/clarifications/c1/answer",
        json={"answer": "order_id"},
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "UNKNOWN_CLARIFICATION"


def test_answering_a_clarification_records_the_answer(
    client: TestClient, persistence: InMemoryPersistence
) -> None:
    persistence.clarifications["c1"] = Clarification(
        clarification_id="c1", analysis_id="a1", question="Which key?", asked_at=NOW
    )
    response = client.post(
        "/api/graph-schema/analyses/a1/clarifications/c1/answer",
        json={"answer": "order_id"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == ClarificationStatus.ANSWERED
    assert response.json()["answer"] == "order_id"

    # Re-answering is refused by the domain state machine, surfaced as 409.
    again = client.post(
        "/api/graph-schema/analyses/a1/clarifications/c1/answer",
        json={"answer": "changed my mind"},
    )
    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "CLARIFICATION_NOT_OPEN"
