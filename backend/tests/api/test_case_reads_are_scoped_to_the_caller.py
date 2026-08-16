"""`/api/cases` answers about the caller's own returns and nobody else's.

The list was scoped from the start; the detail read was not. `GET
/api/cases/{id}` took an id and returned the case -- a case that carries the
customer's order, the lines they are returning and whatever the associate typed
about them -- to anyone holding a console-viewer role in any tenant.

Also covers the `conversationId` filter W1.8 added, which is what lets a
reopened conversation find the return it raised. A filter that is not scoped
would be a second way to read someone else's case, so both live here.

Over real HTTP against a stub repository: what is under test is the router's
scoping decision, and a real datastore would move the assertion from "the
handler refused" to "the query found nothing", which is a different claim.

The route now serves `CaseProjection`, so `load_case_projection_state` is what
the stub answers. It deliberately ignores ownership -- that is the real method's
behaviour, and it is why the check has to live in the handler. What the
projection *contains* is `test_case_projection_route.py`'s subject; this module
only ever asks who was allowed to see it.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from return_platform.api import cases as cases_module
from return_platform.api.cases import router
from return_platform.configuration.return_configuration import (
    LoadedReturnConfiguration,
    load_return_configuration,
)
from return_platform.configuration.settings import DEFAULT_RETURN_CONFIGURATION_PATH
from return_platform.operations.case_projection.assembly import (
    CaseAggregateDocuments,
    assemble_case_projection_state,
)
from return_platform.operations.case_projection.contract import CaseProjectionState
from return_platform.security import roles as r
from return_platform.security.principal import Principal

TENANT = "tenant-a"
PRINCIPAL = "associate-1"
NOW = datetime(2026, 8, 12, tzinfo=UTC)


def _case(
    case_id: str = "case-1",
    *,
    tenant_id: str = TENANT,
    principal_id: str = PRINCIPAL,
    conversation_id: str | None = "disc-1",
) -> dict[str, Any]:
    return {
        "caseId": case_id,
        "tenantId": tenant_id,
        "principalId": principal_id,
        "branchId": None,
        "status": "AWAITING_SUPPORT",
        "channelAConversationId": conversation_id,
        "channelBWorkItemId": None,
        "confirmedOrderReference": "CW273354",
        "version": 0,
        "createdAt": NOW,
        "updatedAt": NOW,
    }


class StubRepository:
    """Answers with whatever it was given, and records how it was asked.

    `load_case_projection_state` deliberately ignores ownership -- that is the
    repository's real behaviour, and a stub that filtered would make the router
    look correct whether or not it checked anything.
    """

    def __init__(self, *, stored: dict[str, Any] | None = None) -> None:
        self._stored = stored
        self.conversation_queries: list[dict[str, Any]] = []
        self.principal_queries: list[dict[str, Any]] = []

    async def load_case_projection_state(self, case_id: str) -> CaseProjectionState | None:
        if self._stored is None:
            return None
        return assemble_case_projection_state(CaseAggregateDocuments(case=self._stored))

    async def get_case_by_conversation(
        self,
        conversation_id: str,
        *,
        tenant_id: str | None = None,
        principal_id: str | None = None,
    ) -> dict[str, Any] | None:
        self.conversation_queries.append(
            {
                "conversationId": conversation_id,
                "tenantId": tenant_id,
                "principalId": principal_id,
            }
        )
        if self._stored is None:
            return None
        if self._stored.get("tenantId") != tenant_id:
            return None
        if self._stored.get("principalId") != principal_id:
            return None
        return self._stored

    async def list_cases_for_principal(
        self, *, tenant_id: str, principal_id: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        self.principal_queries.append({"tenantId": tenant_id, "principalId": principal_id})
        return [] if self._stored is None else [self._stored]


@pytest.fixture(autouse=True)
def _stub_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patched at the router's own reference, not at the definition site."""
    monkeypatch.setattr(
        cases_module,
        "resolve_operational_repository",
        lambda request: request.app.state.stub_repository,
    )


def _client(repository: StubRepository, *, tenant_id: str = TENANT) -> Iterator[TestClient]:
    app = FastAPI()

    @app.middleware("http")
    async def _attach(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.principal = Principal(subject=PRINCIPAL, roles=frozenset({r.CONSOLE_VIEWER}))
        request.state.tenant_id = tenant_id
        request.state.correlation_id = "test-correlation-id"
        return await call_next(request)

    app.include_router(router)
    app.state.stub_repository = repository
    # The route resolves the operator's return-method requirement table from the
    # active release and refuses without one, so every client here carries the
    # shipped configuration.
    app.state.return_configuration = LoadedReturnConfiguration(
        configuration=load_return_configuration(DEFAULT_RETURN_CONFIGURATION_PATH).configuration,
        path=DEFAULT_RETURN_CONFIGURATION_PATH,
        sha256="0" * 64,
    )
    with TestClient(app) as client:
        yield client


def test_the_caller_can_read_their_own_case() -> None:
    repository = StubRepository(stored=_case())
    for client in _client(repository):
        response = client.get("/api/cases/case-1")

    assert response.status_code == 200, response.text
    assert response.json()["data"]["caseId"] == "case-1"


def test_another_principals_case_reads_as_absent() -> None:
    """404, not 403.

    A 403 on a guessed id confirms the id exists, which is most of what an
    attacker enumerating cases wants to learn.
    """
    repository = StubRepository(stored=_case(principal_id="someone-else"))
    for client in _client(repository):
        response = client.get("/api/cases/case-1")

    assert response.status_code == 404, response.text
    assert response.json()["detail"]["code"] == "CASE_NOT_FOUND"


def test_another_tenants_case_reads_as_absent() -> None:
    """The same principal id in a second tenant is a different person.

    Checking only the principal would let a subject name repeated across
    tenants read across the boundary -- the exact hole closed in the
    conversation store.
    """
    repository = StubRepository(stored=_case(tenant_id="tenant-b"))
    for client in _client(repository):
        response = client.get("/api/cases/case-1")

    assert response.status_code == 404, response.text


def test_the_conversation_filter_narrows_to_one_case() -> None:
    repository = StubRepository(stored=_case())
    for client in _client(repository):
        response = client.get("/api/cases", params={"conversationId": "disc-1"})

    assert response.status_code == 200, response.text
    assert [row["caseId"] for row in response.json()["data"]] == ["case-1"]
    # The scope travels into the query rather than being checked afterwards:
    # a conversation id is guessable, and a handler that reads first has
    # already read someone else's case by the time it decides not to send it.
    assert repository.conversation_queries == [
        {"conversationId": "disc-1", "tenantId": TENANT, "principalId": PRINCIPAL}
    ]
    assert repository.principal_queries == []


def test_the_conversation_filter_cannot_reach_another_tenants_case() -> None:
    repository = StubRepository(stored=_case())
    for client in _client(repository, tenant_id="tenant-b"):
        response = client.get("/api/cases", params={"conversationId": "disc-1"})

    assert response.status_code == 200, response.text
    assert response.json()["data"] == []


def test_a_conversation_that_raised_nothing_is_an_empty_list() -> None:
    """Not a 404. "This chat never became a return" is an answer.

    The copilot asks this on every resume, and an error status would make the
    ordinary case -- a conversation that only ever searched -- look broken.
    """
    repository = StubRepository(stored=None)
    for client in _client(repository):
        response = client.get("/api/cases", params={"conversationId": "disc-unknown"})

    assert response.status_code == 200, response.text
    assert response.json()["data"] == []


def test_the_unfiltered_list_is_still_scoped_to_the_caller() -> None:
    repository = StubRepository(stored=_case())
    for client in _client(repository):
        response = client.get("/api/cases")

    assert response.status_code == 200, response.text
    assert repository.principal_queries == [{"tenantId": TENANT, "principalId": PRINCIPAL}]
    assert repository.conversation_queries == []
