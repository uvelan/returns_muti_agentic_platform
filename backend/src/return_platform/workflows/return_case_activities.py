"""Everything `ReturnCaseWorkflow` is not allowed to do itself.

The split is the whole reason the workflow is replayable: Mongo writes, model
calls and support-thread mutations all live here, behind activity names the
workflow references as strings. Nothing in this module is imported by the
workflow module.

Each activity is idempotent on a key the *workflow* supplies, not one minted
here. A Temporal retry re-runs the activity with identical input, and an
idempotency key generated inside would be new each time -- which for
`send_support_reminder` means a second message to a human.

The same derivation is what makes case facts safe to re-append, and it is why
every append below goes through `_append_fact_once`. The fact log is
insert-only against a unique `factId` on purpose, so a derived id that arrives
a second time raises rather than overwrites -- and an activity that let that
raise would fail its own retry on the record of the previous attempt, before
reaching whatever it was retried to do.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Final, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pymongo.errors import DuplicateKeyError
from temporalio import activity

from return_platform.configuration.return_configuration import (
    ReturnPlatformConfiguration,
    build_return_method_requirement_table,
)
from return_platform.operations.business_calendar import (
    BusinessCalendar,
    WorkingPeriod,
    advance_business_time,
)
from return_platform.operations.case_projection.assembly import (
    SUPPORT_OUTCOME_FACT,
    SUPPORT_OUTCOME_REASON_FACT,
)
from return_platform.operations.case_projection.completion import ReturnMethodRequirementTable
from return_platform.operations.case_projection.projection import project_case
from return_platform.operations.case_projection.vocabulary import (
    ReturnRecordStatus,
    SupportOutcome,
)
from return_platform.operations.errors import ConcurrencyConflictError
from return_platform.operations.models import FactAcquisition, FactChannel
from return_platform.operations.order_lines.case_detail import (
    CaseOrderLineDetail,
    OrderLineDetailPort,
)
from return_platform.operations.order_lines.reservations import (
    QuantityReservationExpiredError,
    ReservationState,
    is_held,
)
from return_platform.operations.repository import OperationalRepository
from return_platform.operations.return_issuance import ReturnRecordStorePort
from return_platform.operations.review_aggregate import (
    SYSTEM_ACTOR,
    PendingRevisionError,
    ReviewConflictError,
    ReviewState,
    ReviewStateError,
    ReviewVersionMismatchError,
    TemplateReviewParkReason,
    canonical_review_payload,
)
from return_platform.operations.support_events import canonical_payload_digest
from return_platform.operations.support_handoff import (
    SupportHandoffBay,
    SupportHandoffCustomer,
    SupportHandoffItem,
    SupportHandoffOrder,
    SupportHandoffPolicy,
    SupportHandoffReturn,
    compose_support_handoff,
)
from return_platform.operations.support_template_draft import (
    snapshot_as_facts,
    support_template_snapshot,
)
from return_platform.operations.support_template_gate import (
    PAYLOAD_GAPS,
    SupportTemplateGateService,
    request_ids_for,
)
from return_platform.policy.evaluator import PolicyClock, evaluate_return_eligibility
from return_platform.policy.outcome import PolicyOutcome
from return_platform.policy.vocabulary import PolicyRoute
from return_platform.workflows.case_customer_identity import (
    resolve_confirmed_order_customer,
)
from return_platform.workflows.case_order_date import (
    resolve_confirmed_order_dates,
    resolve_confirmed_order_ship_via,
)
from return_platform.workflows.case_policy_facts import (
    AssembledPolicyFacts,
    assemble_policy_evaluation_input,
)
from return_platform.workflows.return_case_workflow import (
    BayResultNotice,
    CaseEligibilityOutcome,
    DraftSupportRequestInput,
    EvaluateCaseEligibilityInput,
    OpenSupportWorkItemInput,
    PolicyGateState,
    RecordCaseCustomerInput,
    RecordCaseStatusInput,
    RecordSupportOutcomeInput,
    RequestBayAssignmentInput,
    ResolveBusinessDeadlineInput,
    ResolvedBusinessDeadline,
    SendSupportReminderInput,
    SnapshotSentTemplateInput,
    SupportOutcomeReceipt,
    SupportReturnRecord,
    SynchronizeReturnRecordsInput,
    TemplateDeliveryResult,
    TemplateReviewDraftInput,
    TemplateReviewDraftResult,
    TemplateReviewDraftSet,
    TemplateReviewRevisionInput,
)

__all__ = [
    "RETURN_RECORD_MERGED_FIELDS",
    "CaseBayPlacementPort",
    "ReturnCaseActivities",
    "ReturnRecordGraphSyncPort",
    "ReturnRecordStorePort",
    "ReturnRecordSyncOutcome",
    "SupportSlaBasis",
]

logger = logging.getLogger("return_platform.workflows.return_case_activities")


class SupportSlaBasis(StrEnum):
    """Where a Support work item's `slaDueAt` came from. Recorded, never inferred.

    A deadline is only as good as the question it answers, and the values below
    answer different ones. `SUPPORT_ACKNOWLEDGEMENT` is the desk's own promise
    -- `workflow.sla_minutes.support_acknowledgement`, a fixed offset from the
    moment the thread opened. `DELIVERY_CLAIM_REPORTING_WINDOW` is the
    customer's: `delivery_claim.reporting_window` business days after delivery,
    computed once by the policy evaluation that routed the case and carried to
    the work item rather than recomputed, because a deadline computed twice
    against two calendars is a deadline nobody can reconcile.

    The third value is the one that matters most. A delivery claim whose
    `delivery_date` the platform never learned has **no** reporting deadline --
    `DeliveryClaimWindow` reports `UNDETERMINED` rather than inventing one --
    and the work item falls back to the acknowledgement SLA. That fallback is
    honest, but it has to be legible *as* a fallback: a work item silently
    carrying the desk's five minutes where the customer's two business days
    were expected is indistinguishable from one whose window was computed and
    happened to be short.

    Recorded as the case fact `support_sla_basis`, beside the
    `policy_delivery_claim_window_state` it follows from.
    """

    #: The generic desk SLA. Every ordinary work item, and what a delivery claim
    #: falls back to -- distinguished from a computed window by the third member
    #: rather than by nothing.
    SUPPORT_ACKNOWLEDGEMENT = "SUPPORT_ACKNOWLEDGEMENT"
    #: `slaDueAt` is the configured delivery-claim reporting deadline.
    DELIVERY_CLAIM_REPORTING_WINDOW = "DELIVERY_CLAIM_REPORTING_WINDOW"
    #: A delivery claim with no computable window. `slaDueAt` is the generic
    #: desk SLA and this says so.
    DELIVERY_CLAIM_REPORTING_WINDOW_UNDETERMINED = "DELIVERY_CLAIM_REPORTING_WINDOW_UNDETERMINED"


def _fact_text(facts: Mapping[str, Mapping[str, Any]], name: str) -> str | None:
    """One latest fact's value as text, or `None` when it is absent or blank.

    Blank counts as absent: `_append_policy_facts` never writes an empty string,
    so one in the log is not a value anybody stated.
    """
    fact = facts.get(name)
    if fact is None:
        return None
    value = fact.get("value")
    if value is None:
        return None
    text = str(value).strip()
    return text or None


#: The queue each non-standard route is verified on, by name.
#:
#: The names are matched against `support.queues` in the active release rather
#: than assumed: a queue this deployment has not declared is a work item nobody
#: is looking at, which is worse than one on the default queue with the route
#: recorded beside it on the case. `STANDARD_RETURN` is absent because it is not
#: a routing decision -- it is the ordinary path, on the ordinary queue.
_ROUTE_QUEUES: Final[dict[PolicyRoute, str]] = {
    PolicyRoute.WARRANTY: "WARRANTY_SUPPORT",
    PolicyRoute.DELIVERY_CLAIM: "DELIVERY_CLAIM_SUPPORT",
}

#: The fields a Support notice may carry about one RMA, and the fact name each
#: is recorded under. Stored key first, then the `SupportReturnRecord`
#: attribute, then the case-fact name.
#:
#: **Merged, never replaced.** Support answers repeatedly, and a later notice
#: says only what it knows: a tracking number arriving two hours after the RMA
#: carries `label_reference=None`, and applying that null over the label already
#: on the record would delete it. So every write below is "this field, if the
#: notice gave one", and a `None` is the absence of a statement rather than a
#: statement of absence -- exactly the reading `support_events._canonical`
#: already takes when it drops nulls before hashing.
#:
#: `return_method` is the fact name deliberately, and not `approved_return_method`
#: (D23). `warehouse/case_placement.py` already reads a case fact of that name to
#: choose a bay, so the other spelling would resolve the Copilot's completion
#: profile while bay placement still normalized `None`.
RETURN_RECORD_MERGED_FIELDS: Final[tuple[tuple[str, str, str], ...]] = (
    ("trackingReference", "tracking_reference", "tracking_reference"),
    ("labelReference", "label_reference", "label_reference"),
    ("returnLocation", "return_location", "return_location"),
    (
        "shippingInstructionReference",
        "shipping_instruction_reference",
        "shipping_instruction_reference",
    ),
    ("returnMethod", "return_method", "return_method"),
    #: Audit finding #9. `ShipmentProjection.carrier` had a declared field and no
    #: chain to reach it; this row is the link that carries Support's answer from
    #: `SupportReturnRecord.carrier` to `ReturnRecordView.carrier`, from which
    #: `assembly.project_shipments` reads it. Merged like everything above, so a
    #: later notice arriving with `carrier=None` -- a tracking number two hours
    #: after the RMA -- does not blank a carrier already on the record.
    ("carrier", "carrier", "carrier"),
)


@dataclass
class _RecordPlan:
    """One RMA of one notice, resolved against what the case already holds.

    Mutable, unlike everything else here, because the create path can lose a
    race to a concurrent writer and has to become an update path -- and a plan
    that could not be corrected in place would need a second copy of the merge
    to do it.
    """

    #: The id the record actually lives under: the existing one where the case
    #: already holds this RMA, the workflow's minted one where it does not.
    record_id: str
    incoming: SupportReturnRecord
    #: The stored document, or `None` for an RMA the case has not seen.
    existing: dict[str, Any] | None
    #: Stored key -> value after merging the notice over the record. Only ever
    #: non-null values; a field neither side has is simply absent.
    merged: dict[str, Any]
    #: The subset of `merged` that is new information. Empty for a redelivery,
    #: which is what makes a replay write nothing and bump no revision.
    changed: dict[str, Any]


class SupportDraftPort:
    """What the workflow needs from the drafting model, and nothing more.

    A protocol rather than `StructuredOutputInvoker` directly so the activity
    can be exercised without a route pool, and so the fallback below is a
    property of this seam rather than of the gateway.
    """

    async def draft(self, *, case_id: str, facts: dict[str, Any]) -> str:  # pragma: no cover
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class ReturnRecordSyncOutcome:
    """What a record-scoped sync commits, as the activity needs to see it."""

    graph_generation_id: str
    synchronized_record_ids: tuple[str, ...]
    nodes_written: int


class ReturnRecordGraphSyncPort(Protocol):
    """The one method the case workflow needs from the targeted-sync stack.

    Structural, so `workflows` neither imports `dynamic_knowledge` nor learns
    what a generation lease is; the adapter is
    `dynamic_knowledge/integration/return_record_sync.py`. It must raise rather
    than return on failure -- this activity is `blocking`, and a port that
    reported partial success would put the decision in the wrong place.
    """

    async def synchronize_records(
        self, *, case_id: str, return_record_ids: tuple[str, ...]
    ) -> ReturnRecordSyncOutcome: ...


class CaseBayPlacementPort(Protocol):
    """The one method the bay activity needs from placement.

    Structural, so `workflows` does not import the agent registry or the
    warehouse observation stack to ask for a bay. The implementation is
    `operations/warehouse/case_placement.py::CaseBayPlacement`, which is the
    session engine re-keyed -- not a second one.

    It must not raise for "no bay": a state comes back as a recommendation
    carrying its reason. It may raise when placement could not be attempted at
    all, and the workflow's `ActivityError` branch records `REQUEST_FAILED`.
    """

    async def recommend(self, case_id: str) -> Any: ...


def _stated(facts: Mapping[str, Any], name: str) -> str | None:
    """One fact as a non-empty string, or nothing.

    The empty string reads as absent, which is what makes a retraction work:
    `api/order_lines.py` records a cleared contact by appending an empty value
    over it, because the fact log is insert-only and there is nothing to delete.
    """
    value = facts.get(name)
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    return trimmed or None


def _branch_associate_sentence(facts: Mapping[str, Any]) -> str:
    """Who to contact about the label, in the words the case actually holds.

    Ends with a trailing space and is `""` when the case names nobody, so the
    template above reads correctly either way. **Nothing is filled in**: a
    return with a name and no phone says the name and stops. An address
    manufactured to complete the sentence is precisely the failure the whole
    optionality of these three fields exists to prevent.
    """
    name = _stated(facts, "branch_associate_name")
    reachable = [
        value
        for value in (
            _stated(facts, "branch_associate_email"),
            _stated(facts, "branch_associate_phone"),
        )
        if value is not None
    ]
    if name is None and not reachable:
        return ""
    joined = ", ".join(reachable)
    if name is None:
        return f"The branch associate for this return can be reached at {joined}. "
    if not reachable:
        # A name and no way to reach them. Said anyway rather than dropped: it
        # is who Support should ask for at the branch, and leaving it out to
        # keep the sentence tidy would withhold the only thing we were told.
        return f"The branch associate for this return is {name}. "
    return f"The branch associate for this return is {name} ({joined}). "


def _text_of(value: Any) -> str | None:
    """One stored field as text, or `None`. Never the string `"None"`."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    return text or None


def _moment_of(value: Any) -> datetime | None:
    """A stored instant, made aware. Mongo hands back naive UTC."""
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _count_of(value: Any) -> int | None:
    """A quantity, or `None`. A bool is not a count however it stores."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _handoff_item(
    item: Mapping[str, Any],
    detail: CaseOrderLineDetail | None,
    return_method: str | None = None,
) -> SupportHandoffItem:
    """One selected line joined to what the source calls it.

    The selection is the authority for quantity, reason and condition -- they are
    the associate's answers. The source is the authority for the name, the SKU
    and the colour. Neither side is allowed to fill in for the other.
    """
    return SupportHandoffItem(
        line_reference=str(item.get("orderLineId") or item.get("orderLineReference") or ""),
        product_name=detail.description if detail is not None else None,
        colour=detail.colour if detail is not None else None,
        sku=detail.sku if detail is not None else None,
        product_reference=_text_of(item.get("productReference")),
        quantity=_count_of(item.get("quantity")),
        reason=_text_of(item.get("reason")),
        condition=_text_of(item.get("condition")),
        return_method=return_method,
    )


@dataclass(frozen=True, slots=True)
class SupportRequestDraft:
    """Both halves of the handoff, across the activity boundary.

    The text is what a person reads; the payload is the same facts structured,
    and it is what is persisted on the message so no screen has to parse the
    prose back into fields.

    Declared here and again in `return_case_workflow`, because the workflow side
    may not import this module. The two must gain a field together: the shapes
    are serialized across the activity boundary, and a field on one side only is
    a value dropped in transit.
    """

    text: str
    payload: dict[str, Any]
    #: The one line the Support queue draws for this return, composed beside the
    #: message so the row and the body are built from the same facts.
    subject: str = ""


#: Facts that describe the return itself, as opposed to which order it is
#: against. Any one of them means the associate has started describing what is
#: coming back -- the same threshold the item-selection write signals at, which
#: fires on a selection rather than on a complete one. Whether the description
#: is *complete* is a different question, answered by `awaiting` at handoff.
_RETURN_DETAIL_FACTS: Final[frozenset[str]] = frozenset(
    {
        "return_reason",
        "product_condition",
        "return_quantity",
        "ordered_quantity",
        "product_presence",
        "branch_location",
        "proof_reference",
    }
)


def _associate_described_the_return(
    selected: Sequence[Mapping[str, Any]], *, reason_required: bool
) -> bool:
    """Whether the associate has said enough for a human to act on the request.

    Their half of the handoff and only their half: a line, how many of it, and
    why -- where the release publishes a catalogue to choose the why from. What
    Support still owes is a different question, reported beside this one.

    Every line, not any line. A request naming three lines where one has no
    quantity is a request Support has to come back about, and reporting it
    complete because the other two were fine is how the message earns its
    reputation for being wrong.
    """
    if not selected:
        return False
    for item in selected:
        quantity = item.get("quantity")
        if not isinstance(quantity, int) or quantity < 1:
            return False
        if reason_required and not _text_of(item.get("reason")):
            return False
    return True


#: The fact-identity derivation `append_scoped_fact_once` stamps into
#: `identity_version`. The per-callsite derivation `_append_fact_once` relies
#: on predates versioning and is implicitly the unversioned original; this is
#: the first *stamped* algorithm (caller-derived id, `::record_scope` appended
#: when the fact is about one record). Bump on any change to that derivation.
SCOPED_FACT_IDENTITY_VERSION: Final[int] = 2


class ReturnCaseActivities:
    """Narrow injected surface: one repository, one support service, one drafter."""

    def __init__(
        self,
        *,
        repository: OperationalRepository,
        support_service: Any,
        drafter: SupportDraftPort | None = None,
        graph_sync: ReturnRecordGraphSyncPort | None = None,
        return_store: ReturnRecordStorePort | None = None,
        bay_placement: CaseBayPlacementPort | None = None,
        order_line_details: OrderLineDetailPort | None = None,
        configuration: Callable[[], ReturnPlatformConfiguration | None] | None = None,
        shipment_tracking: Any | None = None,
        template_gate: SupportTemplateGateService | None = None,
    ) -> None:
        self._repository = repository
        self._support = support_service
        self._drafter = drafter
        self._graph_sync = graph_sync
        self._return_store = return_store
        self._bay_placement = bay_placement
        self._order_line_details_port = order_line_details
        #: `ShipmentTrackingStore`, when the deployment tracks return shipments.
        #: Optional so every existing worker and test double keeps constructing.
        self._shipment_tracking = shipment_tracking
        # A callable, not a value: Track E's activation loop replaces the
        # process's configuration in place, and an activity that captured the
        # release it started with would go on using a holiday list a release
        # has since corrected.
        self._configuration = configuration
        #: The review gate (contracts.md sect. 6). Optional so every existing
        #: worker and test double keeps constructing, and **`None` refuses
        #: loudly** rather than degrading -- see `_gate`. A process whose
        #: histories carry the gate patch and whose activities cannot open a
        #: review is misconfigured, and an activity that quietly did nothing
        #: would leave the case waiting on a review nobody created.
        self._gate_service = template_gate

    async def _append_fact_once(self, **fact: Any) -> bool:
        """Append one derived-id fact, treating an existing one as already done.

        `append_case_fact` is `insert_one` against a unique `factId`, and that
        is deliberate -- it is what makes the log a log rather than a mutable
        row, and nothing here weakens it. But every `fact_id` on this path is
        *derived* from identifiers the workflow supplies, so a second arrival
        under the same id is not new information: it is the same observation
        about the same case, and the record of it is already durable.

        Insert-only therefore had the retry semantics inverted. Every activity
        below runs under a `RetryPolicy`, and a retry re-runs the whole body
        with identical input; a `DuplicateKeyError` on a fact the previous
        attempt already committed would fail the attempt *before* it reached
        the work it was retried for. `request_bay_assignment` is where that bit
        hardest -- its `bay_assignment_requested` marker is written before
        placement is even asked, so attempt two could never get past it and
        `_BEST_EFFORT_RETRY`'s one retry bought nothing at all.

        Returns whether this call was the one that wrote the fact, so a caller
        that cares can tell "recorded" from "was already recorded". Only a
        duplicate is absorbed: any other write failure still raises, because
        that one is a fact the log genuinely does not hold.
        """
        try:
            await self._repository.append_case_fact(**fact)
        except DuplicateKeyError:
            logger.debug(
                "case_fact_already_recorded",
                extra={
                    "case_id": fact.get("case_id"),
                    "fact_id": fact.get("fact_id"),
                    "fact_name": fact.get("fact_name"),
                },
            )
            return False
        return True

    async def append_scoped_fact_once(
        self, *, record_scope: str | None, actor_id: str | None = None, **fact: Any
    ) -> bool:
        """Append one record-scoped derived-id fact, absorbing a duplicate.

        The scoped sibling of `_append_fact_once`, and a sibling rather than a
        change to it (contracts.md sect. 4): the legacy algorithm and every id
        it has ever derived stay untouched. What this one adds is scope in the
        *identity*: the caller supplies the same derived `fact_id` it always
        would, and when the fact is about one record the stored id becomes
        `{fact_id}::{record_scope}` -- so the same observation about two RMAs
        is two facts, not one shadowing the other. With no scope the derived
        id is exactly the legacy one, which is the replay guarantee: an event
        first recorded through `_append_fact_once` before this path deployed
        and retried through it afterwards meets its own `factId` and is
        absorbed as the duplicate it is, never re-recorded.

        Every write stamps `record_scope` and `identity_version`, so a reader
        can tell which derivation produced an id without guessing from its
        shape. Return semantics are `_append_fact_once`'s: whether this call
        wrote the fact, with only the duplicate absorbed.

        `actor_id` is the server-stamped principal contracts.md sect. 4 requires
        of a command-originated fact -- who authorised it, as against `agent_id`,
        which is what software wrote it. It is passed through to the stored
        `actorId` and is pointedly **absent from the derivation below**: the id
        is built from `fact_id` and `record_scope` and from nothing else. Adding
        the actor to it would turn one observation arriving under two principals
        into two facts, which is precisely the shadowing the scoped identity
        exists to prevent -- so the actor rides along and the duplicate is still
        absorbed, keeping the first stamp. `None` for anything not originated by
        a command, which is most of the log.
        """
        derived_id = str(fact.pop("fact_id"))
        if record_scope is not None:
            derived_id = f"{derived_id}::{record_scope}"
        try:
            await self._repository.append_scoped_case_fact(
                fact_id=derived_id,
                record_scope=record_scope,
                identity_version=SCOPED_FACT_IDENTITY_VERSION,
                actor_id=actor_id,
                **fact,
            )
        except DuplicateKeyError:
            logger.debug(
                "scoped_case_fact_already_recorded",
                extra={
                    "case_id": fact.get("case_id"),
                    "fact_id": derived_id,
                    "fact_name": fact.get("fact_name"),
                    "record_scope": record_scope,
                },
            )
            return False
        return True

    @activity.defn(name="record_case_status")
    async def record_case_status(self, request: RecordCaseStatusInput) -> None:
        case = await self._repository.get_case(request.case_id)
        if case is None:
            raise ValueError(f"case {request.case_id} does not exist")
        await self._repository.update_case(
            request.case_id,
            {"status": request.status},
            expected_version=int(case["version"]),
        )
        if request.fact_name is not None and request.fact_id is not None:
            # `_append_fact_once`, because `fact_id` comes from the workflow and
            # is therefore identical on every retry: a write that committed and
            # whose acknowledgement was lost must not poison all five attempts
            # `_PERSIST_RETRY` grants.
            await self._append_fact_once(
                fact_id=request.fact_id,
                case_id=request.case_id,
                fact_name=request.fact_name,
                value=request.fact_value,
                agent_id="return-workflow-agent",
                channel=FactChannel.SYSTEM,
                acquisition_method=FactAcquisition.DERIVED,
                source_path="RETURN_CASE_WORKFLOW",
            )

    @activity.defn(name="case_has_return_details")
    async def case_has_return_details(self, request: RecordCaseCustomerInput) -> bool:
        """Whether anyone has described this return yet, from the case itself.

        The wait it serves was signal-only, and exactly one surface sends the
        signal: the item-selection write. So an associate who answered the
        agent's "what is bringing it back" in the chat described the return
        perfectly well and the case waited on, then parked, for a click that was
        never coming.

        The case is the authority on what it knows, and a fact recorded by any
        surface -- the pane, the conversation, an operator, a later backfill --
        is the same answer to the same question. The signal stays as the fast
        path; this is the truth the fast path is an optimisation of.

        Reuses `RecordCaseCustomerInput` for its shape: a case id and nothing
        else. A read that invented its own single-field input would be a second
        way to say the same thing.
        """
        selected = await self._selected_items(request.case_id)
        if selected:
            return True
        facts = await self._repository.latest_case_facts(request.case_id)
        return any(
            _text_of(record.get("value"))
            for name, record in facts.items()
            if name in _RETURN_DETAIL_FACTS
        )

    @activity.defn(name="record_case_customer_identity")
    async def record_case_customer_identity(self, request: RecordCaseCustomerInput) -> bool:
        """Name the customer on the case, from the order that states who it is.

        The gap this closes is narrow and total: `project_customer` reads
        `customer_name` and `customer_id` case facts, and their only writer was
        the reasoning model's `observed_facts` -- while `redact_payload` masks
        both before any candidate row reaches a prompt. The model is not allowed
        to see the customer, so it could never report one, so every confirmed
        case projected `customer: null` and every Support handoff was about a
        customer it could not name.

        Written as `SYSTEM` / `OBSERVED`, which is what actually happened: a
        source system reported it. Not `CHANNEL_A` and not `STATED` -- those
        would claim an associate vouched for it, and the difference matters on
        the day a read and a person disagree.

        Returns whether anything was recorded. Best-effort by construction: a
        case whose order is not in the extract keeps `customer: null` and the
        handoff says the customer is unavailable, which is the honest answer and
        not a reason to stop a real return.
        """
        configuration = self._configuration() if self._configuration is not None else None
        if configuration is None:
            logger.warning(
                "case_customer_configuration_unavailable", extra={"case_id": request.case_id}
            )
            return False

        resolution = configuration.source_resolution
        identity = await resolve_confirmed_order_customer(
            self._repository,
            case_id=request.case_id,
            customer_name_paths=resolution.customer_name_paths,
            customer_id_paths=resolution.customer_id_paths,
            account_paths=resolution.customer_account_paths,
            phone_paths=resolution.customer_phone_paths,
            email_paths=resolution.customer_email_paths,
        )
        if identity.empty:
            return False

        recorded = False
        for suffix, fact_name, value in (
            ("name", "customer_name", identity.customer_name),
            ("id", "customer_id", identity.customer_id),
            # The account and how to reach them, from the same read. A Support
            # request naming a customer nobody can telephone is a request whose
            # first action is to look them up, and the order carried all of it
            # the whole time.
            ("account", "customer_account", identity.account),
            ("phone", "customer_phone", identity.phone),
            ("email", "customer_email", identity.email),
        ):
            if value is None:
                continue
            recorded |= await self._append_fact_once(
                # Derived from the workflow's seed, so a retry re-writes the
                # same log entry rather than a second opinion about the same
                # customer.
                fact_id=f"{request.fact_id_seed}:customer:{suffix}",
                case_id=request.case_id,
                fact_name=fact_name,
                value=value,
                agent_id="return-workflow-agent",
                channel=FactChannel.SYSTEM,
                acquisition_method=FactAcquisition.OBSERVED,
                source_system="SALES_ORDER_SOURCE",
                source_path="CONFIRMED_ORDER_CUSTOMER",
            )
        return recorded

    @activity.defn(name="request_bay_assignment")
    async def request_bay_assignment(self, request: RequestBayAssignmentInput) -> BayResultNotice:
        """Recommend a bay for this case, and answer with the whole result.

        This was the BAY-01 defect in one method: it appended a single
        `bay_assignment_requested` fact and returned nothing. It queried no
        graph, consulted no bay, computed no confidence and resolved no
        location -- and the workflow then waited the full bay window for a
        signal nothing sent. "Bay is best-effort" was true; there was simply
        nothing to be best-effort about.

        Deliberately does not raise on "no bay available": that is a state, not
        a failure, and it comes back as a notice whose `reason` says which
        state. It raises only when the *request* could not be made, which is
        what the workflow's `ActivityError` branch is for.

        The marker fact is appended through `_append_fact_once` and not
        directly. It is written before placement is asked, its id is derived
        from the case alone, and the log is insert-only -- so a direct append
        raised `DuplicateKeyError` on attempt two and the activity died before
        reaching `recommend`. `_BEST_EFFORT_RETRY` grants exactly one retry,
        which meant the only retry the policy allows could never succeed and a
        momentary warehouse blip cost the case its bay permanently (BAY-CONC-01).
        """
        await self._append_fact_once(
            fact_id=f"bay-requested-{request.case_id}",
            case_id=request.case_id,
            fact_name="bay_assignment_requested",
            value=True,
            agent_id="bay-assignment-agent",
            channel=FactChannel.SYSTEM,
            acquisition_method=FactAcquisition.DERIVED,
            source_path="RETURN_CASE_WORKFLOW",
        )
        if self._bay_placement is None:
            # Named, not silent. A worker registered without placement gives
            # every case the same empty bay, and a reason an operator can grep
            # for is the difference between a misconfiguration and a mystery.
            return BayResultNotice(
                warehouse_reference=None,
                bay_reference=None,
                reason="BAY_PLACEMENT_NOT_CONFIGURED",
            )
        recommendation = await self._bay_placement.recommend(request.case_id)
        notice = BayResultNotice(
            warehouse_reference=recommendation.warehouse_reference,
            bay_reference=recommendation.bay_reference,
            reason=recommendation.reason,
            return_location=recommendation.return_location,
            confidence_millionths=recommendation.confidence_millionths,
            explanation=recommendation.explanation,
            evidence_reference=recommendation.evidence_reference,
            graph_generation_id=recommendation.graph_generation_id,
            capacity_evidence=recommendation.capacity_evidence,
        )
        await self._record_bay_facts(request.case_id, notice)
        return notice

    async def _record_bay_facts(self, case_id: str, notice: BayResultNotice) -> None:
        """The recommendation, on the case, where the associate's next turn reads it.

        Facts rather than a case column for the reason every other placement
        output is a fact: the agent's turn context is built from the fact
        projection, so writing them here is what lets Order Discovery tell the
        associate where the goods are going without a second query.

        `fact_id` is derived from the case and the fact name, so a bay result
        that arrives twice -- a retry, a replay, a late signal followed by a
        recommendation -- leaves one fact per name instead of a second opinion
        the projection would then resolve by whichever write happened to land
        last. That is contract C2's "one coherent bay result" made durable
        rather than hoped for.

        It does not *rewrite* that fact, and the docstring used to say it did.
        The log is insert-only, so the first coherent result for a case wins
        and every later arrival under the same id is a no-op -- which is the
        honest reading of an append-only log with a derived id, and is why the
        duplicate is recognised here rather than surfacing as a warning with a
        stack trace for something entirely expected.

        Best-effort like everything else on this path: a fact that genuinely
        could not be written never invalidates the recommendation itself.
        """
        values: tuple[tuple[str, Any], ...] = (
            ("bay_warehouse_reference", notice.warehouse_reference),
            ("bay_reference", notice.bay_reference),
            ("bay_return_location", notice.return_location),
            ("bay_confidence_millionths", notice.confidence_millionths),
            ("bay_reason", notice.reason),
            ("bay_evidence_reference", notice.evidence_reference),
            ("bay_capacity_evidence", notice.capacity_evidence),
        )
        for name, value in values:
            if value is None:
                continue
            try:
                await self._append_fact_once(
                    fact_id=f"{name}-{case_id}",
                    case_id=case_id,
                    fact_name=name,
                    value=value,
                    agent_id="bay-assignment-agent",
                    channel=FactChannel.SYSTEM,
                    acquisition_method=FactAcquisition.DERIVED,
                    source_path="RETURN_CASE_WORKFLOW",
                )
            except Exception:  # noqa: BLE001 - advisory, like the bay itself
                logger.warning(
                    "bay_fact_not_recorded",
                    extra={"case_id": case_id, "fact_name": name},
                    exc_info=True,
                )

    @activity.defn(name="resolve_business_deadline")
    async def resolve_business_deadline(
        self, request: ResolveBusinessDeadlineInput
    ) -> ResolvedBusinessDeadline:
        """Business-time arithmetic, on the side of the boundary that may do IO.

        The workflow may not resolve a timezone or read a holiday list -- one
        touches the tz database and the other is configuration, and both are
        determinism hazards in a replayed body. So the whole calculation lives
        here and the workflow receives one absolute instant, which its history
        records.

        The calendar is looked up per call rather than captured, matching how
        every other live-configuration read in this process works: a corrected
        holiday list reaches a case that is already waiting. Durations stay
        pinned on the workflow input, because moving one under a live return
        would make an operator's countdown jump; correcting which days the
        warehouse is shut is fixing the answer to a question already asked.

        No calendar for the id is not an error. It falls back to wall clock --
        exactly what the platform did before SLA-01 -- and says so, so a release
        that forgot to declare its calendar is visible rather than silently
        chasing Support at midnight.
        """
        start = datetime.fromisoformat(request.from_iso)
        calendar = self._business_calendar(request.business_calendar_id, request.timezone)
        if calendar is None:
            logger.warning(
                "business_calendar_not_configured",
                extra={"business_calendar_id": request.business_calendar_id},
            )
            return ResolvedBusinessDeadline(
                instant_iso=(start + timedelta(seconds=request.working_seconds)).isoformat(),
                calendar_applied=False,
            )
        return ResolvedBusinessDeadline(
            instant_iso=advance_business_time(calendar, start, request.working_seconds).isoformat(),
            calendar_applied=True,
        )

    def _business_calendar(
        self, calendar_id: str, fallback_timezone: str
    ) -> BusinessCalendar | None:
        """The declared calendar for this id, as the arithmetic wants it.

        `None` when nothing declares it. Deliberately no default Mon-Fri: a
        calendar the platform invented would be wrong for most deployments and
        wrong invisibly, which is worse than the wall-clock behaviour this
        replaces.
        """
        configuration = self._configuration() if self._configuration is not None else None
        if configuration is None:
            return None
        for declared in configuration.business_calendars:
            if declared.calendar_id != calendar_id:
                continue
            return BusinessCalendar(
                calendar_id=declared.calendar_id,
                # The calendar's own zone wins; the case's timing block carries
                # one only as a fallback for a calendar that declares none.
                timezone=declared.timezone or fallback_timezone,
                working_periods=tuple(
                    WorkingPeriod(
                        weekday=period.weekday,
                        start_minute=period.start_minute,
                        end_minute=period.end_minute,
                    )
                    for period in declared.working_periods
                ),
                holidays=frozenset(declared.holidays),
            )
        return None

    # --- the policy gate (3A.7) ---------------------------------------------

    @activity.defn(name="evaluate_case_eligibility")
    async def evaluate_case_eligibility(
        self, request: EvaluateCaseEligibilityInput
    ) -> CaseEligibilityOutcome:
        """Evaluate one case against the published rule set, and record why.

        The ninth activity, and the one whose absence the audit measured: this
        worker registered exactly eight, none of which evaluated a policy, so
        every return reached Support whatever the facts said.

        The evaluation itself is `policy.evaluator`, which is pure. Everything
        this method adds is the IO the evaluator may not do -- reading the fact
        log, resolving the zone and the business calendar, reading the active
        configuration, and persisting the outcome as case facts.

        **Two failures, kept apart.** No published policy answers
        `POLICY_NOT_CONFIGURED` and the workflow parks the case; anything else
        going wrong answers `EVALUATION_FAILED` and the workflow holds it for a
        supervisor. Neither ever answers with a decision, so no path through here
        can approve a return, and both are recorded on the case before this
        returns -- a case parked for a reason nobody wrote down is a case nobody
        can act on.

        It does not raise for either. A raised activity reaches the workflow as
        an `ActivityError` carrying a message, and the workflow would have to
        parse the message to tell "no rule set was published" from "Mongo was
        briefly unavailable" -- two situations with different operational
        answers. The retry policy still covers the transient case; what is left
        is reported as a value.
        """
        evaluated_at = datetime.fromisoformat(request.evaluated_at_iso)
        configuration = self._configuration() if self._configuration is not None else None

        # Checked before the policy is required, and deliberately so: a
        # deployment that has suspended the gate should not also have to publish
        # a rule set to get past this line. The order is the only thing that
        # makes "evaluation is off" independent of "no policy exists" -- the two
        # are different states with different operational answers, and the
        # release validator still refuses activation on the second.
        if configuration is not None and not configuration.policy_evaluation.enabled:
            reason = configuration.policy_evaluation.disabled_reason or "UNSPECIFIED"
            logger.info(
                "policy_evaluation_skipped_by_configuration",
                extra={"case_id": request.case_id},
            )
            await self._append_policy_facts(
                request,
                (
                    (
                        "policy_evaluation_state",
                        PolicyGateState.SKIPPED_BY_CONFIGURATION.value,
                    ),
                    ("policy_evaluation_skip_reason", reason),
                ),
            )
            # No route, no decision, no reason codes. Every one of those fields
            # would be an answer, and the gate produced none.
            return CaseEligibilityOutcome(
                state=PolicyGateState.SKIPPED_BY_CONFIGURATION.value,
                failure_reason=None,
            )

        if configuration is None or configuration.return_eligibility_policy is None:
            logger.error(
                "return_eligibility_policy_not_configured", extra={"case_id": request.case_id}
            )
            await self._record_policy_failure(
                request,
                state=PolicyGateState.POLICY_NOT_CONFIGURED,
                reason="RETURN_ELIGIBILITY_POLICY_NOT_PUBLISHED",
            )
            return CaseEligibilityOutcome(
                state=PolicyGateState.POLICY_NOT_CONFIGURED.value,
                failure_reason="RETURN_ELIGIBILITY_POLICY_NOT_PUBLISHED",
            )

        policy = configuration.return_eligibility_policy
        log = await self._repository.list_case_facts(request.case_id)
        # The authoritative basis of the standard return window, and the reason
        # the gate stopped reviewing every case on `PURCHASE_DATE_UNKNOWN`. It is
        # resolved *outside* the try below on purpose: every failure mode inside
        # the resolver already answers `None` and logs, so anything that did
        # escape it would be a defect in this platform rather than a
        # contradiction in the case's facts, and reporting it as
        # `POLICY_FACTS_INCONSISTENT` would name the wrong thing.
        # Both instants, from one read of the order. The delivery half is the
        # one the platform could not answer before: `delivery_date` was a fact
        # nobody wrote, so a delivery claim reached the evaluator undated and
        # its reporting window had no basis.
        order_dates = await resolve_confirmed_order_dates(
            self._repository,
            case_id=request.case_id,
            order_date_paths=configuration.source_resolution.order_date_paths,
            delivery_proof=configuration.source_resolution.delivery_proof,
        )
        try:
            assembled = assemble_policy_evaluation_input(
                log,
                request_date=evaluated_at,
                confirmed_order_purchase_date=order_dates.purchase_date,
                confirmed_order_delivery_date=order_dates.delivery_date,
                configuration_release_id=request.configuration_release_id,
                policy_version=policy.version,
            )
        except Exception as error:  # noqa: BLE001 - see the docstring
            logger.warning(
                "policy_facts_inconsistent",
                extra={"case_id": request.case_id},
                exc_info=True,
            )
            await self._record_policy_failure(
                request,
                state=PolicyGateState.EVALUATION_FAILED,
                reason=f"POLICY_FACTS_INCONSISTENT:{type(error).__name__}",
            )
            return CaseEligibilityOutcome(
                state=PolicyGateState.EVALUATION_FAILED.value,
                failure_reason="POLICY_FACTS_INCONSISTENT",
            )

        try:
            outcome = evaluate_return_eligibility(
                policy,
                assembled.facts,
                PolicyClock(
                    evaluated_at=evaluated_at,
                    local_zone=self._local_zone(request.timezone),
                    business_calendar=self._business_calendar(
                        request.business_calendar_id, request.timezone
                    ),
                ),
            )
        except Exception as error:  # noqa: BLE001 - fail closed to review, never to approval
            logger.error(
                "policy_evaluation_failed", extra={"case_id": request.case_id}, exc_info=True
            )
            await self._record_policy_failure(
                request,
                state=PolicyGateState.EVALUATION_FAILED,
                reason=f"POLICY_EVALUATOR_RAISED:{type(error).__name__}",
            )
            return CaseEligibilityOutcome(
                state=PolicyGateState.EVALUATION_FAILED.value,
                failure_reason="POLICY_EVALUATOR_RAISED",
            )

        queue = self._route_queue(outcome.route, configuration)
        await self._record_policy_outcome(request, outcome, assembled, queue)
        return CaseEligibilityOutcome(
            state=PolicyGateState.EVALUATED.value,
            route=outcome.route.value,
            decision=outcome.decision.value if outcome.decision is not None else None,
            reason_codes=tuple(code.value for code in outcome.reason_codes),
            support_queue=queue,
            failure_reason=None,
        )

    @staticmethod
    def _local_zone(timezone: str) -> ZoneInfo:
        """The case's business zone, or UTC when the release names one that is not
        installed.

        UTC rather than a raised error, because the alternative is a case that
        cannot be evaluated at all over a misconfigured zone name -- and the
        substitution is logged and recorded, so a window decided in the wrong
        zone is visible rather than silent.
        """
        try:
            return ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError):
            logger.warning("policy_timezone_not_resolvable", extra={"timezone": timezone})
            return ZoneInfo("UTC")

    @staticmethod
    def _route_queue(route: PolicyRoute, configuration: ReturnPlatformConfiguration) -> str | None:
        """The Support queue that verifies this route, if the release declares one.

        Route context travels as a *configured* queue and never as a work-item
        type field -- `SupportWorkItemView` has none, and adding one is the thing
        plan sect. 7.6 rules out. A release that has not declared the queue yet
        gets `None`, which leaves the support service's own default standing: a
        warranty verification on the ordinary returns queue is a queue-routing
        gap, while a queue name the deployment never declared would be a work
        item nobody is looking at.
        """
        preferred = _ROUTE_QUEUES.get(route)
        if preferred is None:
            return None
        if preferred in configuration.support.queues:
            return preferred
        logger.warning(
            "policy_route_queue_not_configured",
            extra={"route": route.value, "queue": preferred},
        )
        return None

    async def _record_policy_failure(
        self, request: EvaluateCaseEligibilityInput, *, state: PolicyGateState, reason: str
    ) -> None:
        await self._append_policy_facts(
            request,
            (
                ("policy_evaluation_state", state.value),
                ("policy_evaluation_failure", reason),
            ),
        )

    async def _record_policy_outcome(
        self,
        request: EvaluateCaseEligibilityInput,
        outcome: PolicyOutcome,
        assembled: AssembledPolicyFacts,
        queue: str | None,
    ) -> None:
        """The decision, and everything needed to defend it later (3A.8).

        Written as case facts because that is where provenance that must survive
        a correction lives: the log is insert-only, so a supervisor's override
        appends over this rather than replacing it, and both readings stay
        recoverable.

        Scalars, one fact per field. Code *lists* are joined rather than stored
        as arrays because `CaseFactProjection.value` is typed
        `str | int | float | bool | None` -- a list would parse today and fail
        the projection the console reads. The join is on `,` with the evaluator's
        own ordering preserved, which is the order the rules fired in.
        """
        provenance = outcome.provenance
        values: list[tuple[str, Any]] = [
            ("policy_evaluation_state", PolicyGateState.EVALUATED.value),
            ("policy_route", outcome.route.value),
            ("policy_reason_codes", ",".join(code.value for code in outcome.reason_codes)),
            ("policy_conditions", ",".join(condition.value for condition in outcome.conditions)),
            (
                "policy_exceptions",
                ";".join(
                    f"{item.code.value}:{item.authority.value}:{item.reference}"
                    for item in outcome.exceptions
                ),
            ),
            ("policy_applied_rules", ",".join(rule.value for rule in outcome.applied_rules)),
            ("policy_id", provenance.policy_id),
            ("policy_version", provenance.policy_version),
            ("policy_authority", provenance.authority),
            ("policy_source_document", provenance.source_document),
            ("policy_source_revision", provenance.source_revision),
            ("policy_evaluated_at", provenance.evaluated_at.isoformat()),
            ("policy_configuration_release_id", provenance.configuration_release_id),
            ("policy_facts_admitted", ",".join(assembled.admitted)),
            (
                "policy_facts_excluded",
                ",".join(f"{name}:{state}" for name, state in assembled.excluded),
            ),
            ("policy_support_queue", queue),
        ]
        if outcome.decision is not None:
            # `policy_decision` is the evaluator's answer and is never rewritten.
            # `policy_effective_decision` is the same value until an override
            # appends a later one -- two names so that reading "what stands now"
            # can never destroy "what the policy said".
            values.append(("policy_decision", outcome.decision.value))
            values.append(("policy_effective_decision", outcome.decision.value))
        if outcome.restocking_fee is not None:
            values.append(("policy_restocking_fee_applies", outcome.restocking_fee.applies))
            values.append(("policy_restocking_fee_waived", outcome.restocking_fee.waived))
            # The rate is recorded **as evaluated** and never re-read at request
            # time. `_seller_restocking_fee` produced it from
            # `restocking_fee.seller_schedule` in the release this evaluation
            # ran under; reading the live release when the projection is built
            # would report today's rate for a case decided under a release that
            # had a different one -- the provenance failure `policy_version`
            # exists to make visible. The fact log is the provenance, so the
            # rate is written here beside the decision it belongs to.
            #
            # Both or neither. `_append_policy_facts` drops `None`, and
            # `FeeDetermination` already refuses a rate with no source, so a
            # schedule-less policy appends nothing and
            # `assembly._restocking_rate` reads `(None, None)` exactly as it did
            # before this line existed.
            values.append(
                (
                    "policy_restocking_fee_rate_basis_points",
                    outcome.restocking_fee.rate_basis_points,
                )
            )
            values.append(
                (
                    "policy_restocking_fee_rate_source",
                    outcome.restocking_fee.rate_source.value
                    if outcome.restocking_fee.rate_source is not None
                    else None,
                )
            )
        window = outcome.delivery_claim_window
        if window is not None:
            # 3A.6 sets the work item's `slaDueAt` from this rather than
            # recomputing it: a deadline computed twice against two calendars is
            # a deadline nobody can reconcile.
            values.append(("policy_delivery_claim_window_state", window.state.value))
            values.append(("policy_delivery_claim_business_days", window.business_days))
            values.append(
                (
                    "policy_delivery_claim_reporting_deadline",
                    window.reporting_deadline.isoformat()
                    if window.reporting_deadline is not None
                    else None,
                )
            )
        await self._append_policy_facts(request, tuple(values))

    async def _append_policy_facts(
        self, request: EvaluateCaseEligibilityInput, values: tuple[tuple[str, Any], ...]
    ) -> None:
        """Append each named value once, under an id the workflow's seed derives.

        `fact_id_seed` is a `workflow.uuid4()`, so an activity retry writes the
        same ids and `_append_fact_once` recognises the second arrival instead of
        failing the attempt on the record of the first.
        """
        for name, value in values:
            if value is None or value == "":
                continue
            await self._append_fact_once(
                fact_id=f"{name}-{request.fact_id_seed}",
                case_id=request.case_id,
                fact_name=name,
                value=value,
                agent_id="return-eligibility-policy",
                channel=FactChannel.SYSTEM,
                acquisition_method=FactAcquisition.DERIVED,
                source_system="RETURN_ELIGIBILITY_POLICY",
                source_path="DETERMINISTIC_POLICY_EVALUATION",
            )

    @activity.defn(name="draft_support_request")
    async def draft_support_request(self, request: DraftSupportRequestInput) -> SupportRequestDraft:
        """The message Support will read, and the same facts structured beside it.

        **This is composed, not written.** Every value comes from the case's own
        state -- its facts, its selected lines, its bay recommendation, its
        policy outcome -- and `compose_support_handoff` holds the rules about
        what may and may not be said. What it replaces was a single sentence
        naming one order reference and asking for an RMA: true, and short of
        every fact the person receiving it needed.

        **No model may replace it, and that is deliberate rather than
        incidental.** A generated draft cannot be held to "do not invent
        unavailable values", which is the rule that matters most in a handoff a
        human then acts on -- a plausible customer name or a plausible quantity
        is worse than a blank. A configured `drafter` previously *replaced* the
        whole message; it now writes **under** the structured request, labelled,
        so its words still reach Support and the facts are never at the mercy of
        the wording. A drafter that fails changes nothing.

        **The payload is the contract, not the prose.** `SupportRequestDraft`
        carries both halves so `open_support_work_item` can persist the
        structured record on the message; a screen reads that and never parses
        the text back into fields.

        Best-effort in every direction that is not the message itself: an
        unreadable order leaves the product names unavailable, a case with no bay
        result says so, and none of it stops the handoff. A return that cannot be
        described is still a return Support has to be told about.
        """
        handoff = compose_support_handoff(**await self._handoff_arguments(request))
        facts = await self._handoff_facts(request.case_id)
        return SupportRequestDraft(
            text=handoff.text + await self._drafted_note(request.case_id, facts),
            payload=handoff.payload,
            subject=handoff.subject,
        )

    async def _handoff_facts(self, case_id: str) -> dict[str, Any]:
        latest = await self._repository.latest_case_facts(case_id)
        return {name: fact.get("value") for name, fact in latest.items()}

    async def _handoff_arguments(self, request: DraftSupportRequestInput) -> dict[str, Any]:
        """Everything `compose_support_handoff` is called with, assembled once.

        Lifted out of `draft_support_request` unchanged, and the extraction is
        the seam phase 1 built for: `support_template_snapshot` takes **this
        exact argument list**, deliberately, so the composed handoff and the
        templated one are one handoff by two routes rather than two assemblies
        somebody has to keep in step.

        A second copy of these reads is precisely how the two paths would come
        to disagree about what the case says, which is the shape of the
        divergence phase 1 spent a whole review round on. One assembly, two
        renderings.
        """
        # Three narrow reads, not the whole case projection. The projection
        # assembles fifteen blocks this message does not use and needs a
        # requirement table to do it; the handoff needs the case's own row, its
        # fact log and its unassigned items, and reading exactly those keeps the
        # activity doubleable and its dependencies legible.
        case = await self._repository.get_case(request.case_id) or {}
        latest = await self._repository.latest_case_facts(request.case_id)
        facts = {name: fact.get("value") for name, fact in latest.items()}
        selected = await self._selected_items(request.case_id)

        order_reference = _stated(facts, "confirmed_order_reference") or _text_of(
            case.get("confirmedOrderReference")
        )
        details = await self._order_line_details(order_reference)

        # Two questions, and they were being answered as one.
        #
        # "Has the associate described the return" and "is there anything left
        # outstanding on this case" are different, and `awaiting` only ever
        # answers the second: every dimension in it -- RETURN_METHOD, RMA,
        # LABEL, TRACKING, BOL, PICKUP, RETURN_LOCATION -- is Support- or
        # fulfilment-owned, and none of them is anything an associate can state.
        # Reading `required_details_complete = not awaiting` therefore made the
        # verdict on the associate's work depend on Support having already done
        # the work this message exists to request, and it rendered "Incomplete"
        # on every handoff ever composed -- including this one, which carried a
        # line, a quantity of four, SHIPPING_DAMAGE and NEW_IN_ORIGINAL_PACKAGING.
        #
        # Before that it read `bool(selected)`, which was true the moment any
        # item was picked and said "Complete" over a case whose own pane said
        # "Waiting on RETURN_METHOD". Both readings were one line trying to be
        # two answers.
        #
        # So: the associate's half is judged on what the associate supplies --
        # a line, a quantity, and a reason where the release publishes a
        # catalogue to choose one from -- and Support's half is named beside it
        # rather than folded into it.
        #
        # `known` is false when the projection cannot be assembled at all -- an
        # activity double, a narrower port. The outstanding list is then empty
        # rather than guessed, because "we cannot tell" must never render as
        # "nothing is outstanding".
        known, _business_complete, awaiting, _revision = await self._assess_completion(
            request.case_id
        )
        outstanding_support_dimensions = tuple(awaiting) if known else ()
        required_details_complete = _associate_described_the_return(
            selected, reason_required=self._reason_is_published()
        )

        item_methods = self._derive_item_methods(selected, details)

        return dict(
            case_id=request.case_id,
            work_item_id=request.work_item_id,
            created_at=_moment_of(case.get("updatedAt")),
            workflow_status=_text_of(case.get("status")),
            customer=SupportHandoffCustomer(
                name=_stated(facts, "customer_name"),
                reference=_stated(facts, "customer_id"),
                account=_stated(facts, "customer_account"),
                contact_name=_stated(facts, "branch_associate_name"),
                contact_email=_stated(facts, "branch_associate_email"),
                contact_phone=_stated(facts, "branch_associate_phone"),
                customer_phone=_stated(facts, "customer_phone"),
                customer_email=_stated(facts, "customer_email"),
            ),
            order=SupportHandoffOrder(
                reference=order_reference,
                items=tuple(
                    _handoff_item(
                        item,
                        details.get(str(item.get("orderLineId") or "")),
                        item_methods.get(str(item.get("orderLineId") or "")),
                    )
                    for item in selected
                ),
            ),
            return_details=SupportHandoffReturn(
                method=_stated(facts, "return_method")
                or await self._derive_return_method(request.case_id, selected, details),
                requested_resolution=_stated(facts, "requested_resolution"),
                product_presence=_stated(facts, "product_presence"),
                associate_notes=_stated(facts, "associate_notes"),
            ),
            bay=SupportHandoffBay(
                status=_stated(facts, "bay_reason"),
                bay_reference=_stated(facts, "bay_reference"),
                warehouse_reference=_stated(facts, "bay_warehouse_reference"),
                return_location=_stated(facts, "bay_return_location"),
                handling_instructions=_stated(facts, "bay_handling_instructions"),
                unresolved_reason=_stated(facts, "bay_reason"),
            ),
            policy=SupportHandoffPolicy(
                state=_stated(facts, "policy_evaluation_state"),
                skipped_reason=_stated(facts, "policy_evaluation_skip_reason"),
                route=_stated(facts, "policy_route"),
                decision=_stated(facts, "policy_decision"),
            ),
            order_confirmed=order_reference is not None,
            required_details_complete=required_details_complete,
            outstanding_support_dimensions=outstanding_support_dimensions,
            support_state_known=known,
        )

    def _derive_item_methods(
        self,
        selected: Sequence[Mapping[str, Any]],
        details: Mapping[str, CaseOrderLineDetail],
    ) -> dict[str, str]:
        """Each selected line's shipping class, from what its product is.

        Per line because one order can carry a parcel-class grille and an
        LTL-class water heater, and those are different packages: Support
        issues one return record per class. Empty when no derivation is
        configured.
        """
        configuration = self._configuration() if self._configuration else None
        derivation = (
            configuration.return_policy.return_method_derivation
            if configuration is not None
            else None
        )
        if derivation is None:
            return {}
        keywords = tuple(keyword.upper() for keyword in derivation.freight_keywords)
        methods: dict[str, str] = {}
        for item in selected:
            line = str(item.get("orderLineId") or "")
            detail = details.get(line)
            text = " ".join(
                part
                for part in (
                    detail.description if detail else None,
                    detail.sku if detail else None,
                )
                if part
            ).upper()
            methods[line] = (
                derivation.freight_method
                if any(keyword in text for keyword in keywords)
                else derivation.default_method
            )
        return methods

    async def _derive_return_method(
        self,
        case_id: str,
        selected: Sequence[Mapping[str, Any]],
        details: Mapping[str, CaseOrderLineDetail],
    ) -> str | None:
        """The parcel-vs-freight class, from what the product is -- never asked.

        Driven by `return_policy.return_method_derivation`: any selected line
        whose resolved product description or SKU carries a configured freight
        keyword makes the whole return the freight method, because one
        LTL-class item decides the shipment. No derivation configured, or no
        selected lines, answers `None` and Support asks -- the old behaviour,
        and the honest one for a deployment that has not declared the rule.

        The answer is recorded on the case fact log as `return_method` with
        `DERIVED` acquisition, so the handoff, the completion profile and the
        associate's own conversation all read the same statement with its
        provenance intact.
        """
        configuration = self._configuration() if self._configuration else None
        derivation = (
            configuration.return_policy.return_method_derivation
            if configuration is not None
            else None
        )
        if derivation is None or not selected:
            return None
        # How the goods left, first. It is a fact about this order rather than
        # an inference from a description, and it answers the question the
        # keywords can only approximate: an order the customer collected at a
        # counter can be carried back to that counter, whatever it weighs.
        method = derivation.default_method
        ship_via = await resolve_confirmed_order_ship_via(
            self._repository,
            case_id=case_id,
            ship_via_paths=(
                self._configuration().source_resolution.ship_via_paths
                if self._configuration
                else ()
            ),
        )
        mapped = (
            {code.strip().upper(): value for code, value in derivation.ship_via_methods.items()}
        ).get(ship_via or "")
        if mapped is not None:
            method = mapped

        # Keywords remain the override, and only upward: goods the parcel
        # network cannot carry do not become carriable because they happened to
        # leave on a counter pick-up.
        keywords = tuple(keyword.upper() for keyword in derivation.freight_keywords)
        for item in selected:
            detail = details.get(str(item.get("orderLineId") or ""))
            text = " ".join(
                part
                for part in (
                    detail.description if detail else None,
                    detail.sku if detail else None,
                )
                if part
            ).upper()
            if any(keyword in text for keyword in keywords):
                method = derivation.freight_method
                break
        try:
            await self._append_fact_once(
                fact_id=f"return_method-{case_id}",
                case_id=case_id,
                fact_name="return_method",
                value=method,
                agent_id="return-workflow-agent",
                channel=FactChannel.SYSTEM,
                acquisition_method=FactAcquisition.DERIVED,
                source_path="RETURN_CASE_WORKFLOW",
            )
        except Exception:  # noqa: BLE001 - the derivation stands even unrecorded
            logger.warning(
                "return_method_fact_not_recorded", extra={"case_id": case_id}, exc_info=True
            )
        return method

    async def _drafted_note(self, case_id: str, facts: Mapping[str, Any]) -> str:
        """A configured drafter's words, under the structured request.

        Labelled, so a reader can tell the composed facts from a written note and
        knows not to act on the second where the two disagree. Empty when no
        drafter is configured -- which is every deployment this repository ships,
        since `run_return_workflow_worker.py` wires none.
        """
        if self._drafter is None:
            return ""
        try:
            drafted = await self._drafter.draft(case_id=case_id, facts=dict(facts))
        except Exception:  # noqa: BLE001 - a note is never worth failing a handoff for
            logger.warning(
                "support_draft_note_unavailable", extra={"case_id": case_id}, exc_info=True
            )
            return ""
        if not drafted.strip():
            return ""
        return f"\nAdditional note from the return assistant:\n{drafted.strip()}\n"

    def _reason_is_published(self) -> bool:
        """Whether the release gives the associate a reason catalogue to pick from.

        A deployment that publishes none is not one where every return is
        undescribed; it is one where there is no reason to state. Requiring an
        answer nobody can give would report every handoff incomplete on a
        release that never asked for the field.
        """
        configuration = self._configuration() if self._configuration else None
        if configuration is None:
            return False
        vocabulary = getattr(configuration, "selection_vocabulary", None)
        return bool(getattr(vocabulary, "reasons", ()))

    async def _selected_items(self, case_id: str) -> list[Mapping[str, Any]]:
        """The lines the associate named that no RMA covers yet.

        An item carrying a `returnRecordId` belongs to an issued RMA and is not
        part of *this* request; including it would ask Support to authorise
        something already authorised. Same rule `project_selected_items` applies,
        applied here because this reads the items directly.
        """
        items = await self._repository.list_case_return_items(case_id)
        return [item for item in items if not _text_of(item.get("returnRecordId"))]

    async def _order_line_details(
        self, order_reference: str | None
    ) -> Mapping[str, CaseOrderLineDetail]:
        """Product names and colours for the confirmed order, or nothing.

        A worker with no detail port answers with nothing and every product
        renders as unavailable, which is the same shape of degradation the bay
        and the customer already take. It is logged once rather than silently,
        because a deployment that meant to wire it and did not would otherwise
        produce handoffs that look deliberately anonymous.
        """
        if order_reference is None:
            return {}
        if self._order_line_details_port is None:
            logger.info("support_draft_order_line_details_not_configured")
            return {}
        try:
            return await self._order_line_details_port.line_details(order_reference)
        except Exception:  # noqa: BLE001 - see the docstring
            logger.warning(
                "support_draft_order_line_details_unavailable",
                extra={"order_reference": order_reference},
                exc_info=True,
            )
            return {}

    # --- the template review gate (contracts.md sect. 6) ---------------------
    #
    # Four activities, all of them thin. The work is
    # `operations/support_template_gate.py`, which knows nothing about Temporal
    # and is therefore exercisable branch by branch without a worker; these
    # exist to be *names the workflow can reference as strings* and to assemble
    # the render input from the case.

    def _gate(self) -> SupportTemplateGateService:
        """The gate service, or a clear refusal.

        Raised rather than degraded. Every one of these activities is reached
        only from inside `workflow.patched(...)`, so a deployment that has the
        gate in its histories and no review store in its worker is
        misconfigured -- and an activity that quietly did nothing would leave a
        case waiting on a review that was never opened, which is the failure
        mode with no operator signal at all.
        """
        if self._gate_service is None:
            raise RuntimeError(
                "the support template review gate is not wired in this process: "
                "ReturnCaseActivities was built without a review store"
            )
        return self._gate_service

    async def _render_inputs(
        self, request: TemplateReviewDraftInput
    ) -> tuple[dict[tuple[str | None, str], dict[str, Any]], tuple[Any, ...]]:
        """The facts and records one render reads.

        Two halves, merged, and the merge order is the point. The **scoped fact
        log** goes in first, carrying each value's `factId` so the panel can put
        a provenance chip on it; the **draft snapshot** goes over the top for
        the derived values no fact stands behind. A snapshot key never
        overwrites a real fact of the same name, because
        `support_template_draft.SNAPSHOT_KEYS` and `FACT_LOG_KEYS` are disjoint
        and a test in that module holds them so.
        """
        scoped = await self._repository.latest_case_facts_scoped(request.case_id)
        facts: dict[tuple[str | None, str], dict[str, Any]] = {
            key: dict(value) for key, value in scoped.items()
        }
        arguments = await self._handoff_arguments(
            DraftSupportRequestInput(
                case_id=request.case_id,
                configuration_release_id=request.configuration_release_id,
                work_item_id=request.work_item_id,
            )
        )
        facts.update(snapshot_as_facts(support_template_snapshot(**arguments)))
        records = tuple(await self._return_records(request.case_id))
        return facts, records

    async def _return_records(self, case_id: str) -> Sequence[Any]:
        """This case's record projections, or none.

        Best-effort: a projection that cannot be assembled leaves the per-record
        sections empty rather than failing the draft. The opening handoff has no
        records at all by construction -- it is the message that *asks* for the
        first RMA -- so "none" is the ordinary answer here, not a degradation.
        """
        try:
            state = await self._repository.load_case_projection_state(case_id)
        except Exception:  # noqa: BLE001 - see the docstring
            logger.warning(
                "return_records_unavailable_for_template",
                extra={"case_id": case_id},
                exc_info=True,
            )
            return ()
        return () if state is None or state.returnRecords is None else state.returnRecords

    @activity.defn(name="record_template_draft")
    async def record_template_draft(
        self, request: TemplateReviewDraftInput
    ) -> TemplateReviewDraftSet:
        """Open every review this case's grouping asks for. One call, one set.

        The grouping is resolved here rather than in the workflow because it
        reads the case's return records, and a workflow may not. What comes
        back is the whole map -- so the wait is keyed on ids the *history*
        holds, not on ids re-derived from a release that may have moved.

        No `review_id` is supplied per request and none is needed:
        `create_review` is idempotent on `(case_id, request_id, kind, scope)`
        over non-terminal attempts, so a retried activity finds the live review
        and returns it rather than opening a second answer to one question.
        """
        gate = self._gate()
        facts, records = await self._render_inputs(request)
        request_ids = request_ids_for(request.case_id, records, gate.gate().request_grouping)
        drafts: list[TemplateReviewDraftResult] = []
        for index, request_id in enumerate(request_ids):
            draft = await gate.record_draft(
                case_id=request.case_id,
                request_id=request_id,
                # Derived, so a retry that got as far as the second request does
                # not mint a fresh id for the first -- `create_review` would
                # absorb it anyway, and an id that changed per attempt would make
                # the fact log's `record_scope` unreadable.
                review_id=f"{request.fact_id_seed}:{index}",
                fact_id_seed=f"{request.fact_id_seed}:{index}",
                facts=facts,
                records=records,
            )
            if not draft.template_available:
                return TemplateReviewDraftSet(template_available=False)
            drafts.append(
                _draft_result(
                    TemplateReviewDraftInput(
                        case_id=request.case_id,
                        request_id=request_id,
                        review_id=draft.review_id or "",
                        configuration_release_id=request.configuration_release_id,
                        work_item_id=request.work_item_id,
                        fact_id_seed=request.fact_id_seed,
                    ),
                    draft,
                )
            )
        return TemplateReviewDraftSet(drafts=tuple(drafts))

    @activity.defn(name="rerender_template_draft")
    async def rerender_template_draft(
        self, request: TemplateReviewDraftInput
    ) -> TemplateReviewDraftResult:
        """Produce the draft again after a reviewer asked for a revision."""
        facts, records = await self._render_inputs(request)
        draft = await self._gate().rerender_draft(
            case_id=request.case_id,
            request_id=request.request_id,
            review_id=request.review_id,
            fact_id_seed=request.fact_id_seed,
            facts=facts,
            records=records,
        )
        return _draft_result(request, draft)

    @activity.defn(name="record_template_revision")
    async def record_template_revision(self, request: TemplateReviewRevisionInput) -> None:
        """Log the revision request before the re-render, not after.

        A revision asked for against a render that then failed is still a thing
        that happened, and an operator reading the case needs to see it.
        """
        await self._gate().record_revision(
            case_id=request.case_id,
            review_id=request.review_id,
            actor_id=request.actor_id,
            note=request.note,
            fact_id_seed=request.fact_id_seed,
        )

    @activity.defn(name="snapshot_sent_template")
    async def snapshot_sent_template(
        self, request: SnapshotSentTemplateInput
    ) -> TemplateDeliveryResult:
        """Send the frozen payload and settle the review.

        `approve_as_system` is `auto_send`. **The gap rule is enforced here as
        well as in the workflow**, and that duplication is deliberate: it is the
        one rule in the gate whose failure mode is a message asserting something
        the case does not know, and the workflow's copy is a decision while this
        one is a refusal. Contracts.md sect. 6: *an unresolved required gap
        forces hold/escalate regardless of `on_timeout: auto_send`.*
        """
        gate = self._gate()
        if request.approve_as_system:
            blocked = await self._auto_send_or_reason(gate, request)
            if blocked is not None:
                return blocked
        try:
            outcome = await gate.deliver_approved(
                case_id=request.case_id,
                review_id=request.review_id,
                tenant_id=request.tenant_id,
                principal_id=request.principal_id,
                fact_id_seed=request.fact_id_seed,
                queue=request.queue,
            )
        except ReviewStateError as refusal:
            # The review is not `APPROVING`. A signal arrived for a review
            # somebody cancelled or redrafted in between, or one whose approval
            # never committed. **Reported, not raised**: raising would fail the
            # workflow task and retry the same activity forever against a state
            # that will never change on its own, and the case would sit in
            # `AWAITING_TEMPLATE_REVIEW` with nothing on the panel to say why.
            # Returning the state is what lets the wait loop stop waiting.
            logger.warning(
                "template_delivery_refused_by_state",
                extra={
                    "case_id": request.case_id,
                    "review_id": request.review_id,
                    "state": refusal.state.value,
                },
            )
            return TemplateDeliveryResult(review_id=request.review_id, state=refusal.state.value)
        return TemplateDeliveryResult(
            review_id=outcome.review_id,
            state=outcome.state,
            work_item_id=outcome.work_item_id,
            delivery_id=outcome.delivery_id,
            absorbed=outcome.absorbed,
            error_code=outcome.error_code,
        )

    async def _auto_send_or_reason(
        self, gate: SupportTemplateGateService, request: SnapshotSentTemplateInput
    ) -> TemplateDeliveryResult | None:
        """Approve as `SYSTEM`, or say what refused. `None` means "go on".

        Every refusal below is a *guard block*, and they are one answer because
        an operator's next action is the same for all of them: look at the
        review. The distinction that matters -- what refused -- is on the review
        and on the fact log, not in this return value.
        """
        review = await gate.review(case_id=request.case_id, review_id=request.review_id)
        state = str(review.get("state"))
        if state == ReviewState.APPROVING.value:
            # A retry of this activity after its approval committed. Go on and
            # deliver; the send is deduped on the stored delivery identity.
            return None
        if state != ReviewState.OPEN.value:
            # Somebody answered between the deadline firing and this activity
            # running -- approved, cancelled, redrafted. Their decision wins;
            # the system does not overwrite a person's, and reporting the state
            # is how the workflow learns it no longer has to.
            return TemplateDeliveryResult(review_id=request.review_id, state=state)
        gaps = _gap_field_ids(review)
        if gaps:
            logger.warning(
                "auto_send_refused_unresolved_gap",
                extra={
                    "case_id": request.case_id,
                    "review_id": request.review_id,
                    "gap_field_ids": list(gaps),
                },
            )
            return TemplateDeliveryResult(
                review_id=request.review_id,
                state=state,
                guard_blocked_reason=(TemplateReviewParkReason.TEMPLATE_REVIEW_GUARD_BLOCKED.value),
            )
        try:
            await gate.approve(
                case_id=request.case_id,
                review_id=request.review_id,
                actor_id=SYSTEM_ACTOR,
                expected_draft_version=int(review["draftVersion"]),
                expected_canonical_edit_version=int(review["canonicalEditVersion"]),
                canonical_approved_payload_hash=canonical_payload_digest(
                    canonical_review_payload(review)
                ),
                workflow_id=request.workflow_id,
                signal_id=request.signal_id,
                allow_system=True,
            )
        except (
            ReviewConflictError,
            PendingRevisionError,
            ReviewVersionMismatchError,
            ReviewStateError,
        ) as refusal:
            logger.warning(
                "auto_send_refused",
                extra={
                    "case_id": request.case_id,
                    "review_id": request.review_id,
                    "refusal": type(refusal).__name__,
                },
            )
            fresh = await gate.review(case_id=request.case_id, review_id=request.review_id)
            return TemplateDeliveryResult(
                review_id=request.review_id,
                state=str(fresh.get("state")),
                guard_blocked_reason=(TemplateReviewParkReason.TEMPLATE_REVIEW_GUARD_BLOCKED.value),
            )
        return None

    @activity.defn(name="open_support_work_item")
    async def open_support_work_item(self, request: OpenSupportWorkItemInput) -> str:
        """Open the Channel B thread, once.

        `idempotency_key` comes from the case, so a retry or a replay after
        `continue_as_new` re-reads the existing thread instead of starting a
        second conversation with a person.

        `queue` is passed only when the support service accepts it. The
        signature is inspected rather than assumed because the argument is new
        with the policy gate and several support doubles predate it -- and a
        `TypeError` here would fail a return over a routing hint, which is the
        wrong direction to fail in. A service without the argument opens the
        thread on its own default queue and the route stays recorded on the case.

        `sla_due_at` travels the same way and for the same reason. It is the
        delivery-claim reporting deadline (D2); see `_support_sla` for where it
        comes from and why it is read here rather than recomputed.

        The basis is recorded **after** the thread opens, and it is allowed to
        fail the activity. Recording it first would put a statement about a work
        item on the case before the work item existed; swallowing its failure
        would leave a delivery claim carrying the desk's five minutes with
        nothing on the case saying which of the two deadlines that is, which is
        the silent substitution this argument exists to remove. The write is one
        insert against the same database `open_case_thread` just committed to,
        under `_PERSIST_RETRY`, and a duplicate -- the ordinary retry -- is
        absorbed by `_append_fact_once`.
        """
        deadline, basis = await self._support_sla(request.case_id)
        arguments: dict[str, Any] = {
            "case_id": request.case_id,
            "tenant_id": request.tenant_id,
            "principal_id": request.principal_id,
            # A thread whose request text never existed is a conversation a human
            # opens and finds empty, so the minimal wording stands in when
            # composition failed -- and says that it did.
            "support_draft": request.support_draft
            or (
                "RETURN SUPPORT REQUEST\n\n"
                f"Case:\n- Case ID: {request.case_id}\n\n"
                "The full return detail could not be composed for this case. "
                "Please open the case in the console before acting on it."
            ),
            "idempotency_key": request.idempotency_key,
        }
        # Both travel through the signature check for the same reason `queue`
        # does: several support doubles predate them, and a `TypeError` here
        # would fail a return over a message decoration.
        if request.business_payload and self._support_accepts("business_payload"):
            arguments["business_payload"] = request.business_payload
        if request.subject and self._support_accepts("subject"):
            arguments["subject"] = request.subject
        if request.work_item_id is not None and self._support_accepts("work_item_id"):
            arguments["work_item_id"] = request.work_item_id
        if request.queue is not None and self._support_accepts_queue():
            arguments["queue"] = request.queue
        elif request.queue is not None:
            logger.warning(
                "support_service_ignores_queue",
                extra={"case_id": request.case_id, "queue": request.queue},
            )
        if deadline is not None and self._support_accepts("sla_due_at"):
            arguments["sla_due_at"] = deadline
        elif deadline is not None:
            # A support service that cannot take the deadline is a wiring fault,
            # not a business state: the window *was* computed, so recording
            # `UNDETERMINED` would be a second untruth on top of the dropped
            # deadline. The work item gets the generic SLA and the basis says so
            # -- beside `policy_delivery_claim_window_state: WITHIN`, which is
            # what makes the pair legible as the anomaly it is.
            logger.error(
                "support_service_ignores_sla_due_at",
                extra={"case_id": request.case_id, "sla_due_at": deadline.isoformat()},
            )
            basis = SupportSlaBasis.SUPPORT_ACKNOWLEDGEMENT
        work_item_id = await self._support.open_case_thread(**arguments)
        await self._record_support_sla_basis(request.case_id, basis)
        case = await self._repository.get_case(request.case_id)
        if case is not None and case.get("channelBWorkItemId") != work_item_id:
            # The link that makes a support outcome reachable from the
            # associate's conversation.
            await self._repository.update_case(
                request.case_id,
                {"channelBWorkItemId": work_item_id},
                expected_version=int(case["version"]),
            )
        return str(work_item_id)

    def _support_accepts_queue(self) -> bool:
        return self._support_accepts("queue")

    def _support_accepts(self, argument: str) -> bool:
        """Whether `open_case_thread` takes this keyword.

        Inspected rather than assumed, because a `TypeError` raised here would
        fail a return over an argument that only refines how the thread is
        opened -- and because the support doubles in this repository were
        written against successive versions of the signature.
        """
        try:
            signature = inspect.signature(self._support.open_case_thread)
        except (TypeError, ValueError):  # pragma: no cover - a builtin or a C callable
            return False
        return argument in signature.parameters or any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )

    async def _support_sla(self, case_id: str) -> tuple[datetime | None, SupportSlaBasis]:
        """The deadline this case's work item should carry, and where it came from.

        **D2: the configured delivery-claim reporting window had no reader.**
        `policy_delivery_claim_reporting_deadline` was written by
        `_record_policy_outcome` and consumed by nothing, so every delivery
        claim opened on the generic `support_acknowledgement` SLA --
        `reporting_window: { business_days: 2, basis: DELIVERY_DATE }` sitting in
        the release while the work item was due five minutes after it opened.

        **Read, never recomputed.** The deadline is business-day arithmetic over
        a business calendar (`policy.evaluator.reporting_window_deadline`), and
        it was already done on the right side of the determinism boundary: by
        `evaluate_case_eligibility`, an activity, which resolves the zone and
        the holiday list that a workflow body may not touch. Doing it again here
        would resolve a *second* calendar -- the live release's, which a
        correction may have moved since the case was evaluated -- and produce
        two deadlines for one claim, which is precisely what
        `PolicyOutcome.delivery_claim_window` warns against. So this reads the
        answer the evaluation recorded.

        **`None` is a real answer.** A claim whose `delivery_date` the platform
        never learned has no computable deadline; the evaluation records
        `policy_delivery_claim_window_state: UNDETERMINED` and writes no
        deadline fact, and nothing here invents one. The generic SLA stands and
        the returned basis says that it is standing as a fallback.
        """
        facts = await self._repository.latest_case_facts(case_id)
        route = _fact_text(facts, "policy_route")
        if route != PolicyRoute.DELIVERY_CLAIM.value:
            return None, SupportSlaBasis.SUPPORT_ACKNOWLEDGEMENT

        recorded = _fact_text(facts, "policy_delivery_claim_reporting_deadline")
        if recorded is None:
            return None, SupportSlaBasis.DELIVERY_CLAIM_REPORTING_WINDOW_UNDETERMINED
        try:
            deadline = datetime.fromisoformat(recorded)
        except ValueError:
            logger.warning(
                "delivery_claim_reporting_deadline_unreadable",
                extra={"case_id": case_id, "value": recorded},
            )
            return None, SupportSlaBasis.DELIVERY_CLAIM_REPORTING_WINDOW_UNDETERMINED
        if deadline.utcoffset() is None:
            # `reporting_window_deadline` returns UTC. A naive value here is a
            # fact somebody else wrote, and guessing its zone would move the
            # deadline by hours in silence.
            logger.warning(
                "delivery_claim_reporting_deadline_not_aware",
                extra={"case_id": case_id, "value": recorded},
            )
            return None, SupportSlaBasis.DELIVERY_CLAIM_REPORTING_WINDOW_UNDETERMINED
        return deadline, SupportSlaBasis.DELIVERY_CLAIM_REPORTING_WINDOW

    async def _record_support_sla_basis(self, case_id: str, basis: SupportSlaBasis) -> None:
        """Put the basis on the case, beside the window state it follows from.

        The work item carries the deadline; the fact log carries why it is that
        deadline. One home for each, and this is the one a reader already
        consults for provenance -- `policy_delivery_claim_window_state` is two
        rows above it.

        Written for every route, not only for claims. "This work item is on the
        desk's own SLA" is a statement, and a basis recorded only when it is
        interesting is a basis whose absence has to be interpreted.

        Keyed on the case rather than on the attempt, because there is one
        Channel B thread per case and therefore one basis; a retry re-derives
        the same id and `_append_fact_once` recognises it.
        """
        await self._append_fact_once(
            fact_id=f"support_sla_basis-{case_id}",
            case_id=case_id,
            fact_name="support_sla_basis",
            value=basis.value,
            agent_id="return-case-workflow",
            channel=FactChannel.SYSTEM,
            acquisition_method=FactAcquisition.DERIVED,
            source_system="RETURN_CASE_WORKFLOW",
            source_path="SUPPORT_WORK_ITEM_OPENED",
        )

    @activity.defn(name="send_support_reminder")
    async def send_support_reminder(self, request: SendSupportReminderInput) -> None:
        """A polite nudge on the existing thread. Never a new one."""
        await self._support.post_reminder(
            work_item_id=request.work_item_id,
            reminder_number=request.reminder_number,
            max_reminders=request.max_reminders,
            idempotency_key=request.idempotency_key,
        )

    @activity.defn(name="record_support_outcome")
    async def record_support_outcome(
        self, request: RecordSupportOutcomeInput
    ) -> SupportOutcomeReceipt:
        """Merge one Support notice into the case's RMAs, and say where it stands.

        **Upsert by business identity** (plan sect. 10.2). The stable key is
        `(caseId, returnReference)`, under the unique partial index
        `repository.ensure_indexes` already declares -- the one whose comment
        says a record "gets its RMA later from Support". What this replaces
        swallowed the duplicate-key error and `continue`d, which is precisely
        what made a second reply a no-op: the tracking number Support sent two
        hours after the RMA reached the record for the first RMA and stopped
        there, so a real case sits today with a label, an RMA and
        `trackingReference: null`.

        **A field arriving `null` never overwrites a value already present.** A
        later notice states what it knows; the rest of it is silence, and
        silence is not a deletion. The merge is computed once and used for both
        stores, so the authoritative SQL row and the platform case cannot
        disagree about which of the two a null meant.

        **A replay writes nothing.** `changed` is the merge minus what the
        record already holds, and it is empty for a redelivery -- so no update
        runs, no fact is appended, and `cases.version` does not move. The
        durable dedup is the unique `(caseId, supportEventId)` index in
        `case_support_events`; this is the second line of it, and the one that
        keeps a redelivered command from manufacturing a revision change on an
        unchanged projection.
        """
        # T-14: the authoritative SQL return store commits FIRST, in one
        # transaction covering every RMA of this outcome. Only then is the
        # platform case updated, and only then are the affected records
        # synchronized into the graph (the workflow's next activity).
        #
        # Raises rather than skipping when unconfigured, exactly as
        # `synchronize_return_records` does and for the same reason: an
        # activity that silently wrote no authoritative row would leave every
        # case looking complete in MongoDB and absent from the return store,
        # which is the failure this step exists to close.
        if self._return_store is None:
            raise RuntimeError(
                "record_support_outcome was registered without a return store port; "
                f"the RMAs for case {request.case_id} would exist in MongoDB and not "
                "in the authoritative SQL return store"
            )
        plans = await self._plan_support_outcome(request)
        await self._persist_records_to_return_store(request, plans)

        applied = False
        for plan in plans:
            if await self._apply_record(request, plan):
                applied = True
            await self._append_record_facts(request, plan)
            await self._assign_items_to_record(request, plan)

        await self._seed_return_shipments(request, plans)
        await self._record_support_answer(request, issued=bool(plans))
        completion = await self._assess_completion(request.case_id)
        return SupportOutcomeReceipt(
            record_ids=tuple(plan.record_id for plan in plans),
            applied=applied,
            completion_known=completion[0],
            business_complete=completion[1],
            awaiting=completion[2],
            revision=completion[3],
        )

    async def _seed_return_shipments(
        self, request: RecordSupportOutcomeInput, plans: list[Any]
    ) -> None:
        """One shipment document per RMA that carries a tracking number.

        Seeded here because this is the moment the Support reply becomes
        records: one document per return record, never per case, idempotent on
        (return record id, tracking number) so a redelivered reply inserts
        nothing. An RMA with no tracking number seeds nothing -- inventing or
        placeholdering one is forbidden; the record stays awaiting tracking and
        the reminder policy chases Support.

        Best-effort like the bay facts: a seeding failure is logged and never
        fails the outcome -- the authoritative record write above already
        committed, and a retry of this activity re-runs the idempotent seed.
        """
        if self._shipment_tracking is None:
            return
        from return_platform.operations.shipment_tracking import ShipmentSeed

        facts = await self._repository.latest_case_facts(request.case_id)
        destination_warehouse = _fact_text(facts, "bay_warehouse_reference")
        destination_bay = _fact_text(facts, "bay_reference")
        for plan in plans:
            merged = plan.merged
            tracking = _text_of(merged.get("trackingReference"))
            bol = _text_of(merged.get("shippingInstructionReference"))
            method = _text_of(merged.get("returnMethod"))
            mode = self._shipment_tracking.mode_for(method)
            # A parcel with no tracking number is awaiting Support; a freight
            # movement has no PRO yet by nature and seeds on its BOL. Neither
            # identity is ever invented.
            if (mode == "parcel" and not tracking) or (mode == "freight" and not (tracking or bol)):
                logger.info(
                    "return_shipment_awaiting_tracking",
                    extra={
                        "case_id": request.case_id,
                        "return_record_id": plan.record_id,
                        "rma": _text_of(merged.get("returnReference")),
                    },
                )
                continue
            try:
                await self._shipment_tracking.seed(
                    ShipmentSeed(
                        case_id=request.case_id,
                        return_record_id=plan.record_id,
                        # The RMA is the record's identity -- the upsert key -- so
                        # it lives on the incoming notice, not in the merged field
                        # set.
                        rma_reference=(
                            _text_of(merged.get("returnReference"))
                            or getattr(plan.incoming, "return_reference", None)
                            or ""
                        ),
                        tracking_reference=tracking or "",
                        return_method=method,
                        carrier=_text_of(merged.get("carrier")),
                        label_reference=_text_of(merged.get("labelReference")),
                        bol_reference=bol,
                        destination_warehouse=destination_warehouse,
                        destination_bay=destination_bay,
                        provenance={
                            "agent": "return-support",
                            "channel": "CHANNEL_B",
                            "source": "SUPPORT_REPLY",
                            "method": "PARSED",
                            "supportEventId": getattr(request, "support_event_id", None),
                        },
                    )
                )
            except Exception:  # noqa: BLE001 - see the docstring
                logger.warning(
                    "return_shipment_seed_failed",
                    extra={"case_id": request.case_id, "return_record_id": plan.record_id},
                    exc_info=True,
                )

    async def _record_support_answer(
        self, request: RecordSupportOutcomeInput, *, issued: bool
    ) -> None:
        """Write down what Support answered, before anything acts on it.

        **`rejected` and `reason` arrive on every notice and used to be thrown
        away.** The workflow read `rejected` to choose a status and this
        activity read neither, so a refusal left exactly one trace on the case:
        `cases.status: CLOSED` -- the same value `_close_business_complete`
        writes for a return that finished. The projection could not tell them
        apart and reported the refusal as `COMPLETED_EXTERNAL_SETTLEMENT`, a
        credit settled outside the platform, while `awaiting` still named the
        verification the refusal had just answered. This is the writer that
        makes the two distinguishable; `status_mapping` is the reader.

        **Ordered before `_assess_completion` and therefore before the
        workflow's `_set_status`.** The status write is the workflow's next act
        after this activity returns, so recording the answer afterwards would
        leave a window in which the case is `CLOSED` with no answer beside it --
        and a poll landing in that window reads a refused return as a completed
        one. The fact is durable first, and the status catches up.

        **Silence is not an answer.** `AUTHORIZED` is written only when Support
        actually issued something, so a notice carrying neither a rejection nor
        a record -- which states nothing about the case -- records nothing.

        **The instant is the fact's, and the actor is nobody's.**
        `append_case_fact` stamps `recordedAt`, so when Support answered needs
        no field of its own. Who answered has no honest source here:
        `SupportResponseNotice` carries no actor, and the person is known only
        to `case_support_events.actorId`, on the far side of the signal. The
        provenance below is therefore the same `return-support` / Channel B /
        `SUPPORT_REPLY` the RMA facts carry -- it says who *recorded* the
        answer, and claims nothing about who reached it.

        Keyed on the Support event, exactly as `_append_record_facts` is: the
        log is insert-only, so a second notice correcting the first needs its
        own id or it would be absorbed as a duplicate and the correction lost.
        A sender with no event id keeps the unsuffixed id, because for that
        sender there is only ever one notice.
        """
        outcome: SupportOutcome | None = None
        if request.rejected:
            outcome = SupportOutcome.REJECTED
        elif issued:
            outcome = SupportOutcome.AUTHORIZED
        if outcome is None:
            return

        suffix = f"-{request.support_event_id}" if request.support_event_id else ""
        await self._append_fact_once(
            fact_id=f"{SUPPORT_OUTCOME_FACT}-{request.case_id}{suffix}",
            case_id=request.case_id,
            fact_name=SUPPORT_OUTCOME_FACT,
            value=outcome.value,
            agent_id="return-support",
            channel=FactChannel.CHANNEL_B,
            acquisition_method=FactAcquisition.OBSERVED,
            source_system="RETURN_SUPPORT",
            source_path="SUPPORT_REPLY",
        )
        reason = (request.reason or "").strip()
        if not reason:
            # Support gave no reason. An empty one recorded beside the outcome
            # would read as a reason that says nothing, which is worse than the
            # absence it would be hiding.
            return
        await self._append_fact_once(
            fact_id=f"{SUPPORT_OUTCOME_REASON_FACT}-{request.case_id}{suffix}",
            case_id=request.case_id,
            fact_name=SUPPORT_OUTCOME_REASON_FACT,
            value=reason,
            agent_id="return-support",
            channel=FactChannel.CHANNEL_B,
            acquisition_method=FactAcquisition.OBSERVED,
            source_system="RETURN_SUPPORT",
            source_path="SUPPORT_REPLY",
        )

    async def _plan_support_outcome(self, request: RecordSupportOutcomeInput) -> list[_RecordPlan]:
        """Resolve each RMA of the notice against what the case already holds.

        One read of the case's records, keyed by `returnReference`. That is the
        business identity plan sect. 10.2 names, and it is the reason a second
        reply about `RMA-1` updates `RMA-1` rather than colliding with it: the
        workflow's minted id identifies an *attempt*, while the RMA identifies
        the thing Support issued.
        """
        stored = await self._repository.list_return_records(request.case_id)
        by_reference = {
            str(document["returnReference"]): document
            for document in stored
            if document.get("returnReference")
        }
        plans: list[_RecordPlan] = []
        for record, minted_id in zip(request.records, request.return_record_ids, strict=False):
            existing = by_reference.get(record.return_reference)
            plans.append(self._plan_for(record, existing, minted_id))
        return plans

    @staticmethod
    def _plan_for(
        record: SupportReturnRecord, existing: dict[str, Any] | None, minted_id: str
    ) -> _RecordPlan:
        merged: dict[str, Any] = {}
        changed: dict[str, Any] = {}
        for stored_key, attribute, _fact_name in RETURN_RECORD_MERGED_FIELDS:
            incoming = getattr(record, attribute, None)
            held = existing.get(stored_key) if existing is not None else None
            value = incoming if incoming is not None else held
            if value is None:
                continue
            merged[stored_key] = value
            if held != value:
                changed[stored_key] = value
        return _RecordPlan(
            record_id=(str(existing["returnRecordId"]) if existing is not None else minted_id),
            incoming=record,
            existing=existing,
            merged=merged,
            changed=changed,
        )

    async def _apply_record(self, request: RecordSupportOutcomeInput, plan: _RecordPlan) -> bool:
        """Create the RMA or update it in place. Returns whether anything moved.

        The create path is tried only for an RMA the case does not hold, and a
        duplicate key there means a concurrent writer got in first -- so the
        record is re-read and the plan becomes an update rather than being
        abandoned. Abandoning it is the old behaviour, and it is what lost the
        second reply.
        """
        if plan.existing is None:
            try:
                created = await self._repository.create_return_record(
                    return_record_id=plan.record_id,
                    case_id=request.case_id,
                    return_reference=plan.incoming.return_reference,
                    status=ReturnRecordStatus.ISSUED.value,
                    source_system="RETURN_SUPPORT",
                )
            except Exception:  # noqa: BLE001 - a replay, or a concurrent writer
                logger.info(
                    "return_record_already_recorded",
                    extra={"case_id": request.case_id, "rma": plan.incoming.return_reference},
                )
                current = await self._stored_record(request.case_id, plan.incoming.return_reference)
                if current is None:  # pragma: no cover - the insert failed for another reason
                    raise
                refreshed = self._plan_for(plan.incoming, current, plan.record_id)
                plan.record_id = refreshed.record_id
                plan.existing = refreshed.existing
                plan.merged = refreshed.merged
                plan.changed = refreshed.changed
            else:
                plan.record_id = str(created["returnRecordId"])
                if plan.merged:
                    await self._repository.update_return_record(
                        plan.record_id, dict(plan.merged), expected_version=0
                    )
                return True

        if not plan.changed:
            # A redelivery, or a notice that repeats what the record already
            # says. Nothing to write, and therefore no revision to move.
            return False
        return await self._update_record_once_retried(request, plan)

    async def _update_record_once_retried(
        self, request: RecordSupportOutcomeInput, plan: _RecordPlan
    ) -> bool:
        """Update at the version we read, and retry exactly once on a conflict.

        One retry, and it re-reads rather than re-sending: the loser of the
        compare-and-set is looking at a record another writer has moved, and
        re-applying the same `$set` at a guessed version would be the lost
        update the optimistic check exists to catch. If the winner already wrote
        what this notice carries, the retry has nothing left to do and says so.

        A second conflict propagates. The activity runs under `_PERSIST_RETRY`,
        so Temporal re-runs the whole body -- which re-reads the record and
        recomputes the merge, which is a better answer than a loop here that
        could spin against a busy case.
        """
        expected = int((plan.existing or {}).get("version", 0))
        try:
            await self._repository.update_return_record(
                plan.record_id, dict(plan.changed), expected_version=expected
            )
            return True
        except ConcurrencyConflictError:
            current = await self._stored_record(request.case_id, plan.incoming.return_reference)
            if current is None:  # pragma: no cover - the record vanished mid-update
                raise
            refreshed = self._plan_for(plan.incoming, current, plan.record_id)
            plan.record_id = refreshed.record_id
            plan.existing = refreshed.existing
            plan.merged = refreshed.merged
            plan.changed = refreshed.changed
            if not plan.changed:
                logger.info(
                    "return_record_update_already_applied",
                    extra={"case_id": request.case_id, "rma": plan.incoming.return_reference},
                )
                return False
            await self._repository.update_return_record(
                plan.record_id,
                dict(plan.changed),
                expected_version=int(current.get("version", 0)),
            )
            return True

    async def _stored_record(self, case_id: str, return_reference: str) -> dict[str, Any] | None:
        """The case's record for one RMA, read back under the business key."""
        for document in await self._repository.list_return_records(case_id):
            if document.get("returnReference") == return_reference:
                return document
        return None

    async def _append_record_facts(
        self, request: RecordSupportOutcomeInput, plan: _RecordPlan
    ) -> None:
        """The observation log for this RMA, and the step that reaches Channel A.

        The agent's turn context is built from the case's fact projection, so
        writing here is what makes a delayed tracking number appear in the
        associate's *original* conversation on their next turn -- no new chat,
        no client-side join, no poll.

        **The fact id carries the Support event.** The log is insert-only
        against a unique `factId`, so a second tracking number written under the
        first one's id would be absorbed as a duplicate and lost -- the fact
        would say what Support first said and never what it corrected. With the
        event in the id, each notice appends its own observation and
        `latest_case_facts` resolves which one stands. A sender with no event id
        keeps the old shape, because for that sender there is only ever one
        notice.

        Only *changed* fields are appended. A notice repeating what the record
        already holds is not a new observation, and writing it would move the
        revision on a projection nothing changed.
        """
        await self._append_fact_once(
            fact_id=f"rma-{plan.record_id}",
            case_id=request.case_id,
            fact_name="return_reference",
            value=plan.incoming.return_reference,
            agent_id="return-support",
            channel=FactChannel.CHANNEL_B,
            acquisition_method=FactAcquisition.OBSERVED,
            source_system="RETURN_SUPPORT",
            source_path="SUPPORT_REPLY",
        )
        suffix = f"-{request.support_event_id}" if request.support_event_id else ""
        for stored_key, _attribute, fact_name in RETURN_RECORD_MERGED_FIELDS:
            value = plan.changed.get(stored_key)
            if value is None:
                continue
            await self._append_fact_once(
                fact_id=f"{fact_name}-{plan.record_id}{suffix}",
                case_id=request.case_id,
                fact_name=fact_name,
                value=value,
                agent_id="return-support",
                channel=FactChannel.CHANNEL_B,
                acquisition_method=FactAcquisition.OBSERVED,
                source_system="RETURN_SUPPORT",
                source_path="SUPPORT_REPLY",
            )

    async def _assign_items_to_record(
        self, request: RecordSupportOutcomeInput, plan: _RecordPlan
    ) -> None:
        """Attach the RMA's order lines to the items that stand for them.

        **A live hold is consumed by the assignment that supersedes it, in one
        transaction** (plan sect. 12.3). `authorize_reserved_line` is the only
        writer that moves `ACTIVE -> CONSUMED` and sets `returnRecordId` together,
        and going around it is what would let the same units be counted twice:
        `case_line_holdings` partitions a `(case, line)` pair onto *either* the
        authorized item *or* the hold, so a line that gained an RMA while its
        hold stayed `ACTIVE` would be subtracted once here and once again by
        every other case's availability read until the TTL lapsed.

        **A line with no live hold is still assigned, and that is deliberate.**
        The hold's job is to keep two associates from selecting the same units
        before either has an authorization; it is sized for a counter
        conversation (`item_reservation_ttl_seconds`, thirty minutes) while
        Support answers on a business-hours clock
        (`support_response_wait_seconds`, eight *working* hours). By the time a
        reply lands the hold has usually lapsed, and refusing the assignment then
        would refuse an RMA that Support has already issued and that the
        authoritative SQL return store already carries -- `record_support_outcome`
        commits there first (T-14). The case would sit permanently out of step
        with the authoritative store, and `_PERSIST_RETRY` would fail the
        workflow after five attempts over a hold that lapsed hours earlier.

        Nothing is double counted on that path either: `is_held` already reports
        a lapsed hold as holding nothing, so the units it stood for are back in
        everyone's availability read and the item is the only claim on them.

        The two calls are not interchangeable and the choice is made per line,
        from the reservation state read once for the whole outcome. Losing the
        consume to the expiry sweep between that read and the write is not an
        error either -- the sweep and the authorization contend on one document
        by design -- so it falls through to the same assignment a never-held line
        would take, and says so in the log.
        """
        if not plan.incoming.order_line_references:
            return
        held = await self._held_lines(request.case_id)
        for line in plan.incoming.order_line_references:
            for item in await self._repository.list_case_return_items(request.case_id):
                if item.get("orderLineId") != line or item.get("returnRecordId"):
                    continue
                if line in held and await self._authorize_held_line(request, plan, line):
                    break
                await self._repository.assign_return_item_to_record(
                    str(item["returnItemId"]),
                    return_record_id=plan.record_id,
                    expected_version=int(item.get("version", 0)),
                )
                break

    async def _held_lines(self, case_id: str) -> frozenset[str]:
        """The lines this case still holds an unexpired `ACTIVE` reservation on.

        `is_held` rather than the stored state alone: a hold past its deadline is
        `ACTIVE` in the document and holds nothing in the arithmetic, and treating
        it as live here would route the assignment through a consume that the
        conditional update is guaranteed to refuse.

        Read through `getattr` for the reason `_assess_completion` does: several
        suites register this activity set against a narrower repository port, and
        a case with no reservation collection behind it must record its outcome
        exactly as it did before the reservation lifecycle existed.
        """
        lister = getattr(self._repository, "list_case_reservations", None)
        if lister is None:
            return frozenset()
        now = datetime.now(UTC)
        return frozenset(
            view.order_line_reference
            for view in await lister(case_id, states=(ReservationState.ACTIVE,))
            if is_held(view, now=now)
        )

    async def _authorize_held_line(
        self, request: RecordSupportOutcomeInput, plan: _RecordPlan, line: str
    ) -> bool:
        """Consume the hold and attach the item in one transaction. Did it win?

        `False` means the hold was settled between the read above and this write
        -- the expiry sweep, or a concurrent authorization -- and the caller
        falls back to the plain assignment. It never means "assigned"; the two
        outcomes of this method are "the item now carries the RMA and the hold is
        `CONSUMED`" and "nothing was written".
        """
        authorize = getattr(self._repository, "authorize_reserved_line", None)
        if authorize is None:
            return False
        try:
            await authorize(
                case_id=request.case_id,
                order_line_reference=line,
                return_record_id=plan.record_id,
            )
        except QuantityReservationExpiredError as lost:
            logger.info(
                "order_line_hold_settled_before_authorization",
                extra={
                    "case_id": request.case_id,
                    "order_line_reference": line,
                    "reservation_state": (lost.state.value if lost.state is not None else "ABSENT"),
                },
            )
            return False
        return True

    def _requirement_table(self) -> ReturnMethodRequirementTable | None:
        """The released return-method requirement table, for the completion read.

        `None` means **this worker has no configuration source at all** -- the
        activity set was constructed without the `configuration` callable. That
        is a property of the wiring, not of the moment: it is true for every
        pre-Phase-4 activity double and for any worker registered against a
        narrower surface, it is identical on every retry, and no amount of
        retrying makes a table appear. It is the same class of fact as a
        repository with no `load_case_projection_state`, so `_assess_completion`
        reports it the same way -- as "we cannot tell".

        **A wired worker whose configuration is not available raises instead.**
        The callable is bound to the activation state in
        `scripts/run_return_workflow_worker.py`, so `None` from it means a
        release is not active *right now* -- startup, or an activation that has
        not landed. That is the situation `api/cases.py::_requirement_table`
        answers `503 ... "retryable": True` to, and the activity's equivalent of
        a retryable 503 is to fail the attempt and let `_PERSIST_RETRY` run it
        again. Reporting it as "we cannot tell" would be worse than untrue: a
        drained case whose completion is unknown makes the run loop return its
        outcome and stop (`return self._outcome()`), so a transient
        configuration gap would leave every case Support answered during it
        open, unassessed and with no execution watching it -- permanently, and
        behind nothing louder than a warning log.

        What it must never do is substitute `DEFAULT_RETURN_METHOD_REQUIREMENTS`.
        The workflow's "keep waiting or close" and the API's `businessComplete`
        would then be two answers computed from two tables, and the operator's
        release would govern only one of them.
        """
        if self._configuration is None:
            return None
        configuration = self._configuration()
        if configuration is None:
            raise RuntimeError(
                "no return configuration is active, so the return-method requirement table "
                "cannot be resolved and case completion cannot be assessed; the attempt is "
                "failed rather than assessed from a code constant"
            )
        return build_return_method_requirement_table(configuration)

    async def _assess_completion(self, case_id: str) -> tuple[bool, bool, tuple[str, ...], int]:
        """Where the case stands, as the read contract computes it.

        `(known, businessComplete, awaiting, revision)`. The workflow needs this
        to decide whether to keep waiting, and it may not compute it itself: the
        answer is a set difference over the requirement table, the policy
        decision and every child collection of the case.

        `known` is `False` rather than a guess whenever the repository cannot
        assemble the projection -- an activity double, a worker wired to a
        narrower port. The run loop then behaves exactly as it did before this
        phase. Reported rather than defaulted, because "not complete" and "we
        cannot tell" send a case to opposite fates: one keeps waiting and the
        other closes.

        The requirement table is resolved **before** the guard below and
        deliberately outside it. `_requirement_table` raising is the one failure
        here that must reach Temporal: the guard exists to stop an unreadable
        case from being called complete, and swallowing a missing release into
        it would turn "this platform has not been told what a return needs" into
        a warning log and an unsupervised case.
        """
        loader = getattr(self._repository, "load_case_projection_state", None)
        if loader is None:
            return (False, False, (), 0)
        requirements = self._requirement_table()
        if requirements is None:
            return (False, False, (), 0)
        try:
            state = await loader(case_id)
            if state is None:
                return (False, False, (), 0)
            projection = project_case(state, requirements=requirements)
        except Exception:  # noqa: BLE001 - a completion we cannot read is not a completion
            logger.warning(
                "case_completion_not_assessable", extra={"case_id": case_id}, exc_info=True
            )
            return (False, False, (), 0)
        return (
            True,
            projection.businessComplete,
            tuple(str(dimension) for dimension in projection.awaiting),
            projection.revision,
        )

    async def _persist_records_to_return_store(
        self, request: RecordSupportOutcomeInput, plans: list[_RecordPlan]
    ) -> None:
        """Project the support outcome onto the shared issuance seam.

        The mapping onto the SQL store, the `uuid5` derivation of item ids from
        the record id and the order line, and the rule that issuance writes no
        `dbo.return_tracking` row all live in
        `operations/return_issuance.py` now. They moved because this was not the
        only path that issues an RMA: the Support console issues them too and
        wrote none of this, so the authoritative store held nothing for a return
        the screen reported as created. A seam both callers reach is the only
        arrangement in which the two cannot drift.

        What stays here is what is genuinely the workflow's: reading the case,
        resolving items from the case aggregate, and the merge plan. Support
        discovers its records from its own ticket rows and shares none of that.

        Items keep their record. Nothing here promotes a label, tracking
        reference or return location onto the case (contract C3).

        **The merged values go to SQL, not the notice's.** The upsert here is a
        whole-row `UPDATE ... SET`, so sending the notice's raw nulls would
        blank the label column of a record whose second reply carried only a
        tracking number -- the same erasure the Mongo merge exists to prevent,
        in the store that is authoritative. One merge, computed once, used by
        both writers, so the two cannot disagree about what a null meant.

        `return_method` joins that merge (D23). Its column landed in
        `sql_migrations/007_return_record_method.sql`, so the authoritative row
        now carries the value the completion profile is computed from rather
        than only the platform case and the per-record fact. It goes through
        `plan.merged` like every other field for the reason above: a second
        reply carrying a tracking number and no method would otherwise blank the
        method column of a record Support has already decided.

        `carrier` joins it on the same terms (audit finding #9). Its column
        landed in `sql_migrations/008_return_record_carrier.sql`, so the
        authoritative row carries the carrier `ShipmentProjection.carrier`
        reports rather than only the platform case -- and it goes through
        `plan.merged` so a later notice with no carrier cannot blank it either.

        The ordering is unchanged and load-bearing (T-14): this runs before the
        platform case is updated, so a Mongo case can never report a method the
        SQL return store never received.

        The adapter's contracts are imported here rather than at module scope
        so `workflows` does not pull `pymssql` in just by being imported --
        the workflow module sits next to this one and is sandboxed.
        """
        from return_platform.operations.return_issuance import (
            IssuanceIntent,
            IssuanceItem,
            IssuanceRecord,
            ReturnIssuance,
        )

        case = await self._repository.get_case(request.case_id)
        if case is None:
            raise ValueError(f"case {request.case_id} does not exist")

        items_by_line = {
            str(item["orderLineId"]): item
            for item in await self._repository.list_case_return_items(request.case_id)
            if item.get("orderLineId") is not None
        }

        records: list[IssuanceRecord] = []
        for plan in plans:
            record = plan.incoming
            items: list[IssuanceItem] = []
            for line in record.order_line_references:
                source = items_by_line.get(str(line), {})
                items.append(
                    IssuanceItem(
                        order_line_id=str(line),
                        quantity=int(source.get("quantity") or 1),
                        product_id=(
                            str(source["productId"])
                            if source.get("productId") is not None
                            else None
                        ),
                        reason_code=(
                            str(source["reasonCode"])
                            if source.get("reasonCode") is not None
                            else None
                        ),
                    )
                )
            records.append(
                IssuanceRecord(
                    return_record_id=plan.record_id,
                    return_reference=record.return_reference,
                    items=tuple(items),
                    label_reference=plan.merged.get("labelReference"),
                    tracking_reference=plan.merged.get("trackingReference"),
                    return_location=plan.merged.get("returnLocation"),
                    shipping_instruction_reference=plan.merged.get("shippingInstructionReference"),
                    return_method=plan.merged.get("returnMethod"),
                    carrier=plan.merged.get("carrier"),
                )
            )

        assert self._return_store is not None
        await ReturnIssuance(self._return_store).issue(
            IssuanceIntent(
                case_id=request.case_id,
                tenant_id=str(case.get("tenantId") or ""),
                principal_id=str(case.get("principalId") or ""),
                order_reference=(
                    str(case["orderReference"]) if case.get("orderReference") is not None else None
                ),
                records=tuple(records),
            )
        )

    @activity.defn(name="synchronize_return_records")
    async def synchronize_return_records(self, request: SynchronizeReturnRecordsInput) -> str:
        """Project the committed records into the graph, then say which generation.

        Returns *after* the write commits, never before: the port's contract is
        that a successful return is a post-commit fact, because generation-fenced
        writes mean an agent turn landing mid-write reads a case whose RMAs are
        half there. The workflow records the returned generation, so "the sync
        finished" and "which graph the associate will be answered from" are the
        same statement rather than two hopeful ones.

        Raises when no port is configured. A worker that registers this activity
        without one would silently skip the step for every case, which is the
        shape of the bug W2.5 exists to close -- and unlike the drafter, there is
        no honest fallback: nothing else puts a return into the graph before the
        next turn.
        """
        if self._graph_sync is None:
            raise RuntimeError(
                "synchronize_return_records was registered without a graph sync port; "
                "the return records for case "
                f"{request.case_id} would exist in the store and not in the graph"
            )
        outcome = await self._graph_sync.synchronize_records(
            case_id=request.case_id,
            return_record_ids=request.return_record_ids,
        )
        # A fact rather than a case column: which generation answered a case is
        # provenance, and `case_facts` is where provenance that must survive a
        # later correction lives. `fact_id` is derived from the case and the
        # generation, so an activity retry that lands in the same generation
        # finds the fact already recorded and moves on instead of failing on
        # the unique `factId` -- the log stays insert-only either way.
        await self._append_fact_once(
            fact_id=f"return-graph-generation-{request.case_id}-{outcome.graph_generation_id}",
            case_id=request.case_id,
            fact_name="return_graph_generation_id",
            value=outcome.graph_generation_id,
            agent_id="return-workflow-agent",
            channel=FactChannel.SYSTEM,
            acquisition_method=FactAcquisition.DERIVED,
            source_path="RETURN_RECORD_ON_DEMAND_SYNC",
        )
        logger.info(
            "return_records_synchronized",
            extra={
                "case_id": request.case_id,
                "graph_generation_id": outcome.graph_generation_id,
                "record_count": len(outcome.synchronized_record_ids),
                "nodes_written": outcome.nodes_written,
            },
        )
        return outcome.graph_generation_id


def _gap_field_ids(review: Mapping[str, Any]) -> tuple[str, ...]:
    """The gaps on a review's *current* draft payload.

    Read off the payload rather than off the fact log, because the fact log
    accumulates a gap per draft version and a re-render that filled one leaves
    the old fact standing. The payload is what the reviewer is looking at, and
    it is what the message would be built from.
    """
    payload = review.get("draftPayload") or {}
    if not isinstance(payload, Mapping):
        return ()
    gaps = payload.get(PAYLOAD_GAPS) or ()
    if not isinstance(gaps, Sequence):
        return ()
    return tuple(str(gap.get("field_id", "")) for gap in gaps if isinstance(gap, Mapping))


def _draft_result(request: TemplateReviewDraftInput, draft: Any) -> TemplateReviewDraftResult:
    """A `GateDraft` as the workflow's dataclass.

    The two are separate types on purpose: the workflow's is in the *history*,
    so it may only ever gain defaulted fields, while the gate's is free to
    change with the panel. Collapsing them would put the panel's shape into
    every recorded execution.
    """
    return TemplateReviewDraftResult(
        request_id=request.request_id,
        review_id=draft.review_id or request.review_id,
        state=draft.state,
        draft_version=draft.draft_version,
        canonical_edit_version=draft.canonical_edit_version,
        gap_field_ids=draft.gap_field_ids,
        template_available=draft.template_available,
    )
