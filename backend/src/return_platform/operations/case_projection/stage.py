"""`derive_copilot_stage` -- one pure function, one frozen precedence.

```text
1  terminal            7  policy evaluation / approval required
2  settlement          8  return facts
3  warehouse           9  item selection
4  carrier transit    10  order confirmation
5  authorized RMA     11  discovery
6  awaiting support        (includes warranty and delivery-claim verification)
```

**Never a lookup table.** The relationship between `ReturnCaseStatus` and
`CopilotStage` is many-to-many: an `AWAITING_SUPPORT` case is at
`AWAITING_SUPPORT` before Support answers and at `AUTHORIZED_RMA` the moment it
issues an RMA -- with the persisted status unchanged, because the workflow has
not been signalled yet. A table keyed on status cannot express that, and the
version of this logic that tried had the pane telling an associate to wait for
Support while the RMA sat on the screen beside it.

**Mixed shipment states resolve to the furthest-progressed package**, which is
what the precedence order *is*: warehouse outranks transit, so an RMA with one
parcel still moving and one already booked in reads `WAREHOUSE_RECEIVING`. The
associate sees the leading edge of the return rather than its slowest package.

**Monotonicity.** Stage must not regress because a secondary package moved.
`classify_stage_transition` is how that is asserted rather than asserted about:
it names the regression when one happens and says whether one of the three
enumerated reasons -- cancellation, recovery, replacement shipment -- explains
it. Under this derivation cancellation and recovery cannot in fact regress the
stage at all (a cancelled case is terminal, which is the highest stage; a
recovering case keeps whatever its records say), so a replaced shipment is the
only regression the platform can actually produce. The other two stay
enumerated because a future rule change could reach them, and an exception
nobody wrote down is an exception nobody can review.

One monotonicity guarantee has a **persistence precondition**, and it belongs in
the wiring rather than here. `AWAITING_SUPPORT` outranks
`POLICY_EVALUATION`, so a case whose Support work item is marked resolved
*before* its RMA is written would regress for an instant. Plan sect. 6.5 already
requires both writes in one transaction with a single revision bump; that
invariant is what keeps this function monotone, and a projection assembled from
two separate commits would violate it without any change to the rules below.

`isTerminal` -- and therefore the terminal branch below -- derives from the
persisted status only. **The read path never calls Temporal.**
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final

from pydantic import Field

from return_platform.operations.case_projection.completion import (
    effective_decision,
    is_terminal_status,
)
from return_platform.operations.case_projection.contract import (
    CaseProjectionState,
    ProjectionModel,
    Reference,
)
from return_platform.operations.case_projection.vocabulary import (
    STAGE_PRECEDENCE,
    CopilotStage,
    ReturnCaseStatus,
    SettlementStatus,
    ShipmentStatus,
    StageRegressionReason,
    stage_rank,
)
from return_platform.policy import EligibilityDecision, PolicyReasonCode, PolicyRoute

__all__ = [
    "DEFAULT_STAGE_DERIVATION_POLICY",
    "StageDerivationPolicy",
    "StageTransition",
    "classify_stage_transition",
    "derive_copilot_stage",
    "stage_regressed",
]

#: Shipment states that mean the goods have reached the far end.
#:
#: `DELIVERED` counts as receiving. The carrier saying it dropped the pallet is
#: the earliest the receiving pane has anything to show, and holding the stage
#: at `CARRIER_TRANSIT` until a warehouse system books it in would leave the
#: associate watching a parcel that has already arrived.
_ARRIVED: Final[frozenset[ShipmentStatus]] = frozenset(
    {ShipmentStatus.DELIVERED, ShipmentStatus.RECEIVED}
)


class StageDerivationPolicy(ProjectionModel):
    """The one thing stage derivation cannot read off the contract's shape.

    Whether the associate has captured *return* facts is not the same question
    as whether the fact log is non-empty -- order discovery writes facts too,
    and a case that has only ever been searched for would jump to `RETURN_FACTS`
    on the strength of an order number.

    Defaults to the `RETURN_CONTEXT` field group already declared in
    `smart_question.fields`. **Where this belongs:** read from that
    configuration section rather than restated here, once the projection is
    wired to the loaded configuration.
    """

    return_fact_names: frozenset[Reference] = Field(min_length=1)


DEFAULT_STAGE_DERIVATION_POLICY: Final = StageDerivationPolicy(
    return_fact_names=frozenset({"return_reason", "product_presence"})
)


def _is_terminal(case: CaseProjectionState, policy: StageDerivationPolicy) -> bool:
    del policy
    return is_terminal_status(case.status)


def _is_settling(case: CaseProjectionState, policy: StageDerivationPolicy) -> bool:
    del policy
    return case.settlement is not None and case.settlement.status is not (
        SettlementStatus.NOT_INTEGRATED
    )


def _is_receiving(case: CaseProjectionState, policy: StageDerivationPolicy) -> bool:
    del policy
    if case.warehouse is not None and case.warehouse.has_receipt:
        return True
    return any(shipment.shipmentStatus in _ARRIVED for shipment in case.all_shipments())


def _is_in_transit(case: CaseProjectionState, policy: StageDerivationPolicy) -> bool:
    del policy
    return any(
        shipment.shipmentStatus is ShipmentStatus.IN_TRANSIT for shipment in case.all_shipments()
    )


def _has_authorized_rma(case: CaseProjectionState, policy: StageDerivationPolicy) -> bool:
    del policy
    return any(record.returnReference is not None for record in case.records())


def _evaluation_has_a_subject(case: CaseProjectionState) -> bool:
    """Whether the evaluator was in a position to decide anything.

    **The evaluator says so itself.** `REQUIRED_FACT_UNKNOWN` is the reason code
    it emits when a fact a rule needs is missing, and it fails safe to
    `REVIEW_REQUIRED` on the strength of it. That is an honest "nobody has told
    me yet", not a finding against the return -- and an associate whose case is
    missing a required fact belongs on the pane that captures facts, not the one
    that displays verdicts.

    Read from the evaluation rather than inferred from the case, after an
    attempt at the latter failed twice. Counting `selectedItems` looked
    equivalent and is not: `project_selected_items` omits any item an RMA
    already covers, so the list empties as a case *progresses* and the guard
    would have demoted genuinely evaluated cases later in the lifecycle. It also
    could not have worked as written, because that function ends `or None` and
    `CaseAggregateDocuments.return_items` defaults to `()`, leaving "looked and
    found nothing" indistinguishable from "never looked" at the source. The unit
    tests passed only because they built `selectedItems=()` by hand, a value the
    assembler cannot emit.

    A reason code decays with nothing and is the evaluator's own word.
    """
    evaluation = case.policyEvaluation
    if evaluation is None or evaluation.reasonCodes is None:
        return True
    return PolicyReasonCode.REQUIRED_FACT_UNKNOWN not in evaluation.reasonCodes


def _is_awaiting_support(case: CaseProjectionState, policy: StageDerivationPolicy) -> bool:
    """Support has it: the status says so, a work item is open, or a route sent it there.

    The route clause is what makes warranty and delivery-claim verification land
    on the existing stage. Both are `AWAITING_SUPPORT` with an `awaiting`
    dimension -- no new stage, no UI change -- because Support verifies both and
    an approved case rejoins the ordinary RMA lifecycle.

    Only the route clause is gated on there being something selected. The other
    two are observations of the world rather than inferences from an evaluation:
    an open Support work item exists whatever this case's projection holds, and
    hiding it would lose a real thing somebody is doing.
    """
    del policy
    if case.status is ReturnCaseStatus.AWAITING_SUPPORT:
        return True
    if case.support is not None and case.support.resolvedAt is None:
        return True
    return (
        _evaluation_has_a_subject(case)
        and case.policyEvaluation is not None
        and case.policyEvaluation.route is not PolicyRoute.STANDARD_RETURN
    )


def _needs_approval(case: CaseProjectionState, policy: StageDerivationPolicy) -> bool:
    """Both clauses are gated: each is a verdict, and a verdict needs a subject.

    `AWAITING_POLICY_REVIEW` is not exempt because it is a stored status rather
    than a derived one. It is the status the workflow writes *from* a fail-safe
    evaluation, so on an empty case it carries exactly the same emptiness -- and
    it is the clause the observed case actually tripped.
    """
    del policy
    if not _evaluation_has_a_subject(case):
        return False
    if case.status is ReturnCaseStatus.AWAITING_POLICY_REVIEW:
        return True
    return effective_decision(case) is EligibilityDecision.REVIEW_REQUIRED


def _has_policy_evaluation(case: CaseProjectionState, policy: StageDerivationPolicy) -> bool:
    del policy
    return _evaluation_has_a_subject(case) and case.policyEvaluation is not None


def _has_return_facts(case: CaseProjectionState, policy: StageDerivationPolicy) -> bool:
    if case.facts is None:
        return False
    return any(fact.factName in policy.return_fact_names for fact in case.facts)


def _has_selected_items(case: CaseProjectionState, policy: StageDerivationPolicy) -> bool:
    del policy
    return bool(case.selectedItems)


def _has_confirmed_order(case: CaseProjectionState, policy: StageDerivationPolicy) -> bool:
    del policy
    return case.confirmedOrder is not None


def _always(case: CaseProjectionState, policy: StageDerivationPolicy) -> bool:
    """Discovery is where a case is when nothing else is true of it yet."""
    del case, policy
    return True


#: One rule per stage. **The evaluation order is not here** -- it is
#: `STAGE_PRECEDENCE`, and this is a lookup on it.
#:
#: Written that way on purpose. A chain of `if`s carries the precedence in its
#: own shape, so reordering two branches silently reorders the frozen
#: precedence; keeping the order in the vocabulary means a rule can only be
#: reprioritised by editing the thing the plan froze.
_RULES: Final[
    Mapping[CopilotStage, Callable[[CaseProjectionState, StageDerivationPolicy], bool]]
] = {
    # Every terminal status lands on `COMPLETED`, successful or not: the stage
    # says the Copilot has nothing left to drive, and *why* is the status's job.
    # A `CANCELLED` case is not "still selecting items".
    CopilotStage.COMPLETED: _is_terminal,
    CopilotStage.RETURN_SETTLEMENT: _is_settling,
    CopilotStage.WAREHOUSE_RECEIVING: _is_receiving,
    CopilotStage.CARRIER_TRANSIT: _is_in_transit,
    CopilotStage.AUTHORIZED_RMA: _has_authorized_rma,
    CopilotStage.AWAITING_SUPPORT: _is_awaiting_support,
    CopilotStage.APPROVAL_REQUIRED: _needs_approval,
    CopilotStage.POLICY_EVALUATION: _has_policy_evaluation,
    CopilotStage.RETURN_FACTS: _has_return_facts,
    CopilotStage.ITEM_SELECTION: _has_selected_items,
    CopilotStage.ORDER_CONFIRMATION: _has_confirmed_order,
    CopilotStage.DISCOVERY: _always,
}

if set(_RULES) != set(CopilotStage):  # pragma: no cover - import-time guard
    raise RuntimeError(
        "every CopilotStage needs a derivation rule: a stage nothing can reach is a mode "
        "the Copilot can render and the backend can never ask for"
    )


def derive_copilot_stage(
    case: CaseProjectionState,
    *,
    policy: StageDerivationPolicy = DEFAULT_STAGE_DERIVATION_POLICY,
) -> CopilotStage:
    """Which mode the Copilot renders for this case. Pure, and the only producer.

    No stage logic in API handlers, no stage in the database, no second copy in
    the browser. One function, called on read.
    """
    for stage in STAGE_PRECEDENCE:
        if _RULES[stage](case, policy):
            return stage
    raise AssertionError(  # pragma: no cover - DISCOVERY is unconditional
        "stage derivation fell through a precedence that ends in DISCOVERY"
    )


def stage_regressed(previous: CopilotStage, current: CopilotStage) -> bool:
    """Whether the stage moved backwards through the lifecycle."""
    return stage_rank(current) < stage_rank(previous)


@dataclass(frozen=True, slots=True)
class StageTransition:
    """One observed stage change, and whether a regression in it is accounted for."""

    previous: CopilotStage
    current: CopilotStage
    regressed: bool
    #: The enumerated reason that explains a regression, or `None`. `None` on a
    #: regression is a defect: the stage moved backwards for a reason nobody
    #: declared.
    permitted_by: StageRegressionReason | None


def _active_shipment_ids(case: CaseProjectionState) -> frozenset[str]:
    return frozenset(shipment.shipmentId for shipment in case.all_shipments())


def classify_stage_transition(
    previous: CaseProjectionState,
    current: CaseProjectionState,
    *,
    policy: StageDerivationPolicy = DEFAULT_STAGE_DERIVATION_POLICY,
) -> StageTransition:
    """Derive both stages and decide whether a backwards move is one of the three.

    Reasons are checked in the order they can mask one another: a case that is
    cancelled *and* has a superseded shipment is a cancellation, because that is
    the event that closed it.
    """
    previous_stage = derive_copilot_stage(previous, policy=policy)
    current_stage = derive_copilot_stage(current, policy=policy)
    regressed = stage_regressed(previous_stage, current_stage)
    if not regressed:
        return StageTransition(previous_stage, current_stage, False, None)

    reason: StageRegressionReason | None = None
    if current.status is ReturnCaseStatus.CANCELLED and previous.status is not (
        ReturnCaseStatus.CANCELLED
    ):
        reason = StageRegressionReason.CANCELLATION
    elif current.status is ReturnCaseStatus.RECOVERY_REQUIRED and previous.status is not (
        ReturnCaseStatus.RECOVERY_REQUIRED
    ):
        reason = StageRegressionReason.RECOVERY
    elif _active_shipment_ids(previous) - _active_shipment_ids(current):
        reason = StageRegressionReason.REPLACEMENT_SHIPMENT
    return StageTransition(previous_stage, current_stage, True, reason)
