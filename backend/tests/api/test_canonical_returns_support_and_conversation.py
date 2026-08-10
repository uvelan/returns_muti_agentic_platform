"""`/api/returns/{id}/support` and `/{id}/conversation` — the two parked reads.

They were parked because each looked like an unresolved design question. Neither
was; both were missing accessors, and finding that out is most of what these
tests record.

**`/support` looked like two competing stores.** `support_cases` and
`support_work_items` are the artifact/evidence situation again: a shared word
over different things. A case is raised *by the platform* when a return flow
fails, carrying a type, a priority and an SLA. A work item is opened *by a
person* through the support workbench, carrying a message thread and an
eleven-state lifecycle. Different creators, different lifecycles. Both are at
most one per return -- each collection has `sessionId` uniquely indexed -- so
the response is two nullable fields, not a merge and not a list.

**`/conversation` was genuinely unanswerable, for a smaller reason.**
`returnSessionId` is stamped on the conversation when `submit_details` creates
the return, so the link existed in the data -- but only in one direction, with
no accessor and no index for the reverse. Given a session there was no way to
find its conversation. One method and one sparse index closed it.

Stubs, not Mongo: what is under test is which accessor each endpoint calls, how
it handles absence, and the 404-before-anything rule. The accessors themselves
are exercised against real Mongo by the repository and service suites.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from return_platform.api import canonical_returns
from return_platform.api.canonical_returns import router
from return_platform.operations.models import SupportCaseView
from return_platform.security import roles as r
from return_platform.security.principal import Principal

_SESSION_ID = "ret-1"
_NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


def _case() -> SupportCaseView:
    return SupportCaseView(
        id="case-1",
        sessionId=_SESSION_ID,
        caseType="FLOW_FAILURE",
        status="OPEN",
        priority="HIGH",
        reason="The return flow failed and requires operator review.",
        slaDueAt=_NOW + timedelta(hours=4),
        slaBreached=False,
        version=0,
        createdAt=_NOW,
        updatedAt=_NOW,
    )


class _WorkItem:
    """Duck-typed: the handler only calls `model_dump`."""

    def __init__(self) -> None:
        self.calls = 0

    def model_dump(self, mode: str = "python") -> dict[str, Any]:
        del mode
        return {"id": "wi-1", "sessionId": _SESSION_ID, "status": "IN_PROGRESS"}


class _Conversation:
    def model_dump(self, mode: str = "python") -> dict[str, Any]:
        del mode
        return {"id": "conv-1", "returnSessionId": _SESSION_ID, "status": "DISCOVERY"}


class StubRepository:
    def __init__(self, *, session_exists: bool = True, case: SupportCaseView | None = None) -> None:
        self.session_exists = session_exists
        self._case = case
        self.calls: list[str] = []

    async def get_return(self, session_id: str) -> dict[str, Any] | None:
        self.calls.append("get_return")
        return {"id": session_id} if self.session_exists else None

    async def get_support_case_for_session(self, session_id: str) -> SupportCaseView | None:
        self.calls.append("get_support_case_for_session")
        return self._case


class StubSupportService:
    def __init__(self, work_item: object | None) -> None:
        self._work_item = work_item
        self.calls = 0

    async def get_work_item_for_session(self, session_id: str) -> object | None:
        self.calls += 1
        return self._work_item


class StubConversationService:
    def __init__(self, conversation: object | None) -> None:
        self._conversation = conversation
        self.calls = 0

    async def get_for_session(self, session_id: str) -> object | None:
        self.calls += 1
        return self._conversation


def _client(
    *,
    repository: StubRepository,
    support: object | None = None,
    conversation: object | None = None,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    app = FastAPI()

    @app.middleware("http")
    async def _attach(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.principal = Principal(subject="reader", roles=frozenset({r.CONSOLE_VIEWER}))
        request.state.correlation_id = "test-correlation-id"
        return await call_next(request)

    monkeypatch.setattr(
        canonical_returns, "resolve_operational_repository", lambda request: repository
    )
    monkeypatch.setattr(canonical_returns, "_optional_support_service", lambda request: support)
    monkeypatch.setattr(
        canonical_returns,
        "build_associate_conversation_service",
        lambda request: conversation,
    )
    app.include_router(router)
    with TestClient(app) as client:
        yield client


# --- support -----------------------------------------------------------------


def test_support_returns_both_records_side_by_side(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two fields, because they are two things. A response that merged them
    would lose which of the two a caller is looking at -- and only one of them
    was opened by a human."""
    repository = StubRepository(case=_case())
    service = StubSupportService(_WorkItem())
    for client in _client(repository=repository, support=service, monkeypatch=monkeypatch):
        response = client.get(f"/api/returns/{_SESSION_ID}/support")

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["case"]["caseType"] == "FLOW_FAILURE"
    assert data["workItem"]["id"] == "wi-1"


def test_a_return_that_never_failed_has_a_null_case(monkeypatch: pytest.MonkeyPatch) -> None:
    """The common shape. `null` is the answer, not 404 -- the return exists."""
    repository = StubRepository(case=None)
    for client in _client(
        repository=repository, support=StubSupportService(None), monkeypatch=monkeypatch
    ):
        response = client.get(f"/api/returns/{_SESSION_ID}/support")

    assert response.status_code == 200, response.text
    assert response.json()["data"] == {"case": None, "workItem": None}


def test_the_case_still_returns_when_the_work_item_service_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reason `_optional_support_service` exists.

    `api/return_support.py` 503s for this condition, which is right for a router
    whose every endpoint is a work item. Here it would hide the case -- which
    needs only the operational repository and is the record an operator is most
    likely looking for, since the platform raised it because something broke.
    """
    repository = StubRepository(case=_case())
    for client in _client(repository=repository, support=None, monkeypatch=monkeypatch):
        response = client.get(f"/api/returns/{_SESSION_ID}/support")

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["case"]["caseType"] == "FLOW_FAILURE"
    assert data["workItem"] is None


def test_support_404s_for_a_missing_session_before_reading_anything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = StubRepository(session_exists=False)
    service = StubSupportService(_WorkItem())
    for client in _client(repository=repository, support=service, monkeypatch=monkeypatch):
        response = client.get(f"/api/returns/{_SESSION_ID}/support")

    assert response.status_code == 404, response.text
    assert repository.calls == ["get_return"]
    assert service.calls == 0


# --- conversation ------------------------------------------------------------


def test_conversation_returns_the_conversation_the_return_came_from(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = StubRepository()
    service = StubConversationService(_Conversation())
    for client in _client(repository=repository, conversation=service, monkeypatch=monkeypatch):
        response = client.get(f"/api/returns/{_SESSION_ID}/conversation")

    assert response.status_code == 200, response.text
    assert response.json()["data"]["id"] == "conv-1"
    assert service.calls == 1


def test_a_system_channel_return_has_no_conversation_and_that_is_a_200(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """404 would mean "no such return", which the parent check already covers.
    A caller could not tell the two apart, and in a batch-driven deployment the
    "no conversation" case is the majority."""
    repository = StubRepository()
    for client in _client(
        repository=repository, conversation=StubConversationService(None), monkeypatch=monkeypatch
    ):
        response = client.get(f"/api/returns/{_SESSION_ID}/conversation")

    assert response.status_code == 200, response.text
    assert response.json()["data"] is None


def test_conversation_404s_for_a_missing_session_before_looking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = StubRepository(session_exists=False)
    service = StubConversationService(_Conversation())
    for client in _client(repository=repository, conversation=service, monkeypatch=monkeypatch):
        response = client.get(f"/api/returns/{_SESSION_ID}/conversation")

    assert response.status_code == 404, response.text
    assert service.calls == 0


# --- the accessors these endpoints needed ------------------------------------


def test_the_reverse_conversation_lookup_exists_and_is_indexed() -> None:
    """Both halves, because either alone is a trap.

    Without the method the endpoint cannot be written; without the index it is a
    collection scan on every call, on a collection that grows with every
    discovery attempt whether or not it ever became a return.
    """
    import ast
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "return_platform"
        / "operations"
        / "associate_flow.py"
    )
    text = source.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(source))

    assert any(
        isinstance(node, ast.AsyncFunctionDef) and node.name == "get_for_session"
        for node in ast.walk(tree)
    ), "AssociateConversationService.get_for_session is missing"
    assert "returnSessionId" in text
    assert (
        'create_index(\n            [("returnSessionId", 1), ("createdAt", -1)], sparse=True\n        )'
        in text
        or ('("returnSessionId", 1)' in text and "sparse=True" in text)
    ), "the reverse lookup has no index"


def test_the_work_item_session_lookup_relies_on_an_existing_unique_index() -> None:
    """No new index was needed, and the reason matters: `sessionId` is uniquely
    indexed on `support_work_items`, which is *why* the response field is
    singular rather than a list."""
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "return_platform"
        / "operations"
        / "return_support"
        / "service.py"
    ).read_text(encoding="utf-8")

    assert 'create_index("sessionId", unique=True)' in source
    assert "async def get_work_item_for_session" in source
