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

**The policy gate.** Between the bay step and `_open_support` sits one
deterministic eligibility evaluation (3A.7). It is the only path to Support:
`run` opens a work item on the statement immediately after the gate clears, so
a rejected return cannot reach a human by any route through this module. The
gate never approves on failure -- an evaluator error holds the case for a
supervisor, and a deployment with no published rule set parks it as an
operational failure rather than pretending the evaluator ran.

**Support answers repeatedly.** The Support wait is an accumulating drain, not
a single question: the handler appends every notice and the run loop applies
them oldest first, so a delayed tracking number, a replacement label and a
corrected RMA all reach a case that is still alive to receive them. What this
replaced was first-response-wins followed by an immediate return, which is why
real cases sit today with an RMA and a label, `trackingReference: null`, and a
workflow that answered the second reply with `500 workflow execution already
completed`. Redelivery is bounded by `supportEventId` -- the transport is
at-least-once by design, so an accumulating handler with no key would
double-apply on the second delivery rather than recognise it.

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
from typing import Any, Final

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

__all__ = [
    "BayResultNotice",
    "CancelCaseCommand",
    "CaseEligibilityOutcome",
    "CaseTerminalCommand",
    "DraftSupportRequestInput",
    "EvaluateCaseEligibilityInput",
    "OpenSupportWorkItemInput",
    "PolicyDecisionName",
    "PolicyGateState",
    "PolicyOverrideNotice",
    "PolicyRouteName",
    "RecordCaseStatusInput",
    "RecordSupportOutcomeInput",
    "RequestBayAssignmentInput",
    "ResolveBusinessDeadlineInput",
    "ResolvedBusinessDeadline",
    "ReturnCaseOutcome",
    "ReturnCaseState",
    "ReturnCaseStatus",
    "ReturnCaseTimings",
    "ReturnCaseWorkflow",
    "ReturnCaseWorkflowInput",
    "SendSupportReminderInput",
    "SupportOutcomeReceipt",
    "SupportResponseNotice",
    "SupportReturnRecord",
    "SynchronizeReturnRecordsInput",
    "TerminalCommandName",
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

#: How many `supportEventId`s the workflow remembers, newest last.
#:
#: **Bounded on purpose.** An unbounded applied-keys set is workflow state that
#: only ever grows, and it grows in the one place growth is most expensive: it
#: is carried across every `continue_as_new` and re-serialized into every
#: history. Sixty-four is far more than the handful of notices one case receives
#: between two history resets, which is the whole window this set has to cover.
#:
#: It is **not** the durable dedup and does not have to be. The unique index on
#: `(caseId, supportEventId)` in `case_support_events` is what makes an event
#: identifiable forever, and `record_support_outcome` is idempotent by
#: construction -- it merges into the record it finds and writes nothing when
#: the merge changes nothing. This set exists only to keep a redelivery of a
#: command still in flight from costing an activity round trip, and to keep the
#: pre-`supportEventId` senders' first-wins guarantee intact.
_TRACKED_SUPPORT_EVENT_IDS: Final = 64

#: Thirty days, in seconds. The default absolute lifetime cap.
#:
#: Defaulted on the timing block rather than read from the release, because
#: `ReturnCaseTimingConfiguration` is owned elsewhere and a case must not be
#: able to outlive the platform's willingness to hold it open while that catches
#: up. A deployment that wants a different cap sets the field at start, exactly
#: as it sets every other timing.
_DEFAULT_LIFETIME_SECONDS: Final = 30 * 24 * 60 * 60


class ReturnCaseStatus(StrEnum):
    """Mirrors `operations.models.CaseStatus`.

    Redeclared rather than imported: workflow code is replayed against whatever
    version of the module is deployed, and importing the operations package
    into a workflow would drag Mongo, pydantic settings and the repository into
    the sandbox with it.
    """

    GATHERING_INFO = "GATHERING_INFO"
    AWAITING_BAY = "AWAITING_BAY"
    #: 3A.7. The deterministic evaluator approved. Transient by design: the very
    #: next step is `_open_support`, which moves the case to `AWAITING_SUPPORT`.
    #: It exists so that "policy approved this" is a state the log records rather
    #: than something inferred from the absence of a rejection.
    POLICY_APPROVED = "POLICY_APPROVED"
    #: The evaluator said `REVIEW_REQUIRED`, or could not run. A supervisor
    #: override is the only thing that moves this forward, and **no Support work
    #: item is open** -- that is the difference from `AWAITING_SUPPORT`.
    AWAITING_POLICY_REVIEW = "AWAITING_POLICY_REVIEW"
    #: Terminal. The return is outside policy and Support was never asked.
    POLICY_REJECTED = "POLICY_REJECTED"
    #: An operational failure parked the case -- today, only a deployment with no
    #: published eligibility policy. Deliberately **not** `AWAITING_POLICY_REVIEW`:
    #: an absent rule set must never look like the evaluator working. Non-terminal,
    #: because publishing the policy is what fixes it and the case then resumes.
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    AWAITING_SUPPORT = "AWAITING_SUPPORT"
    RMA_RECEIVED = "RMA_RECEIVED"
    IN_TRANSIT = "IN_TRANSIT"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class PolicyGateState(StrEnum):
    """Whether the eligibility evaluation happened, and if not, why not.

    Three states rather than a decision with extra members, because the two
    failures are not eligibility answers and must never be read as one. Plan
    sect. 7.2 draws the line: a missing or malformed policy is an *operational*
    failure, while a valid policy over missing facts is `REVIEW_REQUIRED` and is
    the system working correctly.
    """

    #: The evaluator ran and produced a `PolicyOutcome`.
    EVALUATED = "EVALUATED"
    #: The deployment has suspended the gate through
    #: `policy_evaluation.enabled = false`. **Not a decision and not an
    #: approval**: no rule was applied, no route was chosen, and nothing may
    #: later read this as `APPROVE`. The case proceeds to Support carrying the
    #: operator's stated reason, so a human sees that the gate did not run
    #: rather than seeing a verdict nobody reached.
    SKIPPED_BY_CONFIGURATION = "SKIPPED_BY_CONFIGURATION"
    #: No eligibility policy is published for this deployment. Park the case.
    POLICY_NOT_CONFIGURED = "POLICY_NOT_CONFIGURED"
    #: The evaluator, or the fact assembly in front of it, raised. Fail closed to
    #: review -- never to approval, and never to a work item.
    EVALUATION_FAILED = "EVALUATION_FAILED"
    #: Not an activity result. The marker `continue_as_new` carries so a history
    #: reset mid-review resumes the wait instead of re-evaluating the case.
    AWAITING_OVERRIDE = "AWAITING_OVERRIDE"


class PolicyRouteName(StrEnum):
    """Mirrors `policy.vocabulary.PolicyRoute`, for the same reason
    `ReturnCaseStatus` mirrors `CaseStatus`: importing the policy package into a
    workflow would drag pydantic, the business calendar and the configuration
    model into the replay sandbox with it."""

    STANDARD_RETURN = "STANDARD_RETURN"
    WARRANTY = "WARRANTY"
    DELIVERY_CLAIM = "DELIVERY_CLAIM"


class PolicyDecisionName(StrEnum):
    """Mirrors `workflows.stage_results.EligibilityDecision`. Exactly three."""

    APPROVE = "APPROVE"
    REJECT = "REJECT"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class TerminalCommandName(StrEnum):
    """The three ways a case may be ended on purpose (plan sect. 10.3).

    An enumeration, and validated on arrival, because the thing it replaces is
    an *unrestricted* close: anything holding the workflow id could end a case
    at any point in its life, with no statement of why and nothing recorded
    about who. Each member below carries its own validation and each one is
    audited; a fourth spelling is refused rather than treated as a close.
    """

    #: Only from a case that already satisfies domain completion. A `COMPLETE`
    #: over a case with an outstanding `awaiting` dimension is refused (409) and
    #: the refusal is recorded -- closing it would be the platform asserting a
    #: return finished that the requirement table says is not.
    COMPLETE = "COMPLETE"
    #: An actor ended the return. The actor is stamped by the endpoint and the
    #: instant by `workflow.now()`; see `CaseTerminalCommand`.
    CANCEL = "CANCEL"
    #: The deadline ended it, and nobody did. **System-initiated only** -- a
    #: sender claiming `EXPIRE` is refused, because an expiry that an actor
    #: could assert is an unaudited cancellation wearing another name.
    EXPIRE = "EXPIRE"


class _Waited(StrEnum):
    """Why the Support wait returned. Private: it is loop control, not contract.

    Three answers rather than a boolean, because the run loop does something
    different with each and "the wait ended" would collapse a case that just
    received its label into one whose deadline passed with nothing.
    """

    #: Something is queued: a notice, a cancellation or a terminal command.
    ARRIVED = "ARRIVED"
    #: The Support deadline passed.
    DEADLINE = "DEADLINE"
    #: The reminder cap was reached with nothing received at all.
    REMINDERS_EXHAUSTED = "REMINDERS_EXHAUSTED"


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
    #: The absolute cap on how long one case may stay open, in **wall-clock**
    #: seconds from the moment the Support wait began (plan sect. 10.3).
    #:
    #: Wall clock rather than business time, and that is the point of it. Every
    #: other duration here is a service-level promise measured in working hours;
    #: this one is a backstop against a case that never resolves, and a backstop
    #: measured in a calendar that can be corrected is not a backstop. Reaching
    #: it parks the case for an operator rather than completing or cancelling
    #: it -- the platform has no answer at that point, and inventing one would
    #: be the silent close this whole section removes.
    absolute_lifetime_seconds: int = _DEFAULT_LIFETIME_SECONDS


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
    #: `AWAITING_OVERRIDE` when history was reset while the case was waiting on
    #: a supervisor. Without it the far side would see no work item, conclude the
    #: case had not reached Support yet, and evaluate the policy a second time --
    #: which for a case already found `REVIEW_REQUIRED` would restart the
    #: supervisor's clock on every history reset.
    resumed_policy_state: str | None = None
    resumed_policy_deadline_iso: str | None = None
    policy_reminders_sent: int = 0
    #: The `supportEventId`s the far side must keep recognising as already
    #: applied. Bounded to `_TRACKED_SUPPORT_EVENT_IDS`, newest last -- see the
    #: constant for why an unbounded set is the wrong thing to carry here.
    resumed_support_event_ids: tuple[str, ...] = ()
    #: True once a notice with no `supportEventId` has been applied. Carried
    #: separately because it is a different rule: an unkeyed sender gets
    #: first-wins, which is the only redelivery-safe reading of a notice that
    #: cannot be told apart from a second one.
    resumed_unkeyed_support_applied: bool = False
    #: When the Support wait began, for the absolute lifetime cap. Carried for
    #: the same reason the deadline is: recomputing it on the far side would
    #: grant a fresh lifetime on every history reset, and the cap exists
    #: precisely for the case that resets often.
    resumed_lifetime_start_iso: str | None = None
    #: Whether the case had already reported itself business-complete. A history
    #: reset must not walk a finished case back into the drain loop.
    resumed_business_complete: bool = False


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
    #: How the goods come back, as Support decided it (D23).
    #:
    #: The value the completion profile is computed from. Nothing upstream of
    #: Support may supply it: before Support answers the platform holds a
    #: *recommendation*, and writing a recommendation here would resolve the
    #: requirement set on a guess. `None` means "Support did not say", and a
    #: `None` arriving in a later update never erases a method already recorded.
    return_method: str | None = None
    #: Who is carrying the goods back, as Support arranged it (audit finding #9).
    #:
    #: Per record and never per case, for the reason the tracking reference is:
    #: one reply can issue several RMAs travelling with different carriers, and
    #: a case-level value would be read as the carrier of every package on the
    #: case. `None` is "Support did not say" and never erases a carrier already
    #: recorded -- the same merge rule every other field here follows.
    carrier: str | None = None


@dataclass(frozen=True, slots=True)
class SupportResponseNotice:
    """One thing Support said. Never the whole of what Support will say.

    Every field but `work_item_id` is a partial statement, and that is the
    shape Phase 4 turns on: the RMA arrives, then the tracking number two hours
    later, then a replacement label the next morning, each as its own notice on
    the same case. The handler appends them and the run loop drains them.

    `support_event_id` is the identity that makes redelivery safe. The
    transport below this is at-least-once by design -- a dispatcher that
    signals and then loses its process before acknowledging *will* signal
    again -- so an accumulating handler with no key would double-apply on the
    second delivery. Defaulted to the empty string rather than required,
    because a sender that predates it must keep working, and an unkeyed notice
    falls back to first-wins, which is the only reading of a notice that cannot
    be told apart from a second one.
    """

    work_item_id: str
    records: tuple[SupportReturnRecord, ...]
    rejected: bool = False
    reason: str | None = None
    support_event_id: str = ""


@dataclass(frozen=True, slots=True)
class PolicyOverrideNotice:
    """A supervisor's departure from the evaluator's answer (3A.8).

    Every field here is **server-derived except the decision, the reason code and
    the reason**: `actor` is the authenticated principal and `overridden_at_iso`
    the server clock, both stamped by the endpoint, never by the caller. A
    client-supplied audit field is not audit, which is the same rule
    `order_agent.py` applies to `correlation_id`.

    It carries no `original_decision`. The original is already durable on the
    fact log and this notice is append-only over it -- a field here could be
    filled with a different original, and then two records of one evaluation
    would disagree about what was overridden.
    """

    override_decision: str
    reason_code: str
    actor: str
    overridden_at_iso: str
    reason: str | None = None
    #: The endpoint's own idempotency key, echoed so a redelivered signal is
    #: recognisable as the same override rather than a second one.
    idempotency_key: str | None = None


@dataclass(frozen=True, slots=True)
class CancelCaseCommand:
    reason: str


@dataclass(frozen=True, slots=True)
class CaseTerminalCommand:
    """An instruction to end a case, with the validation each kind carries.

    **There is no timestamp field, and that is deliberate.** An audit instant a
    caller can supply is not audit; the workflow stamps `workflow.now()`, which
    is the server clock and is replay-stable. `PolicyOverrideNotice` documents
    the same rule for `actor`, and `order_agent.py` set the precedent for
    `correlation_id`.

    `actor` is the authenticated principal as the endpoint resolved it, never a
    name the request body carried. It is required for `CANCEL` -- a cancellation
    nobody is attributed with is not audited -- and is `SYSTEM` on the `EXPIRE`
    the workflow raises for itself.
    """

    command: str
    reason_code: str
    actor: str
    reason: str | None = None
    #: The sender's own idempotency key, echoed so a redelivered command is
    #: recognisable as the same one rather than a second close.
    idempotency_key: str | None = None


@dataclass(frozen=True, slots=True)
class SupportOutcomeReceipt:
    """What `record_support_outcome` did, and where the case stands afterwards.

    The activity answers rather than returning `None`, because the run loop now
    has to decide whether to keep waiting -- and the only place that can be
    computed is the side of the boundary that may read the case. A workflow that
    tried would need the requirement table, the policy decision and every child
    collection, none of which it may touch.

    `completion_known` is the honest third state. A worker whose repository
    cannot assemble the projection -- and every pre-Phase-4 activity double --
    answers `False`, and the run loop then behaves exactly as it did before this
    phase: recording the outcome is where the case ends. Defaulting
    `business_complete` to `False` without that flag would be the opposite
    failure, a case held open forever because nothing could tell it it was done.
    """

    #: The record ids that actually carry this outcome. **Not** always the ones
    #: the workflow minted: a second reply about an RMA the case already holds
    #: updates the existing record, and the graph sync must be pointed at that
    #: one rather than at an id nothing was written under.
    record_ids: tuple[str, ...] = ()
    #: Whether anything was written. False for a redelivery whose every field
    #: the record already held -- and the reason a replay bumps no revision.
    applied: bool = False
    completion_known: bool = False
    business_complete: bool = False
    awaiting: tuple[str, ...] = ()
    revision: int = 0


#: `SupportOutcomeReceipt | None`, as the converter's `result_type`.
#:
#: **Optional deliberately.** Temporal converts an activity result against this
#: hint, and a bare `SupportOutcomeReceipt` raises `TypeError` on a `null`
#: payload -- which is exactly what every activity double registered before this
#: phase returns. That failure would land inside the converter and fail the
#: workflow task, so a worker wired to a probe would hang rather than behave as
#: it did before. The run loop already reads a missing receipt as "completion
#: unknown", which is the honest answer for a worker that cannot produce one.
#:
#: Typed `Any` because `execute_activity(result_type=...)` is annotated `type`
#: and a union is not one. What reaches the converter is the value, not the
#: annotation.
_RECEIPT_RESULT_TYPE: Final[Any] = SupportOutcomeReceipt | None


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
class EvaluateCaseEligibilityInput:
    """Everything the deterministic evaluation needs that a workflow may not read.

    `evaluated_at_iso` is `workflow.now()` rather than the activity's clock: the
    evaluation instant decides a 30-day boundary, and an instant read on the
    activity side would differ between the first attempt and a retry, so a case
    could evaluate inside its window and then outside it. The zone and the
    calendar id travel by name because resolving either is IO -- exactly the
    split `resolve_business_deadline` already documents.

    `fact_id_seed` is a `workflow.uuid4()`, so the provenance facts the activity
    writes carry replay-stable ids and a retry re-writes the same log entry
    rather than a second opinion.
    """

    case_id: str
    tenant_id: str
    configuration_release_id: str
    evaluated_at_iso: str
    business_calendar_id: str
    timezone: str
    fact_id_seed: str


@dataclass(frozen=True, slots=True)
class CaseEligibilityOutcome:
    """The gate's reading of one evaluation.

    A projection of `PolicyOutcome` onto plain strings, not the outcome itself.
    The workflow needs four things -- did it run, which route, which decision,
    which queue -- and passing the whole pydantic model through the sandbox
    boundary would make every later field of it part of workflow history.

    The persisted record is richer and lives on the case fact log: the activity
    writes reason codes, conditions, exceptions, applied rules, policy id and
    version, and the source document and revision, before it answers.
    """

    state: str
    route: str | None = None
    decision: str | None = None
    reason_codes: tuple[str, ...] = ()
    #: The Support queue this route is verified on, resolved from configuration
    #: by the activity. `None` on the standard path and whenever the deployment
    #: has not declared the route's queue.
    support_queue: str | None = None
    #: Names the operational failure when `state` is not `EVALUATED`.
    failure_reason: str | None = None


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
    #: The queue the work item belongs on, when the policy route named one.
    #: `None` leaves the support service's own default standing. Route context
    #: travels as a queue and never as a work-item type field (plan sect. 7.6).
    queue: str | None = None


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
    # One per record, minted in the workflow so a replay is stable. Used only
    # where the case does not already hold a record for the RMA -- an update
    # writes under the id the record already has.
    return_record_ids: tuple[str, ...] = ()
    #: The Support event this outcome came from. Empty for a sender that
    #: predates the id. It is what makes the *fact* ids of a later update
    #: distinct from those of the update before it: the log is insert-only, so
    #: a second tracking number written under the first one's id would be
    #: absorbed as a duplicate and lost.
    support_event_id: str = ""


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
    #: What the policy gate concluded. `policy_decision` is the *evaluator's*
    #: answer where the route is standard and an override has not replaced it;
    #: `policy_overridden` says whether a supervisor did.
    policy_state: str | None = None
    policy_route: str | None = None
    policy_decision: str | None = None
    policy_overridden: bool = False
    support_queue: str | None = None
    #: How many Support notices were applied over the case's whole life. One
    #: for the ordinary single reply; more once tracking, a label or a pickup
    #: arrive after the RMA.
    support_responses_applied: int = 0
    #: The last completion reading the case reported, and whether one was
    #: available at all.
    business_complete: bool = False
    awaiting: tuple[str, ...] = ()
    #: Which terminal command ended the case, when one did.
    terminal_command: str | None = None


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
    policy_state: str | None = None
    policy_route: str | None = None
    policy_decision: str | None = None
    policy_overridden: bool = False
    #: How many notices are queued and not yet drained. An operator seeing a
    #: non-zero value on a case that is not moving is seeing a drain loop that
    #: is stuck, which is a different problem from Support never answering.
    support_pending: int = 0
    support_responses_applied: int = 0
    business_complete: bool = False
    awaiting: tuple[str, ...] = ()
    terminal_command: str | None = None


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
    #: The **last applied** notice, not the only one. Kept because "has Support
    #: answered at all" is still a question the query and the park decision ask,
    #: and it is now the answer to that question rather than the whole reply.
    support: SupportResponseNotice | None = None
    #: Notices the handler has accepted and the run loop has not drained. A
    #: list, appended to, because Support answers repeatedly (plan sect. 2.3)
    #: and a field that could hold one notice is what made every later one
    #: unreachable.
    pending_support: list[SupportResponseNotice] = field(default_factory=list)
    #: The `supportEventId`s already accepted, newest last, bounded to
    #: `_TRACKED_SUPPORT_EVENT_IDS`.
    support_event_ids: list[str] = field(default_factory=list)
    #: Whether an unkeyed notice has been accepted. See `SupportResponseNotice`.
    unkeyed_support_applied: bool = False
    support_responses_applied: int = 0
    cancellation: CancelCaseCommand | None = None
    #: The validated terminal command awaiting the run loop, if one arrived.
    terminal_command: CaseTerminalCommand | None = None
    #: Which command actually ended the case. Set when one is applied, so a
    #: refused `COMPLETE` is distinguishable from an accepted one.
    applied_terminal_command: str | None = None
    return_references: list[str] = field(default_factory=list)
    parked_reason: str | None = None
    graph_generation_id: str | None = None
    #: The resolved support deadline, kept so `continue_as_new` carries it.
    support_deadline_iso: str | None = None
    #: When the Support wait began, for the absolute lifetime cap.
    lifetime_start_iso: str | None = None
    #: The last completion reading, and whether the case could produce one.
    completion_known: bool = False
    business_complete: bool = False
    awaiting: tuple[str, ...] = ()
    #: --- the policy gate (3A.7) ---
    policy: CaseEligibilityOutcome | None = None
    policy_override: PolicyOverrideNotice | None = None
    #: True from entering the supervisor wait until it resolves, so
    #: `continue_as_new` can tell "still waiting on a human" from "not evaluated
    #: yet" -- the two look identical from the absence of a work item.
    policy_review_open: bool = False
    policy_review_deadline_iso: str | None = None
    policy_reminders_sent: int = 0
    #: The queue a non-standard route hands its verification to.
    support_queue: str | None = None


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
        """Every notice accumulates; the run loop drains them (plan sect. 10.1).

        What this replaces was first-response-wins, and `run` completed as soon
        as it had recorded that one response. Delayed tracking, a delayed label
        and a corrected RMA could therefore never arrive: a second outcome met a
        closed execution and came back `500 workflow execution already
        completed`, with the record untouched. That is not a hypothetical --
        real cases sit today with an RMA and a label and `trackingReference:
        null`, their workflow `COMPLETED`.

        **Deduplicated on `supportEventId`, never on "have we had one yet".**
        First-wins is what currently makes redelivery safe, and appending
        unconditionally would throw that away: the transport is at-least-once by
        design, so the second delivery *will* happen and would double-apply. The
        id is the event's identity -- the same one the unique index in
        `case_support_events` enforces -- so the second arrival is recognisable
        as the same business event rather than as a new one.

        A notice carrying **no** id keeps the old rule exactly. It cannot be
        told apart from a second one, so the first is applied and the rest are
        comments on a case that has moved on. That is what a sender predating
        the field gets, and it is why turning this on needs no coordinated
        deploy.
        """
        event_id = notice.support_event_id
        if event_id:
            if event_id in self._state.support_event_ids:
                workflow.logger.info(
                    "support response %s was already accepted; ignoring the redelivery",
                    event_id,
                )
                return
            self._state.support_event_ids.append(event_id)
            # Bounded, newest last. See `_TRACKED_SUPPORT_EVENT_IDS`.
            del self._state.support_event_ids[:-_TRACKED_SUPPORT_EVENT_IDS]
        else:
            if self._state.unkeyed_support_applied:
                return
            self._state.unkeyed_support_applied = True
        self._state.pending_support.append(notice)

    @workflow.signal(name="policy_override")
    def policy_override(self, notice: PolicyOverrideNotice) -> None:
        """A supervisor's decision on a case the evaluator sent to review.

        First override wins, exactly as `support_response` does: a supervisor
        clicking twice, or a redelivered signal, must not decide the case twice.

        Only `APPROVE` and `REJECT` resolve the wait. `REVIEW_REQUIRED` is what
        the case is already in, so accepting it as an override would end the wait
        by restating the problem -- and it is refused here as well as at the
        endpoint, because a signal can be sent by anything holding the workflow
        id.

        The notice is recorded, never merged into the evaluation. The original
        decision is on the fact log and stays there; this is appended over it.
        """
        if self._state.policy_override is not None:
            return
        if notice.override_decision not in (
            PolicyDecisionName.APPROVE.value,
            PolicyDecisionName.REJECT.value,
        ):
            workflow.logger.warning(
                "policy override %s is not a decision that resolves a review",
                notice.override_decision,
            )
            return
        self._state.policy_override = notice

    @workflow.signal(name="cancel_case")
    def cancel_case(self, command: CancelCaseCommand) -> None:
        if self._state.cancellation is None:
            self._state.cancellation = command

    @workflow.signal(name="terminal_command")
    def terminal_command(self, command: CaseTerminalCommand) -> None:
        """A validated instruction to end the case (plan sect. 10.3).

        The signal handler does the validation that can be done without reading
        the case: the command has to be one of the three, `EXPIRE` cannot come
        from a sender, and `CANCEL` has to name its actor. What it cannot decide
        here is whether a `COMPLETE` is allowed -- that needs the requirement
        table over the case's children -- so a `COMPLETE` is *accepted* here and
        refused in the run loop, where the completion reading is.

        First command wins, exactly as `policy_override` does. A close is not
        something to apply twice, and the second sender is describing a case
        that has already ended.

        A **refused** `COMPLETE` releases the slot again, because it never won:
        the case went on collecting, and an operator who fixes what it was
        waiting for must be able to ask a second time.
        """
        if self._state.terminal_command is not None:
            return
        if command.command not in (
            TerminalCommandName.COMPLETE.value,
            TerminalCommandName.CANCEL.value,
            TerminalCommandName.EXPIRE.value,
        ):
            workflow.logger.warning(
                "%s is not a terminal command; the case is not closed by it", command.command
            )
            return
        if command.command == TerminalCommandName.EXPIRE.value:
            # System-initiated, and the system does not send itself signals.
            # An actor able to assert an expiry would have an unaudited
            # cancellation, which is the unrestricted close in another shape.
            workflow.logger.warning("EXPIRE is system-initiated; refusing a signalled expiry")
            return
        if not command.actor.strip():
            workflow.logger.warning("a terminal command with no actor is not audited; refusing")
            return
        self._state.terminal_command = command

    @workflow.query(name="execution_state")
    def execution_state(self) -> ReturnCaseState:
        policy = self._state.policy
        override = self._state.policy_override
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
            policy_state=policy.state if policy else None,
            policy_route=policy.route if policy else None,
            policy_decision=policy.decision if policy else None,
            policy_overridden=override is not None,
            support_pending=len(self._state.pending_support),
            support_responses_applied=self._state.support_responses_applied,
            business_complete=self._state.business_complete,
            awaiting=self._state.awaiting,
            terminal_command=self._state.applied_terminal_command,
        )

    # --- run ----------------------------------------------------------------

    @workflow.run
    async def run(self, workflow_input: ReturnCaseWorkflowInput) -> ReturnCaseOutcome:
        self._input = workflow_input
        self._state.reminders_sent = workflow_input.reminders_sent
        self._state.policy_reminders_sent = workflow_input.policy_reminders_sent
        self._state.work_item_id = workflow_input.resumed_work_item_id
        self._state.support_event_ids = list(workflow_input.resumed_support_event_ids)
        self._state.unkeyed_support_applied = workflow_input.resumed_unkeyed_support_applied
        self._state.lifetime_start_iso = workflow_input.resumed_lifetime_start_iso
        self._state.business_complete = workflow_input.resumed_business_complete
        self._state.completion_known = workflow_input.resumed_business_complete
        if workflow_input.resumed_status is not None:
            self._state.status = workflow_input.resumed_status

        timings = workflow_input.timings

        if self._state.work_item_id is None:
            if workflow_input.resumed_policy_state is None:
                await self._gather_bay(timings)
                if self._cancelled():
                    return await self._finish_cancelled()
            # The policy gate (3A.7). Everything below this line is the path a
            # return takes *after* the deterministic evaluator allowed it, and
            # `_open_support` is the next statement on purpose: "a rejected
            # return cannot reach Support" is then a property of two adjacent
            # lines rather than of a convention.
            if not await self._policy_cleared(timings):
                # A cancellation that arrived while a supervisor was deciding
                # ends the case as cancelled rather than as whatever the gate
                # was in the middle of.
                if self._cancelled():
                    return await self._finish_cancelled()
                return self._outcome()
            if self._cancelled():
                return await self._finish_cancelled()
            await self._open_support(timings)
            if self._cancelled():
                return await self._finish_cancelled()

        return await self._serve_case(timings)

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

    # --- the policy gate (3A.7) ---------------------------------------------

    async def _policy_cleared(self, timings: ReturnCaseTimings) -> bool:
        """Whether this return may be taken to Support. The only entry point.

        `True` means "open a work item"; `False` means the case has reached a
        state it does not leave on its own -- rejected, or parked -- and `run`
        returns the outcome. Nothing else in this module decides that question,
        so there is exactly one place a future edit can get it wrong.
        """
        if self._require_input().resumed_policy_state == PolicyGateState.AWAITING_OVERRIDE.value:
            # History was reset while a supervisor was deciding. Resume the wait
            # rather than the evaluation: re-evaluating would produce the same
            # `REVIEW_REQUIRED` and hand the supervisor a fresh clock.
            self._state.policy = CaseEligibilityOutcome(
                state=PolicyGateState.EVALUATED.value,
                route=PolicyRouteName.STANDARD_RETURN.value,
                decision=PolicyDecisionName.REVIEW_REQUIRED.value,
            )
            return await self._resolve_policy_review(timings)
        return await self._evaluate_policy(timings)

    async def _evaluate_policy(self, timings: ReturnCaseTimings) -> bool:
        """Run the deterministic evaluator and act on what it says.

        **Fail closed, in three different directions, none of which approves.**
        An activity failure the retry policy could not survive is treated as an
        evaluator failure; an evaluator failure is `REVIEW_REQUIRED`; an absent
        policy is an operational failure that parks the case. The last one is
        deliberately not review: a deployment that published no rule set would
        otherwise show every return quietly queued for a human, which looks
        exactly like the evaluator working.
        """
        workflow_input = self._require_input()
        try:
            outcome: CaseEligibilityOutcome = await workflow.execute_activity(
                "evaluate_case_eligibility",
                EvaluateCaseEligibilityInput(
                    case_id=workflow_input.case_id,
                    tenant_id=workflow_input.tenant_id,
                    configuration_release_id=workflow_input.configuration_release_id,
                    # The workflow's clock, not the activity's: the instant
                    # decides a window boundary and must survive a retry.
                    evaluated_at_iso=workflow.now().isoformat(),
                    business_calendar_id=timings.business_calendar_id,
                    timezone=timings.timezone,
                    fact_id_seed=str(workflow.uuid4()),
                ),
                result_type=CaseEligibilityOutcome,
                start_to_close_timeout=_PERSIST_TIMEOUT,
                retry_policy=_PERSIST_RETRY,
            )
        except ActivityError:
            workflow.logger.error(
                "policy evaluation failed for case %s; holding for review",
                workflow_input.case_id,
            )
            outcome = CaseEligibilityOutcome(
                state=PolicyGateState.EVALUATION_FAILED.value,
                failure_reason="POLICY_EVALUATION_ACTIVITY_FAILED",
            )
        self._state.policy = outcome

        if outcome.state == PolicyGateState.SKIPPED_BY_CONFIGURATION.value:
            # Proceed, but set no status. `POLICY_APPROVED` would be a verdict
            # nobody reached, and it is the one thing a suspended gate must not
            # produce -- the activity has already recorded the skip and the
            # operator's reason on the fact log, which is what Support reads.
            workflow.logger.info(
                "policy evaluation skipped by configuration for case %s",
                workflow_input.case_id,
            )
            return True

        if outcome.state == PolicyGateState.POLICY_NOT_CONFIGURED.value:
            return await self._park_for_policy_unavailable(
                outcome.failure_reason or PolicyGateState.POLICY_NOT_CONFIGURED.value
            )
        if outcome.state != PolicyGateState.EVALUATED.value:
            return await self._hold_for_policy_review(
                timings, outcome.failure_reason or PolicyGateState.EVALUATION_FAILED.value
            )

        if outcome.route in (
            PolicyRouteName.WARRANTY.value,
            PolicyRouteName.DELIVERY_CLAIM.value,
        ):
            # Not terminal, and not a decision. Support verifies both inside this
            # application and an approved case rejoins the ordinary RMA
            # lifecycle, so the route only decides which queue asks the question.
            self._state.support_queue = outcome.support_queue
            return True

        if outcome.decision == PolicyDecisionName.APPROVE.value:
            await self._set_status(ReturnCaseStatus.POLICY_APPROVED)
            return True
        if outcome.decision == PolicyDecisionName.REJECT.value:
            return await self._reject_by_policy(
                outcome.reason_codes[0] if outcome.reason_codes else "POLICY_REJECTED"
            )
        return await self._hold_for_policy_review(
            timings,
            outcome.reason_codes[0] if outcome.reason_codes else "POLICY_REVIEW_REQUIRED",
        )

    async def _reject_by_policy(self, reason: str) -> bool:
        """Terminal, and no work item was opened. The audit's central defect."""
        self._state.parked_reason = None
        await self._set_status(ReturnCaseStatus.POLICY_REJECTED, fact_value=reason)
        return False

    async def _park_for_policy_unavailable(self, reason: str) -> bool:
        """No published rule set. An operational failure, not an eligibility answer."""
        workflow.logger.error(
            "no return eligibility policy is published; parking case %s",
            self._require_input().case_id,
        )
        self._state.parked_reason = reason
        await self._set_status(ReturnCaseStatus.RECOVERY_REQUIRED, fact_value=reason)
        return False

    async def _hold_for_policy_review(self, timings: ReturnCaseTimings, reason: str) -> bool:
        await self._set_status(ReturnCaseStatus.AWAITING_POLICY_REVIEW, fact_value=reason)
        return await self._resolve_policy_review(timings)

    async def _resolve_policy_review(self, timings: ReturnCaseTimings) -> bool:
        """Wait for a supervisor, then do what they said. Never approve unasked."""
        await self._await_policy_override(timings)
        if self._cancelled():
            return False
        override = self._state.policy_override
        if override is None:
            # Nobody answered inside the window. Parked rather than left waiting
            # silently, and the status stays `AWAITING_POLICY_REVIEW` because
            # that is still true: the return needs a person, and no work item
            # was ever opened for it.
            self._state.parked_reason = "POLICY_REVIEW_UNANSWERED"
            await self._set_status(
                ReturnCaseStatus.AWAITING_POLICY_REVIEW, fact_value="POLICY_REVIEW_UNANSWERED"
            )
            return False
        if override.override_decision == PolicyDecisionName.APPROVE.value:
            await self._set_status(
                ReturnCaseStatus.POLICY_APPROVED, fact_value="POLICY_OVERRIDE_APPROVED"
            )
            return True
        return await self._reject_by_policy("POLICY_OVERRIDE_REJECTED")

    async def _await_policy_override(self, timings: ReturnCaseTimings) -> None:
        """`_await_support`'s durable-timer cycle, waiting on a supervisor instead.

        Deliberately a second loop rather than a parameter on the first one.
        Phase 4 rewrites the Support wait into an accumulating drain, and a
        shared helper would make this gate part of that rewrite; the two also
        differ in the one place a reminder would land -- there is no Channel B
        thread here, because refusing to open one is the entire point of the
        gate, so the cadence bounds the wait and escalates by parking rather than
        by posting to a queue that does not exist.

        Everything durable about the Support wait is kept: the deadline is
        business time resolved by an activity, each leg is a Temporal timer, and
        a long wait resets its history through `continue_as_new` carrying the
        deadline so the supervisor's clock does not restart.
        """
        resumed = self._require_input().resumed_policy_deadline_iso
        deadline = (
            datetime.fromisoformat(resumed)
            if resumed is not None
            else await self._business_deadline(timings, timings.support_response_wait_seconds)
        )
        self._state.policy_review_deadline_iso = deadline.isoformat()
        self._state.policy_review_open = True
        try:
            while self._state.policy_override is None and self._state.cancellation is None:
                remaining = deadline - workflow.now()
                if remaining <= timedelta(0):
                    return
                next_tick = await self._business_deadline(
                    timings, timings.reminder_interval_seconds
                )
                interval = max(next_tick - workflow.now(), timedelta(0))
                try:
                    await workflow.wait_condition(
                        lambda: (
                            self._state.policy_override is not None
                            or self._state.cancellation is not None
                        ),
                        timeout=min(interval, remaining),
                        timeout_summary="policy-review-wait",
                    )
                    return
                except TimeoutError:
                    pass

                if self._state.policy_reminders_sent >= timings.max_reminders:
                    return
                self._state.policy_reminders_sent += 1

                if workflow.info().is_continue_as_new_suggested():
                    await workflow.wait_condition(lambda: workflow.all_handlers_finished())
                    workflow.continue_as_new(self._continued_input())
        finally:
            self._state.policy_review_open = False

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
                # Set only by a warranty or delivery-claim route. One thread per
                # case either way -- the queue decides who verifies it, not how
                # many conversations a human is handed.
                queue=self._state.support_queue,
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

    # --- the Support drain (plan sect. 10.1, 10.3) ---------------------------

    async def _serve_case(self, timings: ReturnCaseTimings) -> ReturnCaseOutcome:
        """Wait, drain, assess -- until the case is complete, ended or capped.

        The loop plan sect. 10.3 asks for: *continue while `businessComplete` is
        false and the deadline has not passed, on the existing reminder
        cadence*. What it replaces is a single wait followed by an unconditional
        return, which is what made a case unreachable the moment it had recorded
        anything at all.

        Every exit is named. There is no path out of here that is "the loop
        stopped": the case is complete, a validated command ended it, the
        deadline passed, the absolute lifetime cap was reached, or a graph sync
        failed and parked it. An unnamed exit is an unaudited close.
        """
        deadline = await self._support_deadline(timings)
        self._state.lifetime_start_iso = (
            self._state.lifetime_start_iso or workflow.now().isoformat()
        )
        while True:
            waited = await self._await_support(timings, deadline)
            if self._cancelled():
                return await self._finish_cancelled()

            drained = await self._drain_support()
            if drained is None:
                # A graph sync failed and parked the case. The park is terminal
                # and has already been recorded.
                return self._outcome()
            if self._cancelled():
                return await self._finish_cancelled()

            ended = await self._resolve_terminal_command()
            if ended is not None:
                return ended

            if self._state.business_complete:
                return await self._close_business_complete()

            if drained and not self._state.completion_known:
                # Nothing on this deployment can say whether the case is done --
                # an activity double, or a repository that cannot assemble the
                # projection. Recording the outcome is then where the case ends,
                # which is exactly what it did before this phase. Holding it open
                # on an unanswerable question would be worse than closing it.
                return self._outcome()

            if self._lifetime_expired(timings):
                return await self._expire("CASE_LIFETIME_CAP_REACHED")
            if waited is _Waited.DEADLINE:
                if self._state.support is None:
                    return await self._park(timings.on_reminders_exhausted)
                return await self._expire("SUPPORT_INCOMPLETE_AT_DEADLINE")
            if waited is _Waited.REMINDERS_EXHAUSTED:
                return await self._park(timings.on_reminders_exhausted)

    async def _support_deadline(self, timings: ReturnCaseTimings) -> datetime:
        """The Support deadline, resolved once and remembered.

        Once, and that is load-bearing now that the wait is a loop: resolving it
        per pass would move the deadline forward every time a notice arrived,
        and a case Support answered often would never reach it.
        """
        resumed = self._require_input().resumed_support_deadline_iso
        deadline = (
            datetime.fromisoformat(resumed)
            if resumed is not None
            else await self._business_deadline(timings, timings.support_response_wait_seconds)
        )
        self._state.support_deadline_iso = deadline.isoformat()
        return deadline

    def _lifetime_expired(self, timings: ReturnCaseTimings) -> bool:
        """Whether the case has been open longer than any case may be.

        The cap the plan asks for. It is not a second deadline: the Support
        deadline is a service level and is measured in business time, while this
        is the point past which the platform stops holding a case open at all,
        however many times its history has been reset.
        """
        started = self._state.lifetime_start_iso
        if started is None or timings.absolute_lifetime_seconds <= 0:
            return False
        elapsed = workflow.now() - datetime.fromisoformat(started)
        return elapsed >= timedelta(seconds=timings.absolute_lifetime_seconds)

    async def _await_support(self, timings: ReturnCaseTimings, deadline: datetime) -> _Waited:
        """Wait, remind, wait -- until something arrives or the wait runs out.

        Each leg is a durable timer, so the whole cycle survives a restart. The
        cadence is the reminder interval; the overall deadline is the Support
        wait, and reaching either one first is meaningful, so both are honoured
        rather than collapsed into one number.

        Both are **working** durations (SLA-01). They were wall clock, and the
        configuration had said otherwise since it was written: a return raised
        at 16:30 on a Friday with an eight-hour wait, two-hour reminders and a
        cap of three chased Support at 18:30, 20:30 and 22:30 into an empty
        queue and parked itself at 00:30 on Saturday, having spent every one of
        its reminders while nobody was there.

        **Exhausting the reminders no longer ends the wait when Support has
        already answered something.** The cap bounds how often a human is
        chased, not how long a case may collect the rest of its own answer -- and
        a case that had its RMA and was waiting on a label would otherwise be
        parked with the label still to come. With nothing received at all it
        parks exactly as it did before, because then there is nothing to wait
        for and somebody has to be told.
        """

        def _arrived() -> bool:
            return bool(
                self._state.pending_support
                or self._state.cancellation is not None
                or self._state.terminal_command is not None
            )

        while not _arrived():
            remaining = deadline - workflow.now()
            if remaining <= timedelta(0):
                return _Waited.DEADLINE
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
                    _arrived,
                    timeout=min(interval, remaining),
                    timeout_summary="support-wait",
                )
                return _Waited.ARRIVED
            except TimeoutError:
                pass

            if self._state.reminders_sent >= timings.max_reminders:
                if self._state.support is None:
                    return _Waited.REMINDERS_EXHAUSTED
                # Partial answer in hand. The reminders are spent; the wait is
                # not, and it runs to the deadline in silence.
            else:
                self._state.reminders_sent += 1
                await self._send_reminder()

            if workflow.info().is_continue_as_new_suggested():
                # A case can wait days. Reset history and carry the coordination
                # state so callers and the outstanding work item are unaffected.
                await workflow.wait_condition(lambda: workflow.all_handlers_finished())
                workflow.continue_as_new(self._continued_input())
        return _Waited.ARRIVED

    async def _drain_support(self) -> int | None:
        """Apply every queued notice, oldest first. `None` if the case parked.

        Oldest first, and one activity call each. Collapsing the queue into a
        single call would lose the ordering Support answered in, and the
        activity's merge is defined over one event -- two events merged in the
        workflow would decide between a corrected RMA and the RMA it corrected
        here, on the side of the boundary that cannot read the record.
        """
        applied = 0
        while self._state.pending_support:
            notice = self._state.pending_support.pop(0)
            if not await self._record_support_outcome(notice):
                return None
            applied += 1
        return applied

    async def _resolve_terminal_command(self) -> ReturnCaseOutcome | None:
        """Apply the validated command, or refuse it and carry on.

        The only place a `COMPLETE` is decided, because it is the only place the
        completion reading exists. A refusal is recorded on the case rather than
        merely logged: a console that asked to close a case and was told nothing
        would ask again, and an operator has to be able to see why it did not.
        """
        command = self._state.terminal_command
        if command is None:
            return None
        if command.command == TerminalCommandName.COMPLETE.value:
            if not self._state.business_complete:
                await self._refuse_terminal_command(command, "CASE_NOT_BUSINESS_COMPLETE")
                # Cleared, so the case goes on collecting rather than re-refusing
                # the same command on every pass.
                self._state.terminal_command = None
                return None
            return await self._close_business_complete(command)
        return await self._cancel_by_command(command)

    async def _refuse_terminal_command(self, command: CaseTerminalCommand, reason: str) -> None:
        """Record the refusal, at the status the case already has.

        Deliberately through `_set_status` with the current status: the case did
        not move, and a refusal that changed its status would be the close it
        just refused. What it does move is the revision, which is correct -- a
        client polling the case has a new fact to read.
        """
        await self._set_status(
            ReturnCaseStatus(self._state.status),
            fact_value=f"{command.command}_REFUSED:{reason}",
        )
        workflow.logger.warning(
            "terminal command %s refused for case %s: %s",
            command.command,
            self._require_input().case_id,
            reason,
        )

    async def _close_business_complete(
        self, command: CaseTerminalCommand | None = None
    ) -> ReturnCaseOutcome:
        """The case satisfied its requirement set. Close it, and say so.

        `CLOSED` is the persisted status a completed case reaches; the read path
        splits it into `COMPLETED` or `COMPLETED_EXTERNAL_SETTLEMENT` on
        settlement, which is not this module's decision to make.
        """
        if command is not None:
            self._state.applied_terminal_command = command.command
        await self._set_status(
            ReturnCaseStatus.CLOSED,
            fact_value=(
                f"COMPLETE:{command.reason_code}" if command is not None else "BUSINESS_COMPLETE"
            ),
        )
        return self._outcome()

    async def _cancel_by_command(self, command: CaseTerminalCommand) -> ReturnCaseOutcome:
        """A `CANCEL`, audited with a server-derived actor and instant.

        The instant is `workflow.now()` and the actor is the one the endpoint
        stamped. Neither is a value the request body could carry -- there is no
        timestamp field on the command at all, which is the strongest form of
        "never trust a caller-supplied audit timestamp" available.
        """
        self._state.applied_terminal_command = command.command
        self._state.cancellation = CancelCaseCommand(
            reason=command.reason or command.reason_code,
        )
        await self._set_status(
            ReturnCaseStatus.CANCELLED,
            fact_value=(
                f"CANCEL:{command.reason_code}:{command.actor}:{workflow.now().isoformat()}"
            ),
        )
        return self._outcome()

    async def _expire(self, reason: str) -> ReturnCaseOutcome:
        """The deadline, or the lifetime cap, ended the wait. Nobody did.

        Parked rather than closed, and the distinction is not cosmetic. A case
        whose Support answer never completed has an RMA that may yet be
        fulfilled, and marking it terminal would stop every client polling a
        return that is still real. `EXPIRED` exists in the read contract's
        vocabulary and no persisted status maps to it today -- see the report
        accompanying this change -- so the honest state is the parked one, under
        a reason that names the cap that was hit.
        """
        self._state.applied_terminal_command = TerminalCommandName.EXPIRE.value
        self._state.parked_reason = reason
        await self._set_status(ReturnCaseStatus(self._state.status), fact_value=reason)
        return self._outcome()

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

    async def _record_support_outcome(self, support: SupportResponseNotice) -> bool:
        """Persist one notice and read back where the case now stands.

        Returns whether the case may carry on. `False` means a graph sync failed
        and the case is parked -- the one failure on this path that stops
        everything, because an RMA in the store and absent from the graph is one
        no agent can tell an associate about.

        The record ids the sync is given come from the **receipt** where there
        is one. A second reply about an RMA the case already holds updates the
        existing record, and the ids minted here are only the ones a *new*
        record would be created under; syncing the minted set would point a
        targeted read at documents nothing was written to.
        """
        workflow_input = self._require_input()
        return_record_ids = tuple(str(workflow.uuid4()) for _ in support.records)
        receipt: SupportOutcomeReceipt | None = await workflow.execute_activity(
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
                support_event_id=support.support_event_id,
            ),
            result_type=_RECEIPT_RESULT_TYPE,
            start_to_close_timeout=_PERSIST_TIMEOUT,
            retry_policy=_PERSIST_RETRY,
        )
        self._state.support = support
        self._state.support_responses_applied += 1
        for record in support.records:
            if record.return_reference not in self._state.return_references:
                self._state.return_references.append(record.return_reference)
        if receipt is not None and receipt.completion_known:
            self._state.completion_known = True
            self._state.business_complete = receipt.business_complete
            self._state.awaiting = receipt.awaiting
        synchronized = (
            receipt.record_ids if receipt is not None and receipt.record_ids else return_record_ids
        )
        if not await self._synchronize_return_records(synchronized):
            return False
        await self._set_status(
            ReturnCaseStatus.CLOSED if support.rejected else ReturnCaseStatus.RMA_RECEIVED
        )
        return True

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
            resumed_policy_state=(
                PolicyGateState.AWAITING_OVERRIDE.value if self._state.policy_review_open else None
            ),
            resumed_policy_deadline_iso=self._state.policy_review_deadline_iso,
            policy_reminders_sent=self._state.policy_reminders_sent,
            # Bounded already by the handler; carried so a redelivery landing on
            # the far side of the reset is still recognised as one.
            resumed_support_event_ids=tuple(self._state.support_event_ids),
            resumed_unkeyed_support_applied=self._state.unkeyed_support_applied,
            resumed_lifetime_start_iso=self._state.lifetime_start_iso,
            resumed_business_complete=self._state.business_complete,
        )

    def _outcome(self) -> ReturnCaseOutcome:
        policy = self._state.policy
        return ReturnCaseOutcome(
            case_id=self._require_input().case_id,
            status=self._state.status,
            work_item_id=self._state.work_item_id,
            return_references=tuple(self._state.return_references),
            reminders_sent=self._state.reminders_sent,
            bay_reference=self._state.bay.bay_reference if self._state.bay else None,
            parked_reason=self._state.parked_reason,
            graph_generation_id=self._state.graph_generation_id,
            policy_state=policy.state if policy else None,
            policy_route=policy.route if policy else None,
            policy_decision=policy.decision if policy else None,
            policy_overridden=self._state.policy_override is not None,
            support_queue=self._state.support_queue,
            support_responses_applied=self._state.support_responses_applied,
            business_complete=self._state.business_complete,
            awaiting=self._state.awaiting,
            terminal_command=self._state.applied_terminal_command,
        )
