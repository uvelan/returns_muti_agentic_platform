"""S2: what recovery must *not* do once a case can be waiting on a person.

Contracts.md sect. 6 and the S2 brief's recovery item. Two rules, both of them
refusals, and both of them about the same mistake in different clothes:

* `AWAITING_TEMPLATE_REVIEW` is a legitimate wait. The time-based sweep has
  only elapsed time to reason from, and a case whose whole design is to sit
  until a reviewer answers gives that sweep no evidence at all. Relaunching
  there restarts a case whose reviewer simply has not answered yet -- and from
  their side, their draft vanishes.
* A command that committed and was never applied, past the outbox's retry
  horizon, is an operations question and not a recovery candidate. Relaunching
  past the horizon replaces one undiagnosed state with another and destroys the
  evidence for the first.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from return_platform.operations.case_projection.status_mapping import project_case_status
from return_platform.operations.case_projection.vocabulary import ReturnCaseStatus
from return_platform.operations.models import CaseStatus
from return_platform.workflows.case_divergence import CaseDivergence, CaseExecutionState
from return_platform.workflows.return_case_launcher import (
    CaseWorkflowResume,
    StartedCaseWorkflow,
)
from return_platform.workflows.return_case_recovery import (
    LEGITIMATE_WAIT_STATUSES,
    RecoveryAction,
    ReturnCaseRecoveryService,
    ReturnCaseWorkflowRecovery,
)

_async = pytest.mark.asyncio

CASE_ID = "case-review-1"
NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)


def _case(
    *,
    case_id: str = CASE_ID,
    status: CaseStatus = CaseStatus.AWAITING_TEMPLATE_REVIEW,
) -> dict[str, Any]:
    return {
        "caseId": case_id,
        "tenantId": "default",
        "principalId": "dev-operator",
        "status": status.value,
        "channelAConversationId": "disc-1",
        "workflowId": None,
        "configurationReleaseId": "release-1",
        "version": 3,
        "createdAt": NOW - timedelta(days=1),
        "updatedAt": NOW - timedelta(days=1),
    }


class _RecordingLauncher:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def ensure_case_workflow(
        self,
        *,
        case_id: str,
        tenant_id: str,
        principal_id: str,
        conversation_id: str,
        configuration_release_id: str,
        resume: CaseWorkflowResume | None = None,
    ) -> StartedCaseWorkflow:
        self.calls.append(case_id)
        return StartedCaseWorkflow(workflow_id=f"return-case-{case_id}", already_running=False)


class _PendingCases:
    def __init__(self, cases: list[dict[str, Any]]) -> None:
        self._cases = cases

    async def list_cases_without_workflow(
        self, *, created_before: datetime, limit: int
    ) -> list[dict[str, Any]]:
        del created_before
        return self._cases[:limit]


class _Store:
    def __init__(self, cases: dict[str, dict[str, Any]]) -> None:
        self.cases = cases
        self.writes: list[str] = []

    async def get_case(self, case_id: str) -> dict[str, Any] | None:
        case = self.cases.get(case_id)
        return dict(case) if case is not None else None

    async def backfill_case(self, case_id: str, *, workflow_terminated: bool) -> Any:
        del workflow_terminated
        self.writes.append(case_id)
        return None


class _Probe:
    def __init__(self, state: CaseExecutionState) -> None:
        self._state = state

    async def execution_state(self, case_id: str) -> tuple[CaseExecutionState, str | None]:
        del case_id
        return self._state, self._state.value


class _Horizon:
    """Commands that committed and were never applied, past the horizon."""

    def __init__(self, commands: list[dict[str, Any]]) -> None:
        self.commands = commands
        self.asked: list[datetime] = []

    async def list_unapplied_commands_past_horizon(
        self, *, older_than: datetime, limit: int
    ) -> list[dict[str, Any]]:
        self.asked.append(older_than)
        return self.commands[:limit]


def _stale_command(command_id: str = "cmd-stale-1") -> dict[str, Any]:
    return {
        "_id": command_id,
        "topic": "return-case.command.signal",
        "aggregateId": CASE_ID,
        "payload": {"caseId": CASE_ID, "review_id": "rev-1"},
        "status": "RETRY",
        "attemptCount": 9,
        "createdAt": NOW - timedelta(days=2),
    }


# --------------------------------------------------------------------------- #
# The wait is legitimate
# --------------------------------------------------------------------------- #


def test_awaiting_template_review_is_a_declared_legitimate_wait() -> None:
    assert CaseStatus.AWAITING_TEMPLATE_REVIEW in LEGITIMATE_WAIT_STATUSES


def test_the_new_status_projects_onto_the_frozen_vocabulary() -> None:
    """Waiting on an approval of the message *to* Support reads as waiting on
    Support -- and `ReturnCaseStatus` is frozen, so it maps rather than grows."""
    assert project_case_status(CaseStatus.AWAITING_TEMPLATE_REVIEW) is (
        ReturnCaseStatus.AWAITING_SUPPORT
    )
    assert project_case_status("AWAITING_TEMPLATE_REVIEW") is ReturnCaseStatus.AWAITING_SUPPORT


@_async
async def test_the_time_based_sweep_never_relaunches_a_case_awaiting_review() -> None:
    """Elapsed time is not evidence about a case designed to wait."""
    launcher = _RecordingLauncher()
    sweep = ReturnCaseWorkflowRecovery(
        launcher=launcher,
        repository=_PendingCases([_case()]),
    )

    assert await sweep.recover_once() == 0
    assert launcher.calls == []


@_async
async def test_the_time_based_sweep_still_relaunches_an_ordinary_stalled_case() -> None:
    """The refusal is scoped to the waits, not a blanket stand-down."""
    launcher = _RecordingLauncher()
    sweep = ReturnCaseWorkflowRecovery(
        launcher=launcher,
        repository=_PendingCases([_case(status=CaseStatus.GATHERING_INFO)]),
    )

    assert await sweep.recover_once() == 1
    assert launcher.calls == [CASE_ID]


@_async
async def test_an_unreadable_status_is_not_treated_as_a_wait() -> None:
    """The gate is a refusal to act, and one that fired on a value nobody
    recognised would stop recovering a whole class of cases -- the opposite
    failure, and a much quieter one."""
    case = _case()
    case["status"] = "SOME_STATUS_NOBODY_SHIPPED"
    launcher = _RecordingLauncher()
    sweep = ReturnCaseWorkflowRecovery(launcher=launcher, repository=_PendingCases([case]))

    assert await sweep.recover_once() == 1
    assert launcher.calls == [CASE_ID]


@_async
async def test_a_confirmed_absent_execution_still_recovers_a_reviewing_case() -> None:
    """The probe-based path is untouched. A vanished execution is real evidence
    whatever the case was waiting for, and a case waiting on a reviewer with no
    execution to resume into is exactly as broken as any other orphan."""
    launcher = _RecordingLauncher()
    service = ReturnCaseRecoveryService(
        launcher=launcher,
        repository=_Store({CASE_ID: _case()}),
        probe=_Probe(CaseExecutionState.ABSENT),
    )

    outcome = await service.reconcile_case(CASE_ID)

    assert outcome.action is RecoveryAction.RELAUNCHED
    assert outcome.assessment is not None
    assert outcome.assessment.divergence is CaseDivergence.RECOVERY_REQUIRED
    assert launcher.calls == [CASE_ID]


# --------------------------------------------------------------------------- #
# The command horizon
# --------------------------------------------------------------------------- #


@_async
async def test_a_command_unapplied_past_the_horizon_surfaces_instead_of_relaunching() -> None:
    """Past the horizon the cause is undiagnosed, and a relaunch would replace
    one unexplained state with another while destroying the evidence."""
    launcher = _RecordingLauncher()
    horizon = _Horizon([_stale_command()])
    service = ReturnCaseRecoveryService(
        launcher=launcher,
        repository=_Store({CASE_ID: _case()}),
        probe=_Probe(CaseExecutionState.CLOSED),
        command_horizon=horizon,
        command_horizon_seconds=3_600.0,
    )

    outcome = await service.reconcile_case(CASE_ID)

    assert outcome.action is RecoveryAction.OPERATIONS_REQUIRED
    assert outcome.stale_command_ids == ("cmd-stale-1",)
    assert launcher.calls == []
    assert horizon.asked


@_async
async def test_a_command_belonging_to_another_case_does_not_hold_this_one_back() -> None:
    other = _stale_command("cmd-other")
    other["aggregateId"] = "case-somebody-else"
    other["payload"] = {"caseId": "case-somebody-else"}
    launcher = _RecordingLauncher()
    service = ReturnCaseRecoveryService(
        launcher=launcher,
        repository=_Store({CASE_ID: _case()}),
        probe=_Probe(CaseExecutionState.CLOSED),
        command_horizon=_Horizon([other]),
    )

    outcome = await service.reconcile_case(CASE_ID)

    assert outcome.action is RecoveryAction.RELAUNCHED
    assert outcome.stale_command_ids == ()
    assert launcher.calls == [CASE_ID]


@_async
async def test_no_stale_commands_means_the_ordinary_relaunch() -> None:
    launcher = _RecordingLauncher()
    service = ReturnCaseRecoveryService(
        launcher=launcher,
        repository=_Store({CASE_ID: _case()}),
        probe=_Probe(CaseExecutionState.CLOSED),
        command_horizon=_Horizon([]),
    )

    outcome = await service.reconcile_case(CASE_ID)

    assert outcome.action is RecoveryAction.RELAUNCHED
    assert launcher.calls == [CASE_ID]


@_async
async def test_without_a_horizon_port_the_question_is_simply_not_asked() -> None:
    """Not a silent pass: a deployment with no port has no way to ask, and the
    difference is visible in the outcome rather than hidden behind a default."""
    launcher = _RecordingLauncher()
    service = ReturnCaseRecoveryService(
        launcher=launcher,
        repository=_Store({CASE_ID: _case()}),
        probe=_Probe(CaseExecutionState.CLOSED),
    )

    outcome = await service.reconcile_case(CASE_ID)

    assert outcome.action is RecoveryAction.RELAUNCHED
    assert outcome.stale_command_ids == ()


@_async
async def test_the_horizon_check_never_fires_on_a_healthy_or_terminal_case() -> None:
    """It sits on the `RECOVERY_REQUIRED` branch only. A live execution is
    deliverable and a terminal case is refused; neither is an operations
    question about an undelivered command."""
    horizon = _Horizon([_stale_command()])
    for state, expected in (
        (CaseExecutionState.RUNNING, RecoveryAction.ALREADY_RUNNING),
        (CaseExecutionState.UNKNOWN, RecoveryAction.DEFERRED_UNKNOWN),
    ):
        service = ReturnCaseRecoveryService(
            launcher=_RecordingLauncher(),
            repository=_Store({CASE_ID: _case()}),
            probe=_Probe(state),
            command_horizon=horizon,
        )
        assert (await service.reconcile_case(CASE_ID)).action is expected

    terminal = ReturnCaseRecoveryService(
        launcher=_RecordingLauncher(),
        repository=_Store({CASE_ID: _case(status=CaseStatus.CLOSED)}),
        probe=_Probe(CaseExecutionState.CLOSED),
        command_horizon=horizon,
    )
    assert (await terminal.reconcile_case(CASE_ID)).action is RecoveryAction.REFUSED_TERMINAL


def test_a_non_positive_horizon_is_refused_at_construction() -> None:
    with pytest.raises(ValueError, match="command_horizon_seconds"):
        ReturnCaseRecoveryService(
            launcher=_RecordingLauncher(),
            repository=_Store({}),
            probe=_Probe(CaseExecutionState.ABSENT),
            command_horizon_seconds=0,
        )
