"""The three routes that record production events *implicitly*, over real HTTP.

Wave D4. These endpoints do something else -- apply a support action, confirm a
pickup, assign a bay -- and record a production workflow transition as a
consequence. Until D4 they never consulted the event authorization table, so the
transition was reachable by whoever could reach the endpoint.

**No datastore participates, on purpose.** Each handler now authorizes before it
resolves its dependencies, so the two outcomes are distinguishable without any
infrastructure:

* **403** -- refused. Nothing was resolved, nothing was mutated.
* **503** -- the caller got *past* every authorization gate and the handler went
  on to look for its datastore, which this app does not provide.

A 503 is therefore the positive result here. Asserting merely "not 403" would
pass if a route stopped checking altogether, so each permitted case asserts 503
exactly.

**Why there is no "event check refuses at the HTTP layer" case.** After the
consolidation there cannot be one: every role each route's dependency admits is
a role the table permits for every event that route can cause -- that is the
invariant `test_every_role_a_route_admits_may_record_every_event_that_route_causes`
asserts directly. A cross-lane caller is refused, but by the route dependency,
which runs first. The event check is the guard that keeps the two agreeing: if
the table is narrowed tomorrow, these routes start refusing at runtime instead
of silently continuing to allow. Writing a test that contrived an HTTP 403 out
of the event check would mean breaking that invariant to observe it.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from return_platform.api.physical_operations import router as physical_router
from return_platform.api.return_support import router as support_router
from return_platform.api.warehouse_placement import router as warehouse_router
from return_platform.security import roles as r
from return_platform.security.principal import Principal

_BAY_ASSIGNMENT = {
    "handlingUnitId": "hu-000001",
    "bayId": "bay-1",
    "expectedHandlingUnitVersion": 1,
}


def _client_for(*role_names: str) -> Iterator[TestClient]:
    app = FastAPI()

    @app.middleware("http")
    async def _attach(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.principal = Principal(subject="test-actor", roles=frozenset(role_names))
        request.state.correlation_id = "test-correlation-id"
        return await call_next(request)

    app.include_router(support_router)
    app.include_router(physical_router)
    app.include_router(warehouse_router)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def support_client() -> Iterator[TestClient]:
    yield from _client_for(r.RETURN_SUPPORT)


def _support_action(action: str, **extra: object) -> dict[str, object]:
    return {
        "action": action,
        "expectedVersion": 1,
        "reason": "recorded during a test",
        **extra,
    }


# ---------------------------------------------------------------------------
# return_support -- the route that actually had the escape
# ---------------------------------------------------------------------------


def test_support_may_record_ltl_shipping_instructions(support_client: TestClient) -> None:
    """The behaviour the `BOL_TENDERED` table entry preserves.

    This is the request that used to reach `BOL_TENDERED` without the table ever
    being consulted. It must still be allowed -- with the transition now
    explicitly permitted rather than merely unexamined. If the entry were
    reverted, this returns 403 instead of 503.
    """
    response = support_client.post(
        "/api/v1/return-support/work-items/wi-1/actions",
        json=_support_action("RECORD_SHIPPING_INSTRUCTIONS", shippingInstructionType="LTL"),
    )
    assert response.status_code == 503, response.text


def test_support_may_record_parcel_shipping_instructions(support_client: TestClient) -> None:
    """The single-event sibling: no BOL is tendered, so authorization covers one
    event rather than two, and must still pass."""
    response = support_client.post(
        "/api/v1/return-support/work-items/wi-1/actions",
        json=_support_action("RECORD_SHIPPING_INSTRUCTIONS", shippingInstructionType="PARCEL"),
    )
    assert response.status_code == 503, response.text


def test_support_may_acknowledge(support_client: TestClient) -> None:
    response = support_client.post(
        "/api/v1/return-support/work-items/wi-1/actions",
        json=_support_action("ACKNOWLEDGE"),
    )
    assert response.status_code == 503, response.text


def test_a_bookkeeping_action_needs_no_event_authorization(
    support_client: TestClient,
) -> None:
    """`ASSIGN` records no workflow event, so nothing about it is gated on the
    event table. Over-refusing is as much a bug as under-refusing; it just fails
    in the direction nobody reports."""
    response = support_client.post(
        "/api/v1/return-support/work-items/wi-1/actions",
        json=_support_action("ASSIGN", assignee="someone"),
    )
    assert response.status_code == 503, response.text


# ---------------------------------------------------------------------------
# physical_operations and warehouse_placement
# ---------------------------------------------------------------------------


def test_logistics_may_confirm_a_pickup_booking() -> None:
    for client in _client_for(r.LOGISTICS_COORDINATOR):
        response = client.post(
            "/api/v1/returns/s-1/pickup-actions",
            json=_support_action("CONFIRM_BOOKING"),
        )
        assert response.status_code == 503, response.text


def test_logistics_may_record_a_pickup() -> None:
    """A different event (`PHYSICAL_HANDOFF_CONFIRMED`) from the same route, so
    the authorization is exercised per action rather than once per endpoint."""
    for client in _client_for(r.LOGISTICS_COORDINATOR):
        response = client.post(
            "/api/v1/returns/s-1/pickup-actions",
            json=_support_action("RECORD_PICKUP"),
        )
        assert response.status_code == 503, response.text


def test_warehouse_may_assign_a_bay() -> None:
    for client in _client_for(r.WAREHOUSE_ASSOCIATE):
        response = client.post("/api/v1/warehouse/returns/s-1/bay-assignment", json=_BAY_ASSIGNMENT)
        assert response.status_code == 503, response.text


# ---------------------------------------------------------------------------
# Cross-lane refusal, without asserting which layer refuses
# ---------------------------------------------------------------------------


def test_a_warehouse_role_cannot_confirm_a_carrier_booking() -> None:
    """Refused -- today by the route dependency, which runs before the event
    check. The assertion is on the outcome rather than the mechanism precisely
    because the two must not disagree; which one fires first is an
    implementation detail, that it is refused at all is the requirement."""
    for client in _client_for(r.WAREHOUSE_ASSOCIATE):
        response = client.post(
            "/api/v1/returns/s-1/pickup-actions",
            json=_support_action("CONFIRM_BOOKING"),
        )
        assert response.status_code == 403, response.text


def test_a_logistics_role_cannot_complete_warehouse_processing() -> None:
    for client in _client_for(r.LOGISTICS_COORDINATOR):
        response = client.post("/api/v1/warehouse/returns/s-1/bay-assignment", json=_BAY_ASSIGNMENT)
        assert response.status_code == 403, response.text


def test_a_reader_cannot_reach_any_of_the_three() -> None:
    """`console_viewer` is a read role. None of these endpoints is reachable by
    it, which is the baseline the lane checks above build on."""
    for client in _client_for(r.CONSOLE_VIEWER):
        for method_path, body in (
            ("/api/v1/return-support/work-items/wi-1/actions", _support_action("ACKNOWLEDGE")),
            ("/api/v1/returns/s-1/pickup-actions", _support_action("CONFIRM_BOOKING")),
            ("/api/v1/warehouse/returns/s-1/bay-assignment", _BAY_ASSIGNMENT),
        ):
            assert client.post(method_path, json=body).status_code == 403, method_path
