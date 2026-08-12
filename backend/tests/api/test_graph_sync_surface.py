"""`/api/graph-sync`: seeing sync runs, and starting one.

Over real HTTP against a stub service. `GraphSyncService` itself has its own
pipeline tests; what is under test here is the surface those tests could never
reach, because until now there was none -- `list_runs` and `get_run` were
written, indexed and called by nothing after Wave F1 unmounted the Data
Console.

The assertions worth making are the ones a screen depends on: that a targeted
run an agent caused is returned in the same list as a scheduled one and says
which conversation caused it, that an unknown run is a 404 rather than a null
payload, that starting a sync is admin-only, and that a service the lifespan
could not build answers 503 instead of raising.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from return_platform.api import graph_sync as module
from return_platform.api.graph_sync import router
from return_platform.data_platform.graph.sync_service import (
    GraphSyncRequest,
    GraphSyncRunView,
    sync_run_view,
)
from return_platform.security import roles as r
from return_platform.security.principal import Principal

STARTED_AT = datetime(2026, 8, 11, 9, 0, tzinfo=UTC)


def _scheduled_run() -> GraphSyncRunView:
    return sync_run_view(
        {
            "_id": "run-scheduled",
            "mode": "FULL",
            "status": "COMPLETED",
            "schemaVersion": "2026.08.04",
            "sourceCounts": {"source_sales": 120},
            "nodeWrites": 300,
            "relationshipWrites": 240,
            "constraintsApplied": ["uq_salesorder_account_id_sales_order_number"],
            "configurationDigest": "a" * 64,
            "startedBy": "console_admin",
            "startedAt": STARTED_AT,
            "completedAt": STARTED_AT,
        }
    )


def _targeted_run() -> GraphSyncRunView:
    return sync_run_view(
        {
            "_id": "run-targeted",
            "mode": "ON_DEMAND",
            "status": "COMPLETED",
            "schemaVersion": "2026.08.04",
            "sourceCounts": {"source_sales": 1},
            "nodeWrites": 4,
            "relationshipWrites": 3,
            "constraintsApplied": [],
            "configurationDigest": "a" * 64,
            "startedBy": "order-discovery-agent",
            "startedAt": STARTED_AT,
            "completedAt": STARTED_AT,
            "graphGenerationId": "legacy",
            "requestDigest": "digest-1",
            "requestedBy": {
                "agentId": "order-discovery-agent",
                "conversationId": "conv-7",
                "clientTurnId": "turn-2",
                "entityId": "sales_order",
                "strongAnchorId": "exact_order_key",
                "anchorFieldIds": ["order_key"],
            },
        }
    )


class StubSyncService:
    """Answers like `GraphSyncService` without any datastore.

    Deliberately not a `MagicMock`: the router reads `mode` back out of the
    filter it was given, and a mock would have accepted a filter the real
    service ignores.
    """

    def __init__(self) -> None:
        self.runs = [_targeted_run(), _scheduled_run()]
        self.started: list[GraphSyncRequest] = []
        self.actors: list[str] = []
        self.fail_with: Exception | None = None

    async def list_runs(
        self, limit: int = 100, *, mode: str | None = None
    ) -> list[GraphSyncRunView]:
        selected = [run for run in self.runs if mode is None or run.mode == mode]
        return selected[:limit]

    async def get_run(self, run_id: str) -> GraphSyncRunView | None:
        return next((run for run in self.runs if run.id == run_id), None)

    async def sync(self, request: GraphSyncRequest, *, actor_id: str) -> GraphSyncRunView:
        if self.fail_with is not None:
            raise self.fail_with
        self.started.append(request)
        self.actors.append(actor_id)
        return _scheduled_run()


@pytest.fixture
def service() -> StubSyncService:
    return StubSyncService()


def _client(principal: Principal, service: StubSyncService | None) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _attach(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.principal = principal
        request.state.correlation_id = "test-correlation-id"
        return await call_next(request)

    if service is not None:
        app.state.graph_sync = service
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def admin_client(service: StubSyncService, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    # The stub is not a `GraphSyncService`, and `_service` is deliberately
    # strict about that so a half-built app state cannot masquerade as a
    # working one. Substituting the resolver is the narrow way past it.
    monkeypatch.setattr(module, "_service", lambda request: service)
    with _client(Principal(subject="op", roles=frozenset({r.CONSOLE_ADMIN})), service) as client:
        yield client


@pytest.fixture
def viewer_client(
    service: StubSyncService, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setattr(module, "_service", lambda request: service)
    with _client(Principal(subject="viewer", roles=frozenset({r.CONSOLE_VIEWER})), service) as c:
        yield c


def test_the_run_list_holds_scheduled_and_agent_initiated_runs_together(
    admin_client: TestClient,
) -> None:
    """One history. An operator seeing an unexpected node in the graph should
    not have to know which of two mechanisms could have written it."""
    runs = admin_client.get("/api/graph-sync/runs").json()["data"]

    assert [run["mode"] for run in runs] == ["ON_DEMAND", "FULL"]


def test_a_targeted_run_says_which_conversation_caused_it(admin_client: TestClient) -> None:
    """The question an operator asks about a run nobody started."""
    runs = admin_client.get("/api/graph-sync/runs").json()["data"]
    targeted = next(run for run in runs if run["mode"] == "ON_DEMAND")

    assert targeted["requestedBy"]["conversationId"] == "conv-7"
    assert targeted["requestedBy"]["strongAnchorId"] == "exact_order_key"
    # Field ids, never the order number: a run list is exported and kept.
    assert targeted["requestedBy"]["anchorFieldIds"] == ["order_key"]


def test_a_scheduled_run_carries_no_invented_requester(admin_client: TestClient) -> None:
    scheduled = admin_client.get("/api/graph-sync/runs?mode=FULL").json()["data"][0]

    assert scheduled["requestedBy"] is None
    assert scheduled["mode"] == "FULL"


def test_an_unknown_mode_is_refused_rather_than_answered_with_nothing(
    admin_client: TestClient,
) -> None:
    """An empty list is indistinguishable from "no runs of that kind", which is
    the wrong answer to a typo."""
    response = admin_client.get("/api/graph-sync/runs?mode=NONSENSE")

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "UNKNOWN_SYNC_MODE"


def test_an_unknown_run_is_a_404(admin_client: TestClient) -> None:
    response = admin_client.get("/api/graph-sync/runs/nope")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "SYNC_RUN_NOT_FOUND"


def test_starting_a_sync_records_who_asked_for_it(
    admin_client: TestClient, service: StubSyncService
) -> None:
    response = admin_client.post(
        "/api/graph-sync/runs", json={"mode": "SOURCE_MONGODB", "maxRecordsPerAsset": 25}
    )

    assert response.status_code == 200
    assert service.actors == ["op"]
    assert service.started[0].maxRecordsPerAsset == 25


def test_a_viewer_may_read_runs_but_not_start_one(
    viewer_client: TestClient, service: StubSyncService
) -> None:
    """A resync reads production sources and rewrites the graph the copilot
    answers from. Reading the history is not the same act."""
    assert viewer_client.get("/api/graph-sync/runs").status_code == 200

    refused = viewer_client.post("/api/graph-sync/runs", json={"mode": "FULL"})

    assert refused.status_code == 403
    assert service.started == []


def test_a_failed_sync_does_not_return_the_underlying_error_text(
    admin_client: TestClient, service: StubSyncService
) -> None:
    """A connector error can carry a DSN, and this response is rendered in a
    browser."""
    service.fail_with = RuntimeError("mongodb://root:hunter2@mongodb:27017 refused")

    response = admin_client.post("/api/graph-sync/runs", json={"mode": "FULL"})

    assert response.status_code == 502
    assert "hunter2" not in response.text
    assert response.json()["detail"]["code"] == "GRAPH_SYNC_FAILED"


def test_a_platform_without_the_databases_answers_503_rather_than_raising() -> None:
    """The lifespan only builds the service when Mongo, the source database and
    Neo4j are all connected, so the router has to survive its absence."""
    with _client(Principal(subject="op", roles=frozenset({r.CONSOLE_ADMIN})), None) as client:
        response = client.get("/api/graph-sync/runs")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "GRAPH_SYNC_UNAVAILABLE"
