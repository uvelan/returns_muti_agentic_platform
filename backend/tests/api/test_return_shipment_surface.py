"""`/api/return-shipments/{rma}/updates`: the way in that SHIP-01 was missing.

The chain underneath this route is proven elsewhere -- against real SQL Server in
`operations/test_return_shipment_state_real_infra.py`, against real SQL, Mongo
and Neo4j in `operations/test_return_shipment_graph_sync_real_infra.py`, and at
the decision layer in `operations/test_return_shipment_reaches_the_case.py`.
None of it is restated here.

What this module owns is the boundary: that the route reaches
`ReturnShipmentStateService.record_update` rather than a second implementation
of it, that the outcome the store decided survives to the caller unchanged, that
a graph sync failure is not reported as success, and that the payload is refused
for the things the store would refuse it for -- before the driver sees it.

The route is mounted through `create_app()` in `test_the_route_is_mounted...`
rather than only against a hand-built app, because "the handler works" and "the
handler is served" are different claims and the platform has already shipped a
404 behind a green suite once (`test_every_console_path_is_mounted.py`).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from return_platform.api import return_shipments as module
from return_platform.api.return_shipments import router
from return_platform.dynamic_knowledge.integration.shipment_state_sync import (
    ShipmentStateSyncFailed,
)
from return_platform.operations.return_shipment_state import ReturnShipmentStateService
from return_platform.operations.sql_business_state import (
    SHIPMENT_UPDATE_APPLIED,
    SHIPMENT_UPDATE_DUPLICATE,
    SHIPMENT_UPDATE_STALE,
    ShipmentUpdate,
    ShipmentUpdateOutcome,
)
from return_platform.security import roles as r
from return_platform.security.principal import Principal
from return_platform.workflows.fulfillment_tracking import ShipmentEvidence, ShipmentObservation

RMA = "RMA-6C21"
TRACKING = "1Z9990009999999999"
GENERATION = "gen-7"
PATH = f"/api/return-shipments/{RMA}/updates"
#: 14:00Z expressed in a zone that is not UTC. Deliberate: the route converts to
#: naive UTC before the store compares it, and a payload already in UTC would
#: pass whether or not it did.
STATUS_AT = "2026-08-14T16:00:00+02:00"
STORED_AT = datetime(2026, 8, 14, 14, 0)


class _BusinessState:
    """The authoritative store, answering however the test needs it to."""

    def __init__(
        self,
        *,
        outcome: str = SHIPMENT_UPDATE_APPLIED,
        record: dict[str, Any] | None = None,
        raises: Exception | None = None,
    ) -> None:
        self._outcome = outcome
        self._record = (
            {"return_record_id": "rec-1", "case_id": "case-1"} if record is None else (record)
        )
        self._raises = raises
        self.updates: list[ShipmentUpdate] = []

    async def record_shipment_update(self, update: ShipmentUpdate) -> ShipmentUpdateOutcome:
        self.updates.append(update)
        if self._raises is not None:
            raise self._raises
        return ShipmentUpdateOutcome(
            outcome=self._outcome,
            return_reference=update.return_reference,
            tracking_reference=update.tracking_reference,
            current_status=update.shipment_status,
            current_status_at=update.status_at,
            row_version=3,
            graph_generation_id=(GENERATION if self._outcome == SHIPMENT_UPDATE_APPLIED else None),
        )

    async def read_return_record_by_reference(self, return_reference: str) -> dict[str, Any] | None:
        del return_reference
        return self._record

    async def read_shipment_state(self, return_reference: str) -> list[dict[str, Any]]:
        """The parcels the store holds, as `dbo.return_tracking` answers them."""
        return [
            {
                "return_reference": return_reference,
                "tracking_reference": update.tracking_reference,
                "carrier_code": update.carrier_code,
                "tracking_status": update.shipment_status,
                "event_at": update.status_at,
            }
            for update in self.updates
        ]


class _Repository:
    def __init__(self) -> None:
        self.facts: list[dict[str, Any]] = []
        self.parcels: list[dict[str, Any]] = []

    async def append_case_fact(self, **fields: Any) -> dict[str, Any]:
        self.facts.append(dict(fields))
        return dict(fields)

    async def record_case_shipment(self, **fields: Any) -> bool:
        self.parcels.append(dict(fields))
        return True


class _Observations:
    async def observe(self, tracking_reference: str) -> ShipmentObservation:
        return ShipmentObservation(
            tracking_reference=tracking_reference,
            evidence=ShipmentEvidence.OBSERVED,
            graph_generation_id=GENERATION,
            current_status="intransit",
            shipment_id="SHP-9",
        )


@pytest.fixture
def business_state() -> _BusinessState:
    return _BusinessState()


@pytest.fixture
def repository() -> _Repository:
    return _Repository()


@pytest.fixture
def client(
    monkeypatch: pytest.MonkeyPatch,
    business_state: _BusinessState,
    repository: _Repository,
) -> Iterator[TestClient]:
    """The real router and the real service, over a stubbed store.

    Only `_service`'s three ports are replaced. The service itself is the
    production `ReturnShipmentStateService`, so the ordering it owns -- store,
    then graph, then reading, then the case -- is exercised rather than mocked
    past.
    """

    async def _service(request: Request) -> ReturnShipmentStateService:
        del request
        return ReturnShipmentStateService(
            business_state=business_state,
            repository=repository,
            observations=_Observations(),
        )

    monkeypatch.setattr(module, "_service", _service)

    app = FastAPI()

    @app.middleware("http")
    async def _attach(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.principal = Principal(
            subject="coordinator-1", roles=frozenset({r.LOGISTICS_COORDINATOR})
        )
        request.state.correlation_id = "test-correlation-id"
        return await call_next(request)

    app.include_router(router)
    with TestClient(app) as made:
        yield made


def _payload(**overrides: Any) -> dict[str, Any]:
    return {
        "trackingReference": TRACKING,
        "shipmentStatus": "IN_TRANSIT",
        "statusAt": STATUS_AT,
        "trackingType": "PPL",
        "carrierCode": "UPS",
    } | overrides


def test_an_accepted_carrier_event_reaches_the_case_through_the_service(
    client: TestClient, business_state: _BusinessState, repository: _Repository
) -> None:
    """The closure: an HTTP carrier event ends as a fact the associate can be told.

    The route's whole job is to reach `record_update`; everything the response
    reports -- the outcome, the generation, the fulfilment reading -- was decided
    below it. Asserting the case facts here is what makes this a connection test
    rather than a proof that a handler returns 200.
    """
    response = client.post(PATH, json=_payload())

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["outcome"] == SHIPMENT_UPDATE_APPLIED
    assert data["returnReference"] == RMA
    assert data["trackingReference"] == TRACKING
    assert data["rowVersion"] == 3
    assert data["graphGenerationId"] == GENERATION
    assert data["reading"] == {
        "caseId": "case-1",
        "fulfillmentStatus": "IN_TRANSIT",
        "evidence": "OBSERVED",
        "evidenceReference": f"SHIPMENT_OBSERVED:{GENERATION}:intransit",
        "graphGenerationId": GENERATION,
        "observedStatus": "intransit",
    }
    assert [fact["fact_name"] for fact in repository.facts] == [
        "fulfillment_status",
        "shipment_evidence",
    ]
    assert business_state.updates[0].return_reference == RMA


def test_the_rma_comes_from_the_path_and_the_parcel_from_the_body(
    client: TestClient, business_state: _BusinessState
) -> None:
    """Contract C4 is RMA-scoped, so the RMA is the resource and not a field.

    One RMA legitimately carries several tracking numbers -- a split return --
    which is why the parcel is in the payload and the RMA is not.
    """
    client.post(PATH, json=_payload())

    update = business_state.updates[0]
    assert update.return_reference == RMA
    assert update.tracking_reference == TRACKING
    assert update.tracking_type == "PPL"
    assert update.carrier_code == "UPS"


def test_the_status_timestamp_reaches_the_store_as_naive_utc(
    client: TestClient, business_state: _BusinessState
) -> None:
    """`event_at` is `DATETIME2(3)` and carries no zone.

    The stale-versus-applied decision is a comparison against that column inside
    the UPDATE's WHERE, so an offset that survived to the driver would be
    compared against naive UTC rows by whatever the driver chose to do with it.
    Converted once, here, rather than left for each caller to remember.
    """
    client.post(PATH, json=_payload())

    status_at = business_state.updates[0].status_at
    assert status_at.tzinfo is None
    assert status_at == STORED_AT


@pytest.mark.parametrize("outcome", [SHIPMENT_UPDATE_DUPLICATE, SHIPMENT_UPDATE_STALE])
def test_a_duplicate_or_stale_event_is_reported_as_itself_and_not_as_an_error(
    monkeypatch: pytest.MonkeyPatch, repository: _Repository, outcome: str
) -> None:
    """200 with the verdict, not 409.

    A caller replaying a carrier feed has to tell "the platform already knew
    that" from "your request was malformed", and both being a 4xx would collapse
    the two. Neither changed stored truth, so neither produces a reading.
    """
    business_state = _BusinessState(outcome=outcome)

    async def _service(request: Request) -> ReturnShipmentStateService:
        del request
        return ReturnShipmentStateService(
            business_state=business_state, repository=repository, observations=_Observations()
        )

    monkeypatch.setattr(module, "_service", _service)
    app = FastAPI()

    @app.middleware("http")
    async def _attach(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.principal = Principal(
            subject="coordinator-1", roles=frozenset({r.LOGISTICS_COORDINATOR})
        )
        request.state.correlation_id = "test-correlation-id"
        return await call_next(request)

    app.include_router(router)
    with TestClient(app) as made:
        response = made.post(PATH, json=_payload())

    assert response.status_code == 200, response.text
    assert response.json()["data"]["outcome"] == outcome
    assert response.json()["data"]["reading"] is None
    assert repository.facts == [], "an update that changed nothing told the case something"


def test_a_graph_sync_failure_is_not_reported_as_success(
    monkeypatch: pytest.MonkeyPatch, repository: _Repository
) -> None:
    """The row committed and the projection did not, and the caller is told so.

    `record_shipment_update` raises rather than swallowing for exactly this
    reason: an accepted shipment the graph has never heard of reads as
    AWAITING_HANDOFF to every agent. A 200 here would make that
    indistinguishable from a return still on the counter.
    """
    business_state = _BusinessState(raises=ShipmentStateSyncFailed("neo4j write refused"))

    async def _service(request: Request) -> ReturnShipmentStateService:
        del request
        return ReturnShipmentStateService(
            business_state=business_state, repository=repository, observations=_Observations()
        )

    monkeypatch.setattr(module, "_service", _service)
    app = FastAPI()

    @app.middleware("http")
    async def _attach(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.principal = Principal(
            subject="coordinator-1", roles=frozenset({r.LOGISTICS_COORDINATOR})
        )
        request.state.correlation_id = "test-correlation-id"
        return await call_next(request)

    app.include_router(router)
    with TestClient(app) as made:
        response = made.post(PATH, json=_payload())

    assert response.status_code == 502, response.text
    assert response.json()["detail"]["code"] == "SHIPMENT_GRAPH_SYNC_FAILED"
    assert response.json()["detail"]["retryable"] is True


def test_an_unzoned_status_timestamp_is_refused_rather_than_assumed(client: TestClient) -> None:
    """This field decides whether an update advances or is rejected as stale.

    Reading a naive timestamp as UTC would let a submitter in another zone
    silently overtake -- or silently lose to -- an event it has no relationship
    to, and nothing downstream could tell that had happened.
    """
    response = client.post(PATH, json=_payload(statusAt="2026-08-14T16:00:00"))

    assert response.status_code == 422, response.text


def test_a_tracking_type_the_database_would_refuse_is_refused_here(client: TestClient) -> None:
    """`CK_return_tracking_type` would reject it as a constraint violation.

    Which surfaces as a 500 the caller cannot act on. Refused at the boundary
    with the field named instead.
    """
    response = client.post(PATH, json=_payload(trackingType="CARRIER_PIGEON"))

    assert response.status_code == 422, response.text


def test_the_tracking_type_is_required_rather_than_defaulted(client: TestClient) -> None:
    """A shipment's ship-via is a property of that shipment.

    Defaulting it would file a BOL freight movement as a parcel, and no
    downstream reader could tell that the value had been assumed.
    """
    payload = _payload()
    del payload["trackingType"]

    response = client.post(PATH, json=payload)

    assert response.status_code == 422, response.text


def test_an_unknown_field_is_refused(client: TestClient) -> None:
    """`extra="forbid"`, so a misspelled field is not silently dropped."""
    response = client.post(PATH, json=_payload(trackingNumber=TRACKING))

    assert response.status_code == 422, response.text


def test_an_over_long_tracking_reference_is_refused_before_the_driver_sees_it(
    client: TestClient,
) -> None:
    """`tracking_reference` is `VARCHAR(128)`."""
    response = client.post(PATH, json=_payload(trackingReference="X" * 129))

    assert response.status_code == 422, response.text


def test_a_principal_without_the_logistics_capability_is_refused(
    monkeypatch: pytest.MonkeyPatch, business_state: _BusinessState, repository: _Repository
) -> None:
    """Recording a carrier event is a logistics act, not a general write.

    A warehouse associate may stage a handling unit and may not tell the platform
    that a parcel has moved -- those are different grants, and the capability is
    the one that says so.
    """

    async def _service(request: Request) -> ReturnShipmentStateService:
        del request
        return ReturnShipmentStateService(
            business_state=business_state, repository=repository, observations=_Observations()
        )

    monkeypatch.setattr(module, "_service", _service)
    app = FastAPI()

    @app.middleware("http")
    async def _attach(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.principal = Principal(
            subject="warehouse-1", roles=frozenset({r.WAREHOUSE_ASSOCIATE})
        )
        request.state.correlation_id = "test-correlation-id"
        return await call_next(request)

    app.include_router(router)
    with TestClient(app) as made:
        response = made.post(PATH, json=_payload())

    assert response.status_code == 403, response.text
    assert business_state.updates == [], "a refused caller still reached the store"


def test_the_route_is_mounted_on_the_application() -> None:
    """ "The handler works" and "the handler is served" are different claims.

    Asserted against the generated contract for the same reason
    `test_every_console_path_is_mounted.py` is: that document is what the
    frontend's typed client is built from, so it is the thing that must carry
    the path.
    """
    from return_platform.main import create_app

    paths = create_app().openapi()["paths"]

    assert "/api/return-shipments/{return_reference}/updates" in paths
    assert "post" in paths["/api/return-shipments/{return_reference}/updates"]
