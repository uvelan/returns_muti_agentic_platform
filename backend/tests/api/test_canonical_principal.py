"""`/api/principal` behaviour, exercised through a real FastAPI app.

No datastore participates: the endpoint reports what the request's principal
already carries, so routing, the 401/403 boundary and the response shape are
the whole surface.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from return_platform.api.canonical_principal import router
from return_platform.security import capabilities as caps
from return_platform.security import roles as r
from return_platform.security.principal import Principal


def _app_for(principal: Principal | None) -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def _attach(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.principal = principal
        request.state.correlation_id = "test-correlation-id"
        return await call_next(request)

    app.include_router(router)
    return app


@pytest.fixture
def support_client() -> Iterator[TestClient]:
    principal = Principal(subject="support-person", roles=frozenset({r.RETURN_SUPPORT}))
    with TestClient(_app_for(principal)) as client:
        yield client


def test_reports_subject_roles_and_capabilities(support_client: TestClient) -> None:
    response = support_client.get("/api/principal")
    assert response.status_code == 200

    data = response.json()["data"]
    assert data["subject"] == "support-person"
    assert data["roles"] == [r.RETURN_SUPPORT]
    assert caps.RETURNS_SUPPORT_ACT in data["capabilities"]


def test_does_not_report_capabilities_the_principal_lacks(support_client: TestClient) -> None:
    granted = support_client.get("/api/principal").json()["data"]["capabilities"]
    assert caps.CONFIG_RELEASE_PROMOTE not in granted
    assert caps.GRAPH_SCHEMA_GENERATION_ACTIVATE not in granted


def test_does_not_leak_the_role_to_capability_table(support_client: TestClient) -> None:
    """The mapping's shape must not become part of the frontend contract.

    Reporting other roles or the table itself would let a UI reimplement
    authorization decisions locally, which is what the capability layer exists
    to prevent.
    """
    data = support_client.get("/api/principal").json()["data"]
    assert set(data) == {"subject", "roles", "capabilities"}
    assert r.CONSOLE_ADMIN not in data["roles"]


def test_capabilities_and_roles_are_sorted(support_client: TestClient) -> None:
    """Stable ordering: an unordered set would churn the response body between
    identical requests and make response diffing useless."""
    data = support_client.get("/api/principal").json()["data"]
    assert data["capabilities"] == sorted(data["capabilities"])
    assert data["roles"] == sorted(data["roles"])


def test_unauthenticated_caller_is_401_not_an_empty_capability_set() -> None:
    """The shell distinguishes "not signed in" from "signed in with nothing"."""
    with TestClient(_app_for(None)) as client:
        assert client.get("/api/principal").status_code == 401


def test_admin_sees_every_capability() -> None:
    principal = Principal(subject="admin", roles=frozenset({r.CONSOLE_ADMIN}))
    with TestClient(_app_for(principal)) as client:
        granted = client.get("/api/principal").json()["data"]["capabilities"]
    assert set(granted) == caps.ALL_CAPABILITIES
