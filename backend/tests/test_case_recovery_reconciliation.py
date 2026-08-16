"""Phase 10. A case that lost its execution is repaired; a finished one is not.

Six real cases read `AWAITING_SUPPORT` with no execution that will accept a
signal. Support could not answer them, the Copilot polled them forever, and
operations reported them as waiting. That is the defect. The repair is a
relaunch -- and the reason this file is long is that the repair is *wrong* for
several cases that look identical from the outbox:

```text
execution unexpectedly unavailable + case expected to accept updates
  -> RECOVERY_REQUIRED          relaunch, resuming where the case was

case legitimately terminal + update incompatible with that state
  -> permanent rejection        the case stays terminal, the event is retained
```

**What is substituted, and only this.** Two edges, each replaced by a double
that reproduces the property the code depends on rather than a convenience:

* `FakeReconciliationOutbox` reproduces the compare-and-set on
  `reconciliationState` that the Mongo implementation performs. A double that
  let a resolved command be resolved twice would make every idempotence
  assertion here vacuous.
* `FakeCaseStore.backfill_case` calls the **real** `plan_case_backfill`, so the
  "never park a terminal case" rule is the shipped one rather than a restatement
  of it.

The launcher is a recorder rather than a Temporal double on purpose: the
duplicate-execution guard under test is the *probe before the start*, so a test
that let the launcher decide would be testing the wrong half.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from return_platform.operations.case_projection.backfill import plan_case_backfill
from return_platform.operations.case_projection.contract import SettlementProjection
from return_platform.operations.case_projection.status_mapping import (
    UnmappedCaseStatusError,
    project_case_status,
)
from return_platform.operations.case_projection.vocabulary import (
    TERMINAL_RETURN_CASE_STATUSES,
    ReturnCaseStatus,
    SettlementStatus,
)
from return_platform.operations.models import CaseStatus
from return_platform.operations.support_events import SUPPORT_EVENT_AGGREGATE_TYPE
from return_platform.workflows.case_divergence import (
    CaseDivergence,
    CaseExecutionState,
    DivergenceReason,
    LateEventDisposition,
    classify_case_divergence,
)
from return_platform.workflows.return_case_launcher import (
    CaseWorkflowResume,
    StartedCaseWorkflow,
)
from return_platform.workflows.return_case_recovery import (
    PERMANENTLY_REJECTED,
    RECONCILED,
    RecoveryAction,
    ReturnCaseRecoveryService,
)

#: The service is async throughout, but roughly a third of this file exercises
#: `classify_case_divergence`, which is a pure function and is called
#: synchronously. `asyncio_mode = "strict"`, so the mark goes on the coroutines
#: rather than on the module -- a module-level mark would warn on every one of
#: the synchronous tests below.
_async = pytest.mark.asyncio

CASE_ID = "case-orphan-1"
WORKFLOW_ID = f"return-case-{CASE_ID}"
CREATED_AT = datetime(2026, 8, 13, 10, 6, 14, tzinfo=UTC)

#: The persisted status each projected terminal member is reached through, and
#: the settlement that splits `CLOSED`. Written as data so the parametrisation
#: below is over `TERMINAL_RETURN_CASE_STATUSES` itself -- if the frozen set ever
#: gains a member, this mapping fails to cover it and the test says so, rather
#: than the new member quietly going unexercised.
_TERMINAL_ROUTES: dict[ReturnCaseStatus, tuple[CaseStatus, SettlementProjection | None]] = {
    ReturnCaseStatus.COMPLETED: (
        CaseStatus.CLOSED,
        SettlementProjection(status=SettlementStatus.SETTLED),
    ),
    ReturnCaseStatus.COMPLETED_EXTERNAL_SETTLEMENT: (CaseStatus.CLOSED, None),
    ReturnCaseStatus.POLICY_REJECTED: (CaseStatus.POLICY_REJECTED, None),
    ReturnCaseStatus.CANCELLED: (CaseStatus.CANCELLED, None),
    ReturnCaseStatus.EXPIRED: (CaseStatus.CLOSED, None),
}


def test_every_frozen_terminal_status_is_covered_by_this_file() -> None:
    """The parametrisation is over the contract, not over a list someone typed.

    `EXPIRED` has no persisted counterpart -- `ReturnCaseWorkflow` writes
    `CLOSED` when a case runs out of time -- so it is routed through `CLOSED`
    here rather than being silently dropped from the sweep of terminal statuses.
    """
    assert set(_TERMINAL_ROUTES) == set(TERMINAL_RETURN_CASE_STATUSES)


# ---------------------------------------------------------------------------
# Documents and doubles
# ---------------------------------------------------------------------------


def _case(
    *,
    case_id: str = CASE_ID,
    status: CaseStatus = CaseStatus.AWAITING_SUPPORT,
    work_item_id: str | None = "wi-1",
    conversation_id: str | None = "disc-1",
    version: int = 4,
) -> dict[str, Any]:
    return {
        "caseId": case_id,
        "tenantId": "default",
        "principalId": "dev-operator",
        "status": status.value,
        "channelAConversationId": conversation_id,
        "channelBWorkItemId": work_item_id,
        "workflowId": f"return-case-{case_id}",
        "configurationReleaseId": "release-1",
        "version": version,
        "createdAt": CREATED_AT,
        "updatedAt": CREATED_AT,
    }


def _command(
    *,
    command_id: str = "cmd-1",
    case_id: str = CASE_ID,
    support_event_id: str = "evt-1",
) -> dict[str, Any]:
    """A Support signal that dead-lettered, exactly as `_dead_letter` leaves one."""
    return {
        "_id": command_id,
        "topic": "return-case.support-response.signal",
        "aggregateType": SUPPORT_EVENT_AGGREGATE_TYPE,
        "aggregateId": case_id,
        "idempotencyKey": f"support-response:{case_id}:{support_event_id}",
        "payload": {
            "caseId": case_id,
            "workflowId": f"return-case-{case_id}",
            "supportEventId": support_event_id,
            "notice": {"work_item_id": "wi-1", "records": [{"return_reference": "RMA-1"}]},
        },
        "status": "DEAD_LETTER",
        "reconciliationState": "REQUIRES_RECONCILIATION",
        "attemptCount": 3,
        "lastErrorCode": "TEMPORAL_SIGNAL_NOT_FOUND",
    }


class FakeProbe:
    """What the workflow host says, and how many times it was asked."""

    def __init__(self, states: dict[str, CaseExecutionState]) -> None:
        self.states = states
        self.calls: list[str] = []

    async def execution_state(self, case_id: str) -> tuple[CaseExecutionState, str | None]:
        self.calls.append(case_id)
        state = self.states.get(case_id, CaseExecutionState.ABSENT)
        detail = {
            CaseExecutionState.RUNNING: "RUNNING",
            CaseExecutionState.CLOSED: "TERMINATED",
            CaseExecutionState.ABSENT: "NOT_FOUND",
            CaseExecutionState.UNKNOWN: None,
        }[state]
        return state, detail


class FakeCaseStore:
    """The two case-store methods, with the shipped backfill rule inside.

    `backfill_case` delegates to the real `plan_case_backfill`, so "a terminal
    case is never parked" and "a second run writes nothing" are the production
    rules rather than this double's opinion of them.
    """

    def __init__(self, cases: dict[str, dict[str, Any]]) -> None:
        self.cases = cases
        self.writes: list[tuple[str, str]] = []

    async def get_case(self, case_id: str) -> dict[str, Any] | None:
        case = self.cases.get(case_id)
        return dict(case) if case is not None else None

    async def backfill_case(self, case_id: str, *, workflow_terminated: bool) -> Any:
        case = self.cases[case_id]
        plan = plan_case_backfill(case, workflow_terminated=workflow_terminated)
        if plan.status is not None:
            case["status"] = plan.status.value
            case["version"] = int(case.get("version", 0)) + 1
            self.writes.append((case_id, plan.status.value))
        return plan


class FakeLauncher:
    """Records what was asked of it. Never decides anything.

    `already_running` and `failure` are set by a test that wants the Temporal
    race or the Temporal outage; by default a start succeeds, because the guard
    under test is what stops us reaching here at all.
    """

    def __init__(self, *, already_running: bool = False, failure: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.already_running = already_running
        self.failure = failure

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
        self.calls.append({"case_id": case_id, "resume": resume})
        if self.failure is not None:
            raise self.failure
        return StartedCaseWorkflow(
            workflow_id=f"return-case-{case_id}",
            already_running=self.already_running,
        )


class FakeReconciliationOutbox:
    """The dead-letter queue, with the compare-and-set the Mongo version has.

    Both writes match only a command still at `REQUIRES_RECONCILIATION`. That is
    the whole of what makes a second sweep a no-op, so a double without it would
    let the idempotence test pass over a service that had none.
    """

    def __init__(self, commands: list[dict[str, Any]]) -> None:
        self.commands = commands

    async def list_commands_requiring_reconciliation(self, *, limit: int) -> list[dict[str, Any]]:
        pending = [
            command
            for command in self.commands
            if command.get("status") == "DEAD_LETTER"
            and command.get("reconciliationState") == "REQUIRES_RECONCILIATION"
        ]
        return pending[:limit]

    def _claim(self, command_id: str) -> dict[str, Any] | None:
        for command in self.commands:
            if (
                command["_id"] == command_id
                and command.get("status") == "DEAD_LETTER"
                and command.get("reconciliationState") == "REQUIRES_RECONCILIATION"
            ):
                return command
        return None

    async def requeue_command(self, command_id: str) -> bool:
        command = self._claim(command_id)
        if command is None:
            return False
        command["status"] = "PENDING"
        command["reconciliationState"] = RECONCILED
        return True

    async def reject_command_permanently(self, command_id: str, *, reason: str) -> bool:
        command = self._claim(command_id)
        if command is None:
            return False
        command["reconciliationState"] = PERMANENTLY_REJECTED
        command["reconciliationReason"] = reason
        return True


def _service(
    *,
    cases: dict[str, dict[str, Any]],
    states: dict[str, CaseExecutionState],
    commands: list[dict[str, Any]] | None = None,
    launcher: FakeLauncher | None = None,
) -> tuple[ReturnCaseRecoveryService, FakeCaseStore, FakeLauncher, FakeReconciliationOutbox]:
    store = FakeCaseStore(cases)
    started = launcher or FakeLauncher()
    outbox = FakeReconciliationOutbox(commands if commands is not None else [])
    service = ReturnCaseRecoveryService(
        launcher=started,
        repository=store,
        probe=FakeProbe(states),
        outbox=outbox,
    )
    return service, store, started, outbox


# ---------------------------------------------------------------------------
# Classification -- pure, no service
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("execution", "reason"),
    [
        (CaseExecutionState.CLOSED, DivergenceReason.EXECUTION_CLOSED_UNDER_ACTIVE_CASE),
        (CaseExecutionState.ABSENT, DivergenceReason.EXECUTION_MISSING_UNDER_ACTIVE_CASE),
    ],
)
def test_an_orphan_classifies_as_recovery_required(
    execution: CaseExecutionState, reason: DivergenceReason
) -> None:
    """An active case with no execution accepting updates is the orphan.

    Both readings of "gone" reach it. The six real cases are `ABSENT` today --
    their terminated executions have since aged out of Temporal's retention --
    and were `CLOSED` when the audit found them, so a classifier that only
    recognised one of the two would have stopped detecting them by now.
    """
    assessment = classify_case_divergence(_case(), execution=execution)

    assert assessment.divergence is CaseDivergence.RECOVERY_REQUIRED
    assert assessment.reason is reason
    assert assessment.is_recoverable is True
    assert assessment.late_event is LateEventDisposition.DRIVES_RECOVERY
    # Non-terminal in both vocabularies, so the Copilot keeps polling a case
    # that is about to resume.
    assert assessment.projected_status is ReturnCaseStatus.AWAITING_SUPPORT


@pytest.mark.parametrize("terminal", sorted(TERMINAL_RETURN_CASE_STATUSES))
def test_a_legitimately_terminal_case_rejects_a_late_event(
    terminal: ReturnCaseStatus,
) -> None:
    """Every terminal status refuses the update, and refuses it the same way.

    Terminality is read from the persisted status *before* the execution is
    considered, so this holds for all four execution states -- including the
    `RUNNING` that would otherwise look like a case still working.
    """
    persisted, settlement = _TERMINAL_ROUTES[terminal]
    for execution in CaseExecutionState:
        assessment = classify_case_divergence(
            _case(status=persisted), execution=execution, settlement=settlement
        )
        assert assessment.divergence is CaseDivergence.CASE_TERMINAL
        assert assessment.is_recoverable is False
        assert assessment.late_event is LateEventDisposition.PERMANENTLY_REJECTED
        assert assessment.persisted_status is persisted
        assert assessment.projected_status is project_case_status(persisted, settlement=settlement)


def test_a_terminal_case_with_an_open_execution_is_reported_not_recovered() -> None:
    """Divergence the other way round. Named, and never acted on.

    A finished case whose execution is somehow still open is a real problem and
    is not this direction's: restarting it would be resurrecting it, so the
    reading says exactly what it found and stops.
    """
    assessment = classify_case_divergence(
        _case(status=CaseStatus.CLOSED), execution=CaseExecutionState.RUNNING
    )

    assert assessment.divergence is CaseDivergence.CASE_TERMINAL
    assert assessment.reason is DivergenceReason.TERMINAL_CASE_WITH_OPEN_EXECUTION
    assert assessment.is_recoverable is False


def test_an_unreachable_workflow_host_is_indeterminate_not_orphaned() -> None:
    """One Temporal outage must not classify the whole estate as orphaned."""
    assessment = classify_case_divergence(_case(), execution=CaseExecutionState.UNKNOWN)

    assert assessment.divergence is CaseDivergence.INDETERMINATE
    assert assessment.is_recoverable is False
    assert assessment.late_event is LateEventDisposition.INDETERMINATE


def test_an_unreadable_status_is_refused_rather_than_defaulted() -> None:
    """A status nobody recognises must not be read as "not terminal".

    That default has one consequence and it is the worst one available:
    restarting a case that had finished.
    """
    with pytest.raises(UnmappedCaseStatusError):
        classify_case_divergence(
            {"caseId": CASE_ID, "status": "SORT_OF_DONE"}, execution=CaseExecutionState.CLOSED
        )


# ---------------------------------------------------------------------------
# The service -- classification with consequences
# ---------------------------------------------------------------------------


@_async
async def test_a_running_execution_is_never_relaunched() -> None:
    """The duplicate-execution guard, asserted where it actually lives.

    The probe comes first and the launcher is never reached, so no second
    execution can be created even by a caller who asked for one. Relying on
    `WorkflowAlreadyStartedError` instead would make this pass while still
    sending a start request for every healthy case an operator clicked on.
    """
    service, store, launcher, _ = _service(
        cases={CASE_ID: _case()},
        states={CASE_ID: CaseExecutionState.RUNNING},
    )

    outcome = await service.reconcile_case(CASE_ID)

    assert outcome.action is RecoveryAction.ALREADY_RUNNING
    assert launcher.calls == []
    assert store.writes == []
    assert outcome.changed_anything is False


@_async
async def test_a_dead_lettered_support_event_drives_recovery() -> None:
    """The reply is re-driven, not re-requested, and the case is restarted once.

    This is the whole of finding #10 in one assertion: Support answered, the
    answer was committed to Mongo, the workflow it was addressed to had gone,
    and the command came to rest at `REQUIRES_RECONCILIATION`. Recovery starts a
    new execution and puts the *existing* command back on the queue -- nobody is
    asked to send the RMA again, and the event document is untouched.
    """
    command = _command()
    service, store, launcher, _outbox = _service(
        cases={CASE_ID: _case()},
        states={CASE_ID: CaseExecutionState.CLOSED},
        commands=[command],
    )

    (outcome,) = await service.reconcile_once()

    assert outcome.action is RecoveryAction.RELAUNCHED
    assert outcome.workflow_id == WORKFLOW_ID
    assert outcome.requeued_commands == 1
    assert [call["case_id"] for call in launcher.calls] == [CASE_ID]
    # Back on the delivery queue, with the dead-letter marker resolved so a
    # second sweep does not see it as owed.
    assert command["status"] == "PENDING"
    assert command["reconciliationState"] == RECONCILED
    # The attempt history is kept: it is the evidence that this command
    # dead-lettered at all.
    assert command["attemptCount"] == 3
    # A successful relaunch does not park the case. Parking a repaired case
    # would show `awaiting: [RECOVERY]` for a return that is running again.
    assert store.writes == []


@_async
async def test_recovery_resumes_the_case_rather_than_restarting_it() -> None:
    """The work item travels, so Support is not asked a second time.

    `ReturnCaseWorkflow.run` branches on `resumed_work_item_id`: with one it goes
    straight to the Support drain, without one it requests a bay, evaluates the
    policy and calls `_open_support`. A relaunch that passed an empty input would
    therefore open a *second* work item for a return Support already holds, and
    re-decide a policy that was already decided.
    """
    service, _, launcher, _ = _service(
        cases={CASE_ID: _case(work_item_id="wi-1")},
        states={CASE_ID: CaseExecutionState.CLOSED},
    )

    await service.reconcile_case(CASE_ID)

    resume = launcher.calls[0]["resume"]
    assert isinstance(resume, CaseWorkflowResume)
    assert resume.work_item_id == "wi-1"
    assert resume.status == CaseStatus.AWAITING_SUPPORT.value
    # The lifetime cap is measured from the case, not from the repair. Passing
    # nothing would hand every recovered case a fresh full lifetime, and a case
    # that orphaned repeatedly would never reach the cap the workflow has one
    # for.
    assert resume.lifetime_start_iso == CREATED_AT.isoformat()


@_async
async def test_a_parked_case_is_not_resumed_at_its_own_repair() -> None:
    """`RECOVERY_REQUIRED` is not a place the workflow was.

    A case parked by an earlier failed pass carries the repair as its status.
    Resuming *at* it would hand the new execution a state that means "recovery
    is owed", so the position is read from the work item instead -- which is the
    invariant `_open_support` establishes in two adjacent statements.
    """
    service, _, launcher, _ = _service(
        cases={CASE_ID: _case(status=CaseStatus.RECOVERY_REQUIRED, work_item_id="wi-1")},
        states={CASE_ID: CaseExecutionState.ABSENT},
    )

    await service.reconcile_case(CASE_ID)

    assert launcher.calls[0]["resume"].status == CaseStatus.AWAITING_SUPPORT.value


@_async
async def test_a_parked_case_that_never_reached_support_runs_its_own_path() -> None:
    """No work item means the case never got to Support, so nothing is skipped."""
    service, _, launcher, _ = _service(
        cases={CASE_ID: _case(status=CaseStatus.RECOVERY_REQUIRED, work_item_id=None)},
        states={CASE_ID: CaseExecutionState.ABSENT},
    )

    await service.reconcile_case(CASE_ID)

    resume = launcher.calls[0]["resume"]
    assert resume.status is None
    assert resume.work_item_id is None


@_async
@pytest.mark.parametrize("terminal", sorted(TERMINAL_RETURN_CASE_STATUSES))
async def test_a_late_event_never_resurrects_a_terminal_case(
    terminal: ReturnCaseStatus,
) -> None:
    """The case stays terminal, the launcher is never called, the event is kept.

    Three assertions because the plan forbids three different failures: the
    resurrection itself, the duplicate execution that would carry it, and the
    quiet deletion of a reply Support genuinely sent. Rejected is not deleted.
    """
    persisted, _ = _TERMINAL_ROUTES[terminal]
    command = _command()
    service, store, launcher, _outbox = _service(
        cases={CASE_ID: _case(status=persisted)},
        states={CASE_ID: CaseExecutionState.CLOSED},
        commands=[command],
    )

    (outcome,) = await service.reconcile_once()

    assert outcome.action is RecoveryAction.REFUSED_TERMINAL
    assert outcome.rejected_commands == 1
    assert launcher.calls == []
    # The case is exactly as it was found.
    assert store.cases[CASE_ID]["status"] == persisted.value
    assert store.cases[CASE_ID]["version"] == 4
    assert store.writes == []
    # The command is retained and marked, not removed. `status` stays
    # `DEAD_LETTER` so the outbox still does nothing with it; only the field
    # that says whether anything is *owed* moves.
    assert command["status"] == "DEAD_LETTER"
    assert command["reconciliationState"] == PERMANENTLY_REJECTED
    assert command["payload"]["supportEventId"] == "evt-1"


@_async
async def test_an_indeterminate_execution_changes_nothing() -> None:
    """A pass that cannot see the workflow host writes nothing at all.

    Not the case, and not the dead letter either: deciding whether a Support
    event is owed a redelivery or a rejection requires knowing whether the
    execution exists, and this pass does not.
    """
    command = _command()
    service, store, launcher, _ = _service(
        cases={CASE_ID: _case()},
        states={CASE_ID: CaseExecutionState.UNKNOWN},
        commands=[command],
    )

    (outcome,) = await service.reconcile_once()

    assert outcome.action is RecoveryAction.DEFERRED_UNKNOWN
    assert outcome.changed_anything is False
    assert launcher.calls == []
    assert store.writes == []
    assert command["reconciliationState"] == "REQUIRES_RECONCILIATION"


@_async
async def test_a_failed_relaunch_parks_the_case_and_leaves_it_for_the_next_pass() -> None:
    """The one path that writes `RECOVERY_REQUIRED`, and it writes it afterwards.

    Parking before the attempt would show `awaiting: [RECOVERY]` on a case that
    was about to be repaired. Parking after a genuine failure is what makes the
    stuck case visible to an operator instead of silently retried forever.
    """
    service, store, _launcher, outbox = _service(
        cases={CASE_ID: _case()},
        states={CASE_ID: CaseExecutionState.CLOSED},
        commands=[_command()],
        launcher=FakeLauncher(failure=RuntimeError("temporal is unreachable")),
    )

    outcome = await service.reconcile_case(CASE_ID)

    assert outcome.action is RecoveryAction.RELAUNCH_FAILED
    assert store.writes == [(CASE_ID, CaseStatus.RECOVERY_REQUIRED.value)]
    assert store.cases[CASE_ID]["status"] == CaseStatus.RECOVERY_REQUIRED.value
    # The command is left owed, so the next pass tries again rather than the
    # reply being marked resolved against a workflow that never started.
    assert outbox.commands[0]["reconciliationState"] == "REQUIRES_RECONCILIATION"


@_async
async def test_a_lost_race_to_another_process_is_reported_not_duplicated() -> None:
    """Two reconcilers, one execution. Temporal's uniqueness settles it.

    The probe and the start are not atomic, so another process can relaunch this
    case in between. Nothing is duplicated -- that is what
    `WorkflowAlreadyStartedError` means -- and the honest report is that this
    pass did not create the execution.
    """
    command = _command()
    service, _, launcher, _ = _service(
        cases={CASE_ID: _case()},
        states={CASE_ID: CaseExecutionState.CLOSED},
        commands=[command],
        launcher=FakeLauncher(already_running=True),
    )

    outcome = await service.reconcile_case(CASE_ID)

    assert outcome.action is RecoveryAction.ALREADY_RUNNING
    assert len(launcher.calls) == 1
    # The reply still gets re-driven: an execution exists, whoever started it.
    assert command["reconciliationState"] == RECONCILED


@_async
async def test_reconciliation_is_idempotent() -> None:
    """Sweeping twice changes nothing the second time.

    Two independent reasons, and both are asserted because either one alone
    would be a coincidence: the dead letter has left `REQUIRES_RECONCILIATION`
    so there is no work list, and the execution the first pass started is now
    live so the guard would refuse anyway.
    """
    command = _command()
    store = FakeCaseStore({CASE_ID: _case()})
    launcher = FakeLauncher()
    probe = FakeProbe({CASE_ID: CaseExecutionState.CLOSED})
    outbox = FakeReconciliationOutbox([command])
    service = ReturnCaseRecoveryService(
        launcher=launcher, repository=store, probe=probe, outbox=outbox
    )

    first = await service.reconcile_once()
    assert [outcome.action for outcome in first] == [RecoveryAction.RELAUNCHED]

    # The relaunch succeeded, so the execution is live on the next pass -- which
    # is the state a real second sweep would find.
    probe.states[CASE_ID] = CaseExecutionState.RUNNING
    second = await service.reconcile_once()

    assert second == ()
    assert len(launcher.calls) == 1
    assert store.writes == []
    assert command["reconciliationState"] == RECONCILED

    # And directly, past the empty work list: the case itself is now refused.
    repeat = await service.reconcile_case(CASE_ID)
    assert repeat.action is RecoveryAction.ALREADY_RUNNING
    assert repeat.changed_anything is False
    assert len(launcher.calls) == 1


@_async
async def test_another_aggregates_dead_letter_is_left_alone() -> None:
    """A carrier booking that dead-lettered is not a case to restart.

    The sweep is scoped to `RETURN_CASE`, because reconciling somebody else's
    aggregate here would be this module deciding about a domain it does not own.
    """
    foreign = _command(command_id="cmd-carrier")
    foreign["aggregateType"] = "CARRIER_BOOKING"
    service, _, launcher, _ = _service(
        cases={CASE_ID: _case()},
        states={CASE_ID: CaseExecutionState.CLOSED},
        commands=[foreign],
    )

    assert await service.reconcile_once() == ()
    assert launcher.calls == []
    assert foreign["reconciliationState"] == "REQUIRES_RECONCILIATION"


@_async
async def test_several_replies_to_one_case_are_classified_once() -> None:
    """Three undelivered replies, one probe, one relaunch, three requeues.

    Grouping is not an optimisation here. Classifying per command would probe --
    and, between the probe and the start, potentially relaunch -- the same case
    three times.
    """
    commands = [
        _command(command_id=f"cmd-{index}", support_event_id=f"evt-{index}")
        for index in range(1, 4)
    ]
    store = FakeCaseStore({CASE_ID: _case()})
    launcher = FakeLauncher()
    probe = FakeProbe({CASE_ID: CaseExecutionState.CLOSED})
    service = ReturnCaseRecoveryService(
        launcher=launcher,
        repository=store,
        probe=probe,
        outbox=FakeReconciliationOutbox(commands),
    )

    (outcome,) = await service.reconcile_once()

    assert probe.calls == [CASE_ID]
    assert len(launcher.calls) == 1
    assert outcome.requeued_commands == 3


@_async
async def test_a_case_that_no_longer_exists_is_reported_not_created() -> None:
    service, _, launcher, _ = _service(cases={}, states={})

    outcome = await service.reconcile_case("case-gone")

    assert outcome.action is RecoveryAction.CASE_NOT_FOUND
    assert outcome.assessment is None
    assert launcher.calls == []


@_async
async def test_a_case_with_no_channel_a_conversation_is_never_given_a_workflow() -> None:
    """The same refusal the start-never-landed sweep makes, for the same reason.

    `ReturnCaseWorkflow` owns a *confirmation*. Handing one to a case that
    reached the collection some other way -- a fixture, an import -- would open a
    Support conversation nobody asked for.
    """
    service, store, launcher, _ = _service(
        cases={CASE_ID: _case(conversation_id=None)},
        states={CASE_ID: CaseExecutionState.ABSENT},
    )

    outcome = await service.reconcile_case(CASE_ID)

    assert outcome.action is RecoveryAction.DEFERRED_UNKNOWN
    assert launcher.calls == []
    assert store.writes == []


@_async
async def test_a_service_with_no_outbox_still_assesses_and_recovers() -> None:
    """A deployment with no dead letters must not fail to answer.

    The outbox is optional on the service for exactly this: an operator asking
    "why is this case stuck" needs a probe and a case, not a delivery queue.
    """
    store = FakeCaseStore({CASE_ID: _case()})
    launcher = FakeLauncher()
    service = ReturnCaseRecoveryService(
        launcher=launcher,
        repository=store,
        probe=FakeProbe({CASE_ID: CaseExecutionState.CLOSED}),
    )

    assessment = await service.assess(CASE_ID)
    assert assessment is not None
    assert assessment.is_recoverable is True

    outcome = await service.reconcile_case(CASE_ID)
    assert outcome.action is RecoveryAction.RELAUNCHED
    assert outcome.requeued_commands == 0
    assert await service.reconcile_once() == ()


@_async
async def test_assessing_a_case_never_writes_anything() -> None:
    """Looking at a stuck case must not be the thing that restarts it."""
    service, store, launcher, _ = _service(
        cases={CASE_ID: _case()},
        states={CASE_ID: CaseExecutionState.CLOSED},
        commands=[_command()],
    )

    assessment = await service.assess(CASE_ID)

    assert assessment is not None
    assert assessment.divergence is CaseDivergence.RECOVERY_REQUIRED
    assert launcher.calls == []
    assert store.writes == []
    assert await service.assess("case-gone") is None
