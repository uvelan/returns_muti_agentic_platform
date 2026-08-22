"""The two frozen enums of the Copilot read contract, and the sets derived from them.

`ReturnCaseStatus` is **persisted, workflow-owned and authoritative**.
`CopilotStage` is **derived and never stored**. The relationship between them is
many-to-many, which is why there is no lookup table anywhere in this package:
one status covers several stages (an `AWAITING_SUPPORT` case is at
`AWAITING_SUPPORT` before Support answers and at `AUTHORIZED_RMA` after it
issues an RMA and before the workflow has moved), and one stage covers several
statuses (`COMPLETED` is the stage of every terminal status, successful or not).

Both sets are declared **complete here and now** (plan sect. 6.2) so that policy,
completion and recovery work do not each extend them. Adding a member later is a
contract break for the frontend, the migration and the mock server at once.

`ShipmentStatus`, `ReturnArtifactType` and `SettlementStatus` are the smaller
vocabularies the stage and completion rules read. They are declared here rather
than beside their models because the rules are the reason they exist, and a
vocabulary that lives next to one consumer acquires a second spelling next to
the other.

`NormalizedReturnMethod` is **imported, never redefined**. The ten values are
already the operator-owned catalogue in `return_policy.normalized_return_methods`
and already an enum in `agents/contracts/dto.py`; a third declaration is how
`CUSTOMER_KEEP` ends up meaning two things.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from return_platform.agents.contracts.dto import NormalizedReturnMethod

__all__ = [
    "AWAITING_DIMENSION_ORDER",
    "COMPLETION_FORBIDDING_STATUSES",
    "STAGE_LIFECYCLE_ORDER",
    "STAGE_PRECEDENCE",
    "STAGE_PRECEDENCE_LEVELS",
    "SUCCESSFUL_TERMINAL_STATUSES",
    "TERMINAL_RETURN_CASE_STATUSES",
    "UNKNOWN_RETURN_METHOD",
    "UNSUCCESSFUL_TERMINAL_STATUSES",
    "AwaitingDimension",
    "CopilotStage",
    "NormalizedReturnMethod",
    "ReturnArtifactType",
    "ReturnCaseStatus",
    "ReturnRecordStatus",
    "SettlementStatus",
    "ShipmentStatus",
    "StageRegressionReason",
    "SupportOutcome",
    "awaiting_dimension_rank",
    "stage_rank",
]


class ReturnCaseStatus(StrEnum):
    """Where the workflow says the case is. Persisted, and the only authority.

    Complete as declared. In particular there are **no `ROUTED_*` members**:
    warranty and delivery claims are `AWAITING_SUPPORT` carrying an `awaiting`
    dimension, because Support verifies both inside this application and an
    approved case rejoins the ordinary RMA lifecycle. A routed terminal status
    would end a case that is only halfway through its own happy path.
    """

    #: The associate is still establishing who, which order and which lines.
    GATHERING_INFO = "GATHERING_INFO"
    #: The deterministic evaluator returned `REVIEW_REQUIRED`; a supervisor
    #: override is the only thing that moves this forward.
    AWAITING_POLICY_REVIEW = "AWAITING_POLICY_REVIEW"
    #: A work item is open on a Support queue. Also the status of the warranty
    #: and delivery-claim verification hand-offs.
    AWAITING_SUPPORT = "AWAITING_SUPPORT"
    #: Support has answered at least once and the return is being fulfilled.
    PROCESSING_RETURN = "PROCESSING_RETURN"
    #: Completed with settlement inside the platform's responsibility.
    COMPLETED = "COMPLETED"
    #: Completed with settlement outside it. Distinct so that a completed-return
    #: count is never misread as a settled-return count (plan sect. 13, Phase 9).
    COMPLETED_EXTERNAL_SETTLEMENT = "COMPLETED_EXTERNAL_SETTLEMENT"
    #: The return was refused on its merits. Terminal.
    #:
    #: **Two producers, and they are distinguishable.** The evaluator returning
    #: `REJECT` is one: `policyEvaluation.route` is `STANDARD_RETURN`,
    #: `originalDecision` is `REJECT`, and no work item was ever opened. Support
    #: refusing a warranty or delivery claim is the other: the route is the
    #: verification route, `originalDecision` is null because those routes carry
    #: no decision, a `support` block is present, and the `support_outcome` fact
    #: reads `REJECTED` with the reason Support gave and the instant it was
    #: recorded.
    #:
    #: They share a member because this enum is frozen and offers exactly one
    #: ending that means refused; the alternative was reporting a refused claim
    #: as `COMPLETED_EXTERNAL_SETTLEMENT`, which asserts a credit nobody issued.
    #: `status_mapping` states the trade in full.
    POLICY_REJECTED = "POLICY_REJECTED"
    #: Closed by an actor. Server-derived actor and timestamp, audited.
    CANCELLED = "CANCELLED"
    #: Closed by the deadline rather than by anyone.
    EXPIRED = "EXPIRED"
    #: The case and its durable execution disagree. **Not terminal** -- recovery
    #: can restart processing, and marking it terminal would stop the client
    #: polling a case that is about to resume.
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


class CopilotStage(StrEnum):
    """Which mode the Copilot renders. Derived on read, never stored.

    Declared in lifecycle order, earliest first. `STAGE_PRECEDENCE` is this
    tuple reversed, because the frozen precedence of plan sect. 6.6 is exactly
    "the furthest-progressed thing that is true wins".
    """

    DISCOVERY = "DISCOVERY"
    ORDER_CONFIRMATION = "ORDER_CONFIRMATION"
    ITEM_SELECTION = "ITEM_SELECTION"
    RETURN_FACTS = "RETURN_FACTS"
    POLICY_EVALUATION = "POLICY_EVALUATION"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    AWAITING_SUPPORT = "AWAITING_SUPPORT"
    AUTHORIZED_RMA = "AUTHORIZED_RMA"
    CARRIER_TRANSIT = "CARRIER_TRANSIT"
    WAREHOUSE_RECEIVING = "WAREHOUSE_RECEIVING"
    RETURN_SETTLEMENT = "RETURN_SETTLEMENT"
    COMPLETED = "COMPLETED"


class AwaitingDimension(StrEnum):
    """What the platform is still waiting for before the return is complete.

    Two kinds of member, and the difference matters:

    * **Unresolved** -- `POLICY`, `RETURN_METHOD`, `RECOVERY`, and the two
      verification dimensions. These say the completion *profile* cannot be
      computed yet, so no requirement set has been consulted at all.
    * **Required** -- `RMA`, `LABEL`, `TRACKING`, `BOL`, `PICKUP`,
      `RETURN_LOCATION`. These come out of the return-method requirement table
      and are the only members a table row may name.

    Settlement is deliberately absent. It never enters `awaiting` and never
    blocks completion (plan sect. 13, Phase 9): a case whose settlement is
    `NOT_INTEGRATED` reaches `COMPLETED_EXTERNAL_SETTLEMENT`, it does not hang.
    """

    # --- Unresolved dimensions.
    #: No `APPROVE` on the effective decision yet -- unevaluated, rejected,
    #: or awaiting a supervisor override.
    POLICY = "POLICY"
    #: Nobody has said how the goods come back, or the method reads `UNKNOWN`.
    RETURN_METHOD = "RETURN_METHOD"
    #: Support must verify a warranty claim before the case rejoins the RMA
    #: lifecycle (plan sect. 7.6).
    WARRANTY_VERIFICATION = "WARRANTY_VERIFICATION"
    #: Support must verify a delivery claim, on the reporting-window deadline.
    DELIVERY_CLAIM_VERIFICATION = "DELIVERY_CLAIM_VERIFICATION"
    #: The case and its execution have diverged. Non-terminal, and never
    #: complete: this is what keeps a `RECOVERY_REQUIRED` case that happens to
    #: satisfy every requirement from reporting itself as done.
    RECOVERY = "RECOVERY"

    # --- Requirement dimensions, produced only by the requirement table.
    RMA = "RMA"
    LABEL = "LABEL"
    TRACKING = "TRACKING"
    BOL = "BOL"
    PICKUP = "PICKUP"
    RETURN_LOCATION = "RETURN_LOCATION"


class ShipmentStatus(StrEnum):
    """One package's progress. Aligned with `FulfillmentTrackingStatus` where they overlap.

    `AWAITING_HANDOFF` and `IN_TRANSIT` are spelled as
    `workflows/fulfillment_tracking.py` already spells them, so the case
    projection and the legacy session path do not describe the same parcel with
    two words.

    `DELIVERED` and `RECEIVED` are separate because they are separate events:
    the carrier delivering to a dock is not the warehouse booking the goods in,
    and a return that is delivered but unbooked is exactly the state a receiving
    supervisor needs to see.
    """

    AWAITING_HANDOFF = "AWAITING_HANDOFF"
    IN_TRANSIT = "IN_TRANSIT"
    DELIVERED = "DELIVERED"
    RECEIVED = "RECEIVED"
    #: Voided, most often because a replacement shipment superseded it. A
    #: cancelled shipment contributes nothing to the stage and satisfies nothing.
    CANCELLED = "CANCELLED"


class ReturnArtifactType(StrEnum):
    """What a shipment artifact is. The three the completion rules distinguish.

    A bill of lading is not a shipping label, and `bol_tendering_instruction_types`
    in the return configuration already treats them as different documents. A
    freight return whose BOL requirement were satisfied by a parcel label would
    complete with nothing to hand the driver.
    """

    SHIPPING_LABEL = "SHIPPING_LABEL"
    BILL_OF_LADING = "BILL_OF_LADING"
    RETURN_INSTRUCTIONS = "RETURN_INSTRUCTIONS"


class SettlementStatus(StrEnum):
    """Where the credit stands.

    `NOT_INTEGRATED`, never `NOT_STARTED`. The latter implies a producer that
    has not run; there is no producer at all, and the distinction is the whole
    reason plan sect. 13 Phase 9 names it.
    """

    NOT_INTEGRATED = "NOT_INTEGRATED"
    PENDING = "PENDING"
    SETTLED = "SETTLED"


class ReturnRecordStatus(StrEnum):
    """Where one issued RMA stands.

    Declared because it was not. `operations/order_lines/availability.py` records
    rejecting this field as a classification input on exactly that ground -- "a
    free string written as `DRAFT` by the repository and `ISSUED` by the
    workflow, with no declared vocabulary and nothing that would fail if a new
    value appeared" -- and then had to classify on case status instead.

    The members are the SQL CHECK constraint's, verbatim:
    `CK_return_record_status` in `sql_migrations/005_case_return_records.sql:85`
    admits `ISSUED`, `CANCELLED` and `CLOSED`. Taking the vocabulary from the
    constraint rather than inventing one beside it is the point: the two stores
    that hold this field previously agreed only by coincidence of two separate
    hardcoded literals -- `return_case_activities.py:1652` for MongoDB and the
    `ReturnRecordWrite.record_status` default for SQL -- and a value legal in one
    could have been rejected by the other.

    **`DRAFT` is deliberately absent.** `case_repository.create_return_record`
    carries it as a parameter default, but its one production caller always
    passes `ISSUED` explicitly, so no record has ever held it. Admitting a member
    nothing writes would make the vocabulary describe the code's history rather
    than its states.

    `CANCELLED` and `CLOSED` are admitted and currently unwritten. That is the
    opposite case and it is intentional: the constraint already accepts them, so
    a writer that starts using one must not be rejected by a narrower Python
    view of the same column.
    """

    ISSUED = "ISSUED"
    CANCELLED = "CANCELLED"
    CLOSED = "CLOSED"


class SupportOutcome(StrEnum):
    """What Support answered, recorded when it answers rather than inferred later.

    **Not part of the wire contract.** It is the vocabulary of the
    `support_outcome` case fact, which reaches a reader through `facts[]` with
    the provenance every fact carries -- writer, channel, source system and the
    instant it was recorded. It is declared here for the reason
    `StageRegressionReason` is: the projection rules are what it exists for, and
    a vocabulary that lives next to one of its two users acquires a second
    spelling next to the other.

    Two members, because Support's reply has two terminal readings and the
    platform previously kept neither. `record_support_outcome` received
    `rejected` and `reason` on every notice and discarded both, so the only
    trace a refusal left was `cases.status: CLOSED` -- the same value a
    *completed* return reaches. That is what made a refused claim project as
    `COMPLETED_EXTERNAL_SETTLEMENT`: not a mapping mistake, but a projection
    reading a status that genuinely could not tell the two apart.

    **On a verification route these are the verification's outcome.**
    `_verification_recorded` already argues that an RMA on a warranty or
    delivery-claim case *is* Support's verification, because nothing else can
    put one there; `AUTHORIZED` is that same statement recorded by the writer
    instead of inferred by the reader, and `REJECTED` is the half that had no
    representation at all -- "we looked and the answer was no".

    **It does not name the person.** `SupportResponseNotice` carries no actor,
    so the fact is written by `return-support` on Channel B and says who
    *recorded* the answer, never who reached it. That absence is deliberate and
    is the remaining half of the audit trail; see `_verification_recorded`.
    """

    #: Support issued at least one RMA. On a verification route, the claim
    #: stands and the case rejoins the ordinary RMA lifecycle.
    AUTHORIZED = "AUTHORIZED"
    #: Support declined. Terminal: no authorization is coming, and on a
    #: verification route this is the claim examined and refused -- which is not
    #: a settlement, and must never be counted as one.
    REJECTED = "REJECTED"


class StageRegressionReason(StrEnum):
    """The only reasons a stage may move backwards (plan sect. 6.6).

    Enumerated rather than implied. Monotonicity is asserted as a property over
    the derivation, and a property with unnamed exceptions is a property nobody
    can test.
    """

    CANCELLATION = "CANCELLATION"
    RECOVERY = "RECOVERY"
    REPLACEMENT_SHIPMENT = "REPLACEMENT_SHIPMENT"


#: The method value that means "nobody has decided". Named because three rules
#: read it and a bare `"UNKNOWN"` in each of them is three chances to typo.
UNKNOWN_RETURN_METHOD: Final = NormalizedReturnMethod.UNKNOWN

#: Terminal and successful.
SUCCESSFUL_TERMINAL_STATUSES: Final[frozenset[ReturnCaseStatus]] = frozenset(
    {
        ReturnCaseStatus.COMPLETED,
        ReturnCaseStatus.COMPLETED_EXTERNAL_SETTLEMENT,
    }
)

#: Terminal and not successful. A case here is finished and was not completed,
#: which is a different thing from a case that is still working.
UNSUCCESSFUL_TERMINAL_STATUSES: Final[frozenset[ReturnCaseStatus]] = frozenset(
    {
        ReturnCaseStatus.POLICY_REJECTED,
        ReturnCaseStatus.CANCELLED,
        ReturnCaseStatus.EXPIRED,
    }
)

#: `isTerminal` is exactly membership of this set, read from the persisted
#: status and nothing else. **The read path never calls Temporal.**
#: `RECOVERY_REQUIRED` is deliberately absent.
TERMINAL_RETURN_CASE_STATUSES: Final[frozenset[ReturnCaseStatus]] = (
    SUCCESSFUL_TERMINAL_STATUSES | UNSUCCESSFUL_TERMINAL_STATUSES
)

#: Statuses that make `businessComplete` false whatever the requirement set says.
#:
#: A case cancelled after its RMA, label and tracking all arrived would
#: otherwise satisfy every requirement and report itself complete. It is not
#: complete; it is cancelled, and the two must not be summed.
#:
#: `RECOVERY_REQUIRED` is not here because it is handled the other way round --
#: as an `awaiting` dimension -- so that the case still reads as *working on
#: something*, which it is.
COMPLETION_FORBIDDING_STATUSES: Final[frozenset[ReturnCaseStatus]] = UNSUCCESSFUL_TERMINAL_STATUSES

#: Lifecycle order, earliest first. Declaration order of the enum.
STAGE_LIFECYCLE_ORDER: Final[tuple[CopilotStage, ...]] = tuple(CopilotStage)

#: The frozen precedence of plan sect. 6.6, highest level first.
#:
#: **Eleven levels for twelve stages.** Level 7 is "policy evaluation /
#: approval required" -- the plan writes them on one line and they are one
#: level, not two. That is not cosmetic: it is what makes an overridden
#: `REVIEW_REQUIRED -> APPROVE` legal. A supervisor approving a case moves it
#: from `APPROVAL_REQUIRED` to `POLICY_EVALUATION`, and if those were separate
#: levels that approval would read as the stage going backwards -- a monotonicity
#: violation produced by the system working exactly as designed.
#:
#: Within a level the more specific rule wins, which is why `APPROVAL_REQUIRED`
#: is written first: a case that has been evaluated *and* needs an approval is
#: better described by the approval it is waiting on.
STAGE_PRECEDENCE_LEVELS: Final[tuple[tuple[CopilotStage, ...], ...]] = (
    (CopilotStage.COMPLETED,),
    (CopilotStage.RETURN_SETTLEMENT,),
    (CopilotStage.WAREHOUSE_RECEIVING,),
    (CopilotStage.CARRIER_TRANSIT,),
    (CopilotStage.AUTHORIZED_RMA,),
    (CopilotStage.AWAITING_SUPPORT,),
    (CopilotStage.APPROVAL_REQUIRED, CopilotStage.POLICY_EVALUATION),
    (CopilotStage.RETURN_FACTS,),
    (CopilotStage.ITEM_SELECTION,),
    (CopilotStage.ORDER_CONFIRMATION,),
    (CopilotStage.DISCOVERY,),
)

#: The levels flattened, highest first. The order `derive_copilot_stage`
#: evaluates its rules in.
STAGE_PRECEDENCE: Final[tuple[CopilotStage, ...]] = tuple(
    stage for level in STAGE_PRECEDENCE_LEVELS for stage in level
)

if set(STAGE_PRECEDENCE) != set(CopilotStage):  # pragma: no cover - import-time guard
    raise RuntimeError(
        "every CopilotStage needs a precedence level: a stage the precedence does not "
        "rank is a stage monotonicity cannot be asserted over"
    )

#: Rank by *level*, so the two stages sharing level 7 rank equal and neither
#: counts as a regression from the other.
_STAGE_RANKS: Final[dict[CopilotStage, int]] = {
    stage: len(STAGE_PRECEDENCE_LEVELS) - 1 - index
    for index, level in enumerate(STAGE_PRECEDENCE_LEVELS)
    for stage in level
}

#: A stable presentation order for `awaiting`, so two equal awaiting sets
#: serialize identically and a revision does not appear to change because a set
#: iterated differently.
AWAITING_DIMENSION_ORDER: Final[tuple[AwaitingDimension, ...]] = tuple(AwaitingDimension)

_AWAITING_RANKS: Final[dict[AwaitingDimension, int]] = {
    dimension: index for index, dimension in enumerate(AWAITING_DIMENSION_ORDER)
}


def stage_rank(stage: CopilotStage) -> int:
    """How far through the lifecycle a stage is. Higher is further."""
    return _STAGE_RANKS[stage]


def awaiting_dimension_rank(dimension: AwaitingDimension) -> int:
    """Presentation position of an awaiting dimension."""
    return _AWAITING_RANKS[dimension]
