"""Completion semantics: what the platform is still waiting for, and when it is done.

```text
authorityStands =
      route is STANDARD_RETURN
        ? policyEvaluation.effectiveDecision == APPROVE
        : Support's verification is recorded

completionProfileResolved =
      authorityStands
  AND returnMethod resolved
  AND returnMethod != UNKNOWN

awaiting = completionProfileResolved
             ? required(returnMethod, policy) - satisfied(case)
             : [unresolved dimensions]

businessComplete = completionProfileResolved AND awaiting.isEmpty()
isTerminal       = ReturnCaseStatus is terminal
```

Four properties of that definition do the work, and each closes a specific way
the platform previously got completion wrong.

**It reads `effectiveDecision`, never `originalDecision`.** A supervisor's
override of `REVIEW_REQUIRED -> APPROVE` must resolve the profile. Reading the
original would leave an approved case waiting forever on an approval it already
has, and the case would look identical to one still queued for review.

**A verification route resolves its profile on the verification, not on a
decision it can never have.** Warranty and delivery claims carry
`decision: null` by construction -- `PolicyOutcome` refuses one, and
`PolicyEvaluationProjection` refuses both a decision and an override on those
routes, because Support verifies the claim rather than policy approving it. So
`effectiveDecision == APPROVE` is unreachable for them, and a rule that only
knew that test left every warranty and every delivery claim permanently
incomplete however much of the return had been fulfilled: RMA, label, tracking
and all, `awaiting` still read `[WARRANTY_VERIFICATION]` and `businessComplete`
was still false. That made the two routes dead ends -- exactly what modelling
them as `AWAITING_SUPPORT` with a dimension, rather than as terminal statuses,
exists to prevent. What stands in place of the approval is
`_verification_recorded`: see it for why the authorization Support issues is
the recorded verification and what would have to change for it to be anything
better.

**A refused claim is the same dead end from the other side, and it is closed
by the status rather than here.** Support declining a warranty or delivery
claim is a real ending -- the claim was examined and the answer was no -- and it
is not a completion, so `record_support_outcome` records the refusal as a
`support_outcome` fact and `project_case_status` reads it to project
`POLICY_REJECTED`. That status is in `COMPLETION_FORBIDDING_STATUSES`, so the
short-circuit below is what makes the case await nothing; nothing in the rules
themselves special-cases a refusal, because a refused case is finished for
exactly the reason a cancelled one is.

**`awaiting` is computed from a table, never mutated imperatively.** Nothing
appends to it and nothing removes from it; it is a set difference evaluated on
read. The method has to drive that table because `NO_PHYSICAL_RETURN` and
`CUSTOMER_KEEP` need no label, no tracking and no pickup -- a hardcoded "needs
tracking and label" rule hangs both of them forever, and neither would ever
appear as a bug because both would simply never complete.

**Completion cannot be reached by an empty requirement set.** Every row of the
table must require `RMA`, enforced at construction, so "the table said nothing
was needed" is not a state a configuration can express. That, plus
`COMPLETION_FORBIDDING_STATUSES`, is what makes a rejected, cancelled or expired
case unable to report itself complete however many requirements it happens to
satisfy.

**`businessComplete` means completion within configured platform
responsibility.** That boundary is the load-bearing assumption of everything
above, and it is what the requirement table encodes: the dimensions a method can
require are the ones *this* platform is answerable for. Work that belongs to
somebody else is outside the boundary and cannot appear in `awaiting`, however
obviously the return is not finished in a wider sense.

Settlement is the case in point, and it appears nowhere below. There is no
settlement producer in the platform, `AwaitingDimension` has no settlement
member, and nothing in this module reads `case.settlement` -- so settlement
cannot enter `awaiting` and cannot block completion even by accident. A return
therefore reaches `businessComplete` with its credit unissued, and
`status_mapping` is where that stops being ambiguous: the case reaches
`COMPLETED_EXTERNAL_SETTLEMENT` rather than `COMPLETED`, so "complete" here is
never mistaken for "settled".
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Final

from pydantic import Field, model_validator

from return_platform.operations.case_projection.contract import (
    CaseProjectionState,
    ProjectionModel,
    Reference,
    ReturnRecordProjection,
)
from return_platform.operations.case_projection.vocabulary import (
    COMPLETION_FORBIDDING_STATUSES,
    TERMINAL_RETURN_CASE_STATUSES,
    UNKNOWN_RETURN_METHOD,
    AwaitingDimension,
    NormalizedReturnMethod,
    ReturnArtifactType,
    ReturnCaseStatus,
    awaiting_dimension_rank,
)
from return_platform.policy import EligibilityDecision, PolicyRoute

__all__ = [
    "DEFAULT_RETURN_METHOD_REQUIREMENTS",
    "POLICY_GATE_STATE_FACT",
    "POLICY_GATE_SUSPENDED",
    "REQUIREMENT_DIMENSIONS",
    "ROUTE_VERIFICATION_DIMENSIONS",
    "UNRESOLVED_DIMENSIONS",
    "CompletionAssessment",
    "ReturnMethodRequirement",
    "ReturnMethodRequirementTable",
    "effective_decision",
    "is_terminal_status",
    "policy_gate_suspended",
    "resolve_completion",
    "resolve_method_requirements",
    "route_authority_stands",
    "verification_dimension",
]

#: The fact `EvaluateCaseEligibility` writes to say whether the gate ran, and the
#: one value of it that means the operator switched the gate off.
#:
#: Named here as literals rather than imported from `PolicyGateState`: that enum
#: lives in the workflow module, and the projection reaching into `workflows/`
#: would invert the dependency and drag `temporalio` into every process that
#: reads a case. `assembly.py` names the bay facts the same way and for the same
#: reason. The pair is pinned to the enum by a test, so the two cannot drift in
#: silence.
POLICY_GATE_STATE_FACT: Final[str] = "policy_evaluation_state"
POLICY_GATE_SUSPENDED: Final[str] = "SKIPPED_BY_CONFIGURATION"

#: The dimensions a requirement table row may name. Exactly the fulfilment ones:
#: an unresolved dimension is a statement about the *profile*, so a table that
#: could require `POLICY` would be a table asserting its own precondition.
REQUIREMENT_DIMENSIONS: Final[frozenset[AwaitingDimension]] = frozenset(
    {
        AwaitingDimension.RMA,
        AwaitingDimension.LABEL,
        AwaitingDimension.TRACKING,
        AwaitingDimension.BOL,
        AwaitingDimension.PICKUP,
        AwaitingDimension.RETURN_LOCATION,
    }
)

#: The dimensions that mean the completion profile could not be computed.
UNRESOLVED_DIMENSIONS: Final[frozenset[AwaitingDimension]] = (
    frozenset(AwaitingDimension) - REQUIREMENT_DIMENSIONS
)

#: The verification each non-standard route hands to Support, by route.
#:
#: Data rather than two `if` chains, and read in exactly two places -- once to
#: decide whether the profile may resolve, once to name what a case that has
#: not been verified is waiting for. Two chains would be two places for a
#: future route to be added to only one of, and the shape of that bug is a
#: route whose dimension is raised and never cleared.
#:
#: `STANDARD_RETURN` is absent by construction: it is the route that carries a
#: decision, and a row for it would mean a decided return waiting on a
#: verification nobody was ever asked for.
ROUTE_VERIFICATION_DIMENSIONS: Final[Mapping[PolicyRoute, AwaitingDimension]] = {
    PolicyRoute.WARRANTY: AwaitingDimension.WARRANTY_VERIFICATION,
    PolicyRoute.DELIVERY_CLAIM: AwaitingDimension.DELIVERY_CLAIM_VERIFICATION,
}

if not set(ROUTE_VERIFICATION_DIMENSIONS.values()) <= UNRESOLVED_DIMENSIONS:
    raise RuntimeError(  # pragma: no cover - import-time guard
        "a verification dimension must be an unresolved dimension: one that a requirement "
        "table could also name would be raised by the route and satisfied by a shipment"
    )


class ReturnMethodRequirement(ProjectionModel):
    """What one return method needs before the return is complete.

    `RMA` is mandatory on every row. It is the authorization the whole
    fulfilment path hangs off, and making it structural is what stops a
    configuration from declaring a method that completes on approval alone.
    """

    method: Reference
    requires: tuple[AwaitingDimension, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_requirement(self) -> ReturnMethodRequirement:
        if self.method.upper() == UNKNOWN_RETURN_METHOD.value:
            raise ValueError(
                "UNKNOWN is the absence of a return method, not a method: a row for it would "
                "let a case with no decided method complete"
            )
        if len(set(self.requires)) != len(self.requires):
            raise ValueError(f"return method {self.method} lists a requirement twice")
        outside = sorted(
            dimension.value for dimension in set(self.requires) - REQUIREMENT_DIMENSIONS
        )
        if outside:
            raise ValueError(
                f"return method {self.method} requires dimensions that are not fulfilment "
                f"requirements: {', '.join(outside)}"
            )
        if AwaitingDimension.RMA not in self.requires:
            raise ValueError(
                f"return method {self.method} must require RMA: a method that completes without "
                "an authorization completes without a return"
            )
        return self


class ReturnMethodRequirementTable(ProjectionModel):
    """The requirement table. Configuration-shaped data, not an `if` chain.

    A method with no row is **unmapped**, and an unmapped method leaves the
    completion profile unresolved. That is the safe direction: an operator who
    adds a return method through the Control Centre and has not yet said what it
    requires gets a case that waits, not one that completes because the code had
    never heard of the method and therefore required nothing of it.
    """

    rows: tuple[ReturnMethodRequirement, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_table(self) -> ReturnMethodRequirementTable:
        keys = [row.method.upper() for row in self.rows]
        if len(set(keys)) != len(keys):
            raise ValueError("a return method may appear in the requirement table only once")
        return self

    def requirements_for(self, method: str | None) -> tuple[AwaitingDimension, ...] | None:
        """What this method requires, or `None` when the table does not know it.

        `None` and `()` would otherwise be the same answer, and they are the
        opposite answer: "no row" must not read as "requires nothing".
        """
        if method is None:
            return None
        key = method.strip().upper()
        for row in self.rows:
            if row.method.upper() == key:
                return row.requires
        return None


#: The baseline table, covering the nine resolvable values of
#: `return_policy.normalized_return_methods`. `UNKNOWN` has no row by
#: construction.
#:
#: **Not a default, and it must never become one again.** This data now lives in
#: the return configuration as `return_policy.return_method_requirements`, and
#: `build_return_method_requirement_table(configuration)` is the one conversion
#: into the shape below. While this constant was the default of
#: `resolve_completion(..., requirements=)`, a caller that forgot the argument
#: still got an answer -- a plausible one, computed from nine rows nobody had
#: released -- so the operator's table could become decorative without a single
#: failure to say so. The parameter has no default now: omitting it is a type
#: error and a `TypeError`, which is the only version of "must pass this" that
#: a future edit cannot quietly undo.
#:
#: What it is still for is comparison. It is the code's reading of the method
#: catalogue, and the four rows below marked as derived are the ones an operator
#: should review against their own release; tests use it as a table that is
#: *known not to be* the released one, which is how they prove a handler
#: answered from configuration.
#:
#: The five rows the remediation plan states verbatim (`PREPAID_PARCEL`,
#: `BRANCH_LTL`, `OFFSITE_LTL`, `CUSTOMER_KEEP`, `NO_PHYSICAL_RETURN`) are
#: marked; the other four are this module's reading of the catalogue.
#:
#: **No caller reaches it by default any more.** `project_case` had one, and
#: `workflows/return_case_activities.py::_assess_completion` called
#: `project_case(state)` with no table -- so the decision "is this return
#: finished" that the workflow acted on was computed from these nine rows while
#: the API answered from the release. The two are identical row for row today,
#: which is why nothing had diverged; the first operator edit to
#: `return_policy.return_method_requirements` would have made the workflow and
#: the API disagree about the same case, with only the API right. The order of
#: the fix was forced: the activity swallows exceptions by design ("a completion
#: we cannot read is not a completion"), so removing the default first would
#: have turned the wiring mistake into a swallowed `TypeError` and a run loop
#: that ends every drained case as unassessable. So the activity was given the
#: table it already had the means to fetch -- `self._configuration` and
#: `build_return_method_requirement_table` -- and only then did the default go.
#: `project_case(state)` is now a `TypeError` at the call site and a type error
#: before that, which is the only version of "must pass this" a future edit
#: cannot quietly undo.
DEFAULT_RETURN_METHOD_REQUIREMENTS: Final[ReturnMethodRequirementTable] = (
    ReturnMethodRequirementTable(
        rows=(
            # --- Stated in the remediation plan.
            ReturnMethodRequirement(
                method=NormalizedReturnMethod.PREPAID_PARCEL,
                requires=(
                    AwaitingDimension.RMA,
                    AwaitingDimension.LABEL,
                    AwaitingDimension.TRACKING,
                ),
            ),
            ReturnMethodRequirement(
                method=NormalizedReturnMethod.BRANCH_LTL,
                requires=(
                    AwaitingDimension.RMA,
                    AwaitingDimension.BOL,
                    AwaitingDimension.PICKUP,
                ),
            ),
            ReturnMethodRequirement(
                method=NormalizedReturnMethod.OFFSITE_LTL,
                requires=(
                    AwaitingDimension.RMA,
                    AwaitingDimension.BOL,
                    AwaitingDimension.PICKUP,
                    AwaitingDimension.RETURN_LOCATION,
                ),
            ),
            ReturnMethodRequirement(
                method=NormalizedReturnMethod.CUSTOMER_KEEP,
                requires=(AwaitingDimension.RMA,),
            ),
            ReturnMethodRequirement(
                method=NormalizedReturnMethod.NO_PHYSICAL_RETURN,
                requires=(AwaitingDimension.RMA,),
            ),
            # --- Derived from the catalogue, and the rows to review first.
            #     A parcel tendered at a branch: the branch is the origin, so
            #     there is no return location to establish, but there is still a
            #     label to print and a number to follow.
            ReturnMethodRequirement(
                method=NormalizedReturnMethod.BRANCH_UPS,
                requires=(
                    AwaitingDimension.RMA,
                    AwaitingDimension.LABEL,
                    AwaitingDimension.TRACKING,
                ),
            ),
            #     A parcel from a customer site. `BRANCH_UPS` plus the location,
            #     for the same reason `OFFSITE_LTL` is `BRANCH_LTL` plus one.
            ReturnMethodRequirement(
                method=NormalizedReturnMethod.OFFSITE_PARCEL,
                requires=(
                    AwaitingDimension.RMA,
                    AwaitingDimension.LABEL,
                    AwaitingDimension.TRACKING,
                    AwaitingDimension.RETURN_LOCATION,
                ),
            ),
            #     Straight back to the vendor. Somebody has to say where; the
            #     carrier paperwork is the vendor's, not ours.
            ReturnMethodRequirement(
                method=NormalizedReturnMethod.DIRECT_VENDOR,
                requires=(
                    AwaitingDimension.RMA,
                    AwaitingDimension.RETURN_LOCATION,
                ),
            ),
            #     Scrapped where it stands. Nothing ships, so nothing but the
            #     authorization can be required -- the same shape as
            #     `NO_PHYSICAL_RETURN`, reached for a different reason.
            ReturnMethodRequirement(
                method=NormalizedReturnMethod.FIELD_SCRAP,
                requires=(AwaitingDimension.RMA,),
            ),
        )
    )
)


def _rma_satisfied(case: CaseProjectionState, record: ReturnRecordProjection) -> bool:
    del case
    return record.returnReference is not None


def _every_package_papered(
    record: ReturnRecordProjection, artifact_type: ReturnArtifactType
) -> bool:
    """Whether every live package on this RMA carries a live document of one type.

    Artifacts live on the record now, so the attribution this reads is
    `ReturnArtifactProjection.shipmentId` rather than which shipment the
    document happened to be nested under. That is the same statement it was
    before -- the assembler already stamped the package's id on the artifact --
    but it is now the *only* statement, so a document cannot satisfy a package
    it does not name.

    And still no package, no satisfaction. A record with a label and no
    shipment has its label visible on the projection and its `LABEL`
    requirement outstanding, because the requirement is that every package is
    papered and there is no package to paper.
    """
    shipments = record.active_shipments()
    return bool(shipments) and all(
        record.active_artifacts_for_shipment(artifact_type, shipment.shipmentId)
        for shipment in shipments
    )


def _label_satisfied(case: CaseProjectionState, record: ReturnRecordProjection) -> bool:
    del case
    return _every_package_papered(record, ReturnArtifactType.SHIPPING_LABEL)


def _bol_satisfied(case: CaseProjectionState, record: ReturnRecordProjection) -> bool:
    del case
    return _every_package_papered(record, ReturnArtifactType.BILL_OF_LADING)


def _tracking_satisfied(case: CaseProjectionState, record: ReturnRecordProjection) -> bool:
    del case
    shipments = record.active_shipments()
    return bool(shipments) and all(shipment.trackingNumber is not None for shipment in shipments)


def _pickup_satisfied(case: CaseProjectionState, record: ReturnRecordProjection) -> bool:
    del record
    return case.pickup is not None and case.pickup.is_scheduled


def _return_location_satisfied(case: CaseProjectionState, record: ReturnRecordProjection) -> bool:
    del case
    return record.returnLocation is not None


#: One predicate per requirement dimension.
#:
#: Every package, not any package. An RMA covering two parcels with one label
#: printed is not a satisfied `LABEL` requirement -- the second parcel has
#: nothing on it, and "any" is how the audit's `labels[0]` reading would come
#: back in a different shape.
_SATISFACTION: Final[
    Mapping[AwaitingDimension, Callable[[CaseProjectionState, ReturnRecordProjection], bool]]
] = {
    AwaitingDimension.RMA: _rma_satisfied,
    AwaitingDimension.LABEL: _label_satisfied,
    AwaitingDimension.BOL: _bol_satisfied,
    AwaitingDimension.TRACKING: _tracking_satisfied,
    AwaitingDimension.PICKUP: _pickup_satisfied,
    AwaitingDimension.RETURN_LOCATION: _return_location_satisfied,
}

if set(_SATISFACTION) != REQUIREMENT_DIMENSIONS:  # pragma: no cover - import-time guard
    raise RuntimeError(
        "every requirement dimension needs a satisfaction rule: a dimension a table can "
        "require and nothing can satisfy would hang every case that used it"
    )


@dataclass(frozen=True, slots=True)
class CompletionAssessment:
    """The four completion values, computed together because they are one statement."""

    completion_profile_resolved: bool
    awaiting: tuple[AwaitingDimension, ...]
    business_complete: bool
    is_terminal: bool


def is_terminal_status(status: ReturnCaseStatus) -> bool:
    """Membership of the terminal set, and nothing else.

    Derived from the persisted status alone. **The read path never calls
    Temporal** -- that would make the workflow host a synchronous dependency of
    every case read, and divergence between the two is Phase 10's concern rather
    than this function's.
    """
    return status in TERMINAL_RETURN_CASE_STATUSES


def effective_decision(case: CaseProjectionState) -> EligibilityDecision | None:
    """The decision that stands, which is the override's when there is one.

    The single reader of the policy decision in this module, so that "never
    `originalDecision`" is one line rather than a convention.
    """
    if case.policyEvaluation is None:
        return None
    return case.policyEvaluation.effectiveDecision


def resolve_method_requirements(
    record: ReturnRecordProjection,
    *,
    requirements: ReturnMethodRequirementTable,
) -> tuple[AwaitingDimension, ...] | None:
    """This RMA's requirement set, or `None` when its method is not resolved.

    Not resolved covers three cases that are one thing to a caller: no method
    recorded, the method is `UNKNOWN`, and the method has no row in the table.

    `requirements` has no default. See `DEFAULT_RETURN_METHOD_REQUIREMENTS`.
    """
    method = record.returnMethod
    if method is None:
        return None
    if method.strip().upper() == UNKNOWN_RETURN_METHOD.value:
        return None
    return requirements.requirements_for(method)


def verification_dimension(case: CaseProjectionState) -> AwaitingDimension | None:
    """The verification this case's route hands to Support, or `None`.

    `None` for an unevaluated case and for a standard return, which are the two
    shapes that wait on a policy decision rather than on Support's reading of a
    claim.
    """
    evaluation = case.policyEvaluation
    if evaluation is None:
        return None
    return ROUTE_VERIFICATION_DIMENSIONS.get(evaluation.route)


def _verification_recorded(case: CaseProjectionState) -> bool:
    """Whether Support has answered the verification with an authorization.

    **This is what the platform actually records when Support verifies a
    warranty or a delivery claim, and it is the whole of it.** There is no
    verified flag, no verification outcome on the work item and no
    `claim_verified` fact anywhere in the platform: a route's only effect is the
    queue the Channel B thread opens on (`_ROUTE_QUEUES`), and the only thing
    Support can send back about a case is a `SupportResponseNotice` -- either
    `rejected`, which closes the case, or one or more RMAs, which
    `record_support_outcome` writes onto it. So an RMA on a warranty case *is*
    Support's verification: nothing else can put one there, and the queue it
    came back from is the one that verifies warranties.

    Read as `returnReference is not None` rather than "a record exists", the
    same predicate `_rma_satisfied` uses. `create_return_record` can mint a
    `DRAFT` record with no reference, and a placeholder is not an authorization.

    **The rejection half is no longer inferred.** `record_support_outcome` now
    writes a `support_outcome` fact -- `AUTHORIZED | REJECTED`, with Support's
    reason and the instant it was recorded -- and `project_case_status` reads it
    to send a refused case to `POLICY_REJECTED`. A refused claim therefore never
    reaches this function at all: `POLICY_REJECTED` is in
    `COMPLETION_FORBIDDING_STATUSES`, so `resolve_completion` short-circuits
    above and the case awaits nothing. That is what closed the other half of the
    dead end. Before it, a refusal projected as
    `COMPLETED_EXTERNAL_SETTLEMENT` -- terminal, and asserting a credit settled
    elsewhere -- while `awaiting` still named the verification it had answered.

    **This predicate is deliberately not rewritten to read that fact.** The
    positive reading above stands on its own and predates the writer, so cases
    answered before it exists are still read correctly; consulting both would
    give one question two authorities that can only agree.

    **What is still missing, named rather than assumed.** The fact records
    *what* Support answered and *when*, and its provenance names the component
    that recorded it -- not the person who decided. `SupportResponseNotice`
    carries no actor, so the verifier's identity lives only in
    `case_support_events.actorId`, on the far side of the signal, and nothing
    on this path may claim to know it. Carrying the actor through the notice is
    the remaining piece of this audit trail.
    """
    return any(record.returnReference is not None for record in case.records())


def policy_gate_suspended(case: CaseProjectionState) -> bool:
    """Whether the operator has switched the eligibility gate off for this case.

    Read from the fact log because that is the only place it exists. A suspended
    gate deliberately writes **no** evaluation -- no route, no decision, no
    reason codes -- since each of those would be an answer and the gate produced
    none, and `POLICY_APPROVED` in particular is the one thing a suspended gate
    must never manufacture. So `policyEvaluation` is `None` here, exactly as it
    is on a case whose evaluation has not run yet, and the fact is what tells
    the two apart.
    """
    for fact in case.facts or ():
        if fact.factName == POLICY_GATE_STATE_FACT:
            return str(fact.value) == POLICY_GATE_SUSPENDED
    return False


def route_authority_stands(case: CaseProjectionState) -> bool:
    """Whether whoever is answerable for this route has said yes.

    Policy on the standard return, Support on the two verification routes. One
    function because it is one question -- *may this case's requirement set be
    consulted at all* -- and because splitting it is how the profile came to
    resolve on a test that one of the two routes can never pass.

    **A suspended gate has nobody answerable, and that is an answer.** With
    `policy_evaluation.enabled = false` no decision will ever be recorded, so
    reading the missing approval as "not yet" left the case awaiting `POLICY`
    for the rest of its life: Support's console printed *Waiting on POLICY* two
    inches below *Policy Evaluation: Skipped by configuration*, and
    `businessComplete` was unreachable however fully the return was fulfilled.
    The operator's decision to switch the gate off is the authority here --
    which is not an approval, and is not recorded as one: nothing below turns
    into `APPROVE`, `POLICY_APPROVED` is still never set, and the skip and its
    stated reason are what a human is shown.
    """
    if verification_dimension(case) is not None:
        return _verification_recorded(case)
    if policy_gate_suspended(case):
        return True
    return effective_decision(case) is EligibilityDecision.APPROVE


def _ordered(dimensions: Iterable[AwaitingDimension]) -> tuple[AwaitingDimension, ...]:
    """Deduplicated and in declaration order.

    Stable output matters more than it looks: `awaiting` is compared across
    polls, and a set that iterated differently would read as a change on a case
    where nothing happened.
    """
    return tuple(sorted(set(dimensions), key=awaiting_dimension_rank))


def _unresolved_dimensions(
    case: CaseProjectionState,
    *,
    requirements: ReturnMethodRequirementTable,
) -> tuple[AwaitingDimension, ...]:
    """What is missing before a requirement set can even be looked up.

    An **unverified** routed case reports only its verification dimension.
    Adding `POLICY` to a warranty hand-off would be wrong twice over: the
    evaluator did decide, and what it decided was that this is Support's to
    verify rather than policy's to approve.

    Once Support has verified it the dimension is gone, and the case is back on
    the ordinary path -- if anything is still unresolved here it is the return
    method, exactly as for a standard return. The early return below is
    therefore conditional, and that condition is the whole of D3: raised
    unconditionally and cleared by nothing, it made `businessComplete`
    unreachable for both routes for the life of the case.

    `POLICY` still cannot appear on a verification route, because
    `route_authority_stands` reads Support rather than the decision for those --
    a verified warranty case with an unmapped method reports `RETURN_METHOD`
    and nothing else.
    """
    dimension = verification_dimension(case)
    if dimension is not None and not _verification_recorded(case):
        return (dimension,)

    dimensions: list[AwaitingDimension] = []
    if not route_authority_stands(case):
        dimensions.append(AwaitingDimension.POLICY)
    records = case.records()
    if not records or any(
        resolve_method_requirements(record, requirements=requirements) is None for record in records
    ):
        dimensions.append(AwaitingDimension.RETURN_METHOD)
    return _ordered(dimensions)


def _status_dimensions(status: ReturnCaseStatus) -> tuple[AwaitingDimension, ...]:
    """What the status itself makes the case wait for.

    Only recovery. It is non-terminal on purpose -- recovery can restart
    processing -- so it cannot be expressed by `isTerminal`, and a case that had
    already collected its RMA, label and tracking before diverging from its
    execution would otherwise satisfy every requirement and report itself done.
    """
    if status is ReturnCaseStatus.RECOVERY_REQUIRED:
        return (AwaitingDimension.RECOVERY,)
    return ()


def resolve_completion(
    case: CaseProjectionState,
    *,
    requirements: ReturnMethodRequirementTable,
) -> CompletionAssessment:
    """`completionProfileResolved`, `awaiting`, `businessComplete` and `isTerminal`.

    **`requirements` is required and has no fallback.** Which dimensions a
    method needs is an operator's decision, released in
    `return_policy.return_method_requirements` and converted by
    `build_return_method_requirement_table`. A default here would mean a caller
    that never reached the configuration still gets a completion answer, and
    "this return is finished" computed from the wrong table is not a
    recoverable error -- it closes cases. Refusing to be callable without one is
    the same stance `api/cases.py` takes when it answers 503 rather than
    projecting from a constant.

    A completion-forbidding status short-circuits the whole computation, and
    that is the **only** place the guard lives. `POLICY_REJECTED`, `CANCELLED`
    and `EXPIRED` are finished: they await nothing, because reporting `[LABEL]`
    on one of them would drive a pane asking Support for a label nobody will
    ever print, and they complete nothing, because a cancelled return that
    happens to have collected its RMA, label and tracking is still a cancelled
    return. A second copy of the rule further down would make this one
    untestable -- either alone would keep the suite green.
    """
    terminal = is_terminal_status(case.status)

    if case.status in COMPLETION_FORBIDDING_STATUSES:
        return CompletionAssessment(
            completion_profile_resolved=False,
            awaiting=(),
            business_complete=False,
            is_terminal=terminal,
        )

    per_record = tuple(
        (record, resolve_method_requirements(record, requirements=requirements))
        for record in case.records()
    )
    profile_resolved = (
        route_authority_stands(case)
        and bool(per_record)
        and all(required is not None for _, required in per_record)
    )

    if profile_resolved:
        outstanding: list[AwaitingDimension] = [
            dimension
            for record, required in per_record
            for dimension in (required or ())
            if not _SATISFACTION[dimension](case, record)
        ]
    else:
        outstanding = list(_unresolved_dimensions(case, requirements=requirements))

    awaiting = _ordered((*_status_dimensions(case.status), *outstanding))
    return CompletionAssessment(
        completion_profile_resolved=profile_resolved,
        awaiting=awaiting,
        business_complete=profile_resolved and not awaiting,
        is_terminal=terminal,
    )
