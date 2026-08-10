"""`/api/returns` reads over real HTTP, against a stub repository.

Wave D4. Covers the two reads added when `production-artifacts` was renamed, and
the 404-before-anything-else rule the whole surface depends on.

**A stub repository, not Mongo.** What is under test is the router: which
repository calls each endpoint makes, what shape it returns, and that a
sub-resource refuses a session that does not exist. A real datastore would
exercise `OperationalRepository`, which has its own coverage, and would make
"did `/evidence` ask for all eleven collections?" much harder to assert than it
is here.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from bson import ObjectId
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from return_platform.api import canonical_returns
from return_platform.api.canonical_returns import ReturnEvidence, router
from return_platform.security import roles as r
from return_platform.security.principal import Principal

_SESSION_ID = "ret-1"


class StubRepository:
    """Returns one `_id`-bearing document from every collection accessor.

    Every list method answers the same shape, so a handler that wired the wrong
    accessor to a field would still produce a well-formed response -- which is
    why the assertions below check *which* accessors were called, not just that
    the body parsed.
    """

    def __init__(self, *, session_exists: bool = True) -> None:
        self.session_exists = session_exists
        self.calls: list[str] = []

    async def get_return(self, session_id: str) -> dict[str, Any] | None:
        self.calls.append("get_return")
        if not self.session_exists:
            return None
        return {"id": session_id, "status": "OPEN"}

    def _documents(self, name: str) -> list[dict[str, Any]]:
        self.calls.append(name)
        return [{"_id": ObjectId(), "marker": name}]

    async def list_return_items(self, session_id: str) -> list[dict[str, Any]]:
        return self._documents("list_return_items")

    async def list_handling_units(self, session_id: str) -> list[dict[str, Any]]:
        return self._documents("list_handling_units")

    async def get_pickup_projection(self, session_id: str) -> dict[str, Any] | None:
        self.calls.append("get_pickup_projection")
        return {"status": "SCHEDULED"}

    async def list_branch_staging_records(self, session_id: str) -> list[dict[str, Any]]:
        return self._documents("list_branch_staging_records")

    async def list_document_artifacts(self, session_id: str) -> list[dict[str, Any]]:
        return self._documents("list_document_artifacts")

    async def list_shipping_instructions(self, session_id: str) -> list[dict[str, Any]]:
        return self._documents("list_shipping_instructions")

    async def list_shipment_events(self, session_id: str) -> list[dict[str, Any]]:
        return self._documents("list_shipment_events")

    async def list_omc_commands(self, session_id: str) -> list[dict[str, Any]]:
        return self._documents("list_omc_commands")

    async def list_integration_commands(self, aggregate_id: str) -> list[dict[str, Any]]:
        return self._documents("list_integration_commands")

    async def list_vendor_return_links(self, session_id: str) -> list[dict[str, Any]]:
        return self._documents("list_vendor_return_links")

    async def list_agent_decisions(self, session_id: str) -> list[dict[str, Any]]:
        return self._documents("list_agent_decisions")


def _client(repository: StubRepository) -> Iterator[TestClient]:
    app = FastAPI()

    @app.middleware("http")
    async def _attach(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.principal = Principal(subject="reader", roles=frozenset({r.CONSOLE_VIEWER}))
        request.state.correlation_id = "test-correlation-id"
        return await call_next(request)

    app.include_router(router)
    with TestClient(app) as client:
        yield client


@pytest.fixture(autouse=True)
def _stub_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patched at the router's own reference, not at the definition site.

    `canonical_returns` imported `resolve_operational_repository` by name, so
    patching `operations.repository` would leave the router holding the original
    and every assertion below would silently exercise the real resolver.
    """
    monkeypatch.setattr(
        canonical_returns,
        "resolve_operational_repository",
        lambda request: request.app.state.stub_repository,
    )


def _app_client(repository: StubRepository) -> Iterator[TestClient]:
    for client in _client(repository):
        client.app.state.stub_repository = repository  # type: ignore[attr-defined]
        yield client


def test_artifacts_returns_the_document_artifact_list_without_mongo_ids() -> None:
    repository = StubRepository()
    for client in _app_client(repository):
        response = client.get(f"/api/returns/{_SESSION_ID}/artifacts")

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data == [{"marker": "list_document_artifacts"}]
    assert "list_document_artifacts" in repository.calls


def test_evidence_reads_every_collection_exactly_once() -> None:
    """Eleven accessors, each wired to its own field.

    Asserting the call set is what catches a copy-paste that points two fields
    at the same accessor -- the response would still be well-formed, and the
    duplicated collection would look like real data.
    """
    repository = StubRepository()
    for client in _app_client(repository):
        response = client.get(f"/api/returns/{_SESSION_ID}/evidence")

    assert response.status_code == 200, response.text
    collection_calls = [call for call in repository.calls if call != "get_return"]
    assert sorted(collection_calls) == sorted(set(collection_calls)), (
        f"an accessor was called twice: {collection_calls}"
    )
    assert len(collection_calls) == len(ReturnEvidence.model_fields)


def test_evidence_carries_no_mongo_ids() -> None:
    repository = StubRepository()
    for client in _app_client(repository):
        data = client.get(f"/api/returns/{_SESSION_ID}/evidence").json()["data"]

    for field, value in data.items():
        if isinstance(value, list):
            assert all("_id" not in item for item in value), field


def test_evidence_omits_the_session_and_the_timeline() -> None:
    """The legacy endpoint embedded both. Carrying them forward would make this
    a third way to read a session and a second way to read a timeline."""
    repository = StubRepository()
    for client in _app_client(repository):
        data = client.get(f"/api/returns/{_SESSION_ID}/evidence").json()["data"]

    assert "return" not in data
    assert "timeline" not in data


@pytest.mark.parametrize("suffix", ["artifacts", "evidence", "timeline"])
def test_a_sub_resource_404s_for_a_session_that_does_not_exist(suffix: str) -> None:
    """Not an empty payload. An empty evidence record and a nonexistent return
    are different answers, and a client cannot tell them apart from `[]`."""
    repository = StubRepository(session_exists=False)
    for client in _app_client(repository):
        response = client.get(f"/api/returns/{_SESSION_ID}/{suffix}")

    assert response.status_code == 404, response.text
    assert repository.calls == ["get_return"], "collections were read for a missing session"
