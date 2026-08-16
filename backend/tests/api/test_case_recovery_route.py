"""`/api/cases/{caseId}/recovery` -- why a case is stuck, and the one repair.

Over real HTTP, so the two things this surface exists to guarantee are asserted
where a caller would meet them:

* **Reading is not repairing.** They are two routes and two verbs, and the GET
  never reaches the launcher. A diagnostic that restarted the case it was asked
  about would be an operator's most dangerous button disguised as a page load.
* **Refusal is a 200 carrying the reason.** `ALREADY_RUNNING`,
  `REFUSED_TERMINAL` and `DEFERRED_UNKNOWN` are answers, not errors, and an
  operator needs to be able to tell them apart. Collapsing them into a 409 would
  lose exactly the distinction Phase 10 is about.

The recovery service itself is substituted, because its rules are proved
exhaustively in `tests/test_case_recovery_reconciliation.py` against the real
classifier. What is real here is the routing, the tenant scope, the role gates
and the serialization -- including that `CaseProjection` is left alone, since
the read contract may never call Temporal.
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
from return_platform.operations.case_projection.vocabulary import ReturnCaseStatus
from return_platform.operations.models import CaseStatus
from return_platform.security import roles as r
from return_platform.security.principal import Principal
from return_platform.workflows.case_divergence import (
    CaseDivergence,
    CaseDivergenceAssessment,
    CaseExecutionState,
    DivergenceReason,
)
from return_platform.workflows.return_case_recovery import CaseRecoveryOutcome, RecoveryAction

TENANT = "tenant-a"
CASE_ID = "case-1"
NOW = datetime(2026, 8, 15, 9, 30, tzinfo=UTC)


def _assessment(
    *,
    persisted: CaseStatus = CaseStatus.AWAITING_SUPPORT,
    projected: ReturnCaseStatus = ReturnCaseStatus.AWAITING_SUPPORT,
    execution: CaseExecutionState = CaseExecutionState.CLOSED,
    divergence: CaseDivergence = CaseDivergence.RECOVERY_REQUIRED,
    reason: DivergenceReason = DivergenceReason.EXECUTION_CLOSED_UNDER_ACTIVE_CASE,
) -> CaseDivergenceAssessment:
    return CaseDivergenceAssessment(
        case_id=CASE_ID,
        persisted_status=persisted,
        projected_status=projected,
        execution=execution,
        divergence=divergence,
        reason=reason,
        execution_detail="TERMINATED",
    )


class StubRepository:
    def __init__(self, case: dict[str, Any] | None) -> None:
        self._case = case

    async def get_case(self, case_id: str) -> dict[str, Any] | None:
        return self._case


class StubService:
    """Records what the route asked of it, and answers what a test set up."""

    def __init__(
        self,
        *,
        assessment: CaseDivergenceAssessment | None = None,
        outcome: CaseRecoveryOutcome | None = None,
    ) -> None:
        self._assessment = assessment
        self._outcome = outcome
        self.assessed: list[str] = []
        self.reconciled: list[str] = []

    async def assess(self, case_id: str) -> CaseDivergenceAssessment | None:
        self.assessed.append(case_id)
        return self._assessment

    async def reconcile_case(self, case_id: str, **_: Any) -> CaseRecoveryOutcome:
        self.reconciled.append(case_id)
        assert self._outcome is not None
        return self._outcome


def _case_document(status: CaseStatus = CaseStatus.AWAITING_SUPPORT) -> dict[str, Any]:
    return {
        "caseId": CASE_ID,
        "tenantId": TENANT,
        "principalId": "associate-1",
        "status": status.value,
        "channelAConversationId": "disc-1",
        "channelBWorkItemId": "wi-1",
        "version": 4,
        "createdAt": NOW,
        "updatedAt": NOW,
    }


def _client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    case: dict[str, Any] | None,
    service: StubService | None,
    roles: frozenset[str] = frozenset({r.CONSOLE_ADMIN}),
    tenant_id: str = TENANT,
) -> Iterator[TestClient]:
    monkeypatch.setattr(
        cases_module,
        "resolve_operational_repository",
        lambda request: request.app.state.stub_repository,
    )
    if service is not None:
        monkeypatch.setattr(cases_module, "_recovery_service", lambda request: service)

    app = FastAPI()

    @app.middleware("http")
    async def _attach(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.principal = Principal(subject="associate-1", roles=roles)
        request.state.tenant_id = tenant_id
        request.state.correlation_id = "test-correlation-id"
        return await call_next(request)

    app.include_router(router)
    app.state.stub_repository = StubRepository(case)
    with TestClient(app) as client:
        yield client


def test_the_recovery_read_says_why_a_case_is_stuck(monkeypatch: pytest.MonkeyPatch) -> None:
    """The diagnosis an operator opens, and it writes nothing to get it."""
    service = StubService(assessment=_assessment())
    for client in _client(monkeypatch, case=_case_document(), service=service):
        response = client.get(f"/api/cases/{CASE_ID}/recovery")

        assert response.status_code == 200, response.text
        body = response.json()["data"]
        assert body["caseId"] == CASE_ID
        assert body["status"] == "AWAITING_SUPPORT"
        assert body["persistedStatus"] == "AWAITING_SUPPORT"
        assert body["executionState"] == "CLOSED"
        assert body["executionStatus"] == "TERMINATED"
        assert body["workflowId"] == f"return-case-{CASE_ID}"
        assert body["divergence"] == "RECOVERY_REQUIRED"
        assert body["reason"] == "EXECUTION_CLOSED_UNDER_ACTIVE_CASE"
        assert body["isRecoverable"] is True
        assert body["lateEventDisposition"] == "DRIVES_RECOVERY"
        # Read only. The GET must never be the thing that restarts a case.
        assert service.reconciled == []
        return
    raise AssertionError("the client fixture yielded nothing")


def test_a_healthy_case_reports_that_it_is_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    service = StubService(
        assessment=_assessment(
            execution=CaseExecutionState.RUNNING,
            divergence=CaseDivergence.HEALTHY,
            reason=DivergenceReason.EXECUTION_LIVE,
        )
    )
    for client in _client(monkeypatch, case=_case_document(), service=service):
        body = client.get(f"/api/cases/{CASE_ID}/recovery").json()["data"]
        assert body["divergence"] == "HEALTHY"
        assert body["isRecoverable"] is False
        assert body["lateEventDisposition"] == "DELIVERABLE"
        return
    raise AssertionError("the client fixture yielded nothing")


def test_relaunching_an_orphan_reports_what_it_started(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A relaunch that acted says so, and says what it re-drove with it."""
    assessment = _assessment()
    service = StubService(
        assessment=assessment,
        outcome=CaseRecoveryOutcome(
            case_id=CASE_ID,
            action=RecoveryAction.RELAUNCHED,
            assessment=assessment,
            workflow_id=f"return-case-{CASE_ID}",
            requeued_commands=2,
        ),
    )
    for client in _client(monkeypatch, case=_case_document(), service=service):
        response = client.post(f"/api/cases/{CASE_ID}/recovery/relaunch")

        assert response.status_code == 200, response.text
        body = response.json()["data"]
        assert body["action"] == "RELAUNCHED"
        assert body["workflowId"] == f"return-case-{CASE_ID}"
        # The replies were durable the whole time; recovery re-drives them
        # rather than asking Support to send the RMA again.
        assert body["requeuedSupportEvents"] == 2
        assert body["rejectedSupportEvents"] == 0
        assert body["recovery"]["reason"] == "EXECUTION_CLOSED_UNDER_ACTIVE_CASE"
        assert service.reconciled == [CASE_ID]
        return
    raise AssertionError("the client fixture yielded nothing")


def test_relaunching_a_running_case_is_refused_and_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 200 naming the refusal, not a duplicate execution and not a 409.

    The operator asked a reasonable question and the answer is "there is
    nothing wrong with this case". Reporting that as an error would train
    whoever is holding the incident to ignore it.
    """
    assessment = _assessment(
        execution=CaseExecutionState.RUNNING,
        divergence=CaseDivergence.HEALTHY,
        reason=DivergenceReason.EXECUTION_LIVE,
    )
    service = StubService(
        outcome=CaseRecoveryOutcome(
            case_id=CASE_ID,
            action=RecoveryAction.ALREADY_RUNNING,
            assessment=assessment,
            workflow_id=f"return-case-{CASE_ID}",
        )
    )
    for client in _client(monkeypatch, case=_case_document(), service=service):
        response = client.post(f"/api/cases/{CASE_ID}/recovery/relaunch")

        assert response.status_code == 200, response.text
        body = response.json()["data"]
        assert body["action"] == "ALREADY_RUNNING"
        assert body["requeuedSupportEvents"] == 0
        assert body["recovery"]["isRecoverable"] is False
        return
    raise AssertionError("the client fixture yielded nothing")


def test_relaunching_a_terminal_case_is_refused_permanently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A finished case stays finished, and the late reply is kept rather than applied."""
    assessment = _assessment(
        persisted=CaseStatus.CLOSED,
        projected=ReturnCaseStatus.COMPLETED_EXTERNAL_SETTLEMENT,
        divergence=CaseDivergence.CASE_TERMINAL,
        reason=DivergenceReason.TERMINAL_CASE_SETTLED,
    )
    service = StubService(
        outcome=CaseRecoveryOutcome(
            case_id=CASE_ID,
            action=RecoveryAction.REFUSED_TERMINAL,
            assessment=assessment,
            rejected_commands=1,
        )
    )
    for client in _client(monkeypatch, case=_case_document(CaseStatus.CLOSED), service=service):
        body = client.post(f"/api/cases/{CASE_ID}/recovery/relaunch").json()["data"]

        assert body["action"] == "REFUSED_TERMINAL"
        assert body["workflowId"] is None
        assert body["rejectedSupportEvents"] == 1
        assert body["recovery"]["status"] == "COMPLETED_EXTERNAL_SETTLEMENT"
        assert body["recovery"]["persistedStatus"] == "CLOSED"
        assert body["recovery"]["lateEventDisposition"] == "PERMANENTLY_REJECTED"
        return
    raise AssertionError("the client fixture yielded nothing")


def test_another_tenants_case_is_absent_not_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 403 on a guessed id confirms the id exists, which is most of the answer."""
    service = StubService(assessment=_assessment())
    for client in _client(
        monkeypatch, case=_case_document(), service=service, tenant_id="tenant-other"
    ):
        for response in (
            client.get(f"/api/cases/{CASE_ID}/recovery"),
            client.post(f"/api/cases/{CASE_ID}/recovery/relaunch"),
        ):
            assert response.status_code == 404, response.text
            assert response.json()["detail"]["code"] == "CASE_NOT_FOUND"
        assert service.assessed == []
        assert service.reconciled == []
        return
    raise AssertionError("the client fixture yielded nothing")


def test_a_viewer_may_not_relaunch_a_case(monkeypatch: pytest.MonkeyPatch) -> None:
    """Restarting a durable execution is an administrator's act, not a reader's."""
    service = StubService(assessment=_assessment())
    for client in _client(
        monkeypatch,
        case=_case_document(),
        service=service,
        roles=frozenset({r.RETURN_AUDITOR}),
    ):
        # The auditor may read the diagnosis -- it is the same gate as the
        # integration-outbox listing this is the other half of.
        assert client.get(f"/api/cases/{CASE_ID}/recovery").status_code == 200
        assert client.post(f"/api/cases/{CASE_ID}/recovery/relaunch").status_code == 403
        assert service.reconciled == []
        return
    raise AssertionError("the client fixture yielded nothing")


def test_a_process_with_no_workflow_host_refuses_rather_than_guessing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No Temporal client means the question cannot be answered, so it is not.

    The service is deliberately *not* stubbed here, so the real dependency
    resolution runs. Answering "healthy" from a process that cannot see the
    workflow host would be the fabrication this programme removes.
    """
    for client in _client(monkeypatch, case=_case_document(), service=None):
        response = client.get(f"/api/cases/{CASE_ID}/recovery")

        assert response.status_code == 503, response.text
        assert response.json()["detail"]["code"] == "WORKFLOW_HOST_UNAVAILABLE"
        return
    raise AssertionError("the client fixture yielded nothing")
