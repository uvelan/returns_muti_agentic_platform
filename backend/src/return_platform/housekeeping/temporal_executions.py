"""Reclaiming Temporal executions that no worker will ever pick up again.

208 orphaned executions had to be terminated by hand on this deployment. They
were holding task queues, and a real-infrastructure suite failed on a *different*
test each run because of it. Nothing reclaims them, so they accumulate for as
long as anyone runs the suite.

**The rule that must never be got wrong.** `ReturnCaseWorkflow` legitimately
runs for days. It performs durable multi-day business waits -- SLA-01's
business-calendar deadlines park a Friday-evening case until Monday -- and it
uses `continue_as_new` across them. "Terminate anything Running for more than N
hours" would kill live cases mid-wait, and `continue_as_new` makes it worse in
both directions: it resets `start_time`, so an age rule is simultaneously unsafe
for the case that has waited a long time on one run and blind to the orphan that
has continued recently. Age is not a discriminator here. It is not used as one.

**What is used.** An execution is reclaimable only if its **task queue is one no
deployed worker polls**. That is a positive statement about the execution, and
it is the literal definition of stranded: a Temporal execution makes progress
only when a worker polls its task queue, so an execution on a queue nothing
polls can never advance, whatever its type or age. It is also exactly what the
suites produce -- every test that starts an execution names an ephemeral queue
(`test-return-case-<uuid>`, `test-order-discovery-<uuid>`,
`reasoning-crash-test-<uuid>`, ...), while every deployed worker polls one of two
fixed queues resolved from `Settings`.

**Why a live case cannot be reached, by construction.** A live
`ReturnCaseWorkflow` is on `settings.return_workflow_task_queue` -- it has to be,
because that is the only queue a deployed worker polls, and a case anywhere else
would never have run its first activity. That queue is in `protected` here, and
`__init__` refuses any configured prefix that matches a protected queue. So a
release cannot express "reap the production queue": the constructor raises
before a reclaimer exists. Configuration selects *among* stranded queues; it
cannot promote a live one into the set.

**Rejected discriminators, and why.**

* *Age alone* -- unsafe, per above.
* *Workflow type* -- looks appealing (`ThrowawayReasoningTestWorkflow` is
  unambiguously a test workflow) but it does not describe the debris. Most of
  the orphans are `return-platform-return-case-v1` and
  `return-platform-order-discovery-v1` executions started by real-infra suites:
  production types doing test work. A type allowlist would leave nearly all of
  them and would need `ReturnCaseWorkflow` excluded by a *negative* rule.
* *Reconciling against the case document* -- an execution whose case is missing
  looks like an orphan, and usually is. But "missing" is also what a reclaimer
  pointed at the wrong Mongo database sees for every live case in the system. A
  misconfiguration and a genuine orphan are indistinguishable at the point of
  decision, which is precisely the property a rule guarding a destructive action
  must not have.

The development/test environment gate below is a second, independent barrier --
the same hard gate `DurableInterceptionProvider` uses for the same reason. It is
not the safety argument; the protected-queue rule is. It is what makes a mistake
in that argument survivable.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta

from temporalio.client import Client, WorkflowExecutionStatus

from return_platform.configuration.settings import Settings
from return_platform.housekeeping.reclamation import ReclamationOutcome
from return_platform.workflows.order_discovery_worker import ORDER_DISCOVERY_TASK_QUEUE
from return_platform.workflows.worker import RETURN_WORKFLOW_TASK_QUEUE

__all__ = [
    "RESOURCE_CLASS",
    "TemporalExecutionReclaimer",
    "deployed_task_queues",
]

logger = logging.getLogger("return_platform.housekeeping.temporal_executions")

RESOURCE_CLASS = "temporal-execution"

#: The environments in which reclamation may run at all. Same set, and the same
#: reasoning, as `DurableInterceptionProvider`: a capability that must never
#: exist in production is gated structurally, not discouraged.
_RECLAIMABLE_ENVIRONMENTS = frozenset({"development", "test"})


def deployed_task_queues(settings: Settings) -> frozenset[str]:
    """Every queue a deployed worker of this platform polls.

    Both the *configured* queues and the module defaults, deliberately. The
    settings are what the workers running right now poll; the defaults are what
    the workers running before someone changed the setting polled, and their
    executions are still out there. Protecting only the current value would make
    a queue-rename a licence to terminate everything left on the old one.
    """

    return frozenset(
        {
            settings.return_workflow_task_queue,
            settings.order_discovery_workflow_task_queue,
            RETURN_WORKFLOW_TASK_QUEUE,
            ORDER_DISCOVERY_TASK_QUEUE,
        }
    )


class TemporalExecutionReclaimer:
    """Terminates running executions stranded on queues nothing polls."""

    def __init__(
        self,
        *,
        client: Client,
        environment: str,
        protected_task_queues: Iterable[str],
        reclaimable_task_queue_prefixes: Sequence[str],
        minimum_age_seconds: float,
        batch_limit: int,
    ) -> None:
        if minimum_age_seconds < 0:
            raise ValueError("minimum_age_seconds must not be negative")
        if batch_limit < 1:
            raise ValueError("batch_limit must be at least 1")
        protected = frozenset(protected_task_queues)
        if not protected:
            # Without a protected set there is nothing for the disjointness
            # check below to protect, and every rule in this class degenerates
            # into "terminate what matches a prefix". Refused rather than
            # defaulted: the caller not passing the deployed queues is a wiring
            # bug, and a default would hide it.
            raise ValueError("protected_task_queues must not be empty")
        prefixes = tuple(prefix for prefix in reclaimable_task_queue_prefixes)
        if not prefixes:
            raise ValueError("reclaimable_task_queue_prefixes must not be empty")
        for prefix in prefixes:
            if not prefix:
                # An empty prefix matches every queue including the deployed
                # ones. This is the single configuration value that would turn
                # this class into the naive reaper it exists not to be.
                raise ValueError("a reclaimable task-queue prefix must not be empty")
            matched = sorted(queue for queue in protected if queue.startswith(prefix))
            if matched:
                # The construction-time half of the safety argument. A release
                # that names a prefix covering a deployed queue does not produce
                # a reclaimer that behaves carefully; it produces no reclaimer.
                raise ValueError(
                    f"reclaimable task-queue prefix {prefix!r} matches deployed task "
                    f"queue(s) {', '.join(matched)}; a queue a worker polls carries live "
                    "work and can never be reclaimable"
                )
        self._client = client
        self._enabled = environment in _RECLAIMABLE_ENVIRONMENTS
        self._environment = environment
        self._protected = protected
        self._prefixes = prefixes
        self._minimum_age_seconds = minimum_age_seconds
        self._batch_limit = batch_limit

    @property
    def enabled(self) -> bool:
        return self._enabled

    def is_reclaimable(self, *, task_queue: str) -> bool:
        """The whole eligibility rule for a queue, in one place.

        Protection is checked first and independently of the prefixes. The
        constructor has already proved the two cannot overlap, so this is
        redundant -- and it stays, because it is the assertion that keeps a
        future change to the prefix logic from being able to reach a deployed
        queue without also changing this line.
        """

        if task_queue in self._protected:
            return False
        return any(task_queue.startswith(prefix) for prefix in self._prefixes)

    async def reclaim_once(self) -> ReclamationOutcome:
        """One pass. Terminates at most `batch_limit` stranded executions."""

        if not self._enabled:
            return ReclamationOutcome.skipped(
                RESOURCE_CLASS,
                f"environment {self._environment!r} is not one of "
                f"{sorted(_RECLAIMABLE_ENVIRONMENTS)}",
            )

        cutoff = datetime.now(UTC) - timedelta(seconds=self._minimum_age_seconds)
        examined = 0
        reclaimed: list[str] = []
        failed = 0
        protected_seen = 0
        too_young = 0

        async for execution in self._client.list_workflows(
            "ExecutionStatus = 'Running'",
        ):
            if len(reclaimed) >= self._batch_limit:
                break
            examined += 1
            # Re-checked in Python rather than trusted from the visibility
            # query: advanced visibility is not guaranteed to be configured,
            # and a server that ignores the filter would otherwise hand this
            # loop closed executions to terminate.
            if execution.status is not WorkflowExecutionStatus.RUNNING:
                continue
            task_queue = execution.task_queue or ""
            if task_queue in self._protected:
                protected_seen += 1
                continue
            if not self.is_reclaimable(task_queue=task_queue):
                continue
            start_time = execution.start_time
            if start_time is None:
                # No start time means no way to tell a suite running right now
                # from one that finished last week. Left alone.
                continue
            if start_time.tzinfo is None:
                start_time = start_time.replace(tzinfo=UTC)
            if start_time > cutoff:
                too_young += 1
                continue
            try:
                await self._client.get_workflow_handle(
                    execution.id, run_id=execution.run_id
                ).terminate(
                    reason=(
                        "return-platform housekeeping: stranded on task queue "
                        f"{task_queue!r}, which no deployed worker polls"
                    )
                )
            except Exception:  # noqa: BLE001 - one execution never stops the pass
                failed += 1
                logger.warning(
                    "housekeeping_temporal_termination_failed",
                    extra={
                        "workflow_id": execution.id,
                        "run_id": execution.run_id,
                        "task_queue": task_queue,
                    },
                    exc_info=True,
                )
                continue
            reclaimed.append(execution.id)
            logger.info(
                "housekeeping_temporal_execution_reclaimed",
                extra={
                    "workflow_id": execution.id,
                    "run_id": execution.run_id,
                    "workflow_type": execution.workflow_type,
                    "task_queue": task_queue,
                },
            )

        return ReclamationOutcome(
            resource_class=RESOURCE_CLASS,
            examined=examined,
            reclaimed=len(reclaimed),
            reclaimed_ids=tuple(reclaimed),
            failed=failed,
            details={
                # Named so a pass that reclaimed nothing is readable: executions
                # on a deployed queue are live work, and ones inside the age
                # floor belong to a suite that is probably still running.
                "protected_task_queue": protected_seen,
                "within_minimum_age": too_young,
            },
        )
