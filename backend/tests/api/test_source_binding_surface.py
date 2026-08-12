"""`/api/source-bindings`: reading where datasets point, and moving them.

Over real HTTP against a stub store. What is under test is the router's
contract -- that a read answers with the *resolved* catalogue rather than the
override list, that a write is validated before it is stored, and that a reset
is not conditional on state the operator cannot see.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from return_platform.api import source_bindings as module
from return_platform.api.source_bindings import router
from return_platform.configuration.settings import DEFAULT_DYNAMIC_KNOWLEDGE_SCHEMA_PATH
from return_platform.dynamic_knowledge.config_loader import load_active_schema
from return_platform.dynamic_knowledge.source_bindings import SourceBinding
from return_platform.security import roles as r
from return_platform.security.principal import Principal

BASELINE = load_active_schema(DEFAULT_DYNAMIC_KNOWLEDGE_SCHEMA_PATH)
CONFIGURED_DATASET = next(iter(BASELINE.sources))


class StubStore:
    def __init__(self) -> None:
        self.bindings: list[SourceBinding] = []
        self.cleared: list[str] = []

    async def list(self) -> list[SourceBinding]:
        return list(self.bindings)

    async def rebind(self, binding: SourceBinding, *, changed_by: str) -> None:
        self.bindings = [item for item in self.bindings if item.dataset != binding.dataset]
        self.bindings.append(binding)

    async def clear(self, dataset: str) -> bool:
        self.cleared.append(dataset)
        before = len(self.bindings)
        self.bindings = [item for item in self.bindings if item.dataset != dataset]
        return len(self.bindings) != before


@pytest.fixture
def store() -> StubStore:
    return StubStore()


@pytest.fixture(autouse=True)
def _stub(monkeypatch: pytest.MonkeyPatch, store: StubStore) -> None:
    monkeypatch.setattr(module, "_store", lambda request: store)


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = FastAPI()

    @app.middleware("http")
    async def _attach(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.principal = Principal(
            subject="operator-1", roles=frozenset({r.CONSOLE_ADMIN})
        )
        request.state.correlation_id = "test-correlation-id"
        return await call_next(request)

    app.include_router(router)

    class _Settings:
        dynamic_knowledge_schema_path = DEFAULT_DYNAMIC_KNOWLEDGE_SCHEMA_PATH

    app.state.settings = _Settings()
    with TestClient(app) as made:
        yield made


def _rebind_payload() -> dict[str, object]:
    return {
        "sourceAssetId": "restored_orders",
        "connectorType": "MONGODB",
        "connectionRef": "vault://data-sources/restored-mongodb",
        "objectRef": {"database": "restore_2026", "name": "salesInv"},
    }


def test_the_list_answers_with_configuration_before_anyone_overrides(
    client: TestClient,
) -> None:
    """An empty override list is not an answer to "where does this come from"."""
    response = client.get("/api/source-bindings")

    assert response.status_code == 200, response.text
    datasets = {row["dataset"] for row in response.json()["data"]}
    assert CONFIGURED_DATASET in datasets
    assert all(row["overridden"] is False for row in response.json()["data"])


def test_a_rebinding_is_marked_as_one(client: TestClient) -> None:
    """Configured or deliberately changed is the first thing a debugger asks."""
    client.put(f"/api/source-bindings/{CONFIGURED_DATASET}", json=_rebind_payload())

    rows = {row["dataset"]: row for row in client.get("/api/source-bindings").json()["data"]}

    assert rows[CONFIGURED_DATASET]["overridden"] is True
    assert rows[CONFIGURED_DATASET]["connectionRef"] == "vault://data-sources/restored-mongodb"


def test_a_malformed_connection_is_refused_at_the_write(client: TestClient) -> None:
    """Not at the next publish, where it reads like a schema problem."""
    payload = {**_rebind_payload(), "objectRef": {}}

    response = client.put(f"/api/source-bindings/{CONFIGURED_DATASET}", json=payload)

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "INVALID_SOURCE_ASSET"


def test_an_unknown_connector_is_refused(client: TestClient) -> None:
    payload = {**_rebind_payload(), "connectorType": "CARRIER_PIGEON"}

    response = client.put(f"/api/source-bindings/{CONFIGURED_DATASET}", json=payload)

    assert response.status_code == 422, response.text


def test_clearing_returns_the_dataset_to_configuration(client: TestClient) -> None:
    client.put(f"/api/source-bindings/{CONFIGURED_DATASET}", json=_rebind_payload())

    removed = client.delete(f"/api/source-bindings/{CONFIGURED_DATASET}")

    assert removed.status_code == 200, removed.text
    assert removed.json()["data"] == {"removed": True}
    rows = {row["dataset"]: row for row in client.get("/api/source-bindings").json()["data"]}
    assert rows[CONFIGURED_DATASET]["overridden"] is False
    assert rows[CONFIGURED_DATASET]["connectionRef"] == (
        BASELINE.sources[CONFIGURED_DATASET].connection_ref
    )


def test_clearing_something_never_bound_is_not_an_error(client: TestClient) -> None:
    response = client.delete("/api/source-bindings/never_bound")

    assert response.status_code == 200, response.text
    assert response.json()["data"] == {"removed": False}


def test_a_dataset_configuration_has_never_heard_of_can_be_bound(client: TestClient) -> None:
    """The case name-matching could not express at all."""
    client.put(
        "/api/source-bindings/warehouse_bays",
        json={**_rebind_payload(), "sourceAssetId": "warehouse_bays"},
    )

    datasets = {row["dataset"] for row in client.get("/api/source-bindings").json()["data"]}

    assert "warehouse_bays" in datasets
