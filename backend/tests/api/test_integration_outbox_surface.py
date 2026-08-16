"""What the outbox read model tells an operator about a command that stopped.

`status` says what the outbox will do next; it does not say whether anything is
owed. A Support answer whose case workflow had already completed comes to rest
at `DEAD_LETTER` — delivery stopped, correctly and deliberately — and the RMA in
it has still never reached the case. The only field that distinguishes that from
a command nobody needs any more is `reconciliationState`, which the dispatcher
stamps beside the status precisely so the two questions stay separate.

Left off this view, the surface answers the first question and silently drops
the second, and the operator reading it has no way to tell an abandoned command
from a lost RMA. Phase 10 consumes the same field.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from return_platform.api.integration_outbox import router
from return_platform.configuration.settings import Settings
from return_platform.data_governance import LoadedAssetCatalog
from return_platform.operations.integrations.outbox import (
    DEAD_LETTER_STATUS,
    REQUIRES_RECONCILIATION,
)
from return_platform.resources import RuntimeResources
from return_platform.security import roles as r
from return_platform.security.principal import Principal

_NOW = datetime(2026, 8, 15, 6, 42, tzinfo=UTC)


def _command(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "_id": "cmd-1",
        "topic": "return-case.support-response.signal",
        "aggregateType": "RETURN_CASE",
        "aggregateId": "case-1",
        "idempotencyKey": "support-response:case-1:evt-1",
        "status": "PENDING",
        "attemptCount": 0,
        "nextAttemptAt": _NOW,
        "createdAt": _NOW,
        "updatedAt": _NOW,
    }
    document.update(overrides)
    return document


class _Cursor:
    """`find(...).sort(...).limit(...)` then `async for`, and nothing else."""

    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self._documents = documents

    def sort(self, *_args: Any, **_kwargs: Any) -> _Cursor:
        return self

    def limit(self, *_args: Any, **_kwargs: Any) -> _Cursor:
        return self

    async def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        for document in self._documents:
            yield document


class _Collection:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self._documents = documents
        self.queries: list[dict[str, Any]] = []

    def find(self, query: dict[str, Any]) -> _Cursor:
        self.queries.append(query)
        return _Cursor(self._documents)


class _Database:
    def __init__(self, collection: _Collection) -> None:
        self._collection = collection

    def __getitem__(self, _name: str) -> _Collection:
        return self._collection


class _Mongo:
    def __init__(self, collection: _Collection) -> None:
        self._database = _Database(collection)

    def __getitem__(self, _name: str) -> _Database:
        return self._database


def _client(
    documents: list[dict[str, Any]],
    test_settings: Settings,
    loaded_empty_catalog: LoadedAssetCatalog,
    *role_names: str,
) -> Iterator[TestClient]:
    app = FastAPI()

    @app.middleware("http")
    async def _attach(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.principal = Principal(subject="auditor", roles=frozenset(role_names))
        request.state.correlation_id = "test-correlation-id"
        return await call_next(request)

    app.include_router(router)
    app.state.resources = RuntimeResources(
        settings=test_settings,
        catalog=loaded_empty_catalog,
        mongo=_Mongo(_Collection(documents)),  # type: ignore[arg-type]
    )
    with TestClient(app) as client:
        yield client


@pytest.fixture
def dead_lettered(
    test_settings: Settings, loaded_empty_catalog: LoadedAssetCatalog
) -> Iterator[TestClient]:
    """Exactly what `IntegrationOutboxDispatcher._dead_letter` writes."""
    yield from _client(
        [
            _command(
                status=DEAD_LETTER_STATUS,
                reconciliationState=REQUIRES_RECONCILIATION,
                lastErrorCode="CASE_WORKFLOW_CLOSED",
                attemptCount=1,
            )
        ],
        test_settings,
        loaded_empty_catalog,
        r.RETURN_AUDITOR,
    )


def test_a_dead_lettered_command_reports_that_it_needs_reconciling(
    dead_lettered: TestClient,
) -> None:
    response = dead_lettered.get("/api/v1/integration-outbox")

    assert response.status_code == 200, response.text
    (view,) = response.json()["data"]
    # Both, and they are not the same claim: the first says the outbox has
    # stopped, the second says a case is still owed its Support answer.
    assert view["status"] == DEAD_LETTER_STATUS
    assert view["reconciliationState"] == REQUIRES_RECONCILIATION


def test_a_live_command_reports_nothing_to_reconcile(
    test_settings: Settings, loaded_empty_catalog: LoadedAssetCatalog
) -> None:
    """`None` rather than a placeholder string.

    Absence has to mean "nothing owed" and not "not recorded", or the field a
    reconciler filters on would match every command ever queued.
    """
    for client in _client([_command()], test_settings, loaded_empty_catalog, r.RETURN_AUDITOR):
        response = client.get("/api/v1/integration-outbox")

    assert response.status_code == 200, response.text
    (view,) = response.json()["data"]
    assert view["reconciliationState"] is None
