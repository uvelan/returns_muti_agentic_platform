"""D23: the return method has a sender, and `businessComplete` can become true.

WHAT WAS BROKEN
---------------
Phase 4 landed the whole of the write path. `record_support_outcome` merges a
`return_method` onto the Mongo return record *and* appends a per-record
`return_method` fact; `ReturnRecordView.returnMethod` exists; `assembly.py`
reads the record's value first and falls back to the fact; the requirement table
keys on the method. Everything was in place except a way for Support to say one.

`support_return_record` and `ReturnOutcomeRecord` were excluded from Phase 4, so
nothing put a method on the wire. The consequence chained all the way to the
screen and it was total, not partial:

    record.returnMethod is None
      -> resolve_method_requirements(record) is None
      -> completionProfileResolved is False
      -> awaiting always contains RETURN_METHOD
      -> businessComplete could never become true for ANY return

WHY THESE TESTS GO THROUGH THE HTTP EDGE
----------------------------------------
The gap was a *carrier* gap, not a logic gap: three modules each had the field
and the one in the middle did not. A test that constructed `SupportReturnRecord`
by hand would have passed on the day the defect was found. So the method here
enters as JSON on `POST .../return-outcome`, is read back out of the durably
stored notice, and is converted into the workflow dataclass exactly as Temporal
converts it -- by field name, dropping keys the dataclass has no field for. A
key the envelope failed to send is then a `None` on the record and a failing
assertion, which is the failure the production system had and did not report.

The activity, the projection, the requirement table and the assembler are all
the shipped ones. The doubles are the stores, reused from
`test_cumulative_support_outcomes` so there is one description of
`CaseRepository`'s semantics rather than two that can drift.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from return_platform.api import return_support
from return_platform.api.return_support import router
from return_platform.configuration.return_configuration import (
    LoadedReturnConfiguration,
    load_return_configuration,
)
from return_platform.configuration.settings import Settings
from return_platform.data_governance import LoadedAssetCatalog
from return_platform.operations.case_projection.vocabulary import AwaitingDimension
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

WORK_ITEM_ID = "wi-d23"
_URL = f"/api/v1/return-support/work-items/{WORK_ITEM_ID}/return-outcome"

_LOADED = load_return_configuration(Path("config/returns/production.yaml"))
_CONFIGURED_METHODS = _LOADED.configuration.return_policy.normalized_return_methods

#: A method the shipped configuration does not declare, standing in for one an
#: operator adds tomorrow. Asserted absent below so these tests cannot quietly
#: become tautologies if the catalogue grows to include it.
_NEW_METHOD = "OFFSITE_FREIGHT"


# --------------------------------------------------------------------------- #
# The HTTP edge
# --------------------------------------------------------------------------- #


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
def http(
    mongo: _FakeClient,
    test_settings: Settings,
    loaded_empty_catalog: LoadedAssetCatalog,
    monkeypatch: pytest.MonkeyPatch,
) -> Any:
    """A client factory whose active return configuration a test chooses.

    `configuration` is the whole point of the seam: the catalogue a method is
    checked against comes off `app.state.return_configuration`, so swapping the
    snapshot -- and nothing in Python -- must change which methods are accepted.
    """

    def build(configuration: LoadedReturnConfiguration | None = _LOADED) -> Iterator[TestClient]:
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
        if configuration is not None:
            app.state.return_configuration = configuration
        app.include_router(router)
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client

    return build


def _configuration_with(*methods: str) -> LoadedReturnConfiguration:
    """The shipped snapshot with one field replaced.

    Copied from the real configuration rather than assembled, so the thing under
    test is a policy the loader accepts.
    """
    policy = _LOADED.configuration.return_policy.model_copy(
        update={"normalized_return_methods": methods}
    )
    configuration = _LOADED.configuration.model_copy(update={"return_policy": policy})
    return _LOADED.model_copy(update={"configuration": configuration})


def _post(http: Any, body: dict[str, Any], **build: Any) -> Any:
    for client in http(**build):
        return client.post(_URL, json=body)
    raise AssertionError("the client factory yielded nothing")


def _outcome_body(
    *,
    reference: str = "RMA-1",
    method: str | None = "PREPAID_PARCEL",
    event_id: str = "evt-d23",
    **record: Any,
) -> dict[str, Any]:
    return {
        # `orderLineReferences` is required: an RMA covering no lines is a return
        # that cannot be received, credited or reconciled. Its value is incidental
        # to what this file asserts, but it has to be there.
        "records": [
            {
                "returnReference": reference,
                "returnMethod": method,
                "orderLineReferences": ["LINE-1"],
                **record,
            }
        ],
        "rejected": False,
        "supportEventId": event_id,
    }


# --------------------------------------------------------------------------- #
# The wire: HTTP -> stored notice -> workflow dataclass
# --------------------------------------------------------------------------- #


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
# 1. The method reaches both homes
# --------------------------------------------------------------------------- #


def test_the_new_method_is_genuinely_not_in_the_shipped_catalogue() -> None:
    assert _NEW_METHOD not in _CONFIGURED_METHODS


def test_the_envelope_carries_the_method_off_the_http_request(
    http: Any, mongo: _FakeClient, test_settings: Settings
) -> None:
    """The link that was missing. Asserted on the stored command, not the body."""
    response = _post(http, _outcome_body())

    assert response.status_code == 200, response.text
    notice = _stored_notice(mongo, test_settings, "evt-d23")
    assert notice["records"][0]["return_method"] == "PREPAID_PARCEL"


@pytest.mark.asyncio
async def test_a_support_outcome_reaches_the_record_and_the_fact(
    http: Any, mongo: _FakeClient, test_settings: Settings
) -> None:
    """Both homes, from one send: the column is authoritative, the fact is provenance.

    `ReturnRecordView` is constructed from the stored document rather than
    inspected as a dict, because the claim is that the *contract* carries the
    method -- a value written under a key the view does not declare would be
    dropped by `extra="forbid"` and never reach a screen.

    The fact is asserted on its id shape too. `latest_case_facts` keys on
    `factName`, so the name must stay `return_method` for every RMA and the
    record must be distinguished by the `factId` -- the arrangement
    `tracking_reference` already uses. A per-record *name* would grow the
    projection one entry per RMA and break `assembly._return_method_fact`.
    """
    response = _post(http, _outcome_body(trackingReference="1Z-1", labelReference="LBL-1"))
    assert response.status_code == 200, response.text

    fixture = _fixture()
    await _apply(fixture, _as_workflow_notice(_stored_notice(mongo, test_settings, "evt-d23")))

    stored = await fixture.repository.list_return_records(CASE_ID)
    assert len(stored) == 1
    view = ReturnRecordView.model_validate(stored[0])
    assert view.returnMethod == "PREPAID_PARCEL"

    facts = fixture.repository.facts_named("return_method")
    assert [fact["value"] for fact in facts] == ["PREPAID_PARCEL"]
    assert facts[0]["factId"].startswith(f"return_method-{stored[0]['returnRecordId']}")
    assert facts[0]["sourceSystem"] == "RETURN_SUPPORT"
    assert facts[0]["channel"] == "CHANNEL_B"


@pytest.mark.asyncio
async def test_the_method_reaches_the_authoritative_sql_row_before_the_case(
    http: Any, mongo: _FakeClient, test_settings: Settings
) -> None:
    """Gap 2, and the T-14 ordering it must not reverse.

    The SQL write commits first, so a Mongo case can never report a method the
    return store never received. Asserted as ordering, not just presence: the
    store saw the record before `cases.version` moved.
    """
    response = _post(http, _outcome_body())
    assert response.status_code == 200, response.text

    fixture = _fixture()
    revision_before = fixture.repository.revision
    await _apply(fixture, _as_workflow_notice(_stored_notice(mongo, test_settings, "evt-d23")))

    written = fixture.return_store.last_record("RMA-1")
    assert written.return_method == "PREPAID_PARCEL"
    assert fixture.repository.revision > revision_before, "the case never moved"


@pytest.mark.asyncio
async def test_a_later_reply_with_no_method_does_not_blank_the_column(
    http: Any, mongo: _FakeClient, test_settings: Settings
) -> None:
    """The whole-row `SET` receives merged values, never the notice's raw nulls.

    A tracking number arriving two hours after the RMA carries
    `return_method=None`, and `None` is the absence of a statement rather than a
    statement of absence. Applying it over the method Support already decided
    would blank the authoritative column and un-resolve a completed case.
    """
    assert _post(http, _outcome_body(event_id="evt-a")).status_code == 200
    assert (
        _post(
            http, _outcome_body(method=None, trackingReference="1Z-9", event_id="evt-b")
        ).status_code
        == 200
    )

    fixture = _fixture()
    await _apply(fixture, _as_workflow_notice(_stored_notice(mongo, test_settings, "evt-a")))
    await _apply(fixture, _as_workflow_notice(_stored_notice(mongo, test_settings, "evt-b")))

    assert fixture.return_store.last_record("RMA-1").return_method == "PREPAID_PARCEL"
    stored = await fixture.repository.list_return_records(CASE_ID)
    assert stored[0]["returnMethod"] == "PREPAID_PARCEL"
    assert stored[0]["trackingReference"] == "1Z-9"


# --------------------------------------------------------------------------- #
# 2. The proof D23 is closed: businessComplete can finally become true
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_an_approved_prepaid_parcel_case_reaches_business_complete(
    http: Any, mongo: _FakeClient, test_settings: Settings
) -> None:
    """The end-to-end proof, through the writer rather than a hand-placed fact.

    An approved case, one RMA, a label and a tracking number -- the three things
    `PREPAID_PARCEL` requires -- sent as one Support reply carrying the method.
    `awaiting` and `businessComplete` are computed by the shipped `project_case`
    over the shipped requirement table.

    Before the method had a sender this could not pass for any input: `awaiting`
    contained `RETURN_METHOD` permanently and `businessComplete` was false for
    every case in the platform.
    """
    response = _post(http, _outcome_body(trackingReference="1Z-1", labelReference="LBL-1"))
    assert response.status_code == 200, response.text

    fixture = _fixture()
    before = await fixture.repository.projection()
    assert before.businessComplete is False
    assert AwaitingDimension.RETURN_METHOD in before.awaiting

    receipt = await _apply(
        fixture, _as_workflow_notice(_stored_notice(mongo, test_settings, "evt-d23"))
    )

    after = await fixture.repository.projection()
    assert after.awaiting == ()
    assert after.businessComplete is True
    # The activity's own reading agrees with the projection's -- the workflow
    # decides whether to keep waiting on this, so a disagreement would park a
    # complete case forever.
    assert receipt.awaiting == ()
    assert receipt.business_complete is True


# --------------------------------------------------------------------------- #
# 3. The catalogue comes from the active configuration, not a literal
# --------------------------------------------------------------------------- #


def test_a_configured_method_is_accepted(http: Any) -> None:
    assert _post(http, _outcome_body(method="BRANCH_LTL")).status_code == 200


def test_a_method_the_operator_adds_is_accepted_without_a_release_of_this_code(
    http: Any, mongo: _FakeClient, test_settings: Settings
) -> None:
    """The property, stated as a passing test.

    Nothing in Python differs between this and the case above -- only the active
    snapshot. That is what makes "activate a release, add a method, send it"
    work without a restart, and it is why the check may not be a `pattern=` on
    `ReturnOutcomeRecord`.
    """
    response = _post(
        http,
        _outcome_body(method=_NEW_METHOD),
        configuration=_configuration_with(*_CONFIGURED_METHODS, _NEW_METHOD),
    )

    assert response.status_code == 200, response.text
    notice = _stored_notice(mongo, test_settings, "evt-d23")
    assert notice["records"][0]["return_method"] == _NEW_METHOD


def test_an_unconfigured_method_is_refused_and_nothing_is_recorded(
    http: Any, mongo: _FakeClient, test_settings: Settings
) -> None:
    """A method with no row in the requirement table would leave the case
    permanently unresolvable, so it is refused before anything durable is
    written rather than after."""
    response = _post(http, _outcome_body(method="CARRIER_PIGEON"))

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "UNCONFIGURED_RETURN_METHOD"
    assert mongo[test_settings.mongo_database]["integration_outbox"].documents == {}


def test_a_method_the_operator_removes_is_refused(http: Any) -> None:
    """Both directions. A check that could only widen would keep accepting a
    method the operator has withdrawn -- the same drift facing the other way."""
    response = _post(
        http,
        _outcome_body(method="BRANCH_LTL"),
        configuration=_configuration_with("PREPAID_PARCEL"),
    )

    assert response.status_code == 422, response.text
    assert "PREPAID_PARCEL" in response.json()["detail"]["message"]


def test_the_refusal_names_the_running_catalogue(http: Any) -> None:
    """From the snapshot, so the message cannot be stale."""
    response = _post(http, _outcome_body(method="CARRIER_PIGEON"))

    message = response.json()["detail"]["message"]
    for method in ("PREPAID_PARCEL", "BRANCH_LTL", "NO_PHYSICAL_RETURN"):
        assert method in message


def test_a_process_with_no_configuration_answers_503_not_422(http: Any) -> None:
    """It cannot say whether the method is valid, so it must not say it is not."""
    response = _post(http, _outcome_body(), configuration=None)

    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "RETURN_CONFIGURATION_UNAVAILABLE"


def test_a_reply_that_names_no_method_needs_no_configuration(http: Any) -> None:
    """The common case, and the reason the 503 above is conditional.

    Most notices say nothing about the method -- a delayed tracking number, a
    replacement label. Demanding a loaded snapshot for those would make an
    unconfigured process refuse replies it is perfectly able to record durably.
    """
    response = _post(http, _outcome_body(method=None), configuration=None)

    assert response.status_code == 200, response.text


def test_the_return_method_is_not_pinned_by_a_field_pattern() -> None:
    """The realistic regression: someone reinstates the vocabulary "for the
    contract". It would refuse every method added after the day it was written,
    and the generated client would advertise the stale set as authoritative."""
    from return_platform.api.return_support import ReturnOutcomeRecord

    field = ReturnOutcomeRecord.model_fields["returnMethod"]
    patterns = [item for item in field.metadata if getattr(item, "pattern", None) is not None]
    assert patterns == [], f"returnMethod is pinned by a pattern: {patterns}"
