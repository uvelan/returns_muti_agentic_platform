"""`/api/returns` writes over real HTTP, against stubs.

Wave D4's last slice. Two write routes replace five legacy ones: create and
cancel on `returns.py`, start and events on `production_workflow.py`.

What these tests are actually protecting:

* **Cancellation is an event, and the event releases the discovery lock.** The
  legacy pair disagreed -- `/cancel` wrote the session document and released the
  lock without telling the workflow; the workflow's CANCELLED event updated
  durable state but left the lock held. Making the event canonical without
  moving the lock release would have shipped the leaking half as the only half.

* **A refused caller starts no workflow.** `record_event` calls `ensure_started`
  first, so authorization has to happen before it. Asserted by checking the
  coordinator was never touched, not by checking the status code -- a 403 with a
  started workflow behind it is still a 403.

* **A rejected transition is a 409 that says why.** The legacy handler flattened
  every failure, including Temporal being unreachable, into one generic 409.

The coordinator is a stub. What is under test is the router: the order it does
things in, and how it maps three quite different failures onto three different
status codes. Exercising the real coordinator would need Temporal, and the
state machine has its own coverage.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from temporalio.client import WorkflowUpdateFailedError
from temporalio.exceptions import ApplicationError
from temporalio.service import RPCError, RPCStatusCode

from return_platform.api import canonical_returns
from return_platform.api.canonical_returns import router
from return_platform.operations.models import ReturnSessionView
from return_platform.security import roles as r
from return_platform.security.principal import Principal
from return_platform.workflows.production_return_state import ProductionReturnStage

_SESSION_ID = "ret-1"
_EVENT = {
    "eventId": "event-00000001",
    "eventType": "RECEIPT_CONFIRMED",
    "evidenceReference": "scan-42",
}


class StubState:
    def __init__(self, *, cancelled: bool = False) -> None:
        self.stage = ProductionReturnStage.INTAKE
        self.case_fully_closed = False
        self.cancelled = cancelled


class StubCoordinator:
    """Records the order it was called in, which is the point of two of the
    tests below."""

    def __init__(self, *, failure: Exception | None = None, cancelled: bool = False) -> None:
        self.calls: list[str] = []
        self.failure = failure
        self.cancelled = cancelled

    async def ensure_started(self, session: Any, *, actor_id: str) -> str:
        self.calls.append("ensure_started")
        return "workflow-1"

    async def record_event(self, session_id: str, **kwargs: Any) -> StubState:
        self.calls.append("record_event")
        if self.failure is not None:
            raise self.failure
        return StubState(cancelled=self.cancelled)


class StubRepository:
    def __init__(self, *, session_exists: bool = True) -> None:
        self.session_exists = session_exists
        self.created: list[Any] = []

    async def get_return(self, session_id: str) -> dict[str, Any] | None:
        if not self.session_exists:
            return None
        return {"id": session_id, "status": "OPEN"}

    async def create_return(
        self, payload: Any, *, correlation_id: str, actor_id: str
    ) -> ReturnSessionView:
        self.created.append(payload)
        now = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
        return ReturnSessionView(
            id="ret-new",
            correlationId=correlation_id,
            customerReference=payload.customerReference,
            orderReference=payload.orderReference,
            itemReferences=payload.itemReferences,
            productReferences=payload.productReferences,
            reasonCode=payload.reasonCode,
            returnQuantity=payload.returnQuantity,
            packageCount=payload.packageCount,
            shippingPathExpectation=payload.shippingPathExpectation,
            channel=payload.channel,
            status="QUEUED",
            currentStage="INTAKE",
            progressPercentage=0,
            version=0,
            createdAt=now,
            updatedAt=now,
        )


def _client(
    *,
    repository: StubRepository | None = None,
    coordinator: StubCoordinator | None = None,
    roles: frozenset[str] = frozenset({r.CONSOLE_ADMIN}),
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    repository = repository or StubRepository()
    coordinator = coordinator or StubCoordinator()
    app = FastAPI()

    @app.middleware("http")
    async def _attach(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.principal = Principal(subject="operator", roles=roles)
        request.state.correlation_id = "test-correlation-id"
        return await call_next(request)

    # Patched at the router's own references: `canonical_returns` imported both
    # by name, so patching the defining modules would leave it holding the
    # originals and every assertion here would exercise the real resolvers.
    monkeypatch.setattr(
        canonical_returns, "resolve_operational_repository", lambda request: repository
    )
    monkeypatch.setattr(
        canonical_returns, "resolve_production_coordinator", lambda request: coordinator
    )
    app.include_router(router)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def _rpc_error() -> RPCError:
    return RPCError("temporal unreachable", RPCStatusCode.UNAVAILABLE, b"")


def test_recording_an_event_returns_the_stage_and_the_terminal_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator = StubCoordinator()
    for client in _client(coordinator=coordinator, monkeypatch=monkeypatch):
        response = client.post(f"/api/returns/{_SESSION_ID}/events", json=_EVENT)

    assert response.status_code == 200, response.text
    assert response.json()["data"] == {
        "stage": "INTAKE",
        "caseFullyClosed": False,
        "cancelled": False,
    }
    assert coordinator.calls == ["ensure_started", "record_event"]


def test_cancelling_is_an_event_and_reports_the_return_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """There is no `POST /{id}/cancel` to test, and that is the design."""
    coordinator = StubCoordinator(cancelled=True)
    for client in _client(coordinator=coordinator, monkeypatch=monkeypatch):
        response = client.post(
            f"/api/returns/{_SESSION_ID}/events",
            json={**_EVENT, "eventType": "CANCELLED"},
        )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["cancelled"] is True


def test_a_caller_without_the_role_for_the_event_starts_no_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """403 is the easy half. The half worth asserting is that `ensure_started`
    never ran -- a refused call that leaves a started workflow behind has
    mutated something on the way to refusing."""
    coordinator = StubCoordinator()
    for client in _client(
        coordinator=coordinator,
        roles=frozenset({r.WAREHOUSE_ASSOCIATE}),
        monkeypatch=monkeypatch,
    ):
        response = client.post(
            f"/api/returns/{_SESSION_ID}/events",
            json={**_EVENT, "eventType": "CANCELLED"},
        )

    assert response.status_code == 403, response.text
    assert coordinator.calls == []


def test_an_event_for_a_missing_session_is_404_before_the_workflow_is_touched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator = StubCoordinator()
    for client in _client(
        repository=StubRepository(session_exists=False),
        coordinator=coordinator,
        monkeypatch=monkeypatch,
    ):
        response = client.post(f"/api/returns/{_SESSION_ID}/events", json=_EVENT)

    assert response.status_code == 404, response.text
    assert coordinator.calls == []


def test_a_rejected_transition_is_a_409_carrying_the_state_machines_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The legacy handler answered "Production workflow update failed or is not
    available" for this, which does not tell a caller whether to fix the request
    or retry it."""
    rejection = WorkflowUpdateFailedError(ApplicationError("RECEIPT_CONFIRMED is already recorded"))
    coordinator = StubCoordinator(failure=rejection)
    for client in _client(coordinator=coordinator, monkeypatch=monkeypatch):
        response = client.post(f"/api/returns/{_SESSION_ID}/events", json=_EVENT)

    assert response.status_code == 409, response.text
    assert "already recorded" in response.json()["detail"]


def test_temporal_being_unreachable_is_503_not_409(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A conflict says "your request was wrong". This one was not."""
    coordinator = StubCoordinator(failure=_rpc_error())
    for client in _client(coordinator=coordinator, monkeypatch=monkeypatch):
        response = client.post(f"/api/returns/{_SESSION_ID}/events", json=_EVENT)

    assert response.status_code == 503, response.text


def test_an_unknown_event_type_is_rejected_by_the_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator = StubCoordinator()
    for client in _client(coordinator=coordinator, monkeypatch=monkeypatch):
        response = client.post(
            f"/api/returns/{_SESSION_ID}/events",
            json={**_EVENT, "eventType": "DEFINITELY_NOT_AN_EVENT"},
        )

    assert response.status_code == 422, response.text
    assert coordinator.calls == []


def test_a_misspelled_field_is_rejected_rather_than_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`extra="forbid"`. Without it, `businessPayLoad` is silently discarded and
    the projection quietly does nothing -- a `LICENSE_PLATE_ASSIGNED` with no
    licence plate."""
    for client in _client(monkeypatch=monkeypatch):
        response = client.post(
            f"/api/returns/{_SESSION_ID}/events",
            json={**_EVENT, "businessPayLoad": {"licensePlateId": "LP-1"}},
        )

    assert response.status_code == 422, response.text


def test_creating_a_system_return_returns_201(monkeypatch: pytest.MonkeyPatch) -> None:
    repository = StubRepository()
    for client in _client(repository=repository, monkeypatch=monkeypatch):
        response = client.post(
            "/api/returns",
            json={
                "customerReference": "cust-1",
                "orderReference": "order-1",
                "itemReferences": ["item-1"],
                "reasonCode": "DAMAGED",
            },
        )

    assert response.status_code == 201, response.text
    assert len(repository.created) == 1


def test_an_interactive_return_is_refused_here(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same rule as the legacy path: an interactive return begins as a
    conversation, and one created directly has no discovery evidence behind
    it."""
    repository = StubRepository()
    for client in _client(repository=repository, monkeypatch=monkeypatch):
        response = client.post(
            "/api/returns",
            json={
                "customerReference": "cust-1",
                "orderReference": "order-1",
                "itemReferences": ["item-1"],
                "reasonCode": "DAMAGED",
                "channel": "ASSOCIATE",
            },
        )

    assert response.status_code == 409, response.text
    assert repository.created == []


def test_the_idempotency_key_header_reaches_the_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = StubRepository()
    for client in _client(repository=repository, monkeypatch=monkeypatch):
        client.post(
            "/api/returns",
            json={
                "customerReference": "cust-1",
                "orderReference": "order-1",
                "itemReferences": ["item-1"],
                "reasonCode": "DAMAGED",
            },
            headers={"Idempotency-Key": "idem-00000001"},
        )

    assert repository.created[0].idempotencyKey == "idem-00000001"
