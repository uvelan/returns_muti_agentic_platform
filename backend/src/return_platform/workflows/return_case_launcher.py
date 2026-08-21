"""Starting the durable execution that owns a confirmed case.

`ReturnCaseWorkflow` was complete and registered and started by nobody: the
only `start_workflow` call naming it lived in a test, so Support's RMA
submission signalled `return-case-<id>` into a namespace where no such
execution had ever existed and the reply was lost to a NOT_FOUND. This module
is the missing half -- the one place a confirmed case becomes a running
workflow.

**Idempotent by identity, not by bookkeeping.** The execution id is
`return_case_workflow_id(case_id)`, derived rather than stored, so a duplicate
or simultaneous confirmation asks Temporal to start the *same* id and Temporal
answers `WorkflowAlreadyStartedError`. That is the convergence point: the
loser adopts the winner rather than creating a second case workflow. It is the
same shape `api/order_agent.py` uses for `OrderDiscoveryWorkflow` and
`operations/orchestrator.py` uses for `ReturnWorkflow`, and it holds without a
lock because the uniqueness is Temporal's, not ours.

**`cases.workflowId` is a link, never a precondition.** It is written after the
start so an operator (and `return_case_recovery`) can see which cases have
their workflow, but nothing derives the id from it -- `return_support.py`
computes the id from the case id. A failed link therefore leaves a fully
reachable case, which is why it is logged rather than raised: the case has its
workflow, and the recovery sweep re-writes the link on its next pass.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from temporalio.client import Client
from temporalio.exceptions import WorkflowAlreadyStartedError

from return_platform.configuration.return_configuration import ReturnCaseTimingConfiguration
from return_platform.workflows.return_case_workflow import (
    ReturnCaseTimings,
    ReturnCaseWorkflow,
    ReturnCaseWorkflowInput,
    return_case_workflow_id,
)

__all__ = [
    "CaseWorkflowRepositoryPort",
    "CaseWorkflowResume",
    "StartedCaseWorkflow",
    "TemporalCaseWorkflowLauncher",
    "case_timings_from_configuration",
]

logger = logging.getLogger("return_platform.workflows.return_case_launcher")


@dataclass(frozen=True, slots=True)
class StartedCaseWorkflow:
    """The execution that owns the case, and whether this call created it.

    `already_running` is not decoration: it is how a retried or duplicated
    confirmation is distinguished from a first one in the log, and it is the
    assertion a connection test makes to prove a second execution was not
    started.
    """

    workflow_id: str
    already_running: bool


@dataclass(frozen=True, slots=True)
class CaseWorkflowResume:
    """Where a recovered case was, so its new execution does not start over.

    Only ever supplied by recovery (plan sect. 13, Phase 10). A confirmation
    passes nothing and gets exactly the input it always got.

    **Why this is not optional decoration.** `ReturnCaseWorkflow.run` branches
    on `resumed_work_item_id`: with one it goes straight to `_serve_case`, and
    without one it requests a bay, evaluates the policy and calls
    `_open_support`. Restarting an orphaned `AWAITING_SUPPORT` case with an
    empty input would therefore open a *second* Support work item for a return
    Support already has, and evaluate a policy that was already decided. Those
    are the two harms a re-launch can do, and this is what prevents both.

    **Every field is reconstructed from Mongo, never guessed.** `status` is
    `cases.status`, `work_item_id` is `cases.channelBWorkItemId`, and
    `lifetime_start_iso` is the case's own `createdAt`.

    `lifetime_start_iso` deserves its reason. The workflow uses it for the
    absolute lifetime cap -- the point past which the platform stops holding a
    case open however many times its history has been reset. The stranded
    execution's own start is unrecoverable (nothing persists it), so the case's
    creation time is used: it is earlier than the true value, which makes the
    cap arrive sooner rather than never. Passing `None` would hand every
    recovered case a fresh thirty days, and a case that orphans repeatedly would
    never reach the cap at all -- which is precisely the failure the cap exists
    for.

    **What is deliberately absent: the applied-event set.**
    `resumed_support_event_ids` is the workflow's redelivery guard, and it
    cannot be reconstructed honestly -- Mongo records that a Support event was
    *delivered*, not that the workflow *applied* it before it died, and those
    differ exactly in the window recovery exists for. Claiming an event was
    applied would silently drop an RMA. So nothing is claimed, and safety comes
    from the other side instead: recovery requeues only the commands that never
    delivered, so a delivered event is never re-signalled and the empty set is
    never consulted for one.
    """

    status: str | None = None
    work_item_id: str | None = None
    lifetime_start_iso: str | None = None


class CaseWorkflowRepositoryPort(Protocol):
    """The one method the launcher needs from the operational repository.

    Structural, so `workflows` does not import `operations.repository` merely
    to record a link -- and so the launcher can be exercised without a Mongo
    client.
    """

    async def bind_case_workflow(self, case_id: str, *, workflow_id: str) -> bool: ...


def case_timings_from_configuration(
    configuration: ReturnCaseTimingConfiguration,
) -> ReturnCaseTimings:
    """Pin the release's timings onto the workflow input.

    Read once, here, at start. `ReturnCaseTimings` documents why: a deadline
    that moved under a return already waiting on it would make an operator's
    countdown jump, so a configuration change applies to cases started after
    it.
    """
    return ReturnCaseTimings(
        bay_wait_seconds=configuration.bay_wait_seconds,
        return_details_wait_seconds=configuration.return_details_wait_seconds,
        return_details_required=configuration.return_details_required,
        support_response_wait_seconds=configuration.support_response_wait_seconds,
        reminder_interval_seconds=configuration.reminder_interval_seconds,
        max_reminders=configuration.max_reminders,
        on_reminders_exhausted=configuration.on_reminders_exhausted,
        business_calendar_id=configuration.business_calendar_id,
        timezone=configuration.timezone,
    )


class TemporalCaseWorkflowLauncher:
    """Starts at most one `ReturnCaseWorkflow` per case, and says which one."""

    def __init__(
        self,
        *,
        client: Client,
        repository: CaseWorkflowRepositoryPort,
        timings: ReturnCaseTimingConfiguration,
        task_queue: str,
    ) -> None:
        self._client = client
        self._repository = repository
        self._timings = timings
        self._task_queue = task_queue

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
        """Start the case's workflow, or adopt the one already running.

        Raises whatever the Temporal client raises when the start could not be
        attempted at all. The caller must not report success in that case: a
        case that exists without its workflow is unreachable by every
        downstream agent, and `return_case_recovery` is what eventually starts
        it.

        `resume` is recovery's, and defaults to `None` so the confirmation path
        builds precisely the input it built before. See `CaseWorkflowResume` for
        why a re-launch that omitted it would open a second Support work item.

        **The duplicate-execution guard is Temporal's, and it covers exactly one
        case: an execution that is still open.** A start against a live id
        raises `WorkflowAlreadyStartedError` and is adopted below, so no caller
        of this method can fork a second live execution. It says nothing about a
        *closed* id -- Temporal permits reuse after close, which is what makes
        recovery possible at all and also what makes an unguarded re-launch able
        to restart a finished case. That guard cannot live here: this method is
        on the confirmation hot path, where the id is new by construction and a
        `describe` round-trip per confirmation would buy nothing. It lives in
        `return_case_recovery`, on the one caller that re-launches ids that may
        already have run.
        """
        workflow_id = return_case_workflow_id(case_id)
        resumption = resume or CaseWorkflowResume()
        already_running = False
        try:
            await self._client.start_workflow(
                ReturnCaseWorkflow.run,
                ReturnCaseWorkflowInput(
                    case_id=case_id,
                    tenant_id=tenant_id,
                    principal_id=principal_id,
                    conversation_id=conversation_id,
                    configuration_release_id=configuration_release_id,
                    timings=case_timings_from_configuration(self._timings),
                    resumed_status=resumption.status,
                    resumed_work_item_id=resumption.work_item_id,
                    resumed_lifetime_start_iso=resumption.lifetime_start_iso,
                ),
                id=workflow_id,
                task_queue=self._task_queue,
            )
        except WorkflowAlreadyStartedError:
            # The convergence point. A duplicate confirmation, a retried HTTP
            # request and two simultaneous confirmations all land here, and all
            # three adopt the execution that won rather than forking a second.
            already_running = True

        try:
            await self._repository.bind_case_workflow(case_id, workflow_id=workflow_id)
        except Exception:  # noqa: BLE001 - the link is provenance, not the mechanism
            logger.warning(
                "case_workflow_link_not_recorded",
                extra={"case_id": case_id, "workflow_id": workflow_id},
                exc_info=True,
            )
        logger.info(
            "case_workflow_started",
            extra={
                "case_id": case_id,
                "workflow_id": workflow_id,
                "already_running": already_running,
                "resumed": resume is not None,
            },
        )
        return StartedCaseWorkflow(workflow_id=workflow_id, already_running=already_running)
