"""`GET /api/cases/{caseId}` serves the projection, and serves it from the release.

Over real HTTP against an in-memory repository that assembles the state exactly
as `CaseRepository.load_case_projection_state` does -- the same
`CaseAggregateDocuments` handed to the same `assemble_case_projection_state`.
What is skipped is Mongo and nothing else, so the route, the assembler,
`project_case`, the completion rules and the response model are all really
exercised. Standing up a database would move the assertions from "the platform
reads this case this way" to "these documents round-tripped", which is a
different claim and one `test_case_aggregate_real_infra.py` already makes.

Four things are proved here that nothing else proves:

* **The blocks the platform has not computed are `null`, not `{}`.** That
  distinction is the whole audit, and it survives serialization only because
  every field on `CaseProjection` is nullable with no `default_factory`. A
  response carrying `warehouse: {}` would render as a warehouse that answered.
* **Another principal's case is 404, not 403.** A 403 on a guessed id confirms
  the id exists.
* **The requirement table comes from the active release, not from the code
  default.** `resolve_completion(..., requirements=)` defaults to
  `DEFAULT_RETURN_METHOD_REQUIREMENTS`, so a handler that forgot the argument
  would still answer -- with the constant -- and the operator's table, including
  the four rows the release flags for review, would be decorative. The
  configuration below is therefore made to *disagree* with the constant, and the
  route is asserted to give the configuration's answer.
* **The real stuck shape.** Record `4e372a39...` is an RMA with a label and a
  null tracking reference. Its label must be served as a live artifact, and the
  case must still report `TRACKING` outstanding -- serving the label without
  admitting the missing package is the fabrication the audit found, and dropping
  the label to avoid saying so is the other half of it.
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
    ReturnPlatformConfiguration,
    build_return_method_requirement_table,
    load_return_configuration,
)
from return_platform.configuration.settings import DEFAULT_RETURN_CONFIGURATION_PATH
from return_platform.operations.case_projection import DEFAULT_RETURN_METHOD_REQUIREMENTS
from return_platform.operations.case_projection.assembly import (
    CaseAggregateDocuments,
    assemble_case_projection_state,
)
from return_platform.operations.case_projection.contract import CaseProjectionState
from return_platform.security import roles as r
from return_platform.security.principal import Principal

TENANT = "tenant-a"
PRINCIPAL = "associate-1"
NOW = datetime(2026, 8, 15, 9, 30, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


def _case(
    case_id: str = "case-1",
    *,
    tenant_id: str = TENANT,
    principal_id: str = PRINCIPAL,
    status: str = "AWAITING_SUPPORT",
    conversation_id: str | None = "disc-1",
    order: str | None = "CW273354",
) -> dict[str, Any]:
    return {
        "caseId": case_id,
        "tenantId": tenant_id,
        "principalId": principal_id,
        "branchId": None,
        "status": status,
        "channelAConversationId": conversation_id,
        "channelBWorkItemId": None,
        "confirmedOrderReference": order,
        "confirmationKey": None,
        "sessionId": None,
        "workflowId": "return-case-case-1",
        "version": 7,
        "createdAt": NOW,
        "updatedAt": NOW,
    }


def _fact(name: str, value: Any) -> dict[str, Any]:
    return {
        "factId": f"fact-{name}",
        "caseId": "case-1",
        "factName": name,
        "value": value,
        "agentId": "return-case-workflow",
        "channel": "SYSTEM",
        "acquisitionMethod": "DERIVED",
        "sourceSystem": "RETURN_PLATFORM",
        "sourcePath": "TEST",
        "observedAt": NOW,
        "recordedAt": NOW,
        "supersedesFactId": None,
    }


def _facts(**values: Any) -> dict[str, dict[str, Any]]:
    return {name: _fact(name, value) for name, value in values.items()}


def _approved_prepaid_parcel() -> dict[str, dict[str, Any]]:
    """A case the policy approved, on a method the requirement table knows."""
    return _facts(
        policy_route="STANDARD_RETURN",
        policy_decision="APPROVE",
        approved_return_method="PREPAID_PARCEL",
    )


def _record(
    *,
    record_id: str = "rec-1",
    reference: str | None = "RMA-OPS01-CD4364",
    label: str | None = None,
    tracking: str | None = None,
) -> dict[str, Any]:
    return {
        "returnRecordId": record_id,
        "caseId": "case-1",
        "returnReference": reference,
        "status": "ISSUED",
        "returnLocation": None,
        "trackingReference": tracking,
        "labelReference": label,
        "shippingInstructionReference": None,
        "sourceSystem": "RETURN_SUPPORT",
        "version": 1,
        "createdAt": NOW,
        "updatedAt": NOW,
    }


# ---------------------------------------------------------------------------
# The app
# ---------------------------------------------------------------------------


class StubRepository:
    """Holds documents; assembles exactly as `CaseRepository` does.

    `load_case_projection_state` is reproduced here rather than stubbed to a
    hand-built `CaseProjectionState`, because a hand-built state would let the
    route look right over an assembler that had stopped agreeing with it. It
    deliberately does **not** filter by tenant or principal: that mirrors the
    real method, and a stub that filtered would make the router look correct
    whether or not it checked anything.
    """

    def __init__(
        self,
        *,
        case: dict[str, Any] | None = None,
        facts: dict[str, dict[str, Any]] | None = None,
        records: list[dict[str, Any]] | None = None,
        items: list[dict[str, Any]] | None = None,
    ) -> None:
        self._case = case
        self._facts = facts or {}
        self._records = records or []
        self._items = items or []
        self.projection_loads: list[str] = []

    async def load_case_projection_state(self, case_id: str) -> CaseProjectionState | None:
        self.projection_loads.append(case_id)
        if self._case is None:
            return None
        return assemble_case_projection_state(
            CaseAggregateDocuments(
                case=self._case,
                facts=self._facts,
                return_records=tuple(self._records),
                return_items=tuple(self._items),
                support_work_item=None,
            )
        )

    async def get_case_by_conversation(
        self,
        conversation_id: str,
        *,
        tenant_id: str | None = None,
        principal_id: str | None = None,
    ) -> dict[str, Any] | None:
        if self._case is None:
            return None
        if self._case.get("tenantId") != tenant_id:
            return None
        if self._case.get("principalId") != principal_id:
            return None
        return self._case

    async def list_cases_for_principal(
        self, *, tenant_id: str, principal_id: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        return [] if self._case is None else [self._case]


def _configuration_with_rows(
    rows: list[dict[str, Any]] | None = None,
) -> ReturnPlatformConfiguration:
    """The shipped release, optionally with its requirement table replaced."""
    shipped = load_return_configuration(DEFAULT_RETURN_CONFIGURATION_PATH).configuration
    if rows is None:
        return shipped
    payload = shipped.model_dump(mode="json")
    payload["return_policy"]["return_method_requirements"] = rows
    return ReturnPlatformConfiguration.model_validate(payload)


@pytest.fixture(autouse=True)
def _stub_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cases_module,
        "resolve_operational_repository",
        lambda request: request.app.state.stub_repository,
    )


def _client(
    repository: StubRepository,
    *,
    tenant_id: str = TENANT,
    configuration: ReturnPlatformConfiguration | None = None,
) -> Iterator[TestClient]:
    app = FastAPI()

    @app.middleware("http")
    async def _attach(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.principal = Principal(subject=PRINCIPAL, roles=frozenset({r.CONSOLE_VIEWER}))
        request.state.tenant_id = tenant_id
        request.state.correlation_id = "test-correlation-id"
        return await call_next(request)

    app.include_router(router)
    app.state.stub_repository = repository
    app.state.return_configuration = LoadedReturnConfiguration(
        configuration=configuration or _configuration_with_rows(),
        path=DEFAULT_RETURN_CONFIGURATION_PATH,
        sha256="0" * 64,
    )
    with TestClient(app) as client:
        yield client


def _read(repository: StubRepository, **kwargs: Any) -> Any:
    for client in _client(repository, **kwargs):
        return client.get("/api/cases/case-1")
    raise AssertionError("the client fixture yielded nothing")


# ---------------------------------------------------------------------------
# A case projects end to end
# ---------------------------------------------------------------------------


def test_a_case_projects_end_to_end_through_the_route() -> None:
    response = _read(
        StubRepository(
            case=_case(),
            facts=_approved_prepaid_parcel(),
            records=[_record(label="LBL-OPS01", tracking="1Z999")],
        )
    )

    assert response.status_code == 200, response.text
    body = response.json()["data"]

    # The envelope Phase 8 polls on.
    assert body["caseId"] == "case-1"
    assert body["status"] == "AWAITING_SUPPORT"
    assert body["revision"] == 7
    # Authorized RMA outranks awaiting support in the frozen precedence: the
    # authorization Support was asked for has arrived, so the pane shows the
    # leading edge of the return rather than the request that produced it.
    assert body["stage"] == "AUTHORIZED_RMA"
    assert body["isTerminal"] is False

    # Derived by `project_case`, not by the handler. `PREPAID_PARCEL` needs an
    # RMA, a label and a tracking number; this case has all three, and the label
    # is on the package rather than merely on the RMA -- so nothing is
    # outstanding and the return is complete within platform responsibility.
    #
    # **Complete is not terminal.** The workflow has not been told yet, and it
    # is the workflow that moves the status. Causal order, never the reverse.
    assert body["awaiting"] == []
    assert body["businessComplete"] is True

    # The nesting is the contract. One RMA, one package, and the label
    # attributed to that package rather than floating on the case.
    (record,) = body["returnRecords"]
    assert record["returnReference"] == "RMA-OPS01-CD4364"
    assert [shipment["trackingNumber"] for shipment in record["shipments"]] == ["1Z999"]
    (artifact,) = record["artifacts"]
    assert artifact["artifactType"] == "SHIPPING_LABEL"
    # The package's id is its tracking number: the only handle persistence holds
    # on a parcel, and the one that stays that parcel's when a second arrives.
    assert artifact["shipmentId"] == "1Z999"


def test_blocks_the_platform_has_not_computed_are_null_not_empty_objects() -> None:
    """`null` and `{}` are opposite answers, and only one of them is true here.

    `{}` would say the platform computed the block and found nothing in it; a
    pane reading that renders "no bay", "no pickup", "no support" as findings. On
    a bare case none of those were computed at all, and the serialized body has
    to keep saying so.
    """
    response = _read(StubRepository(case=_case(status="GATHERING_INFO", order=None)))

    assert response.status_code == 200, response.text
    body = response.json()["data"]

    for block in (
        "customer",
        "confirmedOrder",
        "selectedItems",
        "facts",
        "policyEvaluation",
        "support",
        "returnRecords",
        "pickup",
        "warehouse",
    ):
        assert body[block] is None, f"{block} was defaulted rather than left absent"

    # Settlement is the deliberate exception, and it is a positive statement
    # rather than a default: there is no settlement producer, and the block says
    # so instead of leaving a reader waiting for a credit memo.
    assert body["settlement"] == {
        "status": "NOT_INTEGRATED",
        "creditMemoReference": None,
        "settledAmount": None,
        "settledAt": None,
    }


def test_a_case_with_no_records_awaits_policy_and_a_return_method() -> None:
    response = _read(StubRepository(case=_case(status="GATHERING_INFO")))

    body = response.json()["data"]
    assert body["awaiting"] == ["POLICY", "RETURN_METHOD"]
    assert body["businessComplete"] is False


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


def test_another_principals_case_is_absent_not_forbidden() -> None:
    """404, not 403. A 403 on a guessed id confirms the id exists."""
    response = _read(StubRepository(case=_case(principal_id="someone-else")))

    assert response.status_code == 404, response.text
    assert response.json()["detail"]["code"] == "CASE_NOT_FOUND"


def test_another_tenants_case_is_absent_not_forbidden() -> None:
    response = _read(StubRepository(case=_case(tenant_id="tenant-b")))

    assert response.status_code == 404, response.text
    assert response.json()["detail"]["code"] == "CASE_NOT_FOUND"


def test_a_case_that_does_not_exist_is_the_same_404() -> None:
    """Indistinguishable from someone else's, which is the point of both."""
    absent = _read(StubRepository(case=None))
    foreign = _read(StubRepository(case=_case(principal_id="someone-else")))

    assert absent.status_code == foreign.status_code == 404
    assert absent.json()["detail"] == foreign.json()["detail"]


# ---------------------------------------------------------------------------
# The configured requirement table, not the code default (hazard D21)
# ---------------------------------------------------------------------------


#: A release whose `PREPAID_PARCEL` needs the authorization and nothing else.
#: The code default requires `RMA, LABEL, TRACKING` for the same method, so the
#: two tables give opposite answers about the same case -- which is the only way
#: to tell which one the route used.
_PREPAID_PARCEL_NEEDS_RMA_ONLY: list[dict[str, Any]] = [
    {"method": "PREPAID_PARCEL", "requires": ["RMA"]},
    {"method": "BRANCH_LTL", "requires": ["RMA", "BOL", "PICKUP"]},
    {"method": "OFFSITE_LTL", "requires": ["RMA", "BOL", "PICKUP", "RETURN_LOCATION"]},
    {"method": "CUSTOMER_KEEP", "requires": ["RMA"]},
    {"method": "NO_PHYSICAL_RETURN", "requires": ["RMA"]},
    {"method": "BRANCH_UPS", "requires": ["RMA", "LABEL", "TRACKING"]},
    {"method": "OFFSITE_PARCEL", "requires": ["RMA", "LABEL", "TRACKING", "RETURN_LOCATION"]},
    {"method": "DIRECT_VENDOR", "requires": ["RMA", "RETURN_LOCATION"]},
    {"method": "FIELD_SCRAP", "requires": ["RMA"]},
]


def test_the_two_tables_disagree_so_the_assertion_below_can_discriminate() -> None:
    """Guard on the guard.

    If the configured table ever matched the code default for this method, the
    test beneath would pass whichever table the route used, and would go on
    passing while proving nothing.
    """
    configured = build_return_method_requirement_table(
        _configuration_with_rows(_PREPAID_PARCEL_NEEDS_RMA_ONLY)
    )

    assert configured.requirements_for("PREPAID_PARCEL") != (
        DEFAULT_RETURN_METHOD_REQUIREMENTS.requirements_for("PREPAID_PARCEL")
    )


def test_the_route_completes_a_case_the_configured_table_says_is_complete() -> None:
    """The configured answer, not the constant's.

    The case has an approved policy, `PREPAID_PARCEL`, an RMA reference and
    neither a label nor a package. The code default calls that
    `awaiting: [LABEL, TRACKING]`; the release above calls it done. The route
    reports done, so the release's table is the one in use.
    """
    response = _read(
        StubRepository(
            case=_case(),
            facts=_approved_prepaid_parcel(),
            records=[_record()],
        ),
        configuration=_configuration_with_rows(_PREPAID_PARCEL_NEEDS_RMA_ONLY),
    )

    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert body["awaiting"] == []
    assert body["businessComplete"] is True


def test_the_same_case_is_incomplete_under_the_shipped_table() -> None:
    """The other half of the pair, so neither answer is an accident of the fixture."""
    response = _read(
        StubRepository(
            case=_case(),
            facts=_approved_prepaid_parcel(),
            records=[_record()],
        )
    )

    body = response.json()["data"]
    assert body["awaiting"] == ["LABEL", "TRACKING"]
    assert body["businessComplete"] is False


def test_the_list_also_uses_the_configured_table() -> None:
    """`isTerminal` is status-derived, but `stage` reads completion.

    The list row is built by the same `project_case` call, so a route that
    passed the table to the detail and forgot it here would hand a console two
    different readings of one case.
    """
    repository = StubRepository(
        case=_case(status="CLOSED"),
        facts=_approved_prepaid_parcel(),
        records=[_record()],
    )
    for client in _client(
        repository, configuration=_configuration_with_rows(_PREPAID_PARCEL_NEEDS_RMA_ONLY)
    ):
        response = client.get("/api/cases")

    assert response.status_code == 200, response.text
    (row,) = response.json()["data"]
    assert row["isTerminal"] is True
    assert row["stage"] == "COMPLETED"
    # The projected vocabulary, not the persisted `CLOSED`: there is no
    # settlement producer, so a closed case settled outside this platform.
    assert row["status"] == "COMPLETED_EXTERNAL_SETTLEMENT"


def test_the_route_refuses_rather_than_falling_back_to_the_code_default() -> None:
    """No active release, no projection.

    Answering from `DEFAULT_RETURN_METHOD_REQUIREMENTS` would make an
    unconfigured platform indistinguishable from a configured one, and the
    difference would surface only as a return that completed without its
    paperwork.
    """
    app = FastAPI()

    @app.middleware("http")
    async def _attach(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.principal = Principal(subject=PRINCIPAL, roles=frozenset({r.CONSOLE_VIEWER}))
        request.state.tenant_id = TENANT
        request.state.correlation_id = "test-correlation-id"
        return await call_next(request)

    app.include_router(router)
    app.state.stub_repository = StubRepository(case=_case())
    with TestClient(app) as client:
        response = client.get("/api/cases/case-1")

    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "RETURN_CONFIGURATION_UNAVAILABLE"


# ---------------------------------------------------------------------------
# The real stuck shape: RMA + label + null tracking
# ---------------------------------------------------------------------------


def test_an_rma_with_a_label_and_no_tracking_serves_the_label_and_still_awaits_tracking() -> None:
    """Record `4e372a39...`, which is why the contract was amended.

    The label is real and is served. There is no package, so the label is
    attributed to none -- `shipmentId: null` -- rather than to a shipment
    invented to carry it, and `TRACKING` stays outstanding. Both halves matter:
    minting a shipment is the audit's `TRK-98421049281` in a new costume, and
    dropping the label to avoid minting one hides a document the associate has
    in their hand.
    """
    response = _read(
        StubRepository(
            case=_case(),
            facts=_approved_prepaid_parcel(),
            records=[_record(label="LBL-OPS01", tracking=None)],
        )
    )

    assert response.status_code == 200, response.text
    body = response.json()["data"]
    (record,) = body["returnRecords"]

    # The label is served, live, and attributed to no package.
    (artifact,) = record["artifacts"]
    assert artifact["artifactId"] == "LBL-OPS01"
    assert artifact["artifactType"] == "SHIPPING_LABEL"
    assert artifact["active"] is True
    assert artifact["supersededBy"] is None
    assert artifact["shipmentId"] is None

    # No package was invented to carry it.
    assert record["shipments"] is None

    # And the case still says the package is missing. `LABEL` is outstanding for
    # the same reason: the requirement is that every package is papered, and
    # there is no package to paper.
    assert "TRACKING" in body["awaiting"]
    assert body["awaiting"] == ["LABEL", "TRACKING"]
    assert body["businessComplete"] is False
    assert body["isTerminal"] is False


def test_the_stuck_case_keeps_polling() -> None:
    """The defect this whole shape exists to make impossible.

    An RMA existed, so the shipped client stopped polling and the associate
    watched a return that never appeared to acquire a label. `isTerminal` is
    false here, which is the only signal that predicate is now allowed to read.
    """
    response = _read(
        StubRepository(
            # `RMA_RECEIVED` is the persisted vocabulary; the projection reads it
            # as `PROCESSING_RETURN`. The two enums are not the same set, and
            # `status_mapping` is the one place that reconciles them.
            case=_case(status="RMA_RECEIVED"),
            facts=_approved_prepaid_parcel(),
            records=[_record(label="LBL-OPS01", tracking=None)],
        )
    )

    assert response.json()["data"]["isTerminal"] is False
