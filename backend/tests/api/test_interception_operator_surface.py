"""Opening and answering a held AI request, over HTTP.

Wave D2's operator surface. The queue listing was already there and is
deliberately identity-and-status only; these are the two endpoints that let
someone actually do the work.

Three properties matter more than the happy path:

* **Unsealing is a separate, capability-gated act.** The payload can contain
  block 5 UNTRUSTED SOURCE SAMPLE — rows out of a customer's database — which is
  why it is sealed at rest. Fetching it needs `ai.interception.act`, the
  narrower capability, not the read one that browsing the queue uses.
* **A second answer loses.** The store's `answer` is a conditional write on
  `PENDING`; the surface has to report that as 409 rather than swallow it.
* **An absent store is 503 here and `[]` on the listing.** "No pending
  interceptions" is a true answer for a deployment that never uses the manual
  path; "here is interception X" has no truthful empty form, and answering 404
  would read as "already handled".
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from return_platform.ai.interception.records import (
    Interception,
    InterceptionStatus,
    ResumeCommand,
)
from return_platform.ai.interception.store import InterceptionNotPending
from return_platform.api.canonical_ai import router
from return_platform.security import capabilities as caps
from return_platform.security import roles as r
from return_platform.security.principal import Principal

_ID = "int-1"


def _interception(status: InterceptionStatus = InterceptionStatus.PENDING) -> Interception:
    now = datetime.now(UTC)
    return Interception(
        interception_id=_ID,
        task_id="GRAPH_SCHEMA_PROPOSAL_V1",
        status=status,
        resume=ResumeCommand(run_id="run-1", thread_id="thread-1", workflow_id="wf-1"),
        created_at=now,
        expires_at=now + timedelta(hours=1),
        answered_at=now if status is InterceptionStatus.ANSWERED else None,
        answered_by="operator" if status is InterceptionStatus.ANSWERED else None,
        response_text="an answer" if status is InterceptionStatus.ANSWERED else None,
    )


class _Store:
    """Only the methods this surface uses.

    `cancel` mirrors the real store's contract exactly, including the part that
    matters: it returns *silently* when the record is not PENDING rather than
    raising. A stub that raised would let a handler relying on an exception pass
    here and be wrong in production.
    """

    def __init__(
        self,
        *,
        pending: bool = True,
        payload: dict[str, Any] | None = None,
        exists: bool = True,
    ) -> None:
        self._pending = pending
        self._payload = payload if payload is not None else {"prompt": "sealed content"}
        self._exists = exists
        self._status = InterceptionStatus.PENDING if pending else InterceptionStatus.ANSWERED
        self.answered_with: tuple[str, str] | None = None
        self.cancel_calls = 0

    async def list_pending(self, *, limit: int = 100) -> list[Interception]:
        del limit
        return [_interception()] if self._pending else []

    async def list_by_status(
        self, *, statuses: list[InterceptionStatus], limit: int = 100
    ) -> list[Interception]:
        del limit
        # One record per requested status, so a caller asking for terminal
        # history gets terminal history rather than the pending queue wearing a
        # different label.
        return [_interception(status) for status in statuses]

    async def request_payload(self, interception_id: str) -> dict[str, Any] | None:
        return self._payload if interception_id == _ID else None

    async def get(self, interception_id: str) -> Interception | None:
        if not self._exists or interception_id != _ID:
            return None
        return _interception(self._status)

    async def answer(
        self, *, interception_id: str, response_text: str, answered_by: str
    ) -> Interception:
        if not self._pending:
            raise InterceptionNotPending(
                f"interception {interception_id!r} is ANSWERED, not PENDING"
            )
        self.answered_with = (response_text, answered_by)
        self._pending = False
        self._status = InterceptionStatus.ANSWERED
        return _interception(InterceptionStatus.ANSWERED)

    async def cancel(self, *, interception_id: str, status: InterceptionStatus) -> None:
        self.cancel_calls += 1
        if not self._exists or not self._pending:
            return
        self._pending = False
        self._status = status


def _client(store: object | None, *role_names: str) -> Iterator[TestClient]:
    app = FastAPI()

    @app.middleware("http")
    async def _attach(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.principal = Principal(subject="operator", roles=frozenset(role_names))
        request.state.correlation_id = "test-correlation-id"
        return await call_next(request)

    app.include_router(router)
    app.state.ai_interception_store = store
    with TestClient(app) as client:
        yield client


@pytest.fixture
def admin_store() -> Iterator[tuple[TestClient, _Store]]:
    store = _Store()
    for client in _client(store, r.CONSOLE_ADMIN):
        yield client, store


def test_an_operator_can_read_the_sealed_request(
    admin_store: tuple[TestClient, _Store],
) -> None:
    client, _ = admin_store

    response = client.get(f"/api/ai/interceptions/{_ID}/request")

    assert response.status_code == 200, response.text
    assert response.json()["data"] == {"prompt": "sealed content"}


def test_reading_the_request_needs_the_act_capability() -> None:
    """A role with `ai.interception.read` but not `.act` may browse the queue and
    must not unseal a prompt. Asserted through a role that genuinely lacks it
    rather than by mocking the dependency."""
    reader_roles = [
        role
        for role in sorted(r.ALL_ROLES)
        if caps.AI_INTERCEPTION_ACT not in caps.capabilities_for_roles(frozenset({role}))
    ]
    assert reader_roles, "every role can act; this test cannot distinguish the capabilities"

    for client in _client(_Store(), reader_roles[0]):
        response = client.get(f"/api/ai/interceptions/{_ID}/request")

    assert response.status_code == 403, response.text


def test_the_queue_listing_never_carries_the_payload(
    admin_store: tuple[TestClient, _Store],
) -> None:
    """The whole reason unsealing is a separate endpoint. If the listing ever
    starts embedding prompts, sealing them at rest stops meaning anything."""
    client, _ = admin_store

    listed = client.get("/api/ai/interceptions").json()["data"]

    assert listed, "expected a pending interception"
    for item in listed:
        assert "prompt" not in item
        assert "requestPayload" not in item
        assert set(item) == {
            "interceptionId",
            "taskId",
            "status",
            # Which hold point this is: a request that has not been sent, or a
            # response that has come back. A routing label like `taskId`, and
            # like `taskId` it says nothing about the content -- which is what
            # keeps this an allowlist rather than a growing leak.
            "point",
            "createdAt",
            "expiresAt",
            "answeredBy",
        }


def test_answering_records_the_text_and_the_actor(
    admin_store: tuple[TestClient, _Store],
) -> None:
    client, store = admin_store

    response = client.post(
        f"/api/ai/interceptions/{_ID}/answer", json={"responseText": "use the second candidate"}
    )

    assert response.status_code == 200, response.text
    assert store.answered_with == ("use the second candidate", "operator")
    assert response.json()["data"]["status"] == InterceptionStatus.ANSWERED.value


def test_a_second_answer_is_refused(admin_store: tuple[TestClient, _Store]) -> None:
    """Two operators answering at once produce one winner. The store enforces it
    with a conditional write; this asserts the surface reports it as a conflict
    rather than swallowing it into a 200."""
    client, _ = admin_store
    first = client.post(f"/api/ai/interceptions/{_ID}/answer", json={"responseText": "mine"})
    assert first.status_code == 200

    second = client.post(f"/api/ai/interceptions/{_ID}/answer", json={"responseText": "no, mine"})

    assert second.status_code == 409, second.text
    assert "not PENDING" in second.json()["detail"]


def test_an_empty_answer_is_rejected(admin_store: tuple[TestClient, _Store]) -> None:
    """A blank answer would resume the workflow with nothing, which is worse than
    leaving it held."""
    client, store = admin_store

    response = client.post(f"/api/ai/interceptions/{_ID}/answer", json={"responseText": ""})

    assert response.status_code == 422, response.text
    assert store.answered_with is None


def test_an_unknown_interception_is_404(admin_store: tuple[TestClient, _Store]) -> None:
    client, _ = admin_store

    assert client.get("/api/ai/interceptions/nope/request").status_code == 404


def test_without_a_store_the_operator_endpoints_are_503_but_the_queue_is_empty() -> None:
    """The asymmetry, stated as a test because it looks like an inconsistency
    until you see why."""
    for client in _client(None, r.CONSOLE_ADMIN):
        assert client.get("/api/ai/interceptions").json()["data"] == []
        assert client.get(f"/api/ai/interceptions/{_ID}/request").status_code == 503
        assert (
            client.post(
                f"/api/ai/interceptions/{_ID}/answer", json={"responseText": "x"}
            ).status_code
            == 503
        )
        assert client.post(f"/api/ai/interceptions/{_ID}/cancel").status_code == 503


# --- cancelling --------------------------------------------------------------
#
# The counterpart to answering. Without it the only ways out of PENDING are to
# answer or to wait for `expiresAt`, so an operator who can see the prompt is
# wrong has to invent an answer or leave a caller blocked.


def test_an_operator_can_cancel_a_pending_interception(
    admin_store: tuple[TestClient, _Store],
) -> None:
    client, store = admin_store

    response = client.post(f"/api/ai/interceptions/{_ID}/cancel")

    assert response.status_code == 200, response.text
    assert response.json()["data"] == {"interceptionId": _ID, "status": "CANCELLED"}
    assert store.cancel_calls == 1


def test_cancelling_an_already_answered_interception_is_409() -> None:
    """The case the handler has to work for.

    `store.cancel` returns *silently* when the record is not PENDING -- its other
    caller is the expiry sweep, for which losing the race is normal. A handler
    that trusted the absence of an exception would answer 200 here and tell the
    operator they cancelled something that was in fact answered. The outcome is
    read back instead.
    """
    store = _Store(pending=False)
    for client in _client(store, r.CONSOLE_ADMIN):
        response = client.post(f"/api/ai/interceptions/{_ID}/cancel")

    assert response.status_code == 409, response.text
    assert "ANSWERED" in response.json()["detail"]


def test_cancelling_an_unknown_interception_is_404() -> None:
    store = _Store(exists=False)
    for client in _client(store, r.CONSOLE_ADMIN):
        response = client.post("/api/ai/interceptions/nope/cancel")

    assert response.status_code == 404, response.text


def test_cancelling_needs_the_act_capability() -> None:
    """Ending someone's request is an act, not a read."""
    reader_roles = [
        role
        for role in sorted(r.ALL_ROLES)
        if caps.AI_INTERCEPTION_ACT not in caps.capabilities_for_roles(frozenset({role}))
    ]
    assert reader_roles, "every role can act; this test cannot distinguish the capabilities"
    store = _Store()
    for client in _client(store, reader_roles[0]):
        response = client.post(f"/api/ai/interceptions/{_ID}/cancel")

    assert response.status_code == 403, response.text
    assert store.cancel_calls == 0


# ---------------------------------------------------------------------------
# The queue can be read by status (UIAUDIT-009)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status",
    [
        InterceptionStatus.PENDING,
        InterceptionStatus.ANSWERED,
        InterceptionStatus.ALLOWED,
        InterceptionStatus.CANCELLED,
        InterceptionStatus.EXPIRED,
    ],
)
def test_every_status_can_be_asked_for_and_answered(status: InterceptionStatus) -> None:
    """One fixture per tile, and every one of them can be non-zero.

    Three of the operator screen's tiles read zero in every deployment, and not
    because the queue was quiet: the endpoint took no parameters, so
    `?status=ALLOWED` was accepted and silently ignored, and the screen had
    nothing to count but a pending-only list. A tile that is structurally
    incapable of being non-zero is worse than an absent one -- it reports a fact
    about the world rather than about itself.
    """
    for client in _client(_Store(), r.CONSOLE_ADMIN):
        response = client.get(f"/api/ai/interceptions?status={status.value}")

        assert response.status_code == 200
        assert [row["status"] for row in response.json()["data"]] == [status.value]


def test_several_statuses_can_be_asked_for_at_once() -> None:
    """History is one question, not four round trips."""
    for client in _client(_Store(), r.CONSOLE_ADMIN):
        response = client.get(
            "/api/ai/interceptions?status=ANSWERED&status=CANCELLED&status=EXPIRED"
        )

        assert response.status_code == 200
        assert [row["status"] for row in response.json()["data"]] == [
            "ANSWERED",
            "CANCELLED",
            "EXPIRED",
        ]


def test_asking_for_nothing_still_answers_the_pending_queue() -> None:
    """The default is unchanged, so no existing caller moved."""
    for client in _client(_Store(pending=True), r.CONSOLE_ADMIN):
        response = client.get("/api/ai/interceptions")

        assert response.status_code == 200
        assert [row["status"] for row in response.json()["data"]] == ["PENDING"]


def test_an_unknown_status_is_refused_rather_than_ignored() -> None:
    """Silently ignoring the filter is how the tiles came to read zero."""
    for client in _client(_Store(), r.CONSOLE_ADMIN):
        response = client.get("/api/ai/interceptions?status=NOT_A_STATUS")

        assert response.status_code == 422
