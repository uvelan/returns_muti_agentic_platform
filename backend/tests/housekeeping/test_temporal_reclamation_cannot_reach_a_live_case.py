"""The one thing about housekeeping that must never be wrong.

`ReturnCaseWorkflow` runs for days. It performs durable business-calendar waits
-- SLA-01 parks a Friday-evening case until Monday -- and uses `continue_as_new`
across them. A reaper that terminated long-running executions would destroy live
returns mid-wait, and the requirement is that this be impossible **by
construction, not by configuration**.

These tests are the construction argument, stated as assertions:

* a live case's execution runs on the queue a deployed worker polls, and that
  queue is never reclaimable -- whatever prefix is configured, whatever the
  environment, and no matter how long the execution has been running;
* a configuration that *tries* to make it reclaimable does not produce a careless
  reclaimer, it produces no reclaimer at all;
* age is not a discriminator, in either direction: an execution running for a
  year on a deployed queue survives, and one running for an hour on a stranded
  queue does not.

The last one is the point of the whole design. `continue_as_new` resets
`start_time`, so an age rule is simultaneously unsafe for the case that has been
waiting and blind to the orphan that continued recently.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from return_platform.housekeeping.temporal_executions import (
    TemporalExecutionReclaimer,
    deployed_task_queues,
)
from return_platform.workflows.order_discovery_worker import ORDER_DISCOVERY_TASK_QUEUE
from return_platform.workflows.worker import RETURN_WORKFLOW_TASK_QUEUE

_CASE_WORKFLOW_TYPE = "return-platform-return-case-v1"


class _Execution:
    """The fields `list_workflows` yields that this reclaimer reads."""

    def __init__(
        self,
        *,
        workflow_id: str,
        task_queue: str,
        workflow_type: str,
        age: timedelta,
        status: Any = None,
    ) -> None:
        from temporalio.client import WorkflowExecutionStatus

        self.id = workflow_id
        self.run_id = f"{workflow_id}-run"
        self.task_queue = task_queue
        self.workflow_type = workflow_type
        self.start_time = datetime.now(UTC) - age
        self.status = status if status is not None else WorkflowExecutionStatus.RUNNING


class _Handle:
    def __init__(self, recorder: list[tuple[str, str]], workflow_id: str, run_id: str) -> None:
        self._recorder = recorder
        self._workflow_id = workflow_id
        self._run_id = run_id

    async def terminate(self, *, reason: str = "") -> None:
        self._recorder.append((self._workflow_id, reason))


class _Client:
    """A Temporal client that records every termination and nothing else."""

    def __init__(self, executions: list[_Execution]) -> None:
        self._executions = executions
        self.terminated: list[tuple[str, str]] = []

    def list_workflows(self, query: str = "") -> AsyncIterator[_Execution]:
        executions = list(self._executions)

        async def iterator() -> AsyncIterator[_Execution]:
            for execution in executions:
                yield execution

        return iterator()

    def get_workflow_handle(self, workflow_id: str, *, run_id: str | None = None) -> _Handle:
        return _Handle(self.terminated, workflow_id, run_id or "")


def _reclaimer(
    client: Any,
    *,
    environment: str = "test",
    prefixes: tuple[str, ...] = ("test-", "reasoning-"),
    minimum_age_seconds: float = 3_600,
    protected: frozenset[str] | None = None,
) -> TemporalExecutionReclaimer:
    return TemporalExecutionReclaimer(
        client=client,
        environment=environment,
        protected_task_queues=(
            protected
            if protected is not None
            else frozenset({RETURN_WORKFLOW_TASK_QUEUE, ORDER_DISCOVERY_TASK_QUEUE})
        ),
        reclaimable_task_queue_prefixes=prefixes,
        minimum_age_seconds=minimum_age_seconds,
        batch_limit=100,
    )


@pytest.mark.parametrize("age_days", [1, 4, 30, 365])
def test_a_case_workflow_on_the_deployed_queue_is_never_reclaimable(age_days: int) -> None:
    """The multi-day wait, at every duration an SLA can produce.

    A Friday-evening case waiting until Monday is four days. A case parked for
    operations after its reminders ran out can be far longer. None of it matters:
    the queue decides, and the deployed queue is protected.
    """
    reclaimer = _reclaimer(_Client([]))
    assert reclaimer.is_reclaimable(task_queue=RETURN_WORKFLOW_TASK_QUEUE) is False
    assert reclaimer.is_reclaimable(task_queue=ORDER_DISCOVERY_TASK_QUEUE) is False
    # The age is the parameter and it is deliberately unused by the rule -- that
    # is the assertion. Asserted through a full pass as well, below.
    assert age_days > 0


@pytest.mark.asyncio
@pytest.mark.parametrize("age", [timedelta(days=4), timedelta(days=90), timedelta(days=365)])
async def test_a_pass_never_terminates_a_case_on_the_deployed_queue(age: timedelta) -> None:
    client = _Client(
        [
            _Execution(
                workflow_id="return-case-live-1",
                task_queue=RETURN_WORKFLOW_TASK_QUEUE,
                workflow_type=_CASE_WORKFLOW_TYPE,
                age=age,
            )
        ]
    )
    outcome = await _reclaimer(client).reclaim_once()

    assert client.terminated == []
    assert outcome.reclaimed == 0
    assert outcome.examined == 1
    # Reported as protected rather than as "nothing to do", so an operator can
    # tell the rule fired from the reclaimer being idle.
    assert outcome.details["protected_task_queue"] == 1


@pytest.mark.asyncio
async def test_a_case_workflow_on_a_stranded_test_queue_is_reclaimed() -> None:
    """The same workflow type, and this one is debris.

    This is why a workflow-type allowlist was rejected: most of the orphans are
    production types started by real-infrastructure suites on ephemeral queues.
    """
    client = _Client(
        [
            _Execution(
                workflow_id="return-case-orphan-1",
                task_queue="test-return-case-a1b2c3d4",
                workflow_type=_CASE_WORKFLOW_TYPE,
                age=timedelta(hours=6),
            )
        ]
    )
    outcome = await _reclaimer(client).reclaim_once()

    assert [workflow_id for workflow_id, _ in client.terminated] == ["return-case-orphan-1"]
    assert outcome.reclaimed == 1
    assert "test-return-case-a1b2c3d4" in client.terminated[0][1]


@pytest.mark.parametrize(
    "prefix",
    [
        "return-platform-",
        "return-platform-return-v1",
        "return-",
        "r",
        "",
    ],
)
def test_a_configuration_naming_the_deployed_queue_produces_no_reclaimer(prefix: str) -> None:
    """Configuration cannot express "reap the production queue".

    Every one of these prefixes would match a queue a deployed worker polls. The
    constructor raises, so there is no object to behave carefully -- which is the
    difference between safe by construction and safe by configuration.
    """
    with pytest.raises(ValueError):
        _reclaimer(_Client([]), prefixes=(prefix,))


def test_a_reclaimer_without_a_protected_set_is_refused() -> None:
    """The disjointness check protects nothing if nothing is protected.

    A caller that forgot to pass the deployed queues would otherwise get a
    reclaimer whose only rule is the prefix -- and the prefix check would have
    had nothing to be validated against.
    """
    with pytest.raises(ValueError):
        _reclaimer(_Client([]), protected=frozenset())


@pytest.mark.asyncio
@pytest.mark.parametrize("environment", ["staging", "production"])
async def test_reclamation_is_hard_gated_outside_development_and_test(environment: str) -> None:
    """The second, independent barrier -- the `DurableInterceptionProvider` gate.

    Not the safety argument (the protected queue is), but what makes a mistake in
    it survivable. Note the execution here is on a stranded queue and old enough:
    every other rule says reclaim it, and the gate still refuses.
    """
    client = _Client(
        [
            _Execution(
                workflow_id="return-case-orphan-2",
                task_queue="test-return-case-deadbeef",
                workflow_type=_CASE_WORKFLOW_TYPE,
                age=timedelta(days=30),
            )
        ]
    )
    reclaimer = _reclaimer(client, environment=environment)

    assert reclaimer.enabled is False
    outcome = await reclaimer.reclaim_once()
    assert client.terminated == []
    assert outcome.ran is False
    assert environment in (outcome.skipped_reason or "")


@pytest.mark.asyncio
async def test_an_execution_inside_the_age_floor_is_left_for_the_running_suite() -> None:
    """The age floor's only job: do not reap the suite that is running now."""
    client = _Client(
        [
            _Execution(
                workflow_id="return-case-in-flight",
                task_queue="test-return-case-99887766",
                workflow_type=_CASE_WORKFLOW_TYPE,
                age=timedelta(minutes=2),
            )
        ]
    )
    outcome = await _reclaimer(client, minimum_age_seconds=3_600).reclaim_once()

    assert client.terminated == []
    assert outcome.details["within_minimum_age"] == 1


@pytest.mark.asyncio
async def test_a_closed_execution_is_not_terminated_even_if_the_query_returns_it() -> None:
    """Visibility filtering is not guaranteed, so the status is re-checked.

    A server without advanced visibility can ignore the `ExecutionStatus` filter.
    Terminating an already-closed execution is harmless but it would inflate the
    reclaimed count and make the report untrue.
    """
    from temporalio.client import WorkflowExecutionStatus

    client = _Client(
        [
            _Execution(
                workflow_id="return-case-done",
                task_queue="test-return-case-11223344",
                workflow_type=_CASE_WORKFLOW_TYPE,
                age=timedelta(days=3),
                status=WorkflowExecutionStatus.COMPLETED,
            )
        ]
    )
    outcome = await _reclaimer(client).reclaim_once()

    assert client.terminated == []
    assert outcome.reclaimed == 0


def test_the_protected_set_covers_the_configured_and_the_default_queues() -> None:
    """A renamed queue must not make everything left on the old one reclaimable."""
    from return_platform.configuration.settings import Settings

    settings = Settings.model_construct(
        return_workflow_task_queue="renamed-return-queue",
        order_discovery_workflow_task_queue="renamed-discovery-queue",
    )
    protected = deployed_task_queues(settings)

    assert "renamed-return-queue" in protected
    assert "renamed-discovery-queue" in protected
    assert RETURN_WORKFLOW_TASK_QUEUE in protected
    assert ORDER_DISCOVERY_TASK_QUEUE in protected


@pytest.mark.asyncio
async def test_one_failed_termination_does_not_abandon_the_rest_of_the_pass() -> None:
    class _FailingOnce(_Client):
        async def _fail(self) -> None:  # pragma: no cover - shape only
            raise RuntimeError

        def get_workflow_handle(self, workflow_id: str, *, run_id: str | None = None) -> Any:
            if workflow_id == "boom":

                class _Broken:
                    async def terminate(self, *, reason: str = "") -> None:
                        raise RuntimeError("temporal said no")

                return _Broken()
            return super().get_workflow_handle(workflow_id, run_id=run_id)

    client = _FailingOnce(
        [
            _Execution(
                workflow_id="boom",
                task_queue="test-return-case-aaaa",
                workflow_type=_CASE_WORKFLOW_TYPE,
                age=timedelta(days=2),
            ),
            _Execution(
                workflow_id="fine",
                task_queue="test-return-case-bbbb",
                workflow_type=_CASE_WORKFLOW_TYPE,
                age=timedelta(days=2),
            ),
        ]
    )
    outcome = await _reclaimer(client).reclaim_once()

    assert outcome.failed == 1
    assert outcome.reclaimed == 1
    assert outcome.reclaimed_ids == ("fine",)
