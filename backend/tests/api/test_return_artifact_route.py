"""`GET /api/cases/{id}/returns/{id}/artifacts/{id}` -- the label, and who may have it.

The route the Copilot's print button needs. What it replaced was
`window.print()`, which printed the web page, because `ReturnArtifactProjection`
named a `SHIPPING_LABEL` and nothing served it.

Four claims are proved here and nothing else proves them:

* **The label of a real record is retrievable.** `LBL-OPS01` on record
  `4e372a39...` -- the RMA with a label and a null tracking reference -- comes
  back through the route, marked live, attributed to no package.
* **Containment is the authorization.** A foreign principal, a foreign tenant, a
  record on another case and an artifact on another record are all 404 and all
  the *same* 404. An artifact is never validated on its id alone, so the two
  path segments above it are not decoration.
* **Package attribution survives retrieval.** Two labels on one RMA, one per
  package: each is addressable by its own id and each reports its own
  `shipmentId`. Neither can be fetched as the other, which is the
  cross-attribution the record-level `artifacts[]` amendment exists to prevent.
* **A superseded artifact stays addressable and stops being active.** Both
  halves: audit needs the document, and the label action must never take it.

**No document is invented.** `labelReference` is a string Support typed; this
platform has no object store behind it, so the response is the reference and its
metadata under `contentState: REFERENCE_ONLY`. A test asserting a PDF here would
be asserting a fabrication.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
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
from return_platform.operations.case_projection.contract import (
    CaseProjectionState,
    ReturnArtifactProjection,
    ReturnRecordProjection,
    ShipmentProjection,
)
from return_platform.operations.case_projection.vocabulary import (
    ReturnArtifactType,
    ReturnCaseStatus,
)
from return_platform.security import roles as r
from return_platform.security.principal import Principal

TENANT = "tenant-ops01"
PRINCIPAL = "associate-1"
CASE = "d3190045-3baa-4895-8ad0-461d080eb750"
RECORD = "4e372a39-882a-4617-b2c8-60c14e094c64"
LABEL = "LBL-OPS01"
NOW = datetime(2026, 8, 15, 9, 30, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Documents, and the states persistence cannot yet express
# ---------------------------------------------------------------------------


def _case(
    *,
    tenant_id: str = TENANT,
    principal_id: str = PRINCIPAL,
) -> dict[str, Any]:
    return {
        "caseId": CASE,
        "tenantId": tenant_id,
        "principalId": principal_id,
        "branchId": None,
        "status": "AWAITING_SUPPORT",
        "channelAConversationId": "disc-1",
        "channelBWorkItemId": None,
        "confirmedOrderReference": "CW273354",
        "confirmationKey": None,
        "sessionId": None,
        "workflowId": f"return-case-{CASE}",
        "version": 7,
        "createdAt": NOW,
        "updatedAt": NOW,
    }


def _record(
    *,
    record_id: str = RECORD,
    reference: str = "RMA-OPS01-CD4364",
    label: str | None = LABEL,
    tracking: str | None = None,
) -> dict[str, Any]:
    return {
        "returnRecordId": record_id,
        "caseId": CASE,
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


def _state_with_artifacts(*artifacts: ReturnArtifactProjection) -> CaseProjectionState:
    """A case built directly, for shapes the legacy columns cannot hold.

    Assembling from documents is the right default and the sibling route test
    argues for it: a hand-built state can look correct over an assembler that
    stopped agreeing with it. It is not available here. `ReturnRecordView`
    carries **one** `labelReference` and **one** `trackingReference`, so two
    artifacts on one RMA, a superseded label and an expiry are all
    inexpressible in persistence -- the writer that would produce them is the
    Support outcome path, and it holds no version history.

    That is a real gap and it is reported rather than papered over: these three
    tests prove the *route* handles shapes the contract can express, and
    `test_the_real_records_label_is_retrievable` proves the assembled path on
    the one shape persistence does hold. Neither claim is made by the other.
    """
    return CaseProjectionState(
        caseId=CASE,
        tenantId=TENANT,
        principalId=PRINCIPAL,
        status=ReturnCaseStatus.AWAITING_SUPPORT,
        revision=7,
        updatedAt=NOW,
        returnRecords=(
            ReturnRecordProjection(
                returnRecordId=RECORD,
                returnReference="RMA-OPS01-CD4364",
                status="ISSUED",
                shipments=(
                    ShipmentProjection(shipmentId="SHP-1", trackingNumber="1Z999AA1"),
                    ShipmentProjection(shipmentId="SHP-2", trackingNumber="1Z999BB2"),
                ),
                artifacts=artifacts,
            ),
        ),
    )


def _artifact(
    artifact_id: str,
    *,
    shipment_id: str | None = None,
    active: bool = True,
    superseded_by: str | None = None,
    version: int = 1,
    expires_at: datetime | None = None,
) -> ReturnArtifactProjection:
    return ReturnArtifactProjection(
        artifactId=artifact_id,
        artifactType=ReturnArtifactType.SHIPPING_LABEL,
        shipmentId=shipment_id,
        version=version,
        active=active,
        supersededBy=superseded_by,
        expiresAt=expires_at,
    )


# ---------------------------------------------------------------------------
# The app
# ---------------------------------------------------------------------------


class StubRepository:
    """Documents assembled exactly as `CaseRepository` does, or a prepared state.

    It deliberately does not filter by tenant or principal, mirroring the real
    method: a stub that filtered would make the router look correct whether or
    not it checked anything.
    """

    def __init__(
        self,
        *,
        case: dict[str, Any] | None = None,
        records: list[dict[str, Any]] | None = None,
        state: CaseProjectionState | None = None,
    ) -> None:
        self._case = case
        self._records = records or []
        self._state = state

    async def load_case_projection_state(self, case_id: str) -> CaseProjectionState | None:
        if self._state is not None:
            return self._state if case_id == self._state.caseId else None
        if self._case is None or case_id != self._case["caseId"]:
            return None
        return assemble_case_projection_state(
            CaseAggregateDocuments(
                case=self._case,
                facts={},
                return_records=tuple(self._records),
                return_items=(),
                support_work_item=None,
            )
        )


@pytest.fixture(autouse=True)
def _stub_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
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
    app.state.return_configuration = LoadedReturnConfiguration(
        configuration=load_return_configuration(DEFAULT_RETURN_CONFIGURATION_PATH).configuration,
        path=DEFAULT_RETURN_CONFIGURATION_PATH,
        sha256="0" * 64,
    )
    with TestClient(app) as client:
        yield client


def _fetch(
    repository: StubRepository,
    *,
    case_id: str = CASE,
    record_id: str = RECORD,
    artifact_id: str = LABEL,
    tenant_id: str = TENANT,
) -> Any:
    for client in _client(repository, tenant_id=tenant_id):
        return client.get(f"/api/cases/{case_id}/returns/{record_id}/artifacts/{artifact_id}")
    raise AssertionError("the client fixture yielded nothing")


def _assembled() -> StubRepository:
    """The real stuck record, out of documents: RMA + label + null tracking."""
    return StubRepository(case=_case(), records=[_record()])


# ---------------------------------------------------------------------------
# The label is retrievable
# ---------------------------------------------------------------------------


def test_the_real_records_label_is_retrievable() -> None:
    """Record `4e372a39...`, assembled from its documents and served.

    The one shape persistence actually holds, so this is the test that proves
    the assembler, the route and the response model agree on a real case rather
    than on a fixture written to suit them.
    """
    response = _fetch(_assembled())

    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert body["caseId"] == CASE
    assert body["returnRecordId"] == RECORD
    assert body["artifactId"] == LABEL
    assert body["artifactType"] == "SHIPPING_LABEL"
    assert body["isActive"] is True
    assert body["supersededBy"] is None
    # No package has been tendered, so the label is attributed to none. A
    # shipment id here would be the audit's `TRK-98421049281` in a new costume.
    assert body["shipmentId"] is None


def test_no_document_is_invented_behind_the_reference() -> None:
    """The honest answer, stated positively.

    `labelReference` is a string Support typed into `ReturnOutcomeRecord`. There
    is no object store, no bucket, no provider URL and no bytes behind it, so
    the route serves the reference and says so. An empty PDF, or a 404 that read
    as "wrong id", would both be worse than the truth.
    """
    body = _fetch(_assembled()).json()["data"]

    assert body["contentState"] == "REFERENCE_ONLY"
    assert body["fileName"] is None
    assert body["mediaType"] is None
    # And nowhere for a storage URL to hide, on the response as on the contract.
    assert not [name for name in body if "url" in name.lower()]


# ---------------------------------------------------------------------------
# Containment is the authorization
# ---------------------------------------------------------------------------


def test_a_foreign_principal_gets_404_not_403() -> None:
    """A 403 on a guessed id confirms the id exists."""
    response = _fetch(StubRepository(case=_case(principal_id="someone-else"), records=[_record()]))

    assert response.status_code == 404, response.text
    assert response.json()["detail"]["code"] == "CASE_NOT_FOUND"


def test_a_foreign_tenant_gets_the_identical_404() -> None:
    foreign = _fetch(_assembled(), tenant_id="tenant-b")
    absent = _fetch(StubRepository(case=None))

    assert foreign.status_code == absent.status_code == 404
    assert foreign.json()["detail"] == absent.json()["detail"]


def test_an_artifact_belonging_to_another_return_is_404() -> None:
    """The fourth check, and the one plan sect. 11 is written for.

    Two RMAs on one case, each with its own label. `LBL-OTHER` is a real
    artifact and the caller is legitimately on the case -- and it is still
    absent under `4e372a39...`, because it is not that return's document. An
    artifact validated on its id alone is an artifact servable to whoever
    guesses it.
    """
    repository = StubRepository(
        case=_case(),
        records=[
            _record(),
            _record(record_id="rec-2", reference="RMA-OPS01-OTHER", label="LBL-OTHER"),
        ],
    )

    # Each is served under its own record.
    assert _fetch(repository).status_code == 200
    assert _fetch(repository, record_id="rec-2", artifact_id="LBL-OTHER").status_code == 200

    # Neither is served under the other's.
    crossed = _fetch(repository, artifact_id="LBL-OTHER")
    assert crossed.status_code == 404, crossed.text
    assert crossed.json()["detail"]["code"] == "RETURN_ARTIFACT_NOT_FOUND"
    assert _fetch(repository, record_id="rec-2", artifact_id=LABEL).status_code == 404


def test_an_unknown_record_and_an_unknown_artifact_are_one_answer() -> None:
    """Indistinguishable, so the refusal is not an oracle.

    A distinct "no such return record" would tell a caller which half of the
    path was wrong, and two probes would then enumerate another return's
    paperwork one segment at a time.
    """
    repository = _assembled()

    unknown_record = _fetch(repository, record_id="rec-nope")
    unknown_artifact = _fetch(repository, artifact_id="LBL-NOPE")

    assert unknown_record.status_code == unknown_artifact.status_code == 404
    assert unknown_record.json()["detail"]["code"] == "RETURN_ARTIFACT_NOT_FOUND"
    assert unknown_artifact.json()["detail"]["code"] == "RETURN_ARTIFACT_NOT_FOUND"


def test_a_record_on_another_case_is_not_reachable_through_this_one() -> None:
    """Containment upwards as well: the record must be on *this* case."""
    response = _fetch(_assembled(), case_id="case-somebody-else")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "CASE_NOT_FOUND"


# ---------------------------------------------------------------------------
# Package attribution, multi-label
# ---------------------------------------------------------------------------


def test_two_labels_on_one_rma_are_each_addressable_as_their_own_package() -> None:
    """Never assume 1 RMA = 1 package.

    Two packages, two labels, one RMA. Each label answers under its own id and
    reports its own `shipmentId`; fetching one never yields the other's
    attribution. That is the failure two homes for a document used to make
    possible, and it is asserted at the retrieval boundary and not only in the
    projection.
    """
    repository = StubRepository(
        state=_state_with_artifacts(
            _artifact("LBL-1", shipment_id="SHP-1"),
            _artifact("LBL-2", shipment_id="SHP-2"),
        )
    )

    first = _fetch(repository, artifact_id="LBL-1").json()["data"]
    second = _fetch(repository, artifact_id="LBL-2").json()["data"]

    assert first["artifactId"] == "LBL-1"
    assert first["shipmentId"] == "SHP-1"
    assert second["artifactId"] == "LBL-2"
    assert second["shipmentId"] == "SHP-2"
    # The whole point: neither is served for the other's package.
    assert first["shipmentId"] != second["shipmentId"]


# ---------------------------------------------------------------------------
# Superseding
# ---------------------------------------------------------------------------


def test_a_superseded_artifact_is_addressable_and_is_not_the_active_one() -> None:
    """Both halves, and they pull in opposite directions.

    The replaced label must stay fetchable -- an audit that cannot read the
    document it is auditing is not an audit, which is why superseding is not a
    delete. And it must not be what the single label action resolves to. The
    route serves it with `isActive: false` and a pointer to its replacement, so
    the caller has the document and cannot mistake it for the live one.
    """
    repository = StubRepository(
        state=_state_with_artifacts(
            _artifact("LBL-V1", shipment_id="SHP-1", version=1, superseded_by="LBL-V2"),
            _artifact("LBL-V2", shipment_id="SHP-1", version=2),
        )
    )

    old = _fetch(repository, artifact_id="LBL-V1")
    new = _fetch(repository, artifact_id="LBL-V2")

    assert old.status_code == 200, old.text
    assert old.json()["data"]["isActive"] is False
    assert old.json()["data"]["supersededBy"] == "LBL-V2"
    assert old.json()["data"]["version"] == 1

    assert new.json()["data"]["isActive"] is True
    assert new.json()["data"]["supersededBy"] is None


def test_an_artifact_the_platform_never_marked_live_is_not_active() -> None:
    """`active` unset is not active.

    An artifact nobody declared live is not evidence that a label exists, and
    `is_active` reads both clauses -- which is the server-side resolution plan
    sect. 11 requires instead of the client taking `labels[0]`.
    """
    repository = StubRepository(state=_state_with_artifacts(_artifact("LBL-X", active=False)))

    assert _fetch(repository, artifact_id="LBL-X").json()["data"]["isActive"] is False


# ---------------------------------------------------------------------------
# Expiry
# ---------------------------------------------------------------------------


def test_an_expired_artifact_is_a_409_with_a_re_issue_hint() -> None:
    """Not a broken link, and not a 404.

    404 would say the artifact does not exist, and the operator would go looking
    for a mistake in the id. 409 says the document was real and is no longer
    usable, and names the act that fixes it.
    """
    repository = StubRepository(
        state=_state_with_artifacts(
            _artifact("LBL-OLD", expires_at=datetime.now(UTC) - timedelta(hours=1))
        )
    )

    response = _fetch(repository, artifact_id="LBL-OLD")

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "RETURN_ARTIFACT_EXPIRED"
    assert "re-issue" in detail["message"]
    assert RECORD in detail["message"]


def test_an_unexpired_artifact_is_served() -> None:
    """The other half of the pair, so the 409 is not simply "any expiresAt"."""
    repository = StubRepository(
        state=_state_with_artifacts(
            _artifact("LBL-NEW", expires_at=datetime.now(UTC) + timedelta(days=1))
        )
    )

    response = _fetch(repository, artifact_id="LBL-NEW")

    assert response.status_code == 200, response.text
    assert response.json()["data"]["expiresAt"] is not None


# ---------------------------------------------------------------------------
# The projection and the route agree
# ---------------------------------------------------------------------------


def test_every_artifact_the_case_read_advertises_is_retrievable() -> None:
    """No dead links on the pane.

    The Copilot builds the label action out of `returnRecords[].artifacts[]`, so
    an artifact the case read names and this route refuses would be a button
    that 404s. Walked rather than asserted on one id, so a future artifact type
    is covered by the same test.
    """
    repository = _assembled()

    for client in _client(repository):
        case = client.get(f"/api/cases/{CASE}").json()["data"]
        for record in case["returnRecords"]:
            for artifact in record["artifacts"] or ():
                fetched = client.get(
                    f"/api/cases/{CASE}/returns/{record['returnRecordId']}"
                    f"/artifacts/{artifact['artifactId']}"
                )
                assert fetched.status_code == 200, fetched.text
                assert fetched.json()["data"]["artifactId"] == artifact["artifactId"]
                assert fetched.json()["data"]["shipmentId"] == artifact["shipmentId"]
