"""The durable retry path for a case whose workflow start did not land.

Confirmation commits the case to Mongo and then starts its workflow. Those are
two operations against two systems, so there is a window -- a Temporal outage,
a killed worker, a network partition -- in which the case is durable and the
execution that owns it is not. The confirmation *fails* in that window and the
associate is told so, but a failed turn is not a repair: nothing about an
associate walking away makes the case reachable again, and a case with no
workflow is invisible to Support, to Bay and to every later agent turn.

This sweep is the repair. It is deliberately not a second reliability
framework:

* **No new collection and no new record.** The case document already carries
  `workflowId` -- a field created for exactly this link, with a unique partial
  index behind it -- and it is null on a case whose workflow never started. The
  queue is therefore the cases themselves, and it cannot drift out of step with
  them the way a parallel outbox row can.
* **No new idempotency rule.** Recovery calls the same
  `TemporalCaseWorkflowLauncher` the confirmation node calls, so a case whose
  workflow is in fact running (the start succeeded and only the link write
  failed) converges through `WorkflowAlreadyStartedError` and simply has its
  link rewritten.
* **No delivery guarantee of its own.** A failed pass logs and leaves the case
  exactly as it found it, so the next pass retries. There is no lease, no
  attempt counter and no dead-letter state, because the terminal condition is
  observable from the case itself.

`grace_seconds` is what keeps this from racing the confirmation that is still
in flight: a case created a moment ago is being started right now by the node
that created it, and starting it here as well would be harmless but noisy.

The `integration_outbox` was considered and rejected for this. It is scoped to
*external* dependency commands: its topics are enumerated in
`IntegrationConfiguration`, its dispatchers are HTTP adapters, its terminal
failure state is `BLOCKED_EXTERNAL_DEPENDENCY`, and a topic with no registered
adapter is marked non-retryable and abandoned -- which is precisely the outcome
a case must never reach.

---

**The second sweep in this module is Phase 10's, and it is a different repair.**
`ReturnCaseWorkflowRecovery` above answers "the workflow was never started".
`ReturnCaseRecoveryService` below answers "the workflow existed and is gone",
which is a strictly harder question because the answer is sometimes *do
nothing*:

```text
execution unexpectedly unavailable + case expected to accept updates
  -> RECOVERY_REQUIRED           relaunch, resuming where the case was

case legitimately terminal + update incompatible with that state
  -> permanent rejection         the case stays terminal, the event is retained
```

The classification itself is `workflows/case_divergence.py`, pure and with no
Temporal in it. This module is the part that talks to the workflow host, the
case store and the outbox, in that order, and it holds three guarantees:

* **No duplicate execution, ever.** Every relaunch is preceded by a `describe`
  of the derived id and refused unless the execution is closed or absent. A
  start against a live id would also be refused by Temporal, but "refused by the
  server" is not the property to rely on: it is the property that stops a second
  execution existing, not the one that stops us asking for it, and an operator
  clicking Relaunch on a healthy case deserves to be told so rather than to have
  the request silently adopted.
* **No resurrection.** Terminality is decided on the persisted status before the
  execution is even considered, so no reading of Temporal -- including the
  `UNKNOWN` an outage produces -- can make a finished case look recoverable.
* **No lost Support reply.** A permanently undeliverable Support event is
  durable in Mongo already. Recovery requeues the *command*, so the event is
  re-driven at the restarted workflow rather than Support being asked to send it
  again. A command against a terminal case is stamped as permanently rejected
  and kept: rejected is not deleted, and the audit trail is the point.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Final, Protocol, cast

from pymongo.asynchronous.database import AsyncDatabase
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.service import RPCError, RPCStatusCode

from return_platform.configuration.return_configuration import ReturnCaseTimingConfiguration
from return_platform.operations.integrations.outbox import (
    DEAD_LETTER_STATUS,
    INTEGRATION_OUTBOX_COLLECTION,
    REQUIRES_RECONCILIATION,
)
from return_platform.operations.models import CaseStatus
from return_platform.operations.support_events import SUPPORT_EVENT_AGGREGATE_TYPE
from return_platform.workflows.case_divergence import (
    CaseDivergence,
    CaseDivergenceAssessment,
    CaseExecutionState,
    classify_case_divergence,
    read_persisted_status,
)
from return_platform.workflows.return_case_launcher import (
    CaseWorkflowResume,
    StartedCaseWorkflow,
    TemporalCaseWorkflowLauncher,
)
from return_platform.workflows.return_case_workflow import return_case_workflow_id

__all__ = [
    "PERMANENTLY_REJECTED",
    "RECONCILED",
    "CaseExecutionProbePort",
    "CaseRecoveryOutcome",
    "MongoReconciliationOutbox",
    "ReconciliationOutboxPort",
    "RecoverableCaseRepositoryPort",
    "RecoveryAction",
    "RecoveryCaseRepositoryPort",
    "ReturnCaseRecoveryService",
    "ReturnCaseWorkflowRecovery",
    "TemporalCaseExecutionProbe",
    "build_case_recovery_service",
]

logger = logging.getLogger("return_platform.workflows.return_case_recovery")

#: The reconciliation state a command reaches once its case has been repaired
#: and the command has been put back on the queue. It replaces
#: `REQUIRES_RECONCILIATION`, which is what makes a second sweep find nothing.
RECONCILED: Final = "RECONCILED"

#: The reconciliation state of a command whose case is legitimately terminal.
#: The command stays `DEAD_LETTER` and the Support event stays in
#: `case_support_events`: this is a decision recorded, not a record removed.
PERMANENTLY_REJECTED: Final = "PERMANENTLY_REJECTED"


class RecoverableCaseRepositoryPort(Protocol):
    async def list_cases_without_workflow(
        self, *, created_before: datetime, limit: int
    ) -> list[dict[str, Any]]: ...


class CaseWorkflowLauncherPort(Protocol):
    async def ensure_case_workflow(
        self,
        *,
        case_id: str,
        tenant_id: str,
        principal_id: str,
        conversation_id: str,
        configuration_release_id: str,
        resume: CaseWorkflowResume | None = None,
    ) -> StartedCaseWorkflow: ...


class ReturnCaseWorkflowRecovery:
    """Finds confirmed cases with no workflow and starts the one they are owed."""

    def __init__(
        self,
        *,
        launcher: CaseWorkflowLauncherPort,
        repository: RecoverableCaseRepositoryPort,
        grace_seconds: float = 120.0,
        batch_size: int = 100,
        interval_seconds: float = 30.0,
    ) -> None:
        if grace_seconds < 0:
            raise ValueError("grace_seconds must not be negative")
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        self._launcher = launcher
        self._repository = repository
        self._grace_seconds = grace_seconds
        self._batch_size = batch_size
        self._interval_seconds = interval_seconds

    async def recover_once(self) -> int:
        """One pass. Returns how many cases now have a workflow because of it.

        One failing case never stops the pass: the cases in a batch are
        independent returns belonging to different associates, and letting the
        first Temporal error abandon the rest would turn one stuck case into a
        stuck queue.
        """
        cutoff = datetime.now(UTC) - timedelta(seconds=self._grace_seconds)
        pending = await self._repository.list_cases_without_workflow(
            created_before=cutoff, limit=self._batch_size
        )
        recovered = 0
        for case in pending:
            case_id = str(case.get("caseId") or "")
            conversation_id = case.get("channelAConversationId")
            if not case_id or not isinstance(conversation_id, str) or not conversation_id:
                # Not a confirmed Channel A case. `ReturnCaseWorkflow` is the
                # owner of a *confirmation*; giving one to a case that reached
                # the collection some other way would start a support
                # conversation nobody asked for.
                continue
            try:
                started = await self._launcher.ensure_case_workflow(
                    case_id=case_id,
                    tenant_id=str(case.get("tenantId") or ""),
                    principal_id=str(case.get("principalId") or ""),
                    conversation_id=conversation_id,
                    configuration_release_id=str(case.get("configurationReleaseId") or ""),
                )
            except Exception:  # noqa: BLE001 - the next pass retries this case
                logger.warning(
                    "case_workflow_recovery_failed",
                    extra={"case_id": case_id},
                    exc_info=True,
                )
                continue
            recovered += 1
            logger.info(
                "case_workflow_recovered",
                extra={
                    "case_id": case_id,
                    "workflow_id": started.workflow_id,
                    "already_running": started.already_running,
                },
            )
        return recovered

    async def run_forever(self) -> None:
        while True:
            try:
                await self.recover_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a sweep that dies stops repairing
                logger.error("case_workflow_recovery_pass_failed", exc_info=True)
            await asyncio.sleep(self._interval_seconds)


# ---------------------------------------------------------------------------
# Phase 10 -- divergence between a case and the execution that owns it
# ---------------------------------------------------------------------------


class CaseExecutionProbePort(Protocol):
    """Ask the workflow host what state a case's execution is in.

    A port rather than a Temporal client, because every rule that consumes the
    answer must be testable without one -- and because the answer is four
    values, not a Temporal type.
    """

    async def execution_state(self, case_id: str) -> tuple[CaseExecutionState, str | None]: ...


class RecoveryCaseRepositoryPort(Protocol):
    """The two case-store methods reconciliation needs, and no others.

    `backfill_case` rather than a status write of this module's own.
    `plan_case_backfill` already owns the "active case, terminated execution ->
    RECOVERY_REQUIRED" write, already refuses to touch a terminal case, and is
    already idempotent by inspection of the document. A second writer with the
    same intent is how the two come to disagree about `POLICY_REJECTED`.
    """

    async def get_case(self, case_id: str) -> dict[str, Any] | None: ...

    async def backfill_case(self, case_id: str, *, workflow_terminated: bool) -> Any: ...


class ReconciliationOutboxPort(Protocol):
    """The dead-letter queue Phase 3B fills, read and resolved.

    Three methods, and both writes are compare-and-set on `reconciliationState`.
    That is what makes the sweep idempotent and safe to run in two processes at
    once without a lease: the second attempt matches nothing and reports it.
    """

    async def list_commands_requiring_reconciliation(
        self, *, limit: int
    ) -> list[dict[str, Any]]: ...

    async def requeue_command(self, command_id: str) -> bool: ...

    async def reject_command_permanently(self, command_id: str, *, reason: str) -> bool: ...


class TemporalCaseExecutionProbe:
    """`describe` on the derived execution id, reduced to the four states.

    **`NOT_FOUND` is `ABSENT`, and nothing else is.** Every other RPC failure --
    an unreachable cluster, a deadline, a permission problem -- is `UNKNOWN`,
    because "we could not ask" and "there is nothing there" lead to opposite
    decisions and collapsing them would let one Temporal outage classify the
    whole estate as orphaned. The same split `temporal_signal.classify_rpc_error`
    draws, kept deliberately narrower here: a dispatcher stopping delivery on a
    permission error is a bounded mistake, and a reconciler restarting cases on
    one is not.

    `CONTINUED_AS_NEW` cannot reach the caller as such: `describe` on a workflow
    id answers about its latest run, and the run that continued as new is not
    the latest one. It is mapped to `CLOSED` anyway, for the reader who checks.
    """

    #: The one status that still accepts a signal.
    _RUNNING: Final = WorkflowExecutionStatus.RUNNING

    def __init__(self, *, client: Client) -> None:
        self._client = client

    async def execution_state(self, case_id: str) -> tuple[CaseExecutionState, str | None]:
        handle = self._client.get_workflow_handle(return_case_workflow_id(case_id))
        try:
            description = await handle.describe()
        except RPCError as error:
            if error.status is RPCStatusCode.NOT_FOUND:
                return CaseExecutionState.ABSENT, error.status.name
            logger.warning(
                "case_execution_probe_failed",
                extra={"case_id": case_id, "status": error.status.name},
            )
            return CaseExecutionState.UNKNOWN, error.status.name
        except Exception:  # noqa: BLE001 - a probe that raises stops the sweep
            logger.warning("case_execution_probe_failed", extra={"case_id": case_id}, exc_info=True)
            return CaseExecutionState.UNKNOWN, None

        status = description.status
        if status is None:
            return CaseExecutionState.UNKNOWN, None
        if status is self._RUNNING:
            return CaseExecutionState.RUNNING, status.name
        return CaseExecutionState.CLOSED, status.name


class MongoReconciliationOutbox:
    """The `{DEAD_LETTER, REQUIRES_RECONCILIATION}` sweep, index-backed.

    Exactly the pair `status_1_reconciliationState_1` was built for, so both
    equality terms are planned by one index rather than one of them being a
    filter applied after the fact. The read is deliberately **unsorted**: a sort
    on `createdAt` would need either a third index key or an in-memory sort of
    the whole dead-letter set, and there is no ordering requirement here -- the
    sweep drains the set, and which command it looks at first changes nothing.
    """

    def __init__(self, database: AsyncDatabase[dict[str, object]]) -> None:
        self._collection = database[INTEGRATION_OUTBOX_COLLECTION]

    @staticmethod
    def _unreconciled(command_id: str) -> dict[str, Any]:
        """The compare-and-set filter both writes share.

        Naming `reconciliationState` in the filter is what makes a second sweep
        -- or a second process -- match nothing rather than restamping a command
        somebody has already resolved.
        """
        return {
            "_id": command_id,
            "status": DEAD_LETTER_STATUS,
            "reconciliationState": REQUIRES_RECONCILIATION,
        }

    async def list_commands_requiring_reconciliation(self, *, limit: int) -> list[dict[str, Any]]:
        cursor = self._collection.find(
            {"status": DEAD_LETTER_STATUS, "reconciliationState": REQUIRES_RECONCILIATION}
        ).limit(limit)
        return [cast(dict[str, Any], document) async for document in cursor]

    async def requeue_command(self, command_id: str) -> bool:
        """Put a permanently-failed command back on the queue against a live case.

        `attemptCount` is **not** reset. It is the audit of how hard the
        platform tried, and zeroing it would erase the evidence that this
        command dead-lettered at all. `nextAttemptAt` is set to now instead,
        which is what actually makes `claim()` pick it up: the backoff was never
        the thing holding it, the `DEAD_LETTER` status was.
        """
        now = datetime.now(UTC)
        result = await self._collection.update_one(
            self._unreconciled(command_id),
            {
                "$set": {
                    "status": "PENDING",
                    "reconciliationState": RECONCILED,
                    "nextAttemptAt": now,
                    "reconciledAt": now,
                    "updatedAt": now,
                    "leaseOwner": None,
                    "leaseUntil": None,
                }
            },
        )
        return result.modified_count == 1

    async def reject_command_permanently(self, command_id: str, *, reason: str) -> bool:
        """Record that this command will never be applied, and keep it.

        The status stays `DEAD_LETTER` -- the outbox will still do nothing with
        it -- and only `reconciliationState` moves, because that is the field
        that says whether anything is *owed*. Nothing is deleted here and the
        Support event in `case_support_events` is not touched: a reply Support
        genuinely sent against a case that had already closed is a thing that
        happened, and the audit needs it.
        """
        now = datetime.now(UTC)
        result = await self._collection.update_one(
            self._unreconciled(command_id),
            {
                "$set": {
                    "reconciliationState": PERMANENTLY_REJECTED,
                    "reconciliationReason": reason[:128],
                    "reconciledAt": now,
                    "updatedAt": now,
                }
            },
        )
        return result.modified_count == 1


class RecoveryAction(StrEnum):
    """What reconciliation actually did to one case. One member per outcome.

    An enum rather than a boolean, because "did not relaunch" has four
    completely different meanings and an operator who cannot tell them apart
    cannot act on any of them.
    """

    #: A new execution was started, resuming where the case was.
    RELAUNCHED = "RELAUNCHED"
    #: The execution is live. Refused, and that refusal is the duplicate guard.
    ALREADY_RUNNING = "ALREADY_RUNNING"
    #: The case is legitimately terminal. Refused permanently.
    REFUSED_TERMINAL = "REFUSED_TERMINAL"
    #: The workflow host could not be asked. Left exactly as found.
    DEFERRED_UNKNOWN = "DEFERRED_UNKNOWN"
    #: The relaunch was attempted and the workflow host refused it. The case has
    #: been parked at `RECOVERY_REQUIRED` and the next pass retries.
    RELAUNCH_FAILED = "RELAUNCH_FAILED"
    #: No case with this id.
    CASE_NOT_FOUND = "CASE_NOT_FOUND"


@dataclass(frozen=True, slots=True)
class CaseRecoveryOutcome:
    """What one reconciliation pass over one case decided and did."""

    case_id: str
    action: RecoveryAction
    #: `None` only for `CASE_NOT_FOUND`, where there was nothing to classify.
    assessment: CaseDivergenceAssessment | None = None
    workflow_id: str | None = None
    #: Dead-lettered Support commands put back on the queue by this pass.
    requeued_commands: int = 0
    #: Dead-lettered Support commands recorded as never-to-be-applied.
    rejected_commands: int = 0

    @property
    def changed_anything(self) -> bool:
        """False on the second sweep of a case the first one settled.

        The property the idempotence test asserts, and it is deliberately about
        *writes* rather than about the action: a second pass over a relaunched
        case reports `ALREADY_RUNNING`, which is a different action and still no
        change.
        """
        return (
            self.action is RecoveryAction.RELAUNCHED
            or self.requeued_commands > 0
            or self.rejected_commands > 0
        )


class ReturnCaseRecoveryService:
    """Detect a case that has lost its execution, and repair it exactly once.

    Constructed from ports throughout, so the whole decision surface is
    exercisable without Mongo or Temporal. `outbox` is optional: an operator
    asking "why is this case stuck" needs no outbox, and a deployment with no
    dead letters must not fail to answer.
    """

    def __init__(
        self,
        *,
        launcher: CaseWorkflowLauncherPort,
        repository: RecoveryCaseRepositoryPort,
        probe: CaseExecutionProbePort,
        outbox: ReconciliationOutboxPort | None = None,
        batch_size: int = 100,
        interval_seconds: float = 60.0,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        self._launcher = launcher
        self._repository = repository
        self._probe = probe
        self._outbox = outbox
        self._batch_size = batch_size
        self._interval_seconds = interval_seconds

    # --- reading ----------------------------------------------------------

    async def assess(self, case_id: str) -> CaseDivergenceAssessment | None:
        """Classify one case. Reads, and writes nothing.

        This is the whole of what the operator surface needs, and it is separate
        from `reconcile_case` on purpose: looking at a stuck case must never be
        the thing that restarts it.
        """
        case = await self._repository.get_case(case_id)
        if case is None:
            return None
        execution, detail = await self._probe.execution_state(case_id)
        return classify_case_divergence(case, execution=execution, execution_detail=detail)

    # --- repairing --------------------------------------------------------

    async def reconcile_case(
        self, case_id: str, *, commands: Sequence[dict[str, Any]] | None = None
    ) -> CaseRecoveryOutcome:
        """Classify one case and do the one thing that classification implies.

        The order is: classify, then relaunch, then requeue -- and it is not
        interchangeable. Requeuing before the relaunch would put the Support
        command back on a queue whose dispatcher would immediately dead-letter
        it again against the execution that is still gone, burning an attempt
        and ending exactly where it started.

        A terminal case takes the other branch and never reaches the launcher at
        all.

        `commands` is this case's slice of an already-read dead-letter set. A
        caller with no slice passes nothing and the outbox is read here.
        """
        case = await self._repository.get_case(case_id)
        if case is None:
            return CaseRecoveryOutcome(case_id=case_id, action=RecoveryAction.CASE_NOT_FOUND)

        execution, detail = await self._probe.execution_state(case_id)
        assessment = classify_case_divergence(case, execution=execution, execution_detail=detail)

        if assessment.divergence is CaseDivergence.CASE_TERMINAL:
            rejected = await self._reject_commands(
                case_id, commands, reason=assessment.reason.value
            )
            return CaseRecoveryOutcome(
                case_id=case_id,
                action=RecoveryAction.REFUSED_TERMINAL,
                assessment=assessment,
                rejected_commands=rejected,
            )

        if assessment.divergence is CaseDivergence.INDETERMINATE:
            # Nothing is written, including the dead letters. A pass that cannot
            # see the workflow host has no basis for deciding whether a Support
            # event is owed a redelivery or a rejection.
            return CaseRecoveryOutcome(
                case_id=case_id,
                action=RecoveryAction.DEFERRED_UNKNOWN,
                assessment=assessment,
            )

        if assessment.divergence is CaseDivergence.HEALTHY:
            # The execution is live and the command is deliverable after all --
            # a Temporal blip that dead-lettered on a status the dispatcher
            # classifies as permanent, or a case an earlier pass already
            # relaunched. Requeue rather than ask Support to re-send.
            requeued = await self._requeue_commands(case_id, commands)
            return CaseRecoveryOutcome(
                case_id=case_id,
                action=RecoveryAction.ALREADY_RUNNING,
                assessment=assessment,
                workflow_id=return_case_workflow_id(case_id),
                requeued_commands=requeued,
            )

        return await self._relaunch(case, assessment, commands)

    async def _relaunch(
        self,
        case: dict[str, Any],
        assessment: CaseDivergenceAssessment,
        commands: Sequence[dict[str, Any]] | None,
    ) -> CaseRecoveryOutcome:
        """Start a new execution for an orphan, resuming where the case was.

        Reached only from the `RECOVERY_REQUIRED` branch above, which is reached
        only when the probe said the execution is closed or absent. That is the
        duplicate-execution guard, and it is a precondition rather than an
        exception handler.
        """
        case_id = assessment.case_id
        conversation_id = case.get("channelAConversationId")
        if not isinstance(conversation_id, str) or not conversation_id:
            # The same refusal the sweep above makes, for the same reason:
            # `ReturnCaseWorkflow` owns a *confirmation*, and giving one to a
            # case that reached the collection some other way would open a
            # Support conversation nobody asked for.
            logger.warning("case_recovery_skipped_non_channel_a", extra={"case_id": case_id})
            return CaseRecoveryOutcome(
                case_id=case_id,
                action=RecoveryAction.DEFERRED_UNKNOWN,
                assessment=assessment,
            )
        try:
            started = await self._launcher.ensure_case_workflow(
                case_id=case_id,
                tenant_id=str(case.get("tenantId") or ""),
                principal_id=str(case.get("principalId") or ""),
                conversation_id=conversation_id,
                configuration_release_id=str(case.get("configurationReleaseId") or ""),
                resume=_resume_from_case(case),
            )
        except Exception:  # noqa: BLE001 - the case is parked and the next pass retries
            logger.warning(
                "case_recovery_relaunch_failed", extra={"case_id": case_id}, exc_info=True
            )
            # Only now. Marking before the attempt would park a case that was
            # about to be repaired, and `RECOVERY_REQUIRED` on the projection is
            # an `awaiting` dimension an associate sees.
            await self._park(case_id)
            return CaseRecoveryOutcome(
                case_id=case_id,
                action=RecoveryAction.RELAUNCH_FAILED,
                assessment=assessment,
            )

        if started.already_running:
            # Temporal's own uniqueness answering a race: another process
            # relaunched this case between the probe and the start. Nothing was
            # duplicated -- that is what the error means -- and the honest
            # report is that this pass did not create it.
            requeued = await self._requeue_commands(case_id, commands)
            return CaseRecoveryOutcome(
                case_id=case_id,
                action=RecoveryAction.ALREADY_RUNNING,
                assessment=assessment,
                workflow_id=started.workflow_id,
                requeued_commands=requeued,
            )

        requeued = await self._requeue_commands(case_id, commands)
        logger.info(
            "case_recovery_relaunched",
            extra={
                "case_id": case_id,
                "workflow_id": started.workflow_id,
                "reason": assessment.reason.value,
                "requeued_commands": requeued,
            },
        )
        return CaseRecoveryOutcome(
            case_id=case_id,
            action=RecoveryAction.RELAUNCHED,
            assessment=assessment,
            workflow_id=started.workflow_id,
            requeued_commands=requeued,
        )

    async def _park(self, case_id: str) -> None:
        """Record `RECOVERY_REQUIRED` on the case, through the one writer of it.

        `backfill_case` is that writer. It refuses a terminal case, it is
        idempotent by inspection of the document, and it bumps the revision in
        the same write as the status -- which plan sect. 6.5 requires, because
        the projection changes.
        """
        try:
            await self._repository.backfill_case(case_id, workflow_terminated=True)
        except Exception:  # noqa: BLE001 - the relaunch already failed; do not mask it
            logger.warning("case_recovery_park_failed", extra={"case_id": case_id}, exc_info=True)

    # --- the dead-letter queue -------------------------------------------

    async def _case_commands(
        self, case_id: str, supplied: Sequence[dict[str, Any]] | None
    ) -> list[dict[str, Any]]:
        """This case's unreconciled commands, fetched only when not already held.

        `reconcile_once` reads the dead-letter set once and hands each case its
        own slice; a route reconciling a single case has no such slice and reads
        for itself. Without the parameter the sweep would re-read the whole set
        once per case, which is the shape that turns a bounded queue into a
        quadratic one.
        """
        if supplied is not None:
            return list(supplied)
        if self._outbox is None:
            return []
        commands = await self._outbox.list_commands_requiring_reconciliation(limit=self._batch_size)
        return [command for command in commands if _case_id_of(command) == case_id]

    async def _requeue_commands(
        self, case_id: str, supplied: Sequence[dict[str, Any]] | None
    ) -> int:
        outbox = self._outbox
        if outbox is None:
            return 0
        requeued = 0
        for command in await self._case_commands(case_id, supplied):
            if await outbox.requeue_command(str(command["_id"])):
                requeued += 1
        return requeued

    async def _reject_commands(
        self, case_id: str, supplied: Sequence[dict[str, Any]] | None, *, reason: str
    ) -> int:
        outbox = self._outbox
        if outbox is None:
            return 0
        rejected = 0
        for command in await self._case_commands(case_id, supplied):
            if await outbox.reject_command_permanently(str(command["_id"]), reason=reason):
                rejected += 1
        return rejected

    # --- the sweep --------------------------------------------------------

    async def reconcile_once(self) -> tuple[CaseRecoveryOutcome, ...]:
        """One pass over every Support event whose delivery permanently failed.

        The queue is the outbox, not the case collection, and that is the point
        of Phase 10 task 3: a dead letter is the platform's own record that
        something is owed to a specific case, so the sweep has a bounded work
        list rather than a scan of every case ever created.

        One failing case never stops the pass, for the same reason the sweep
        above gives: the cases in a batch belong to different associates.
        """
        if self._outbox is None:
            return ()
        commands = await self._outbox.list_commands_requiring_reconciliation(limit=self._batch_size)
        by_case: dict[str, list[dict[str, Any]]] = {}
        for command in commands:
            if str(command.get("aggregateType") or "") != SUPPORT_EVENT_AGGREGATE_TYPE:
                # Another aggregate's dead letter. It may well need an operator;
                # it does not need a case workflow restarted, and reconciling it
                # here would be this module deciding about a domain it does not
                # own.
                continue
            case_id = _case_id_of(command)
            if not case_id:
                continue
            # Grouped, so a case with three undelivered replies is classified
            # once and all three are resolved by that one decision -- rather
            # than being probed, and possibly relaunched, three times.
            by_case.setdefault(case_id, []).append(command)

        outcomes: list[CaseRecoveryOutcome] = []
        for case_id, case_commands in by_case.items():
            try:
                outcomes.append(await self.reconcile_case(case_id, commands=case_commands))
            except Exception:  # noqa: BLE001 - the next pass retries this case
                logger.warning(
                    "case_reconciliation_failed", extra={"case_id": case_id}, exc_info=True
                )
        return tuple(outcomes)

    async def run_forever(self) -> None:
        while True:
            try:
                await self.reconcile_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a sweep that dies stops repairing
                logger.error("case_reconciliation_pass_failed", exc_info=True)
            await asyncio.sleep(self._interval_seconds)


def _case_id_of(command: dict[str, Any]) -> str:
    """The case a Support-signal command belongs to.

    `aggregateId` first, because that is the field the operator surface lists
    by and the one an index would be built on. The payload's `caseId` is the
    fallback for a command written before the aggregate was set, and the two are
    the same value in every command `DurableSupportEventStore` writes.
    """
    aggregate_id = command.get("aggregateId")
    if isinstance(aggregate_id, str) and aggregate_id:
        return aggregate_id
    payload = command.get("payload")
    if isinstance(payload, dict):
        case_id = payload.get("caseId")
        if isinstance(case_id, str):
            return case_id
    return ""


def _resume_from_case(case: dict[str, Any]) -> CaseWorkflowResume:
    """Where the new execution should pick the case up, read from Mongo only.

    `RECOVERY_REQUIRED` is translated rather than passed through. It is the
    status *recovery itself* writes when it cannot repair a case; it is not a
    place the workflow ever was, so resuming at it would hand the new execution
    its own repair as a state. What the case's real position was is recoverable
    from the work item: `_open_support` sets the work item and the
    `AWAITING_SUPPORT` status in adjacent statements, so a parked case holding
    one was with Support, and a parked case holding none never got there and
    should run its own path from the top.
    """
    status = read_persisted_status(case.get("status"))
    work_item_id = case.get("channelBWorkItemId")
    work_item = work_item_id if isinstance(work_item_id, str) and work_item_id else None

    if status is CaseStatus.RECOVERY_REQUIRED:
        resumed_status = CaseStatus.AWAITING_SUPPORT.value if work_item else None
    else:
        resumed_status = status.value

    created_at = case.get("createdAt")
    lifetime_start = created_at.isoformat() if isinstance(created_at, datetime) else None

    return CaseWorkflowResume(
        status=resumed_status,
        work_item_id=work_item,
        lifetime_start_iso=lifetime_start,
    )


def build_case_recovery_service(
    *,
    temporal: Client,
    repository: Any,
    database: AsyncDatabase[dict[str, object]] | None,
    timings: ReturnCaseTimingConfiguration,
    task_queue: str,
) -> ReturnCaseRecoveryService:
    """Assemble the service from the four things every process already holds.

    Here rather than in the API module or the worker, so the operator route and
    the background sweep are demonstrably the *same* service with the same
    guards. A route that built its own launcher would be a second place the
    duplicate-execution rule lives.

    `database` is optional because the outbox is: a caller that only wants to
    ask "why is this case stuck" needs no dead-letter queue, and passing `None`
    yields a service whose reads work and whose command requeue is a no-op
    rather than a crash.
    """
    return ReturnCaseRecoveryService(
        launcher=TemporalCaseWorkflowLauncher(
            client=temporal,
            repository=repository,
            timings=timings,
            task_queue=task_queue,
        ),
        repository=repository,
        probe=TemporalCaseExecutionProbe(client=temporal),
        outbox=None if database is None else MongoReconciliationOutbox(database),
    )
