"""`ReturnCaseWorkflow` against a real Temporal server.

The property that matters is the one a unit test cannot show: **the waits are
durable**. The worker is stopped mid-wait and started again, and the case
carries on -- the timer, the reminder count and the outstanding work item all
survive, because they are Temporal state rather than a coroutine's stack.

That is the difference this workflow exists to make. What it replaced was
`asyncio.sleep` twelve times at five seconds inside an ordinary coroutine: sixty
seconds against a business-days SLA, and nothing at all after a restart.

Activities are probes rather than the real ones. Under test is the workflow's
own coordination -- ordering, timers, signals, idempotency keys, continuation --
and pointing it at Mongo would make a slow test of the repository instead.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any

import pytest
from temporalio import activity
from temporalio.client import Client
from temporalio.worker import Worker

from return_platform.workflows.return_case_workflow import (
    BayResultNotice,
    CancelCaseCommand,
    DraftSupportRequestInput,
    OpenSupportWorkItemInput,
    RecordCaseStatusInput,
    RecordSupportOutcomeInput,
    RequestBayAssignmentInput,
    ReturnCaseTimings,
    ReturnCaseWorkflow,
    ReturnCaseWorkflowInput,
    SendSupportReminderInput,
    SupportResponseNotice,
    SupportReturnRecord,
    SynchronizeReturnRecordsInput,
)

pytestmark = pytest.mark.asyncio

_TEMPORAL_TARGET = os.getenv("PLATFORM_TEST_TEMPORAL_TARGET", "localhost:7233")


class _Probe:
    """Records what the workflow asked for, in order."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        # One event per activity name, set when it first runs. Lets a test await
        # a step instead of polling for it -- the activity runs on the worker's
        # own task, so there is nothing else to await.
        self._reached: dict[str, asyncio.Event] = {}
        self.reminder_keys: list[str] = []
        self.work_item_keys: list[str] = []
        self.outcomes: list[RecordSupportOutcomeInput] = []
        self.syncs: list[SynchronizeReturnRecordsInput] = []
        self.statuses: list[str] = []
        self.bay_should_fail = False
        self.sync_should_fail = False
        self.graph_generation_id = "gen-under-test"

    @activity.defn(name="record_case_status")
    async def record_case_status(self, request: RecordCaseStatusInput) -> None:
        self._record("record_case_status")
        self.statuses.append(request.status)

    @activity.defn(name="request_bay_assignment")
    async def request_bay_assignment(self, request: RequestBayAssignmentInput) -> None:
        del request
        self._record("request_bay_assignment")
        if self.bay_should_fail:
            raise RuntimeError("warehouse service unavailable")

    @activity.defn(name="draft_support_request")
    async def draft_support_request(self, request: DraftSupportRequestInput) -> str:
        del request
        self._record("draft_support_request")
        return "Hello -- could you raise the RMA for this return?"

    @activity.defn(name="open_support_work_item")
    async def open_support_work_item(self, request: OpenSupportWorkItemInput) -> str:
        self._record("open_support_work_item")
        self.work_item_keys.append(request.idempotency_key)
        return f"wi-{request.case_id}"

    @activity.defn(name="send_support_reminder")
    async def send_support_reminder(self, request: SendSupportReminderInput) -> None:
        self._record("send_support_reminder")
        self.reminder_keys.append(request.idempotency_key)

    @activity.defn(name="record_support_outcome")
    async def record_support_outcome(self, request: RecordSupportOutcomeInput) -> None:
        self._record("record_support_outcome")
        self.outcomes.append(request)

    @activity.defn(name="synchronize_return_records")
    async def synchronize_return_records(self, request: SynchronizeReturnRecordsInput) -> str:
        self._record("synchronize_return_records")
        self.syncs.append(request)
        if self.sync_should_fail:
            raise RuntimeError("the graph refused the write")
        return self.graph_generation_id

    def _record(self, name: str) -> None:
        self.calls.append(name)
        self._reached.setdefault(name, asyncio.Event()).set()

    async def reached(self, name: str, *, within_seconds: float = 30.0) -> None:
        """Wait until `name` has run once.

        Named `within_seconds` rather than `timeout`: the deadline is here, not
        at the call site, so the failure can say *which* step never ran --
        "open_support_work_item did not run within 20s" is a diagnosis, and a
        bare CancelledError from a caller-side scope is not.
        """
        event = self._reached.setdefault(name, asyncio.Event())
        try:
            async with asyncio.timeout(within_seconds):
                await event.wait()
        except TimeoutError:
            raise AssertionError(f"{name} did not run within {within_seconds}s") from None

    def all(self) -> tuple[Any, ...]:
        return (
            self.record_case_status,
            self.request_bay_assignment,
            self.draft_support_request,
            self.open_support_work_item,
            self.send_support_reminder,
            self.record_support_outcome,
            self.synchronize_return_records,
        )


def _timings(**overrides: Any) -> ReturnCaseTimings:
    base: dict[str, Any] = {
        "bay_wait_seconds": 1,
        "support_response_wait_seconds": 60,
        "reminder_interval_seconds": 1,
        "max_reminders": 2,
        "on_reminders_exhausted": "PARK_FOR_OPERATIONS",
        "business_calendar_id": "default",
        "timezone": "UTC",
    }
    base.update(overrides)
    return ReturnCaseTimings(**base)


def _case_input(**overrides: Any) -> ReturnCaseWorkflowInput:
    base: dict[str, Any] = {
        "case_id": f"case-{uuid.uuid4().hex[:8]}",
        "tenant_id": "tenant-a",
        "principal_id": "associate-1",
        "conversation_id": f"conv-{uuid.uuid4().hex[:8]}",
        "configuration_release_id": "release-1",
        "timings": _timings(),
    }
    base.update(overrides)
    return ReturnCaseWorkflowInput(**base)


async def _start(client: Client, probe: _Probe, workflow_input: ReturnCaseWorkflowInput) -> Any:
    queue = f"test-return-case-{uuid.uuid4().hex[:8]}"
    return queue, await client.start_workflow(
        ReturnCaseWorkflow.run,
        workflow_input,
        id=f"test-return-case-{uuid.uuid4().hex[:8]}",
        task_queue=queue,
    )


async def test_the_case_completes_when_support_answers() -> None:
    client = await Client.connect(_TEMPORAL_TARGET)
    probe = _Probe()
    queue, handle = await _start(client, probe, _case_input())

    async with Worker(
        client, task_queue=queue, workflows=(ReturnCaseWorkflow,), activities=probe.all()
    ):
        await probe.reached("open_support_work_item")
        await handle.signal(
            ReturnCaseWorkflow.support_response,
            SupportResponseNotice(
                work_item_id="wi-1",
                records=(SupportReturnRecord(return_reference="RMA-1", label_reference="LBL-1"),),
            ),
        )
        outcome = await handle.result()

    assert outcome.status == "RMA_RECEIVED"
    assert outcome.return_references == ("RMA-1",)
    # Bay was asked for and did not answer within its window; the case went on.
    assert "request_bay_assignment" in probe.calls
    assert probe.calls.index("request_bay_assignment") < probe.calls.index("open_support_work_item")


async def test_the_support_wait_survives_a_worker_restart() -> None:
    """The step this workflow exists for.

    The worker is stopped while the case is waiting on Support and started
    again. If the wait lived in a coroutine it would be gone; because it is a
    Temporal timer, the case is still waiting and still answerable.
    """
    client = await Client.connect(_TEMPORAL_TARGET)
    probe = _Probe()
    queue, handle = await _start(
        client, probe, _case_input(timings=_timings(support_response_wait_seconds=300))
    )

    async with Worker(
        client, task_queue=queue, workflows=(ReturnCaseWorkflow,), activities=probe.all()
    ):
        await probe.reached("open_support_work_item")

    # The worker is gone and with it the coroutine that was waiting. Nothing is
    # running this case; only Temporal knows it exists.
    #
    # The state is read *after* the replacement worker starts, not here: a query
    # is served by a worker replaying the workflow, so querying with none
    # running times out rather than telling us anything. That the replay
    # produces the right answer at all is the durability claim.
    resumed = _Probe()
    async with Worker(
        client, task_queue=queue, workflows=(ReturnCaseWorkflow,), activities=resumed.all()
    ):
        state = await handle.query(ReturnCaseWorkflow.execution_state)
        assert state.status == "AWAITING_SUPPORT"
        assert state.support_resolved is False
        assert state.work_item_id is not None, (
            "the outstanding support thread did not survive the restart"
        )

        await handle.signal(
            ReturnCaseWorkflow.support_response,
            SupportResponseNotice(
                work_item_id="wi-1",
                records=(SupportReturnRecord(return_reference="RMA-AFTER-RESTART"),),
            ),
        )
        outcome = await handle.result()

    assert outcome.return_references == ("RMA-AFTER-RESTART",)
    # The second worker never re-opened the thread: the work item survived.
    assert "open_support_work_item" not in resumed.calls


async def test_reminders_fire_on_the_configured_cadence_and_then_park() -> None:
    """A cap with no terminal branch leaves a case waiting forever, unseen."""
    client = await Client.connect(_TEMPORAL_TARGET)
    probe = _Probe()
    queue, handle = await _start(
        client,
        probe,
        _case_input(
            timings=_timings(
                support_response_wait_seconds=20, reminder_interval_seconds=1, max_reminders=2
            )
        ),
    )

    async with Worker(
        client, task_queue=queue, workflows=(ReturnCaseWorkflow,), activities=probe.all()
    ):
        outcome = await handle.result()

    assert outcome.reminders_sent == 2
    assert outcome.parked_reason == "SUPPORT_REMINDERS_EXHAUSTED"
    # Numbered, so a retry re-sends the same reminder rather than adding one to
    # a person's queue.
    assert probe.reminder_keys == sorted(set(probe.reminder_keys))
    assert len(set(probe.reminder_keys)) == 2


async def test_a_bay_failure_does_not_stop_the_return() -> None:
    """Bay is best-effort. `orchestrator._handle` used to let it fail the case."""
    client = await Client.connect(_TEMPORAL_TARGET)
    probe = _Probe()
    probe.bay_should_fail = True
    queue, handle = await _start(client, probe, _case_input())

    async with Worker(
        client, task_queue=queue, workflows=(ReturnCaseWorkflow,), activities=probe.all()
    ):
        await probe.reached("open_support_work_item")
        await handle.signal(
            ReturnCaseWorkflow.support_response,
            SupportResponseNotice(
                work_item_id="wi-1", records=(SupportReturnRecord(return_reference="RMA-1"),)
            ),
        )
        outcome = await handle.result()

    assert outcome.status == "RMA_RECEIVED"
    assert outcome.bay_reference is None


async def test_a_bay_result_arriving_before_the_wait_is_kept() -> None:
    """Signals can land before the workflow reaches the wait for them."""
    client = await Client.connect(_TEMPORAL_TARGET)
    probe = _Probe()
    queue, handle = await _start(client, probe, _case_input(timings=_timings(bay_wait_seconds=30)))

    async with Worker(
        client, task_queue=queue, workflows=(ReturnCaseWorkflow,), activities=probe.all()
    ):
        await handle.signal(
            ReturnCaseWorkflow.bay_result,
            BayResultNotice(warehouse_reference="WH-1", bay_reference="BAY-7"),
        )
        await probe.reached("open_support_work_item", within_seconds=20)
        await handle.signal(
            ReturnCaseWorkflow.support_response,
            SupportResponseNotice(
                work_item_id="wi-1", records=(SupportReturnRecord(return_reference="RMA-1"),)
            ),
        )
        outcome = await handle.result()

    # It did not sit out the 30-second bay wait, and it kept the answer.
    assert outcome.bay_reference == "BAY-7"


async def test_a_duplicate_support_response_does_not_issue_a_second_set_of_rmas() -> None:
    """Support clicking send twice, or a transport redelivering."""
    client = await Client.connect(_TEMPORAL_TARGET)
    probe = _Probe()
    queue, handle = await _start(client, probe, _case_input())

    async with Worker(
        client, task_queue=queue, workflows=(ReturnCaseWorkflow,), activities=probe.all()
    ):
        await probe.reached("open_support_work_item")
        first = SupportResponseNotice(
            work_item_id="wi-1", records=(SupportReturnRecord(return_reference="RMA-1"),)
        )
        second = SupportResponseNotice(
            work_item_id="wi-1", records=(SupportReturnRecord(return_reference="RMA-DUPLICATE"),)
        )
        await handle.signal(ReturnCaseWorkflow.support_response, first)
        await handle.signal(ReturnCaseWorkflow.support_response, second)
        outcome = await handle.result()

    assert outcome.return_references == ("RMA-1",)
    assert len(probe.outcomes) == 1


async def test_the_rma_reaches_the_graph_before_the_case_reports_it_received() -> None:
    """W2.5. The RMA is written to Mongo and then to the graph, in that order.

    The agent answering the associate's next turn reads the graph. Reporting
    `RMA_RECEIVED` before the record is projected would make "Support answered"
    true on the case and false everywhere an associate can see it.

    The sync is record-scoped: the ids are the ones `record_support_outcome`
    was given, not a request to re-sync the collection.
    """
    client = await Client.connect(_TEMPORAL_TARGET)
    probe = _Probe()
    queue, handle = await _start(client, probe, _case_input())

    async with Worker(
        client, task_queue=queue, workflows=(ReturnCaseWorkflow,), activities=probe.all()
    ):
        await probe.reached("open_support_work_item")
        await handle.signal(
            ReturnCaseWorkflow.support_response,
            SupportResponseNotice(
                work_item_id="wi-1",
                records=(
                    SupportReturnRecord(return_reference="RMA-1"),
                    SupportReturnRecord(return_reference="RMA-2"),
                ),
            ),
        )
        outcome = await handle.result()

    assert outcome.status == "RMA_RECEIVED"
    assert outcome.graph_generation_id == "gen-under-test"
    assert probe.calls.index("record_support_outcome") < probe.calls.index(
        "synchronize_return_records"
    )
    assert len(probe.syncs) == 1
    # Exactly the records just committed, and each one's id -- not a collection
    # scope, and not a re-derivation of what the store happens to hold by now.
    assert probe.syncs[0].return_record_ids == probe.outcomes[0].return_record_ids
    assert len(probe.syncs[0].return_record_ids) == 2


async def test_a_graph_sync_failure_parks_the_case_loudly() -> None:
    """Return Workflow is `blocking`, so this failure is not stepped over.

    The RMA exists in the platform store and in no graph any agent reads. A case
    reported `RMA_RECEIVED` in that state would have Order Discovery telling the
    associate on their next turn that no return exists -- worse than a parked
    case somebody can see and retry.
    """
    client = await Client.connect(_TEMPORAL_TARGET)
    probe = _Probe()
    probe.sync_should_fail = True
    queue, handle = await _start(client, probe, _case_input())

    async with Worker(
        client, task_queue=queue, workflows=(ReturnCaseWorkflow,), activities=probe.all()
    ):
        await probe.reached("open_support_work_item")
        await handle.signal(
            ReturnCaseWorkflow.support_response,
            SupportResponseNotice(
                work_item_id="wi-1", records=(SupportReturnRecord(return_reference="RMA-1"),)
            ),
        )
        outcome = await handle.result()

    assert outcome.parked_reason == "RETURN_GRAPH_SYNC_FAILED"
    assert outcome.graph_generation_id is None
    # Not RMA_RECEIVED: the platform cannot claim a state whose evidence an
    # associate would be shown from the graph.
    assert outcome.status != "RMA_RECEIVED"
    assert "RETURN_GRAPH_SYNC_FAILED" in probe.statuses or outcome.status == "AWAITING_SUPPORT"


async def test_a_rejected_return_needs_no_graph_sync() -> None:
    """Support declining issues no RMA, so there is nothing to project.

    Syncing an empty record set would fail loudly for no reason and park a case
    that reached a legitimate terminal state.
    """
    client = await Client.connect(_TEMPORAL_TARGET)
    probe = _Probe()
    queue, handle = await _start(client, probe, _case_input())

    async with Worker(
        client, task_queue=queue, workflows=(ReturnCaseWorkflow,), activities=probe.all()
    ):
        await probe.reached("open_support_work_item")
        await handle.signal(
            ReturnCaseWorkflow.support_response,
            SupportResponseNotice(
                work_item_id="wi-1", records=(), rejected=True, reason="outside the return window"
            ),
        )
        outcome = await handle.result()

    assert outcome.status == "CLOSED"
    assert "synchronize_return_records" not in probe.calls


async def test_cancelling_stops_the_case_without_asking_support() -> None:
    client = await Client.connect(_TEMPORAL_TARGET)
    probe = _Probe()
    queue, handle = await _start(client, probe, _case_input(timings=_timings(bay_wait_seconds=30)))

    async with Worker(
        client, task_queue=queue, workflows=(ReturnCaseWorkflow,), activities=probe.all()
    ):
        await probe.reached("request_bay_assignment", within_seconds=20)
        await handle.signal(
            ReturnCaseWorkflow.cancel_case, CancelCaseCommand(reason="customer changed their mind")
        )
        outcome = await handle.result()

    assert outcome.status == "CANCELLED"
    assert "open_support_work_item" not in probe.calls
