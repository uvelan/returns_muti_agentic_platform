"""The durable owner of one return case.

`ReturnWorkflow` sequences code-owned stages and is unchanged by this module --
it stays the stage subflow. What did not exist was anything owning the *case*:
the bay wait, the Support wait and the reminder cadence were absent entirely
(`reminder` appeared nowhere in the codebase), and the only implemented Support
wait was `asyncio.sleep` twelve times at five seconds -- sixty seconds, against
an SLA measured in business days, in a coroutine that a restart forgets.

Everything time-shaped here is a Temporal durable timer, so a worker restart
mid-wait resumes rather than abandons.

**Determinism.** The workflow body performs no IO, calls no model, and reads no
clock other than `workflow.now()`. Every wall-clock decision is a
`wait_condition` timeout; every side effect is an activity. Timings are pinned
onto the workflow input at start (see `ReturnCaseTimings`) rather than read from
configuration here: configuration is IO, and a deadline that moved under a
return already waiting on it would be a worse bug than a stale one.

**Failure policy.** Bay is `best_effort`: its activity is dispatched with a
retry policy and its failure is recorded and stepped over. Support is on the
critical path; a failure there parks the case for an operator instead of
completing it silently. The graph sync that follows the return record is
`blocking` for the same reason Support is: an RMA that exists in the store and
not in the graph is one no agent can tell an associate about.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

__all__ = [
    "BayResultNotice",
    "CancelCaseCommand",
    "DraftSupportRequestInput",
    "OpenSupportWorkItemInput",
    "RecordCaseStatusInput",
    "RecordSupportOutcomeInput",
    "RequestBayAssignmentInput",
    "ResolveBusinessDeadlineInput",
    "ResolvedBusinessDeadline",
    "ReturnCaseOutcome",
    "ReturnCaseState",
    "ReturnCaseTimings",
    "ReturnCaseWorkflow",
    "ReturnCaseWorkflowInput",
    "SendSupportReminderInput",
    "SupportResponseNotice",
    "SupportReturnRecord",
    "SynchronizeReturnRecordsInput",
    "return_case_workflow_id",
]


def return_case_workflow_id(case_id: str) -> str:
    """The workflow execution that owns a case.

    Derived rather than stored so any process holding a case id can reach its
    workflow without a lookup -- the Support console signalling an outcome is
    the caller this exists for. Mirrors `_order_discovery_workflow_id`.
    """
    return f"return-case-{case_id}"


# Persistence activities. Short, because they are a Mongo write each.
_PERSIST_TIMEOUT: Final = timedelta(seconds=30)
# Drafting invokes a model through the shared route pool, which has its own
# global deadline; this is the outer bound, not the budget.
_DRAFT_TIMEOUT: Final = timedelta(minutes=5)
# A record-scoped graph sync is a source read plus a Neo4j write per record, and
# a case can carry several RMAs. Longer than a Mongo write, far short of the
# draft budget: this sits on the critical path with the associate waiting.
_SYNC_TIMEOUT: Final = timedelta(minutes=2)

# Persistence is idempotent (unique keys throughout), so retrying a transient
# Mongo blip is safe and is the difference between a hiccup and a parked case.
_PERSIST_RETRY: Final = RetryPolicy(maximum_attempts=5)
# Drafting is not idempotent in cost -- each attempt is a paid model call -- so
# it retries far less eagerly, and a persistent failure falls back rather than
# hammering the provider.
_DRAFT_RETRY: Final = RetryPolicy(maximum_attempts=2)
# Bay is advisory, and it sits in front of every return. Retrying it on the
# persistence policy meant five attempts with exponential backoff -- roughly
# fifteen seconds added to the critical path before the workflow could conclude
# what it already knew: there is no bay, carry on. Two attempts cover a genuine
# blip; anything past that is a warehouse service that is down, and waiting
# longer does not make one appear.
_BEST_EFFORT_RETRY: Final = RetryPolicy(maximum_attempts=2)


class ReturnCaseStatus(StrEnum):
    """Mirrors `operations.models.CaseStatus`.

    Redeclared rather than imported: workflow code is replayed against whatever
    version of the module is deployed, and importing the operations package
    into a workflow would drag Mongo, pydantic settings and the repository into
    the sandbox with it.
    """

    GATHERING_INFO = "GATHERING_INFO"
    AWAITING_BAY = "AWAITING_BAY"
    AWAITING_SUPPORT = "AWAITING_SUPPORT"
    RMA_RECEIVED = "RMA_RECEIVED"
    IN_TRANSIT = "IN_TRANSIT"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class ReturnCaseTimings:
    """Pinned at start, never re-read.

    A configuration release that changes these applies to cases started after
    it. Re-reading mid-flight would move a deadline under a return already
    waiting on it, and an operator watching a countdown would see it jump.
    """

    bay_wait_seconds: int
    support_response_wait_seconds: int
    reminder_interval_seconds: int
    max_reminders: int
    on_reminders_exhausted: str
    business_calendar_id: str
    timezone: str


@dataclass(frozen=True, slots=True)
class ReturnCaseWorkflowInput:
    """Business-data-free. Identifiers and policy only.

    The case document is the record of what the return *is*; carrying a copy on
    the workflow input would create a second version of it that history would
    then preserve forever, including whatever customer data it held.
    """

    case_id: str
    tenant_id: str
    principal_id: str
    conversation_id: str
    configuration_release_id: str
    timings: ReturnCaseTimings
    # Carried across a continue_as_new boundary so a history reset is invisible.
    resumed_status: str | None = None
    resumed_work_item_id: str | None = None
    reminders_sent: int = 0
    #: The support deadline, once resolved. Carried across `continue_as_new`
    #: for the same reason `reminders_sent` is: recomputing it on the far side
    #: would grant a fresh full wait every time history was reset, and a case
    #: that resets often would never reach its cap.
    resumed_support_deadline_iso: str | None = None


@dataclass(frozen=True, slots=True)
class BayResultNotice:
    """One coherent placement answer, or a stated reason there is none (C2).

    Advisory. `bay_reference` is None when no bay could be recommended, and
    `reason` then names *which* state applied -- no warehouse reference, a
    warehouse the graph does not hold, a graph that could not be read, or a
    warehouse whose bays are all ineligible. An operator who cannot tell those
    apart cannot act on any of them.

    The fields after `reason` were absent, and their absence is what made this
    a partial result: a bay id with no location, no confidence and no evidence
    obliged every reader to go and find the rest somewhere else.
    `confidence_millionths` is `BayAssignmentAgent`'s computed margin over the
    runner-up -- never a constant -- and is None when there is no
    recommendation to be confident about.

    All of them default, so a caller that only knows a bay id (a late external
    signal, say) still constructs a valid notice.
    """

    warehouse_reference: str | None
    bay_reference: str | None
    reason: str | None = None
    return_location: str | None = None
    confidence_millionths: int | None = None
    explanation: str | None = None
    #: The reading placement was made from, in the shape the return event log
    #: uses -- `WAREHOUSE_OBSERVED:<generation>:<count>` and its two siblings.
    evidence_reference: str | None = None
    graph_generation_id: str | None = None
    #: `LIVE` when the ranking weighed each bay's declared maximum less its
    #: unexpired reservations; `DECLARED` when only the maximum was available
    #: (BAY-02). The two carry different odds of the reservation succeeding, so
    #: an operator seeing refusals can tell which reading produced them.
    capacity_evidence: str | None = None


@dataclass(frozen=True, slots=True)
class SupportReturnRecord:
    """One RMA as Support issued it.

    A list of these, not a single set of fields: one reply can create several
    RMAs with different labels going to different places, and flattening them
    would be the very thing the case model was changed to stop.
    """

    return_reference: str
    tracking_reference: str | None = None
    label_reference: str | None = None
    return_location: str | None = None
    shipping_instruction_reference: str | None = None
    order_line_references: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SupportResponseNotice:
    work_item_id: str
    records: tuple[SupportReturnRecord, ...]
    rejected: bool = False
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class CancelCaseCommand:
    reason: str


# --- Activity payloads ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RecordCaseStatusInput:
    case_id: str
    status: str
    fact_name: str | None = None
    fact_value: str | None = None
    # Supplied by the workflow via `workflow.uuid4()` so a replay reuses the id
    # rather than minting a new one and writing the fact twice.
    fact_id: str | None = None
    occurred_at_iso: str = ""


@dataclass(frozen=True, slots=True)
class RequestBayAssignmentInput:
    case_id: str
    tenant_id: str


@dataclass(frozen=True, slots=True)
class ResolveBusinessDeadlineInput:
    """Ask for an instant `working_seconds` of working time after `from_iso`.

    The calendar is named, not carried: a corrected holiday list has to reach a
    case that is already waiting, which is the one thing here that legitimately
    differs from the pinned `ReturnCaseTimings`. Moving a *duration* under a
    live return would make an operator's countdown jump; correcting the days
    the warehouse is shut is fixing the answer to a question already asked.
    """

    from_iso: str
    working_seconds: int
    business_calendar_id: str
    timezone: str


@dataclass(frozen=True, slots=True)
class ResolvedBusinessDeadline:
    """When the wait expires, and whether a calendar decided it.

    `calendar_applied` is false when no calendar matches the id -- the instant
    is then plain wall clock, which is what the platform did before SLA-01. It
    is reported rather than assumed so a release that forgets to declare its
    calendar shows up as a case fact instead of as reminders arriving at
    midnight.
    """

    instant_iso: str
    calendar_applied: bool


@dataclass(frozen=True, slots=True)
class DraftSupportRequestInput:
    case_id: str
    configuration_release_id: str


@dataclass(frozen=True, slots=True)
class OpenSupportWorkItemInput:
    case_id: str
    tenant_id: str
    principal_id: str
    support_draft: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class SendSupportReminderInput:
    case_id: str
    work_item_id: str
    reminder_number: int
    max_reminders: int
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class RecordSupportOutcomeInput:
    case_id: str
    work_item_id: str
    records: tuple[SupportReturnRecord, ...]
    rejected: bool
    reason: str | None
    # One per record, minted in the workflow so a replay is stable.
    return_record_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SynchronizeReturnRecordsInput:
    """Record-scoped, and that is the whole point.

    The ids are the ones `record_support_outcome` was given, so a retry syncs
    the same records rather than whatever the collection holds by then, and the
    read the activity compiles matches one document per id instead of scanning
    every return in the platform.
    """

    case_id: str
    return_record_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReturnCaseOutcome:
    case_id: str
    status: str
    work_item_id: str | None
    return_references: tuple[str, ...]
    reminders_sent: int
    bay_reference: str | None
    parked_reason: str | None = None
    #: The generation the return records were committed into. An agent turn
    #: reading before this is set is reading a graph that does not have them.
    graph_generation_id: str | None = None


@dataclass(frozen=True, slots=True)
class ReturnCaseState:
    """Read-only coordination state, for observability."""

    case_id: str
    status: str
    work_item_id: str | None
    reminders_sent: int
    bay_reference: str | None
    bay_resolved: bool
    support_resolved: bool
    cancelled: bool
    #: None until the return records are in the graph. An operator watching a
    #: case that has an RMA and no generation is watching one whose sync has not
    #: landed, which is a different problem from one still waiting on Support.
    graph_generation_id: str | None = None


@dataclass
class _Mutable:
    """The workflow's own mutable coordination state.

    Kept in one object so `continue_as_new` has an obvious set to carry and a
    reviewer can see everything that survives a signal in one place.
    """

    status: str = ReturnCaseStatus.GATHERING_INFO.value
    work_item_id: str | None = None
    reminders_sent: int = 0
    bay: BayResultNotice | None = None
    support: SupportResponseNotice | None = None
    cancellation: CancelCaseCommand | None = None
    return_references: list[str] = field(default_factory=list)
    parked_reason: str | None = None
    graph_generation_id: str | None = None
    #: The resolved support deadline, kept so `continue_as_new` carries it.
    support_deadline_iso: str | None = None


@workflow.defn(name="return-platform-return-case-v1")
class ReturnCaseWorkflow:
    """One durable execution per return case."""

    def __init__(self) -> None:
        self._input: ReturnCaseWorkflowInput | None = None
        self._state = _Mutable()

    def _require_input(self) -> ReturnCaseWorkflowInput:
        if self._input is None:
            raise RuntimeError("ReturnCaseWorkflow has not started")
        return self._input

    # --- signals ------------------------------------------------------------

    @workflow.signal(name="bay_result")
    def bay_result(self, notice: BayResultNotice) -> None:
        """Advisory, and idempotent: a repeated signal keeps the first answer.

        Bay is best-effort, so a late or duplicated result must never disturb a
        case that has already stopped waiting for it.
        """
        if self._state.bay is None:
            self._state.bay = notice

    @workflow.signal(name="support_response")
    def support_response(self, notice: SupportResponseNotice) -> None:
        """First response wins.

        Support replying twice -- a human clicking send again, or a transport
        redelivering -- must not create a second set of RMAs. The workflow acts
        on exactly one response; anything after it is a comment on a case that
        has already moved on.
        """
        if self._state.support is None:
            self._state.support = notice

    @workflow.signal(name="cancel_case")
    def cancel_case(self, command: CancelCaseCommand) -> None:
        if self._state.cancellation is None:
            self._state.cancellation = command

    @workflow.query(name="execution_state")
    def execution_state(self) -> ReturnCaseState:
        return ReturnCaseState(
            case_id=self._require_input().case_id,
            status=self._state.status,
            work_item_id=self._state.work_item_id,
            reminders_sent=self._state.reminders_sent,
            bay_reference=self._state.bay.bay_reference if self._state.bay else None,
            bay_resolved=self._state.bay is not None,
            support_resolved=self._state.support is not None,
            cancelled=self._state.cancellation is not None,
            graph_generation_id=self._state.graph_generation_id,
        )

    # --- run ----------------------------------------------------------------

    @workflow.run
    async def run(self, workflow_input: ReturnCaseWorkflowInput) -> ReturnCaseOutcome:
        self._input = workflow_input
        self._state.reminders_sent = workflow_input.reminders_sent
        self._state.work_item_id = workflow_input.resumed_work_item_id
        if workflow_input.resumed_status is not None:
            self._state.status = workflow_input.resumed_status

        timings = workflow_input.timings

        if self._state.work_item_id is None:
            await self._gather_bay(timings)
            if self._cancelled():
                return await self._finish_cancelled()
            await self._open_support(timings)
            if self._cancelled():
                return await self._finish_cancelled()

        await self._await_support(timings)
        if self._cancelled():
            return await self._finish_cancelled()

        if self._state.support is None:
            # Reminders exhausted. Parking is a terminal state an operator can
            # see and act on -- the alternative a reminder cap creates is a case
            # waiting forever with nobody told.
            return await self._park(timings.on_reminders_exhausted)

        await self._record_support_outcome()
        return self._outcome()

    # --- phases -------------------------------------------------------------

    async def _gather_bay(self, timings: ReturnCaseTimings) -> None:
        """Ask for a bay, wait a bounded time, proceed either way.

        Best-effort in both directions: a failed request is recorded and
        stepped over, and an unanswered one simply times out. Nothing
        downstream reads the bay, so neither outcome may stop the return.

        The activity now *answers* rather than merely acknowledging (BAY-01).
        It used to write one `bay_assignment_requested` fact and return None,
        leaving the workflow to wait `bay_wait_seconds` for a `bay_result`
        signal whose only sender was a test -- so every case waited the full
        bay window and then proceeded with nothing, and the wait looked like a
        timeout rather than like a step that was never wired.

        The signal is kept, and is still the way a *late* or externally-decided
        result arrives: `bay_result` ignores a second notice, so an answer
        already in hand is never overwritten by one that arrives afterwards.
        """
        workflow_input = self._require_input()
        await self._set_status(ReturnCaseStatus.AWAITING_BAY)
        try:
            notice: BayResultNotice | None = await workflow.execute_activity(
                "request_bay_assignment",
                RequestBayAssignmentInput(
                    case_id=workflow_input.case_id, tenant_id=workflow_input.tenant_id
                ),
                result_type=BayResultNotice,
                start_to_close_timeout=_PERSIST_TIMEOUT,
                retry_policy=_BEST_EFFORT_RETRY,
            )
        except ActivityError:
            # Recorded, not raised. `orchestrator._handle` used to call the bay
            # builder inline and let its exception fail the whole return.
            workflow.logger.warning("bay request failed; continuing without a bay")
            self._state.bay = BayResultNotice(
                warehouse_reference=None, bay_reference=None, reason="REQUEST_FAILED"
            )
            return

        if notice is not None and self._state.bay is None:
            # First answer wins, exactly as `bay_result` decides it: a signal
            # that raced ahead of the activity is already the case's bay, and
            # replacing it here would make the outcome depend on scheduling.
            self._state.bay = notice

        if timings.bay_wait_seconds <= 0:
            return
        try:
            await workflow.wait_condition(
                lambda: self._state.bay is not None or self._state.cancellation is not None,
                timeout=timedelta(seconds=timings.bay_wait_seconds),
                timeout_summary="bay-wait",
            )
        except TimeoutError:
            # Not an error. The return proceeds and the bay may arrive later as
            # a signal nobody is blocking on.
            workflow.logger.info("bay wait elapsed; proceeding without a bay")

    async def _open_support(self, timings: ReturnCaseTimings) -> None:
        del timings
        workflow_input = self._require_input()
        try:
            draft: str = await workflow.execute_activity(
                "draft_support_request",
                DraftSupportRequestInput(
                    case_id=workflow_input.case_id,
                    configuration_release_id=workflow_input.configuration_release_id,
                ),
                result_type=str,
                start_to_close_timeout=_DRAFT_TIMEOUT,
                retry_policy=_DRAFT_RETRY,
            )
        except ActivityError:
            # The model is unavailable. Support still needs asking, and a
            # deterministic request is better than a parked case -- the
            # activity's own fallback decides the wording.
            workflow.logger.warning("support draft unavailable; using the deterministic template")
            draft = ""

        work_item_id: str = await workflow.execute_activity(
            "open_support_work_item",
            OpenSupportWorkItemInput(
                case_id=workflow_input.case_id,
                tenant_id=workflow_input.tenant_id,
                principal_id=workflow_input.principal_id,
                support_draft=draft,
                # Derived from the case, not minted per attempt: a retry, and a
                # replay after continue_as_new, must not open a second thread
                # with a human on the other end of it.
                idempotency_key=f"support:{workflow_input.case_id}",
            ),
            result_type=str,
            start_to_close_timeout=_PERSIST_TIMEOUT,
            retry_policy=_PERSIST_RETRY,
        )
        self._state.work_item_id = work_item_id
        await self._set_status(ReturnCaseStatus.AWAITING_SUPPORT)

    async def _business_deadline(
        self, timings: ReturnCaseTimings, working_seconds: int
    ) -> datetime:
        """`workflow.now()` plus that many *working* seconds (SLA-01, C8).

        The arithmetic is an activity, not a local computation, and that is the
        determinism boundary rather than a preference. Resolving a zone reads
        the tz database -- `SubmitOrderDiscoveryTurnCommand` already documents
        why that must never happen in a workflow body, "a determinism hazard
        the moment the tz database on the worker changes" -- and the holiday
        list is configuration, which is IO. Both live on the activity side; the
        workflow receives one absolute instant, which its history records and a
        replay returns unchanged.
        """
        resolved: ResolvedBusinessDeadline = await workflow.execute_activity(
            "resolve_business_deadline",
            ResolveBusinessDeadlineInput(
                from_iso=workflow.now().isoformat(),
                working_seconds=working_seconds,
                business_calendar_id=timings.business_calendar_id,
                timezone=timings.timezone,
            ),
            result_type=ResolvedBusinessDeadline,
            start_to_close_timeout=_PERSIST_TIMEOUT,
            retry_policy=_PERSIST_RETRY,
        )
        if not resolved.calendar_applied:
            workflow.logger.warning(
                "business calendar %s is not configured; the wait is wall-clock",
                timings.business_calendar_id,
            )
        return datetime.fromisoformat(resolved.instant_iso)

    async def _await_support(self, timings: ReturnCaseTimings) -> None:
        """Wait, remind, wait -- until Support answers or the reminders run out.

        Each leg is a durable timer, so the whole cycle survives a restart. The
        cadence is the reminder interval; the overall deadline is the Support
        wait, and reaching either one first is meaningful, so both are honoured
        rather than collapsed into one number.

        Both are **working** durations now (SLA-01). They were wall clock, and
        the configuration had said otherwise since it was written: a return
        raised at 16:30 on a Friday with an eight-hour wait, two-hour reminders
        and a cap of three chased Support at 18:30, 20:30 and 22:30 into an
        empty queue and parked itself at 00:30 on Saturday, having spent every
        one of its reminders while nobody was there. The durable-timer
        machinery below is unchanged; only the instants it counts to moved.
        """
        resumed = self._require_input().resumed_support_deadline_iso
        deadline = (
            datetime.fromisoformat(resumed)
            if resumed is not None
            else await self._business_deadline(timings, timings.support_response_wait_seconds)
        )
        self._state.support_deadline_iso = deadline.isoformat()
        while self._state.support is None and self._state.cancellation is None:
            remaining = deadline - workflow.now()
            if remaining <= timedelta(0):
                return
            # The next reminder is a working interval from now, not a
            # wall-clock one: on a Friday evening the next nudge is Monday
            # morning, and the three the cap allows are three the recipient
            # will actually be present for.
            next_reminder = await self._business_deadline(
                timings, timings.reminder_interval_seconds
            )
            interval = max(next_reminder - workflow.now(), timedelta(0))
            try:
                await workflow.wait_condition(
                    lambda: self._state.support is not None or self._state.cancellation is not None,
                    timeout=min(interval, remaining),
                    timeout_summary="support-wait",
                )
                return
            except TimeoutError:
                pass

            if self._state.reminders_sent >= timings.max_reminders:
                return
            self._state.reminders_sent += 1
            await self._send_reminder()

            if workflow.info().is_continue_as_new_suggested():
                # A case can wait days. Reset history and carry the coordination
                # state so callers and the outstanding work item are unaffected.
                await workflow.wait_condition(lambda: workflow.all_handlers_finished())
                workflow.continue_as_new(self._continued_input())

    async def _send_reminder(self) -> None:
        workflow_input = self._require_input()
        work_item_id = self._state.work_item_id
        if work_item_id is None:  # pragma: no cover - set before this is reachable
            return
        try:
            await workflow.execute_activity(
                "send_support_reminder",
                SendSupportReminderInput(
                    case_id=workflow_input.case_id,
                    work_item_id=work_item_id,
                    reminder_number=self._state.reminders_sent,
                    max_reminders=workflow_input.timings.max_reminders,
                    # Numbered, so a retry re-sends the *same* reminder rather
                    # than adding one to a human's queue.
                    idempotency_key=(
                        f"reminder:{workflow_input.case_id}:{self._state.reminders_sent}"
                    ),
                ),
                start_to_close_timeout=_PERSIST_TIMEOUT,
                retry_policy=_PERSIST_RETRY,
            )
        except ActivityError:
            # A reminder that could not be sent is not a reason to fail a return
            # that is otherwise waiting patiently.
            workflow.logger.warning("reminder %s failed to send", self._state.reminders_sent)

    async def _record_support_outcome(self) -> None:
        workflow_input = self._require_input()
        support = self._state.support
        if support is None:  # pragma: no cover - guarded by the caller
            return
        return_record_ids = tuple(str(workflow.uuid4()) for _ in support.records)
        await workflow.execute_activity(
            "record_support_outcome",
            RecordSupportOutcomeInput(
                case_id=workflow_input.case_id,
                work_item_id=support.work_item_id,
                records=support.records,
                rejected=support.rejected,
                reason=support.reason,
                # Minted here so a replay reuses them and the activity's
                # create-if-absent is genuinely idempotent.
                return_record_ids=return_record_ids,
            ),
            start_to_close_timeout=_PERSIST_TIMEOUT,
            retry_policy=_PERSIST_RETRY,
        )
        self._state.return_references = [record.return_reference for record in support.records]
        if not await self._synchronize_return_records(return_record_ids):
            return
        await self._set_status(
            ReturnCaseStatus.CLOSED if support.rejected else ReturnCaseStatus.RMA_RECEIVED
        )

    async def _synchronize_return_records(self, return_record_ids: tuple[str, ...]) -> bool:
        """Put the committed records into the graph before any agent reads (W2.5).

        Ordered after `record_support_outcome`, never concurrent with it: the
        targeted read the activity compiles goes to the platform's own store, so
        a sync racing the write would read the document as it was before Support
        answered and project a DRAFT record over the ISSUED one.

        Return Workflow is `blocking`, so this is the one activity here whose
        failure stops the case. Continuing would leave the RMA in the store,
        absent from the graph, and Order Discovery telling the associate on their
        next turn that no return exists -- which is worse than a parked case
        somebody can see and retry.
        """
        workflow_input = self._require_input()
        if not return_record_ids:
            # Support rejected the case without issuing anything. Nothing to
            # project, and a sync of an empty set would fail loudly for no
            # reason.
            return True
        try:
            generation: str = await workflow.execute_activity(
                "synchronize_return_records",
                SynchronizeReturnRecordsInput(
                    case_id=workflow_input.case_id,
                    return_record_ids=return_record_ids,
                ),
                result_type=str,
                start_to_close_timeout=_SYNC_TIMEOUT,
                retry_policy=_PERSIST_RETRY,
            )
        except ActivityError:
            workflow.logger.error(
                "return record graph sync failed for case %s; parking",
                workflow_input.case_id,
            )
            await self._park_for_graph_sync_failure()
            return False
        self._state.graph_generation_id = generation
        return True

    # --- terminal states ------------------------------------------------------

    async def _park_for_graph_sync_failure(self) -> None:
        """Terminal, and loud.

        `RMA_RECEIVED` is deliberately not set: Support did answer, but the
        platform cannot honestly claim the case has reached the state an
        associate would be shown, because the thing an associate is shown is
        read from the graph. The status stays `AWAITING_SUPPORT` and the parked
        reason names the real cause, so S2 shows a case needing attention rather
        than one that looks complete and answers nothing.
        """
        self._state.parked_reason = "RETURN_GRAPH_SYNC_FAILED"
        await self._set_status(
            ReturnCaseStatus.AWAITING_SUPPORT, fact_value="RETURN_GRAPH_SYNC_FAILED"
        )

    async def _park(self, disposition: str) -> ReturnCaseOutcome:
        reason = "SUPPORT_ESCALATED" if disposition == "ESCALATE" else "SUPPORT_REMINDERS_EXHAUSTED"
        self._state.parked_reason = reason
        await self._set_status(ReturnCaseStatus.AWAITING_SUPPORT, fact_value=reason)
        return self._outcome()

    async def _finish_cancelled(self) -> ReturnCaseOutcome:
        cancellation = self._state.cancellation
        await self._set_status(
            ReturnCaseStatus.CANCELLED,
            fact_value=cancellation.reason if cancellation else None,
        )
        return self._outcome()

    def _cancelled(self) -> bool:
        return self._state.cancellation is not None

    async def _set_status(self, status: ReturnCaseStatus, *, fact_value: str | None = None) -> None:
        workflow_input = self._require_input()
        self._state.status = status.value
        await workflow.execute_activity(
            "record_case_status",
            RecordCaseStatusInput(
                case_id=workflow_input.case_id,
                status=status.value,
                fact_name="case_status" if fact_value is not None else None,
                fact_value=fact_value,
                fact_id=str(workflow.uuid4()) if fact_value is not None else None,
                occurred_at_iso=workflow.now().isoformat(),
            ),
            start_to_close_timeout=_PERSIST_TIMEOUT,
            retry_policy=_PERSIST_RETRY,
        )

    def _continued_input(self) -> ReturnCaseWorkflowInput:
        workflow_input = self._require_input()
        return ReturnCaseWorkflowInput(
            case_id=workflow_input.case_id,
            tenant_id=workflow_input.tenant_id,
            principal_id=workflow_input.principal_id,
            conversation_id=workflow_input.conversation_id,
            configuration_release_id=workflow_input.configuration_release_id,
            timings=workflow_input.timings,
            resumed_status=self._state.status,
            resumed_work_item_id=self._state.work_item_id,
            reminders_sent=self._state.reminders_sent,
            resumed_support_deadline_iso=self._state.support_deadline_iso,
        )

    def _outcome(self) -> ReturnCaseOutcome:
        return ReturnCaseOutcome(
            case_id=self._require_input().case_id,
            status=self._state.status,
            work_item_id=self._state.work_item_id,
            return_references=tuple(self._state.return_references),
            reminders_sent=self._state.reminders_sent,
            bay_reference=self._state.bay.bay_reference if self._state.bay else None,
            parked_reason=self._state.parked_reason,
            graph_generation_id=self._state.graph_generation_id,
        )
