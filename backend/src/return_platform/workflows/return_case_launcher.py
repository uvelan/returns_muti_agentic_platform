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
    ) -> StartedCaseWorkflow:
        """Start the case's workflow, or adopt the one already running.

        Raises whatever the Temporal client raises when the start could not be
        attempted at all. The caller must not report success in that case: a
        case that exists without its workflow is unreachable by every
        downstream agent, and `return_case_recovery` is what eventually starts
        it.
        """
        workflow_id = return_case_workflow_id(case_id)
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
            },
        )
        return StartedCaseWorkflow(workflow_id=workflow_id, already_running=already_running)
