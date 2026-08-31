"""The policy gate against a real Temporal server.

`tests/policy/test_case_policy_gate.py` proves what the gate *decides*, with the
real evaluator and the real rule set, in the normal suite. This file proves the
two things a substituted runtime cannot:

* the supervisor wait is **durable** -- the worker is stopped mid-review and
  started again, and the case is still waiting and still answerable; and
* the gate behaves the same way when a real workflow task, a real signal and a
  real activity dispatch sit between the branches.

Activities are probes here, exactly as in `test_return_case_workflow_real_infra`
and for the same reason: what is under test is the workflow's own coordination,
and pointing it at Mongo and the evaluator would make a slow test of those
instead.

Follows that file's litter discipline to the letter -- one client for the
module, every execution terminated after the test that started it. A run must
leave nothing Running behind; 168 orphans are what made the sibling file flaky.
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
    CaseEligibilityOutcome,
    ClarificationAnswerResult,
    ClarificationRelayView,
    DraftSupportRequestInput,
    EvaluateCaseEligibilityInput,
    HoldUnsettledReviewsResult,
    OpenSupportWorkItemInput,
    PolicyDecisionName,
    PolicyGateState,
    PolicyOverrideNotice,
    PolicyRouteName,
    RecordCaseCustomerInput,
    RecordCaseStatusInput,
    RecordSupportOutcomeInput,
    RequestBayAssignmentInput,
    ResolveBusinessDeadlineInput,
    ResolvedBusinessDeadline,
    ReturnCaseStatus,
    ReturnCaseTimings,
    ReturnCaseWorkflow,
    ReturnCaseWorkflowInput,
    SendSupportReminderInput,
    SupportRequestDraft,
    SupportResponseNotice,
    SupportReturnRecord,
    SynchronizeReturnRecordsInput,
    TemplateDeliveryResult,
    TemplateReviewDraftResult,
    TemplateReviewDraftSet,
)
from tests.activity_probe import declared_activities
from tests.workflow_result import result_within

pytestmark = pytest.mark.asyncio(loop_scope="module")

_TEMPORAL_TARGET = os.getenv("PLATFORM_TEST_TEMPORAL_TARGET", "localhost:7233")


class _Probe:
    """Records what the workflow asked for, and answers the gate however a test says.

    `work_items` is a dictionary rather than a counter on purpose: the claim this
    file exists to check is about the *collection* of open Support threads, and a
    call count cannot distinguish an attempt that was refused from one that was
    never made.
    """

    def __init__(self, outcome: CaseEligibilityOutcome) -> None:
        self.calls: list[str] = []
        self._reached: dict[str, asyncio.Event] = {}
        self.statuses: list[str] = []
        self.work_items: dict[str, dict[str, Any]] = {}
        self.outcome = outcome
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
        return None

    @activity.defn(name="evaluate_case_eligibility")
    async def evaluate_case_eligibility(
        self, request: EvaluateCaseEligibilityInput
    ) -> CaseEligibilityOutcome:
        self._record("evaluate_case_eligibility")
        self.evaluations.append(request)
        return self.outcome

    @activity.defn(name="resolve_business_deadline")
    async def resolve_business_deadline(
        self, request: ResolveBusinessDeadlineInput
    ) -> ResolvedBusinessDeadline:
        self._record("resolve_business_deadline")
        return ResolvedBusinessDeadline(
            instant_iso=(
                datetime.fromisoformat(request.from_iso)
                + timedelta(seconds=request.working_seconds)
            ).isoformat(),
            calendar_applied=False,
        )

    @activity.defn(name="draft_support_request")
    async def draft_support_request(
        self, request: DraftSupportRequestInput
    ) -> SupportRequestDraft:
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
        work_item_id = f"wi-{request.case_id}"
        self.work_items[work_item_id] = {
            "caseId": request.case_id,
            "queue": request.queue or "RETURNS_SUPPORT",
        }
        return work_item_id

    @activity.defn(name="send_support_reminder")
    async def send_support_reminder(self, request: SendSupportReminderInput) -> None:
        del request
        self._record("send_support_reminder")

    @activity.defn(name="record_support_outcome")
    async def record_support_outcome(self, request: RecordSupportOutcomeInput) -> None:
        del request
        self._record("record_support_outcome")

    @activity.defn(name="synchronize_return_records")
    async def synchronize_return_records(self, request: SynchronizeReturnRecordsInput) -> str:
        del request
        self._record("synchronize_return_records")
        return "gen-under-test"

    # The review gate, the clarification round-trip and the return-details poll.
    # Registered on the same reasoning `worker.py` states for its own list: what
    # a case runs is decided by its pinned release, not by what this file's
    # scenarios happen to reach, and a worker missing an activity leaves such a
    # case stopped with nothing raised and nothing logged. The gate answers "no
    # template published" so an approved case takes the composed path, which is
    # what this file's approval scenarios asserted before the gate existed.

    @activity.defn(name="record_template_draft")
    async def record_template_draft(self, request: Any) -> TemplateReviewDraftSet:
        del request
        self._record("record_template_draft")
        return TemplateReviewDraftSet(drafts=(), template_available=False)

    @activity.defn(name="rerender_template_draft")
    async def rerender_template_draft(self, request: Any) -> TemplateReviewDraftResult:
        self._record("rerender_template_draft")
        return TemplateReviewDraftResult(
            request_id=request.request_id, review_id=request.review_id, state="DRAFT"
        )

    @activity.defn(name="record_template_revision")
    async def record_template_revision(self, request: Any) -> None:
        del request
        self._record("record_template_revision")

    @activity.defn(name="hold_unsettled_reviews")
    async def hold_unsettled_reviews(self, request: Any) -> HoldUnsettledReviewsResult:
        del request
        self._record("hold_unsettled_reviews")
        return HoldUnsettledReviewsResult(held_review_ids=())

    @activity.defn(name="snapshot_sent_template")
    async def snapshot_sent_template(self, request: Any) -> TemplateDeliveryResult:
        self._record("snapshot_sent_template")
        return TemplateDeliveryResult(
            review_id=request.review_id, state="SENT", work_item_id=f"wi-{request.case_id}"
        )

    @activity.defn(name="record_clarification_answer")
    async def record_clarification_answer(self, request: Any) -> ClarificationAnswerResult:
        del request
        self._record("record_clarification_answer")
        return ClarificationAnswerResult(recorded=True)

    @activity.defn(name="relay_clarification_to_support")
    async def relay_clarification_to_support(self, request: Any) -> ClarificationRelayView:
        self._record("relay_clarification_to_support")
        return ClarificationRelayView(
            delivery_id=f"dlv-{request.clarification_id}",
            message_id=f"msg-{request.clarification_id}",
        )

    @activity.defn(name="case_has_return_details")
    async def case_has_return_details(self, request: Any) -> bool:
        del request
        self._record("case_has_return_details")
        return True

    def _record(self, name: str) -> None:
        self.calls.append(name)
        self._reached.setdefault(name, asyncio.Event()).set()

    async def reached(self, name: str, *, within_seconds: float = 30.0) -> None:
        event = self._reached.setdefault(name, asyncio.Event())
        try:
            async with asyncio.timeout(within_seconds):
                await event.wait()
        except TimeoutError:
            raise AssertionError(f"{name} did not run within {within_seconds}s") from None

    def all(self) -> tuple[Any, ...]:
        """Derived, not listed -- see `tests/activity_probe.py`.

        This probe carried the same stale tuple of ten as its sibling in
        `test_return_case_workflow_real_infra.py`. It stayed green only because
        an approved case here is the exception rather than the rule, so most of
        its scenarios stop at the gate and never reach the activities it was
        missing. Green because the scenarios could not exercise the property.
        """
        return declared_activities(self)


def _timings(**overrides: Any) -> ReturnCaseTimings:
    base: dict[str, Any] = {
        "bay_wait_seconds": 0,
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


def _evaluated(
    *, decision: str | None = None, route: str = PolicyRouteName.STANDARD_RETURN.value, **extra: Any
) -> CaseEligibilityOutcome:
    return CaseEligibilityOutcome(
        state=PolicyGateState.EVALUATED.value, route=route, decision=decision, **extra
    )


#: Handles started during the current test.
_STARTED: list[Any] = []


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def client() -> AsyncIterator[Client]:
    yield await Client.connect(_TEMPORAL_TARGET)


@pytest_asyncio.fixture(autouse=True, loop_scope="module")
async def _terminate_started_executions() -> AsyncIterator[None]:
    _STARTED.clear()
    yield
    for handle in _STARTED:
        try:
            await handle.terminate("test cleanup")
        except Exception:  # noqa: BLE001 - already closed, or never started
            pass
    _STARTED.clear()


async def _start(client: Client, workflow_input: ReturnCaseWorkflowInput) -> Any:
    queue = f"test-policy-gate-{uuid.uuid4().hex[:8]}"
    handle = await client.start_workflow(
        ReturnCaseWorkflow.run,
        workflow_input,
        id=f"test-policy-gate-{uuid.uuid4().hex[:8]}",
        task_queue=queue,
    )
    _STARTED.append(handle)
    return queue, handle


async def test_a_rejected_return_opens_no_support_work_item(client: Client) -> None:
    """The central claim, through a real workflow task and a real dispatch."""
    probe = _Probe(_evaluated(decision=PolicyDecisionName.REJECT.value))
    queue, handle = await _start(client, _case_input())

    async with Worker(
        client, task_queue=queue, workflows=(ReturnCaseWorkflow,), activities=probe.all()
    ):
        outcome = await result_within(handle)

    assert probe.work_items == {}, "a rejected return opened a Support work item"
    assert outcome.status == ReturnCaseStatus.POLICY_REJECTED.value
    assert outcome.work_item_id is None
    assert "open_support_work_item" not in probe.calls
    assert probe.calls.index("evaluate_case_eligibility") < len(probe.calls)


async def test_an_approved_return_reaches_support(client: Client) -> None:
    probe = _Probe(_evaluated(decision=PolicyDecisionName.APPROVE.value))
    queue, handle = await _start(client, _case_input())

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

    assert len(probe.work_items) == 1
    assert ReturnCaseStatus.POLICY_APPROVED.value in probe.statuses
    assert probe.calls.index("evaluate_case_eligibility") < probe.calls.index(
        "open_support_work_item"
    )
    assert outcome.policy_decision == PolicyDecisionName.APPROVE.value


@pytest.mark.parametrize(
    ("route", "queue_name"),
    [
        (PolicyRouteName.WARRANTY.value, "WARRANTY_SUPPORT"),
        (PolicyRouteName.DELIVERY_CLAIM.value, "DELIVERY_CLAIM_SUPPORT"),
    ],
)
async def test_a_routed_case_opens_a_work_item_on_its_own_queue(
    client: Client, route: str, queue_name: str
) -> None:
    """Not terminal, and carrying no decision. Support verifies it."""
    probe = _Probe(_evaluated(route=route, support_queue=queue_name))
    queue, handle = await _start(client, _case_input())

    async with Worker(
        client, task_queue=queue, workflows=(ReturnCaseWorkflow,), activities=probe.all()
    ):
        await probe.reached("open_support_work_item")
        state = await handle.query(ReturnCaseWorkflow.execution_state)
        assert state.status == ReturnCaseStatus.AWAITING_SUPPORT.value
        assert state.policy_route == route
        assert state.policy_decision is None
        await handle.signal(
            ReturnCaseWorkflow.support_response,
            SupportResponseNotice(
                work_item_id="wi-1", records=(SupportReturnRecord(return_reference="RMA-1"),)
            ),
        )
        await result_within(handle)

    (work_item,) = probe.work_items.values()
    assert work_item["queue"] == queue_name


async def test_an_absent_policy_parks_the_case_and_asks_nobody(client: Client) -> None:
    probe = _Probe(
        CaseEligibilityOutcome(
            state=PolicyGateState.POLICY_NOT_CONFIGURED.value,
            failure_reason="RETURN_ELIGIBILITY_POLICY_NOT_PUBLISHED",
        )
    )
    queue, handle = await _start(client, _case_input())

    async with Worker(
        client, task_queue=queue, workflows=(ReturnCaseWorkflow,), activities=probe.all()
    ):
        outcome = await result_within(handle)

    assert outcome.status == ReturnCaseStatus.RECOVERY_REQUIRED.value
    assert outcome.parked_reason == "RETURN_ELIGIBILITY_POLICY_NOT_PUBLISHED"
    assert probe.work_items == {}


async def test_the_policy_review_wait_survives_a_worker_restart(client: Client) -> None:
    """The step this gate borrows the Support wait's machinery for.

    The worker is stopped while a supervisor has not yet answered, and started
    again. If the wait lived in a coroutine it would be gone; because it is a
    Temporal timer, the case is still `AWAITING_POLICY_REVIEW`, still holds no
    work item, and is still answerable by an override.
    """
    outcome_probe = _evaluated(decision=PolicyDecisionName.REVIEW_REQUIRED.value)
    probe = _Probe(outcome_probe)
    queue, handle = await _start(
        client,
        _case_input(
            timings=_timings(support_response_wait_seconds=300, reminder_interval_seconds=300)
        ),
    )

    async with Worker(
        client, task_queue=queue, workflows=(ReturnCaseWorkflow,), activities=probe.all()
    ):
        await probe.reached("evaluate_case_eligibility")

    resumed = _Probe(outcome_probe)
    async with Worker(
        client, task_queue=queue, workflows=(ReturnCaseWorkflow,), activities=resumed.all()
    ):
        state = await handle.query(ReturnCaseWorkflow.execution_state)
        assert state.status == ReturnCaseStatus.AWAITING_POLICY_REVIEW.value
        assert state.work_item_id is None, "a case awaiting review had a Support thread open"
        assert state.policy_overridden is False

        await handle.signal(
            ReturnCaseWorkflow.policy_override,
            PolicyOverrideNotice(
                override_decision=PolicyDecisionName.APPROVE.value,
                reason_code="SUPERVISOR_JUDGEMENT",
                actor="supervisor-1",
                overridden_at_iso="2026-08-15T12:00:00+00:00",
                idempotency_key="override-key-1",
            ),
        )
        await resumed.reached("open_support_work_item")
        await handle.signal(
            ReturnCaseWorkflow.support_response,
            SupportResponseNotice(
                work_item_id="wi-1", records=(SupportReturnRecord(return_reference="RMA-1"),)
            ),
        )
        outcome = await result_within(handle)

    assert outcome.policy_overridden is True
    assert len(resumed.work_items) == 1
    # The second worker never re-evaluated: the review survived the restart as a
    # wait rather than as a question to ask again.
    assert "evaluate_case_eligibility" not in resumed.calls


async def test_an_unanswered_review_parks_without_asking_support(client: Client) -> None:
    probe = _Probe(_evaluated(decision=PolicyDecisionName.REVIEW_REQUIRED.value))
    queue, handle = await _start(
        client,
        _case_input(
            timings=_timings(
                support_response_wait_seconds=4, reminder_interval_seconds=1, max_reminders=1
            )
        ),
    )

    async with Worker(
        client, task_queue=queue, workflows=(ReturnCaseWorkflow,), activities=probe.all()
    ):
        outcome = await result_within(handle)

    assert outcome.status == ReturnCaseStatus.AWAITING_POLICY_REVIEW.value
    assert outcome.parked_reason == "POLICY_REVIEW_UNANSWERED"
    assert probe.work_items == {}
    # No Channel B thread exists to remind anyone on, so none was attempted.
    assert "send_support_reminder" not in probe.calls


async def test_cancelling_during_review_stops_the_case(client: Client) -> None:
    from return_platform.workflows.return_case_workflow import CancelCaseCommand

    probe = _Probe(_evaluated(decision=PolicyDecisionName.REVIEW_REQUIRED.value))
    queue, handle = await _start(
        client,
        _case_input(
            timings=_timings(support_response_wait_seconds=300, reminder_interval_seconds=300)
        ),
    )

    async with Worker(
        client, task_queue=queue, workflows=(ReturnCaseWorkflow,), activities=probe.all()
    ):
        await probe.reached("evaluate_case_eligibility")
        await handle.signal(
            ReturnCaseWorkflow.cancel_case, CancelCaseCommand(reason="customer changed their mind")
        )
        outcome = await result_within(handle)

    assert outcome.status == ReturnCaseStatus.CANCELLED.value
    assert probe.work_items == {}
