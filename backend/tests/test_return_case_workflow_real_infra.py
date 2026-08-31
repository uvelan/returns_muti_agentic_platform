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

WHY THIS FILE OWNS ITS EXECUTIONS
---------------------------------
It used to fail one test per whole-file run -- a different one each time, all
of them passing in isolation, reproducible on a pristine tree. That is the
signature of load rather than of a defect in what is under test, and the load
was this suite's own litter.

Two leaks, both measured on the shared dev server. Every test opened its own
`Client.connect` and closed none, so a run left thirteen gRPC connections
behind. And `_start` began an execution that only some tests ran to completion,
on a task queue minted fresh per test, and nothing ever terminated the rest --
so each run added orphaned *running* executions, each holding a task queue the
matching service goes on carrying. By the time this was diagnosed the `default`
namespace held **299 executions, 168 of them still Running**, several hours old
and spread across every real-infra module in the suite. A workflow task on a
queue nobody polls is not free, and the accumulated cost lands on whichever
test happens to be waiting when the server is slowest. Hence a different test
each run.

So: one client for the module, and every execution terminated after the test
that started it. A run now leaves nothing Running behind, measured rather than
assumed.

**A dedicated namespace was tried and rejected on evidence.** It is the obvious
isolation, and on this server it made every test in the file five to sixteen
times slower -- 3.5s to 47s for the duplicate-response case, 1.1s to 18s for
cancellation -- turning an occasional timeout into a reliable one. Whatever a
freshly registered namespace costs here, it costs more than the contention it
avoids. Left recorded so the next person does not spend the same afternoon
finding out.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from temporalio import activity
from temporalio.client import Client
from temporalio.worker import Worker

from return_platform.workflows.return_case_workflow import (
    BayResultNotice,
    CancelCaseCommand,
    CaseEligibilityOutcome,
    DraftSupportRequestInput,
    EvaluateCaseEligibilityInput,
    OpenSupportWorkItemInput,
    PolicyDecisionName,
    PolicyGateState,
    PolicyRouteName,
    RecordCaseCustomerInput,
    RecordCaseStatusInput,
    RecordSupportOutcomeInput,
    RequestBayAssignmentInput,
    ResolveBusinessDeadlineInput,
    ResolvedBusinessDeadline,
    ReturnCaseTimings,
    ReturnCaseWorkflow,
    ReturnCaseWorkflowInput,
    SendSupportReminderInput,
    SupportRequestDraft,
    SupportResponseNotice,
    SupportReturnRecord,
    SynchronizeReturnRecordsInput,
)
from tests.workflow_result import result_within

#: Module-scoped loop, so one client can serve every test in the file.
pytestmark = pytest.mark.asyncio(loop_scope="module")

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
        #: What `request_bay_assignment` answers with. None until a scenario
        #: sets one -- see the activity below.
        self.bay_notice: BayResultNotice | None = None
        #: When set, every business deadline resolves this many seconds after
        #: the instant it was asked from, whatever duration was requested --
        #: which is what a calendar does when the desk is shut. None means the
        #: requested duration is used unchanged, i.e. wall clock.
        self.deadline_seconds: float | None = None
        self.deadline_requests: list[ResolveBusinessDeadlineInput] = []
        self.sync_should_fail = False
        self.graph_generation_id = "gen-under-test"
        #: What the policy gate answers. Approving by default, because every
        #: scenario in this file is about what happens *after* a return is
        #: allowed through to Support.
        self.eligibility = CaseEligibilityOutcome(
            state=PolicyGateState.EVALUATED.value,
            route=PolicyRouteName.STANDARD_RETURN.value,
            decision=PolicyDecisionName.APPROVE.value,
        )
        self.evaluations: list[EvaluateCaseEligibilityInput] = []

    @activity.defn(name="record_case_status")
    async def record_case_status(self, request: RecordCaseStatusInput) -> None:
        self._record("record_case_status")
        self.statuses.append(request.status)

    @activity.defn(name="record_case_customer_identity")
    async def record_case_customer_identity(self, request: RecordCaseCustomerInput) -> bool:
        del request
        self._record("record_case_customer_identity")
        return True

    @activity.defn(name="request_bay_assignment")
    async def request_bay_assignment(
        self, request: RequestBayAssignmentInput
    ) -> BayResultNotice | None:
        del request
        self._record("request_bay_assignment")
        if self.bay_should_fail:
            raise RuntimeError("warehouse service unavailable")
        # None is still a valid answer and stays exercised: it is what a worker
        # registered without placement produced before BAY-01, and the workflow
        # must go on treating "the activity said nothing" as "wait for a
        # signal, then proceed" rather than as a result.
        return self.bay_notice

    @activity.defn(name="resolve_business_deadline")
    async def resolve_business_deadline(
        self, request: ResolveBusinessDeadlineInput
    ) -> ResolvedBusinessDeadline:
        self._record("resolve_business_deadline")
        self.deadline_requests.append(request)
        seconds = (
            request.working_seconds if self.deadline_seconds is None else self.deadline_seconds
        )
        return ResolvedBusinessDeadline(
            instant_iso=(
                datetime.fromisoformat(request.from_iso) + timedelta(seconds=seconds)
            ).isoformat(),
            calendar_applied=self.deadline_seconds is not None,
        )

    @activity.defn(name="evaluate_case_eligibility")
    async def evaluate_case_eligibility(
        self, request: EvaluateCaseEligibilityInput
    ) -> CaseEligibilityOutcome:
        """Approve, so that the scenarios below still reach Support.

        The gate (3A.7) sits between the bay step and `_open_support`, so every
        case in this file now passes through it. What this file is about is the
        Support wait and its durability, so the eligibility answer is a constant
        here and the gate's own branching is tested in
        `test_return_case_policy_gate_real_infra.py` and in the normal suite.
        """
        self._record("evaluate_case_eligibility")
        self.evaluations.append(request)
        return self.eligibility

    @activity.defn(name="draft_support_request")
    async def draft_support_request(self, request: DraftSupportRequestInput) -> SupportRequestDraft:
        del request
        self._record("draft_support_request")
        # Returns what the activity returns. This used to answer `str`, which
        # the activity stopped doing when the draft grew a payload and a
        # subject -- and because the workflow decodes the result into
        # `SupportRequestDraft`, the double wedged every case that reached
        # Support rather than failing one assertion.
        return SupportRequestDraft(
            text="Hello -- could you raise the RMA for this return?",
            payload={},
            subject="Return",
        )

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
            self.record_case_customer_identity,
            self.resolve_business_deadline,
            self.request_bay_assignment,
            self.evaluate_case_eligibility,
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


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def client() -> AsyncIterator[Client]:
    """One client for the whole module.

    One rather than thirteen because none of the thirteen was ever closed, and
    a gRPC connection per test is thirteen live connections by the end of a run
    in a process that is also starting and stopping thirteen workers.
    """
    yield await Client.connect(_TEMPORAL_TARGET)


@pytest_asyncio.fixture(autouse=True, loop_scope="module")
async def _terminate_started_executions() -> AsyncIterator[None]:
    """Leave no execution running behind a test.

    The tests that end in `await result_within(handle)` close their own; the ones
    that assert on a *waiting* case do not, and a failing test never reaches
    its assertions at all. Without this each run left orphans on the server,
    and 168 of them were what made this file flaky.
    """
    _STARTED.clear()
    yield
    for handle in _STARTED:
        try:
            await handle.terminate("test cleanup")
        except Exception:  # noqa: BLE001 - already closed, or never started
            pass
    _STARTED.clear()


#: Handles started by `_start` during the current test.
_STARTED: list[Any] = []


async def _start(client: Client, probe: _Probe, workflow_input: ReturnCaseWorkflowInput) -> Any:
    del probe
    queue = f"test-return-case-{uuid.uuid4().hex[:8]}"
    handle = await client.start_workflow(
        ReturnCaseWorkflow.run,
        workflow_input,
        id=f"test-return-case-{uuid.uuid4().hex[:8]}",
        task_queue=queue,
    )
    _STARTED.append(handle)
    return queue, handle


async def test_the_case_completes_when_support_answers(client: Client) -> None:
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
        outcome = await result_within(handle)

    assert outcome.status == "RMA_RECEIVED"
    assert outcome.return_references == ("RMA-1",)
    # Bay was asked for and did not answer within its window; the case went on.
    assert "request_bay_assignment" in probe.calls
    assert probe.calls.index("request_bay_assignment") < probe.calls.index("open_support_work_item")


async def test_the_support_wait_survives_a_worker_restart(client: Client) -> None:
    """The step this workflow exists for.

    The worker is stopped while the case is waiting on Support and started
    again. If the wait lived in a coroutine it would be gone; because it is a
    Temporal timer, the case is still waiting and still answerable.
    """
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
        outcome = await result_within(handle)

    assert outcome.return_references == ("RMA-AFTER-RESTART",)
    # The second worker never re-opened the thread: the work item survived.
    assert "open_support_work_item" not in resumed.calls


async def test_reminders_fire_on_the_configured_cadence_and_then_park(client: Client) -> None:
    """A cap with no terminal branch leaves a case waiting forever, unseen."""
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
        outcome = await result_within(handle)

    assert outcome.reminders_sent == 2
    assert outcome.parked_reason == "SUPPORT_REMINDERS_EXHAUSTED"
    # Numbered, so a retry re-sends the same reminder rather than adding one to
    # a person's queue.
    assert probe.reminder_keys == sorted(set(probe.reminder_keys))
    assert len(set(probe.reminder_keys)) == 2


async def test_a_bay_failure_does_not_stop_the_return(client: Client) -> None:
    """Bay is best-effort. `orchestrator._handle` used to let it fail the case."""
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
        outcome = await result_within(handle)

    assert outcome.status == "RMA_RECEIVED"
    assert outcome.bay_reference is None


async def test_a_bay_result_arriving_before_the_wait_is_kept(client: Client) -> None:
    """Signals can land before the workflow reaches the wait for them."""
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
        outcome = await result_within(handle)

    # It did not sit out the 30-second bay wait, and it kept the answer.
    assert outcome.bay_reference == "BAY-7"


async def test_the_bay_activity_answers_and_no_signal_is_needed(client: Client) -> None:
    """BAY-01's connection, at the workflow boundary.

    `request_bay_assignment` used to return nothing, so every case waited out
    `bay_wait_seconds` for a `bay_result` signal that no production component
    sent, and then proceeded with no bay. The activity now answers, and the
    result is the case's bay without anyone signalling anything.

    `bay_wait_seconds` is 30 here and the test does not take 30 seconds: that
    is the assertion. A workflow still waiting for a signal would.
    """
    probe = _Probe()
    probe.bay_notice = BayResultNotice(
        warehouse_reference="WH-1",
        bay_reference="BAY-9",
        reason="RECOMMENDED",
        return_location="WH-1/BAY-9",
        confidence_millionths=750_000,
        explanation="The recommendation is advisory.",
        evidence_reference="WAREHOUSE_OBSERVED:gen-1:3",
        graph_generation_id="gen-1",
    )
    queue, handle = await _start(client, probe, _case_input(timings=_timings(bay_wait_seconds=30)))

    async with Worker(
        client, task_queue=queue, workflows=(ReturnCaseWorkflow,), activities=probe.all()
    ):
        await probe.reached("open_support_work_item", within_seconds=20)
        state = await handle.query(ReturnCaseWorkflow.execution_state)
        assert state.bay_resolved is True
        assert state.bay_reference == "BAY-9"
        await handle.signal(
            ReturnCaseWorkflow.support_response,
            SupportResponseNotice(
                work_item_id="wi-1", records=(SupportReturnRecord(return_reference="RMA-1"),)
            ),
        )
        outcome = await result_within(handle)

    assert outcome.bay_reference == "BAY-9"


async def test_a_signal_that_won_the_race_is_not_overwritten_by_the_activity(
    client: Client,
) -> None:
    """First answer wins, as `bay_result` already decided it.

    Both an early signal and the activity's own result are now possible for one
    case. If the activity overwrote what the signal had already established,
    the case's bay would depend on scheduling.
    """
    probe = _Probe()
    probe.bay_notice = BayResultNotice(warehouse_reference="WH-2", bay_reference="BAY-LATE")
    queue, handle = await _start(client, probe, _case_input(timings=_timings(bay_wait_seconds=30)))

    await handle.signal(
        ReturnCaseWorkflow.bay_result,
        BayResultNotice(warehouse_reference="WH-1", bay_reference="BAY-FIRST"),
    )
    async with Worker(
        client, task_queue=queue, workflows=(ReturnCaseWorkflow,), activities=probe.all()
    ):
        await probe.reached("open_support_work_item", within_seconds=20)
        await handle.signal(
            ReturnCaseWorkflow.support_response,
            SupportResponseNotice(
                work_item_id="wi-1", records=(SupportReturnRecord(return_reference="RMA-1"),)
            ),
        )
        outcome = await result_within(handle)

    assert outcome.bay_reference == "BAY-FIRST"


async def test_a_duplicate_support_response_does_not_issue_a_second_set_of_rmas(
    client: Client,
) -> None:
    """Support clicking send twice, or a transport redelivering."""
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
        outcome = await result_within(handle)

    assert outcome.return_references == ("RMA-1",)
    assert len(probe.outcomes) == 1


async def test_the_rma_reaches_the_graph_before_the_case_reports_it_received(
    client: Client,
) -> None:
    """W2.5. The RMA is written to Mongo and then to the graph, in that order.

    The agent answering the associate's next turn reads the graph. Reporting
    `RMA_RECEIVED` before the record is projected would make "Support answered"
    true on the case and false everywhere an associate can see it.

    The sync is record-scoped: the ids are the ones `record_support_outcome`
    was given, not a request to re-sync the collection.
    """
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
        outcome = await result_within(handle)

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


async def test_a_graph_sync_failure_parks_the_case_loudly(client: Client) -> None:
    """Return Workflow is `blocking`, so this failure is not stepped over.

    The RMA exists in the platform store and in no graph any agent reads. A case
    reported `RMA_RECEIVED` in that state would have Order Discovery telling the
    associate on their next turn that no return exists -- worse than a parked
    case somebody can see and retry.
    """
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
        outcome = await result_within(handle)

    assert outcome.parked_reason == "RETURN_GRAPH_SYNC_FAILED"
    assert outcome.graph_generation_id is None
    # Not RMA_RECEIVED: the platform cannot claim a state whose evidence an
    # associate would be shown from the graph.
    assert outcome.status != "RMA_RECEIVED"
    assert "RETURN_GRAPH_SYNC_FAILED" in probe.statuses or outcome.status == "AWAITING_SUPPORT"


async def test_a_rejected_return_needs_no_graph_sync(client: Client) -> None:
    """Support declining issues no RMA, so there is nothing to project.

    Syncing an empty record set would fail loudly for no reason and park a case
    that reached a legitimate terminal state.
    """
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
        outcome = await result_within(handle)

    assert outcome.status == "CLOSED"
    assert "synchronize_return_records" not in probe.calls


async def test_cancelling_stops_the_case_without_asking_support(client: Client) -> None:
    probe = _Probe()
    queue, handle = await _start(client, probe, _case_input(timings=_timings(bay_wait_seconds=30)))

    async with Worker(
        client, task_queue=queue, workflows=(ReturnCaseWorkflow,), activities=probe.all()
    ):
        await probe.reached("request_bay_assignment", within_seconds=20)
        await handle.signal(
            ReturnCaseWorkflow.cancel_case, CancelCaseCommand(reason="customer changed their mind")
        )
        outcome = await result_within(handle)

    assert outcome.status == "CANCELLED"
    assert "open_support_work_item" not in probe.calls


async def test_the_wait_counts_business_time_not_wall_clock(client: Client) -> None:
    """SLA-01 at the workflow boundary, and the Friday-evening case in miniature.

    The reminder interval is one second, so wall-clock arithmetic would nudge
    Support immediately. The calendar says the desk is shut for an hour, and
    the workflow must honour the instant the activity returned rather than the
    duration it asked about -- which is the whole of what was broken: the
    duration was always right and nothing ever converted it.
    """
    probe = _Probe()
    probe.deadline_seconds = 3600.0
    queue, handle = await _start(
        client,
        probe,
        _case_input(
            timings=_timings(
                bay_wait_seconds=0,
                support_response_wait_seconds=7200,
                reminder_interval_seconds=1,
            )
        ),
    )

    async with Worker(
        client, task_queue=queue, workflows=(ReturnCaseWorkflow,), activities=probe.all()
    ):
        await probe.reached("open_support_work_item", within_seconds=20)
        # Long enough that a one-second wall-clock interval would have fired
        # several times over.
        await asyncio.sleep(5)
        state = await handle.query(ReturnCaseWorkflow.execution_state)
        assert state.reminders_sent == 0, "a shut desk was nudged anyway"
        assert "send_support_reminder" not in probe.calls

        await handle.signal(
            ReturnCaseWorkflow.support_response,
            SupportResponseNotice(
                work_item_id="wi-1", records=(SupportReturnRecord(return_reference="RMA-1"),)
            ),
        )
        outcome = await result_within(handle)

    assert outcome.reminders_sent == 0
    # The support wait and the reminder cadence both went through the calendar,
    # carrying the configured calendar id rather than a hardcoded one.
    assert [request.working_seconds for request in probe.deadline_requests][:2] == [7200, 1]
    assert {request.business_calendar_id for request in probe.deadline_requests} == {"default"}
