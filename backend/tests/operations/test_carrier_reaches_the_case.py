"""Audit finding #9: the carrier has a sender, and a package can name it.

WHAT WAS BROKEN
---------------
The Copilot rendered `session.orderSource` -- an order's *source system* -- under
the heading "Carrier & Service". Phase 7 deleted the fabrication and
`ShipmentProjection.carrier` became honestly `None`, because nothing case-keyed
produced a carrier.

A carrier value did exist on the platform: `SupportActionRequest.carrier`. It
travels the **session** path, inside a block guarded by
`data.sessionId is not None`, and a Copilot case has no session. The case path
was:

    ReturnOutcomeRecord -> SupportReturnRecord -> RETURN_RECORD_MERGED_FIELDS
                        -> ReturnRecordView -> project_shipments

and none of those five carried a carrier at all. Reading the session-scoped
value onto a case would have attributed one return's carrier to another return's
package -- the same class of error as the `orderSource` substitution.

WHY THESE TESTS GO THROUGH THE HTTP EDGE
----------------------------------------
This was a *carrier* gap in the transport sense: several modules each had to
grow one field, and a test that constructed `SupportReturnRecord` by hand would
pass on the day any single link was missing. So the carrier here enters as JSON
on `POST .../return-outcome`, is read back out of the **stored outbox command**,
and is converted into the workflow dataclass exactly as Temporal converts it --
by field name, dropping keys the dataclass has no field for. A key the envelope
failed to send is then a `None` on the record and a failing assertion. The
activity, the merge, the SQL projection and the assembler are all the shipped
ones; the stores are the established doubles.

Modelled on `test_return_method_reaches_the_case.py`, which is the working
reference for a chain built this way.

HOW AN UNRECOGNISED CARRIER IS HANDLED, AND WHY
-----------------------------------------------
It is accepted. There is no operator-owned carrier vocabulary anywhere in the
platform -- `return_policy` declares none, and `dbo.return_tracking.carrier_code`
has no CHECK -- so there is nothing to validate against, and inventing a list in
Python or a `pattern=` on the field would pin a runtime-owned vocabulary to the
day it was written and advertise the stale set through OpenAPI as authoritative
(CFG-03). The asymmetry with `returnMethod` is deliberate and is about
consequence: a method outside `normalized_return_methods` resolves to no row in
the requirement table and leaves the case permanently unresolvable, so it is
refused before anything durable is written. An unrecognised carrier resolves to
nothing at all -- it is a string beside a tracking number -- and refusing it
would lose Support's whole reply over a label on a screen. What *is* enforced is
shape: non-blank and at most 64 characters, the length
`dbo.return_record.carrier` can hold.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from typing import Any, cast

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from return_platform.api import return_support
from return_platform.api.return_support import router
from return_platform.configuration.settings import Settings
from return_platform.data_governance import LoadedAssetCatalog
from return_platform.operations.models import ReturnRecordView
from return_platform.resources import RuntimeResources
from return_platform.security import roles as r
from return_platform.security.principal import Principal
from return_platform.workflows.return_case_workflow import (
    RecordSupportOutcomeInput,
    SupportResponseNotice,
    SupportReturnRecord,
)

# The MongoDB double that enforces the unique indexes and rolls a transaction
# back, and the case-aggregate double that reproduces `CaseRepository`'s
# semantics. Reused rather than re-described.
from tests.operations.test_durable_support_events import (  # reuse the established doubles
    _FakeClient,
)
from tests.test_cumulative_support_outcomes import (  # reuse the established doubles
    CASE_ID,
    _Fixture,
    _fixture,
)

WORK_ITEM_ID = "wi-carrier"
_URL = f"/api/v1/return-support/work-items/{WORK_ITEM_ID}/return-outcome"


class _StubSupportService:
    async def get_work_item(self, work_item_id: str) -> Any:
        if work_item_id != WORK_ITEM_ID:
            return None
        return type("_Item", (), {"caseId": CASE_ID})()


class _StubRepository:
    async def get_case(self, case_id: str) -> dict[str, Any] | None:
        del case_id
        return None


@pytest.fixture
def mongo() -> _FakeClient:
    return _FakeClient()


@pytest.fixture
def client(
    mongo: _FakeClient,
    test_settings: Settings,
    loaded_empty_catalog: LoadedAssetCatalog,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    """The shipped router, with the stores replaced and nothing else.

    No `return_configuration` is attached deliberately: a carrier is checked
    against no catalogue, so a reply carrying one must be accepted by a process
    that has loaded no snapshot. A test that attached one would hide a
    configuration read if somebody added it later.
    """
    app = FastAPI()

    @app.middleware("http")
    async def _attach(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.principal = Principal(
            subject="support-1", roles=frozenset({r.RETURN_SUPPORT})
        )
        request.state.correlation_id = "test-correlation-id"
        return await call_next(request)

    monkeypatch.setattr(return_support, "_service", lambda request: _StubSupportService())
    monkeypatch.setattr(
        return_support, "OperationalRepository", lambda *args, **kwargs: _StubRepository()
    )
    app.state.resources = RuntimeResources(
        settings=test_settings, catalog=loaded_empty_catalog, mongo=cast(Any, mongo)
    )
    app.include_router(router)
    with TestClient(app, raise_server_exceptions=False) as http:
        yield http


def _body(
    *,
    reference: str = "RMA-1",
    carrier: str | None = "UPS",
    event_id: str = "evt-carrier",
    **record: Any,
) -> dict[str, Any]:
    return {
        # Required now; incidental to what this file asserts. See the note in
        # `test_return_method_reaches_the_case.py`.
        "records": [
            {
                "returnReference": reference,
                "carrier": carrier,
                "orderLineReferences": ["LINE-1"],
                **record,
            }
        ],
        "rejected": False,
        "supportEventId": event_id,
    }


def _stored_notice(mongo: _FakeClient, settings: Settings, event_id: str) -> dict[str, Any]:
    """The notice as the outbox will hand it to the dispatcher.

    Read from the *outbox command payload* rather than from the request body:
    that document is what actually travels, and reading it is the only way a
    field dropped between the handler and the store shows up as a failure.
    """
    outbox = mongo[settings.mongo_database]["integration_outbox"]
    commands = [
        document
        for document in outbox.documents.values()
        if document["payload"]["supportEventId"] == event_id
    ]
    assert len(commands) == 1, f"expected one queued command for {event_id}, got {commands}"
    return cast(dict[str, Any], commands[0]["payload"]["notice"])


def _as_workflow_notice(notice: dict[str, Any]) -> SupportResponseNotice:
    """Convert the way Temporal's converter converts: by field name.

    Keys the dataclass has no field for are dropped silently -- which is
    precisely why the envelope and the dataclass are a contract, and why a test
    that skipped this step would prove nothing about the wire.
    """
    record_fields = {field.name for field in dataclasses.fields(SupportReturnRecord)}
    notice_fields = {field.name for field in dataclasses.fields(SupportResponseNotice)}
    records = tuple(
        SupportReturnRecord(
            **{
                key: (tuple(value) if isinstance(value, list) else value)
                for key, value in record.items()
                if key in record_fields
            }
        )
        for record in notice["records"]
    )
    return SupportResponseNotice(
        **{
            key: value for key, value in notice.items() if key in notice_fields and key != "records"
        },
        records=records,
    )


async def _apply(fixture: _Fixture, notice: SupportResponseNotice) -> Any:
    """Run the shipped activity over the notice that came off the wire."""
    return await fixture.activities.record_support_outcome(
        RecordSupportOutcomeInput(
            case_id=CASE_ID,
            work_item_id=WORK_ITEM_ID,
            records=notice.records,
            rejected=notice.rejected,
            reason=notice.reason,
            return_record_ids=tuple(fixture.next_record_id() for _ in notice.records),
            support_event_id=notice.support_event_id,
        )
    )


# --------------------------------------------------------------------------- #
# The wire
# --------------------------------------------------------------------------- #


def test_the_envelope_carries_the_carrier_off_the_http_request(
    client: TestClient, mongo: _FakeClient, test_settings: Settings
) -> None:
    """The link that was missing, asserted on the stored command."""
    response = client.post(_URL, json=_body())

    assert response.status_code == 200, response.text
    notice = _stored_notice(mongo, test_settings, "evt-carrier")
    assert notice["records"][0]["carrier"] == "UPS"


def test_the_workflow_dataclass_receives_the_carrier_the_envelope_sent(
    client: TestClient, mongo: _FakeClient, test_settings: Settings
) -> None:
    """Temporal converts by field name; a dataclass without the field drops it."""
    assert client.post(_URL, json=_body()).status_code == 200

    notice = _as_workflow_notice(_stored_notice(mongo, test_settings, "evt-carrier"))

    assert notice.records[0].carrier == "UPS"


# --------------------------------------------------------------------------- #
# Both homes, and the screen
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_support_carrier_reaches_the_record_the_fact_and_the_package(
    client: TestClient, mongo: _FakeClient, test_settings: Settings
) -> None:
    """The whole chain, ending where audit finding #9 was raised.

    `ReturnRecordView` is constructed from the stored document rather than
    inspected as a dict, because the claim is that the *contract* carries the
    carrier -- a value written under a key the view does not declare would be
    dropped by `extra="forbid"` and never reach a screen. The projection is the
    shipped assembler over the shipped documents, so the last assertion is what
    an operator would actually see in place of the deleted `orderSource`.

    The fact is asserted on its id shape too. `latest_case_facts` keys on
    `factName`, so the name stays `carrier` for every RMA and the record is
    distinguished by the `factId` -- the arrangement `tracking_reference` and
    `return_method` already use.
    """
    response = client.post(_URL, json=_body(trackingReference="1Z-1"))
    assert response.status_code == 200, response.text

    fixture = _fixture()
    await _apply(fixture, _as_workflow_notice(_stored_notice(mongo, test_settings, "evt-carrier")))

    stored = await fixture.repository.list_return_records(CASE_ID)
    assert len(stored) == 1
    view = ReturnRecordView.model_validate(stored[0])
    assert view.carrier == "UPS"

    facts = fixture.repository.facts_named("carrier")
    assert [fact["value"] for fact in facts] == ["UPS"]
    assert facts[0]["factId"].startswith(f"carrier-{stored[0]['returnRecordId']}")
    assert facts[0]["sourceSystem"] == "RETURN_SUPPORT"
    assert facts[0]["channel"] == "CHANNEL_B"

    projection = await fixture.repository.projection()
    (record,) = projection.records()
    assert record.shipments is not None
    (shipment,) = record.shipments
    assert shipment.carrier == "UPS"
    # The two that still have no producer are untouched by this change.
    assert shipment.serviceLevel is None
    assert shipment.estimatedDeliveryAt is None


@pytest.mark.asyncio
async def test_the_carrier_reaches_the_authoritative_sql_row_before_the_case(
    client: TestClient, mongo: _FakeClient, test_settings: Settings
) -> None:
    """T-14, which this change must not reverse.

    The SQL write commits first, so a Mongo case can never report a carrier the
    return store never received. Asserted as ordering, not just presence: the
    store saw the record before `cases.version` moved.
    """
    assert client.post(_URL, json=_body(trackingReference="1Z-1")).status_code == 200

    fixture = _fixture()
    revision_before = fixture.repository.revision
    await _apply(fixture, _as_workflow_notice(_stored_notice(mongo, test_settings, "evt-carrier")))

    assert fixture.return_store.last_record("RMA-1").carrier == "UPS"
    assert fixture.repository.revision > revision_before, "the case never moved"


@pytest.mark.asyncio
async def test_a_later_reply_with_no_carrier_does_not_blank_the_one_recorded(
    client: TestClient, mongo: _FakeClient, test_settings: Settings
) -> None:
    """A `null` is silence, never a deletion -- in both stores.

    A tracking number arriving two hours after the RMA carries `carrier=None`.
    Applying it over the carrier Support already named would delete it, and the
    whole-row SQL `SET` would delete the authoritative copy with it. The merge is
    computed once and used by both writers, so they cannot disagree about what
    the null meant.
    """
    assert client.post(_URL, json=_body(event_id="evt-a")).status_code == 200
    assert (
        client.post(
            _URL, json=_body(carrier=None, trackingReference="1Z-9", event_id="evt-b")
        ).status_code
        == 200
    )

    fixture = _fixture()
    await _apply(fixture, _as_workflow_notice(_stored_notice(mongo, test_settings, "evt-a")))
    await _apply(fixture, _as_workflow_notice(_stored_notice(mongo, test_settings, "evt-b")))

    assert fixture.return_store.last_record("RMA-1").carrier == "UPS"
    stored = await fixture.repository.list_return_records(CASE_ID)
    assert stored[0]["carrier"] == "UPS"
    assert stored[0]["trackingReference"] == "1Z-9"

    projection = await fixture.repository.projection()
    (record,) = projection.records()
    assert record.shipments is not None
    assert record.shipments[0].carrier == "UPS"


@pytest.mark.asyncio
async def test_a_later_reply_naming_a_different_carrier_corrects_it(
    client: TestClient, mongo: _FakeClient, test_settings: Settings
) -> None:
    """Silence does not overwrite; a statement does.

    The other half of the merge rule. Support re-tendering the parcel with
    another carrier is a correction, and a record that could never be corrected
    would be as wrong as one that blanked itself.
    """
    assert (
        client.post(_URL, json=_body(event_id="evt-a", trackingReference="1Z-1")).status_code == 200
    )
    assert client.post(_URL, json=_body(carrier="FEDEX", event_id="evt-b")).status_code == 200

    fixture = _fixture()
    await _apply(fixture, _as_workflow_notice(_stored_notice(mongo, test_settings, "evt-a")))
    await _apply(fixture, _as_workflow_notice(_stored_notice(mongo, test_settings, "evt-b")))

    stored = await fixture.repository.list_return_records(CASE_ID)
    assert stored[0]["carrier"] == "FEDEX"
    assert fixture.return_store.last_record("RMA-1").carrier == "FEDEX"


@pytest.mark.asyncio
async def test_two_rmas_on_one_reply_keep_their_own_carriers(
    client: TestClient, mongo: _FakeClient, test_settings: Settings
) -> None:
    """Per record, never per case. A split return goes back on two carriers."""
    response = client.post(
        _URL,
        json={
            "records": [
                {"returnReference": "RMA-1", "carrier": "UPS", "trackingReference": "1Z-1",
                 "orderLineReferences": ["LINE-1"]},
                {"returnReference": "RMA-2", "carrier": "FEDEX", "trackingReference": "1Z-2",
                 "orderLineReferences": ["LINE-2"]},
            ],
            "rejected": False,
            "supportEventId": "evt-split",
        },
    )
    assert response.status_code == 200, response.text

    fixture = _fixture()
    await _apply(fixture, _as_workflow_notice(_stored_notice(mongo, test_settings, "evt-split")))

    projection = await fixture.repository.projection()
    carriers = {
        record.returnReference: record.shipments[0].carrier
        for record in projection.records()
        if record.shipments
    }
    assert carriers == {"RMA-1": "UPS", "RMA-2": "FEDEX"}


# --------------------------------------------------------------------------- #
# What the boundary does and does not enforce
# --------------------------------------------------------------------------- #


def test_a_carrier_nobody_configured_is_accepted(
    client: TestClient, mongo: _FakeClient, test_settings: Settings
) -> None:
    """There is no catalogue to be outside of. See the module docstring.

    A regional LTL nobody has heard of is still the carrier that has the goods,
    and refusing the reply would lose the RMA, the label and the tracking number
    with it.
    """
    response = client.post(_URL, json=_body(carrier="SAIA MOTOR FREIGHT"))

    assert response.status_code == 200, response.text
    notice = _stored_notice(mongo, test_settings, "evt-carrier")
    assert notice["records"][0]["carrier"] == "SAIA MOTOR FREIGHT"


def test_a_reply_that_names_no_carrier_is_accepted_and_stores_a_null(
    client: TestClient, mongo: _FakeClient, test_settings: Settings
) -> None:
    """The common case: most notices say nothing about the carrier."""
    response = client.post(
        _URL,
        json={
            "records": [{"returnReference": "RMA-1", "orderLineReferences": ["LINE-1"]}],
            "supportEventId": "evt-carrier",
        },
    )

    assert response.status_code == 200, response.text
    notice = _stored_notice(mongo, test_settings, "evt-carrier")
    assert notice["records"][0]["carrier"] is None


@pytest.mark.parametrize("carrier", ["", "U" * 65])
def test_a_carrier_that_is_not_a_carrier_is_refused_at_the_boundary(
    client: TestClient, mongo: _FakeClient, test_settings: Settings, carrier: str
) -> None:
    """Shape is enforced even though vocabulary is not.

    The empty string is the dangerous one. `None` means "Support did not say"
    and leaves a recorded carrier standing, but `""` is not `None`: it would
    sail through the merge and erase one -- the deletion the merge exists to
    prevent, arriving through the front door. Over-long is refused because
    `dbo.return_record.carrier` is `VARCHAR(64)`, and a value the API accepts
    must be one the authoritative store can hold.

    Refused before anything durable is written, which is what the empty outbox
    asserts.
    """
    response = client.post(_URL, json=_body(carrier=carrier))

    assert response.status_code == 422, response.text
    assert mongo[test_settings.mongo_database]["integration_outbox"].documents == {}


@pytest.mark.asyncio
async def test_a_whitespace_carrier_passes_the_boundary_and_renders_as_nothing(
    client: TestClient, mongo: _FakeClient, test_settings: Settings
) -> None:
    """The second line, for the blank `min_length` cannot see.

    `"   "` is three characters, so the boundary accepts it. It is not refused
    deeper down either -- refusing it there would lose the RMA and the tracking
    number over a stray space bar. It is simply never rendered: `_text` treats
    blank as absent, exactly as it does for `returnReference`, so no operator
    ever sees a carrier called nothing.
    """
    assert client.post(_URL, json=_body(carrier="   ", trackingReference="1Z-1")).status_code == 200

    fixture = _fixture()
    await _apply(fixture, _as_workflow_notice(_stored_notice(mongo, test_settings, "evt-carrier")))

    projection = await fixture.repository.projection()
    (record,) = projection.records()
    assert record.shipments is not None
    assert record.shipments[0].carrier is None


def test_the_carrier_is_not_pinned_by_a_field_pattern() -> None:
    """The realistic regression: someone reinstates a carrier list "for the
    contract". It would refuse a carrier a deployment starts using tomorrow and
    the generated client would advertise the stale set as authoritative."""
    from return_platform.api.return_support import ReturnOutcomeRecord

    field = ReturnOutcomeRecord.model_fields["carrier"]
    patterns = [item for item in field.metadata if getattr(item, "pattern", None) is not None]
    assert patterns == [], f"carrier is pinned by a pattern: {patterns}"


def test_no_service_level_or_delivery_estimate_was_added_anywhere(
    # No fixtures: this is a statement about the models, not about a request.
) -> None:
    """The absence Phase 5 established, kept intact by the change that filled
    the field beside it.

    `PickupRequest.serviceLevel` is the service a freight *collection* was booked
    at, keyed by pickup request and session -- not the service a parcel is moving
    under. Nothing anywhere computes a return-leg delivery estimate. A carrier
    arriving is not a reason to guess either one.
    """
    from return_platform.api.return_support import ReturnOutcomeRecord
    from return_platform.operations.sql_business_state import ReturnRecordWrite

    for model in (ReturnOutcomeRecord, ReturnRecordView):
        assert "serviceLevel" not in model.model_fields
        assert "estimatedDeliveryAt" not in model.model_fields

    write_fields = {field.name for field in dataclasses.fields(ReturnRecordWrite)}
    assert "service_level" not in write_fields
    assert "estimated_delivery_at" not in write_fields

    record_fields = {field.name for field in dataclasses.fields(SupportReturnRecord)}
    assert "service_level" not in record_fields
    assert "estimated_delivery_at" not in record_fields
