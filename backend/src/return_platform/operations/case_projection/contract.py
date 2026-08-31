"""The Copilot read contract: one case, everything the Copilot may believe about it.

A **read projection contract**, not a Mongo document. What assembles it is the
repository's problem; what it may say is this module's.

**Every block is nullable and absent rather than defaulted.** That single rule is
the whole audit. `artifacts: ()` means the platform looked and there are none;
`artifacts: None` means the platform has not computed them. A default of `()`
collapses those two into one value and the screen renders "no label" for a case
whose label nobody has asked about yet -- which is how a pane ends up lying
without anybody writing a lie. There is therefore no `default_factory` anywhere
below, and `test_case_projection_contract.py` asserts there never is.

The nesting is load-bearing too. `returnRecords[] -> shipments[]`: one RMA may
cover several packages, so a shipment is an element and not a set of flat fields
on the record.

**Artifacts belong to the return record, not to the shipment, and
`ShipmentProjection.labelArtifacts` is removed rather than retained as a derived
view.** A document can exist before any package does: an RMA issued with a
prepaid label and no tracking reference yet is a real shape in the dev database
(`RMA-OPS01-CD4364` carries `labelReference: LBL-OPS01` and
`trackingReference: null`), and a contract reachable only as
`shipments[] -> labelArtifacts[]` had nowhere to put that label but a package
nobody had tendered. Minting one would have meant inventing a shipment id and a
shipment status the platform does not have -- the same fabrication as the
audit's `TRK-98421049281`, in a new costume. So the contract was wrong, not the
data.

`ReturnArtifactProjection.shipmentId` carries the attribution instead: `None`
means "this RMA has this document, no package is known yet", and a value
attributes it to one package. Package-scoped artifacts are found by filtering on
that field -- `ReturnRecordProjection.active_artifacts_for_shipment` -- so
"label LBL-2 belongs to package SHP-2 of RMA-1" is still expressible and still
cannot be misread.

The derived view was **not** kept. A `labelArtifacts` that filtered the record's
collection would be a second place for a reader to look and a second place for a
writer to disagree with, and two homes for one fact is exactly how a label ends
up attributed to the wrong package -- which plan sect. 11 names as a required
test.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from return_platform.operations.case_projection.vocabulary import (
    AwaitingDimension,
    CopilotStage,
    ReturnArtifactType,
    ReturnCaseStatus,
    SettlementStatus,
    ShipmentStatus,
)
from return_platform.policy import (
    EligibilityDecision,
    FeeAmountSource,
    MonetaryAmount,
    PolicyCondition,
    PolicyReasonCode,
    PolicyRoute,
    PolicyRule,
)

__all__ = [
    "ApprovedItemProjection",
    "CaseFactProjection",
    "CaseProjection",
    "CaseProjectionState",
    "ConfirmedOrderProjection",
    "CustomerProjection",
    "PickupProjection",
    "PolicyEvaluationProjection",
    "PolicyOverrideProjection",
    "ProjectionModel",
    "Reference",
    "ReturnArtifactProjection",
    "ReturnRecordProjection",
    "SelectedItemProjection",
    "SettlementProjection",
    "ShipmentProjection",
    "SupportProjection",
    "WarehouseProjection",
]

#: Identifiers and short references. Blank is not a value: an empty
#: `returnReference` would satisfy "an RMA exists" and complete a case that has
#: none.
Reference = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]


class ProjectionModel(BaseModel):
    """Frozen and extra-forbidding.

    Frozen because the derived block is computed from the rest exactly once, on
    read; a mutable projection invites a handler to "just fix" a stage, and plan
    sect. 6.6 is explicit that no stage logic lives in API handlers.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


class CustomerProjection(ProjectionModel):
    """Who the return is for, as the platform resolved them."""

    customerReference: Reference | None = None
    accountReference: Reference | None = None
    displayName: str | None = Field(default=None, max_length=512)
    branchReference: Reference | None = None


class ConfirmedOrderProjection(ProjectionModel):
    """The order the associate confirmed, and the confirmation that created the case.

    Present only after `CONFIRM_ORDER`. Its absence is what distinguishes
    discovery from confirmation, so a candidate the associate is still looking at
    must never be projected here.
    """

    orderReference: Reference
    orderSource: Reference | None = None
    sourceWebOrderNumber: Reference | None = None
    trilogieOrderNumber: Reference | None = None
    #: tenant | conversation | order | line-set. The idempotency boundary a
    #: retried confirmation turn cannot fork the case across.
    confirmationKey: Reference | None = None
    candidateSetId: Reference | None = None
    candidateId: Reference | None = None
    confirmedAt: AwareDatetime | None = None


class SelectedItemProjection(ProjectionModel):
    """One order line the associate named, before any RMA covers it."""

    returnItemId: Reference
    orderLineReference: Reference
    productReference: Reference | None = None
    quantity: int | None = Field(default=None, ge=1)
    reason: Reference | None = None
    condition: Reference | None = None
    packageReference: Reference | None = None


class CaseFactProjection(ProjectionModel):
    """One fact at its latest value, with the provenance that decides trust.

    Serves the existing `latest_case_facts` projection. The backend already
    computes it and the console duplicates it client-side in `latestFacts`;
    serving it here is what deletes the duplicate.

    `value` is deliberately untyped -- the fact log is heterogeneous by design --
    but every provenance field beside it is not, because provenance is what makes
    a fact admissible to policy evaluation (plan sect. 7.3).

    **`actorId` is not `agentId`.** The agent is what produced the observation;
    the actor is the principal on whose authority a *command* caused it
    (contracts.md sect. 4, server-stamped). Without it a UI asking "who
    authorised this" got "nobody" for every case-level command fact, which is
    the one provenance question an audit actually asks. `None` is the honest
    answer for an observation, which has no actor at all.

    Still omitted, deliberately: `record_scope` and `identity_version`, because
    this projection is keyed by fact *name* over the unscoped
    `latest_case_facts` -- per-record facts collapse before they reach here, so a
    `recordScope` field would name whichever scope won the collapse and imply a
    per-record view that does not exist. It belongs with the
    `latest_case_facts_scoped` convergence (contracts.md sect. 10's registered
    follow-up), not ahead of it. `turnId` and `correlationId` are request-tracing
    links rather than provenance, and are not what the audit question needs.

    **No field here carries a default, and that is the whole point.** In
    Pydantic *any* default -- `= None` included -- emits the field as
    non-required, so eleven always-serialised fields were published as nine
    optional ones and a client had to type them optional and write defensive
    code for an absence that cannot occur. Declaring `X | None` with no default
    produces required-and-nullable, which is what the writer actually
    guarantees: `case_repository.append_scoped_case_fact` writes every key,
    `None` included, and `assembly.project_facts` binds all eleven on every
    construction. *Absent* and *null* therefore stay distinguishable, which is
    the difference between provenance and a gap. Adding a default to any field
    below silently re-opens that hole, and neither `tsc` nor any type-level
    assertion can see it -- the console's `Served<T>` alias strips optionality
    regardless of the document. The guard is a *data* assertion over the
    emitted `required` array, `frontend/scripts/check-served-fields.js`, run by
    `npm run contracts:check` and gated by CI's `contract drift` job.
    """

    factId: Reference
    factName: Reference
    value: str | int | float | bool | None
    agentId: Reference | None
    actorId: Reference | None
    channel: Reference | None
    sourceSystem: Reference | None
    acquisitionMethod: Reference | None
    observedAt: AwareDatetime | None
    recordedAt: AwareDatetime | None
    supersedesFactId: Reference | None


class PolicyOverrideProjection(ProjectionModel):
    """A supervisor's departure from the evaluator's answer. Append-only.

    Every field here is **server-derived** except the decision and the reason:
    `actor` comes from the authenticated principal and `overriddenAt` from the
    server clock, for the same reason `order_agent.py` derives `correlation_id`.
    A client-supplied audit field is not audit.
    """

    overrideDecision: EligibilityDecision
    reasonCode: Reference
    reason: str | None = Field(default=None, max_length=2_000)
    actor: Reference
    overriddenAt: AwareDatetime


class PolicyEvaluationProjection(ProjectionModel):
    """What the deterministic evaluator decided, and what stands after any override.

    `effectiveDecision` is what completion reads and it is enforced here rather
    than trusted: with an override it *is* the override's decision, without one
    it *is* the original. Two fields that can disagree are two fields that will,
    and the disagreement would be invisible -- an overridden `REVIEW_REQUIRED ->
    APPROVE` that never resolved the completion profile looks exactly like a
    case still waiting for a supervisor.

    A non-standard route carries no decision, matching `PolicyOutcome`'s own
    invariant, and carries no override either: warranty and delivery claims are
    verified by Support, and a policy override on one of them would be a
    supervisor approving a claim the verification exists to test.

    **The restocking fee is carried as a rate and never as a currency figure.**
    `rateBasisPoints` and `rateSource` mirror `FeeDetermination.rate_basis_points`
    and `.rate_source` and stop exactly where those do. The evaluator is pure and
    holds no line prices -- `quantity` is carried and read by no rule -- so the
    rate is the policy answer and the money is arithmetic belonging where prices
    live. There is deliberately no `restockingFeeAmount` here for a downstream
    reader to fill from something plausible, and nothing in this contract may
    multiply the rate by anything: the audit's `18.75` is what a projected
    currency figure looks like once nobody can say which price produced it.

    Applicability is **not** repeated as a field of its own. It already travels
    as `RESTOCKING_FEE_APPLIES` / `RESTOCKING_FEE_WAIVED` in `conditions`, which
    is where the evaluator puts it, and a second home for one fact is how the two
    come to disagree.
    """

    route: PolicyRoute
    originalDecision: EligibilityDecision | None = None
    effectiveDecision: EligibilityDecision | None = None
    override: PolicyOverrideProjection | None = None
    reasonCodes: tuple[PolicyReasonCode, ...] | None = None
    conditions: tuple[PolicyCondition, ...] | None = None
    appliedRules: tuple[PolicyRule, ...] | None = None
    policyId: Reference | None = None
    policyVersion: Reference | None = None
    evaluatedAt: AwareDatetime | None = None

    #: The restocking rate the evaluation determined, in basis points
    #: (1500 = 15.00%). Bounded exactly as `FeeDetermination.rate_basis_points`
    #: is, so a fact holding 150_000 is refused here rather than rendered as a
    #: 1500% fee.
    rateBasisPoints: int | None = Field(default=None, ge=0, le=10_000)
    #: The authority that set the rate. Required beside it, for the same reason
    #: `FeeDetermination.amount_source` is required beside an amount: a
    #: percentage with no named authority is indistinguishable from an invented
    #: one. `SELLER_CONFIGURATION` is what keeps a seller's own rate from
    #: presenting itself as published Ferguson policy -- Ferguson publishes no
    #: universal restocking percentage, and `FeeAmountSource` has no
    #: `POLICY_DEFAULT` member precisely so nothing can claim it does.
    rateSource: FeeAmountSource | None = None

    @model_validator(mode="after")
    def validate_evaluation(self) -> PolicyEvaluationProjection:
        if self.route is PolicyRoute.STANDARD_RETURN:
            if self.originalDecision is None:
                raise ValueError(
                    "a standard return carries one of APPROVE, REJECT or REVIEW_REQUIRED"
                )
        else:
            if self.originalDecision is not None:
                raise ValueError(
                    f"route {self.route.value} is a verification hand-off, not a decision: "
                    "Support verifies it, so the decision must be null"
                )
            if self.override is not None:
                raise ValueError(
                    f"route {self.route.value} is verified by Support, not overridden: "
                    "a policy override here would approve the claim the verification tests"
                )

        expected = (
            self.override.overrideDecision if self.override is not None else self.originalDecision
        )
        if self.effectiveDecision != expected:
            raise ValueError(
                "effectiveDecision must be the override's decision when one exists and the "
                "original decision otherwise"
            )
        if self.rateBasisPoints is not None and self.rateSource is None:
            raise ValueError(
                "a restocking rate must name the authority that set it: a percentage with "
                "no source is indistinguishable from an invented one"
            )
        return self


class SupportProjection(ProjectionModel):
    """The open work item, if Support has one.

    Distinguished by `queue`, never by a type field. `SupportWorkItemView` has no
    `type` and route context travels as a configured queue
    (`RETURNS_SUPPORT | WARRANTY_SUPPORT | DELIVERY_CLAIM_SUPPORT`), so adding one
    here would create the field plan sect. 7.6 says not to add.
    """

    workItemId: Reference
    threadId: Reference | None = None
    queue: Reference | None = None
    status: Reference | None = None
    subject: str | None = Field(default=None, max_length=1_000)
    priority: Reference | None = None
    assignedTo: Reference | None = None
    slaDueAt: AwareDatetime | None = None
    openedAt: AwareDatetime | None = None
    resolvedAt: AwareDatetime | None = None


class ReturnArtifactProjection(ProjectionModel):
    """One document belonging to one RMA, and to one of its packages once there is one.

    It lives on `ReturnRecordProjection.artifacts`. `shipmentId` is the
    attribution and it is genuinely optional: `None` says the RMA has this
    document and no package is known yet -- a printed label before anything was
    tendered -- and a value says which package it goes on. Authorization is
    record-scoped for the same reason (plan sect. 11: the artifact must belong
    to *that return*, never validated on artifact id alone).

    Served through an opaque authenticated endpoint, never a storage URL -- so
    there is deliberately no `url` field to put one in.

    `supersededBy` rather than deletion: a replaced label stays auditable, and
    the active artifact is the one with `active` true and `supersededBy` null.
    **Never `labels[0]`.**
    """

    artifactId: Reference
    artifactType: ReturnArtifactType
    shipmentId: Reference | None = None
    fileName: str | None = Field(default=None, max_length=512)
    mediaType: Reference | None = None
    version: int | None = Field(default=None, ge=1)
    active: bool | None = None
    supersededBy: Reference | None = None
    expiresAt: AwareDatetime | None = None
    createdAt: AwareDatetime | None = None

    @property
    def is_active(self) -> bool:
        """Whether this is the artifact the single label action resolves to.

        Both clauses, and `active` unset is not active: an artifact the platform
        has not marked live is not evidence that a label exists.
        """
        return self.active is True and self.supersededBy is None


class ShipmentProjection(ProjectionModel):
    """One package. Explicit fields only.

    `carrier` is a carrier, not `orderSource`; `serviceLevel` is a carrier
    service, not a return method; `estimatedDeliveryAt` is an estimate somebody
    gave for this package, not `shippingPathExpectation` reinterpreted. All
    three substitutions were live defects and none can be repeated here, because
    no source field for any of them exists on this model.

    **Two of the three are `None` on every assembled case today, and honestly
    so.** `carrier` gained a producer: Support states it on the RMA and it
    travels `ReturnOutcomeRecord` -> `SupportReturnRecord` ->
    `RETURN_RECORD_MERGED_FIELDS` -> `ReturnRecordView` -> here. Nothing
    case-keyed produces a service level or a return-leg delivery estimate --
    the only service level is `PickupRequest.serviceLevel`, which is the service
    a freight *collection* was booked at rather than the service a parcel moves
    under, and no estimate is computed or received at all;
    `assembly.project_shipments` names the missing producer for each one. They stay declared for the reason the eight producerless
    `WarehouseProjection` fields do: the contract is where a producer lands, and
    a field the model cannot express is a field a writer cannot deliver.

    **No `labelArtifacts`.** Documents hang off the return record and name their
    package through `ReturnArtifactProjection.shipmentId`; this package's are
    `record.active_artifacts_for_shipment(type, shipment.shipmentId)`. A label
    that exists before any package must still be expressible, and one home for
    the fact is what stops it being attributed to the wrong package.
    """

    shipmentId: Reference
    shipmentStatus: ShipmentStatus | None = None
    carrier: Reference | None = None
    serviceLevel: Reference | None = None
    trackingNumber: Reference | None = None
    estimatedDeliveryAt: AwareDatetime | None = None
    createdAt: AwareDatetime | None = None
    updatedAt: AwareDatetime | None = None


class ApprovedItemProjection(ProjectionModel):
    """One line an RMA authorized, at the quantity it authorized."""

    returnItemId: Reference
    orderLineReference: Reference | None = None
    productReference: Reference | None = None
    quantityApproved: int | None = Field(default=None, ge=0)
    disposition: Reference | None = None
    itemStatus: Reference | None = None


class ReturnRecordProjection(ProjectionModel):
    """One RMA and everything belonging to it rather than to the case.

    `returnMethod` is a plain string, not the `NormalizedReturnMethod` enum,
    and that is deliberate. The catalogue is operator-owned in
    `return_policy.normalized_return_methods`; a closed enum here would refuse
    to *render* a case whose method an operator added through the Control
    Centre, which is the same failure the removed `pattern=` on
    `shippingPathExpectation` caused on the write path. A method the requirement
    table does not know is treated as unresolved, not as invalid.
    """

    returnRecordId: Reference
    returnReference: Reference | None = None
    status: Reference | None = None
    returnMethod: Reference | None = None
    returnLocation: Reference | None = None
    approvedItems: tuple[ApprovedItemProjection, ...] | None = None
    shipments: tuple[ShipmentProjection, ...] | None = None
    #: Every document this RMA has, whether or not a package exists to carry it.
    #: The single home; see the module docstring for why there is not a second.
    artifacts: tuple[ReturnArtifactProjection, ...] | None = None

    def active_shipments(self) -> tuple[ShipmentProjection, ...]:
        """Packages that still count. A cancelled one satisfies nothing."""
        if self.shipments is None:
            return ()
        return tuple(
            shipment
            for shipment in self.shipments
            if shipment.shipmentStatus is not ShipmentStatus.CANCELLED
        )

    def active_artifacts(
        self, artifact_type: ReturnArtifactType
    ) -> tuple[ReturnArtifactProjection, ...]:
        """The live artifacts of one type on this RMA, whatever they are attributed to.

        Includes the ones with no `shipmentId`. This is what makes a label
        issued before any package expressible at all, and it is deliberately
        *not* what the completion rules read -- see
        `active_artifacts_for_shipment`.
        """
        if self.artifacts is None:
            return ()
        return tuple(
            artifact
            for artifact in self.artifacts
            if artifact.artifactType is artifact_type and artifact.is_active
        )

    def active_artifacts_for_shipment(
        self, artifact_type: ReturnArtifactType, shipment_id: str
    ) -> tuple[ReturnArtifactProjection, ...]:
        """The live artifacts of one type carried by one named package.

        Attribution is the `shipmentId` field and nothing else. An artifact with
        no `shipmentId` is not on *this* package -- it is on the RMA and no
        package yet -- so it does not paper a parcel that has nothing on it,
        which is the cross-attribution that having two homes for one document
        used to make possible.
        """
        return tuple(
            artifact
            for artifact in self.active_artifacts(artifact_type)
            if artifact.shipmentId == shipment_id
        )


class PickupProjection(ProjectionModel):
    """A collection somebody scheduled. Freight and offsite returns wait on this."""

    pickupReference: Reference | None = None
    scheduledAt: AwareDatetime | None = None
    windowStartsAt: AwareDatetime | None = None
    windowEndsAt: AwareDatetime | None = None
    location: str | None = Field(default=None, max_length=512)
    contactName: str | None = Field(default=None, max_length=256)
    contactPhone: str | None = Field(default=None, max_length=64)
    status: Reference | None = None

    @property
    def is_scheduled(self) -> bool:
        """Whether a collection has actually been booked.

        A pickup block with a contact name and no time is a request, not a
        booking, and a return method that requires `PICKUP` is waiting on the
        booking.
        """
        return self.scheduledAt is not None


class WarehouseProjection(ProjectionModel):
    """Receiving, inspection and disposition. Only fields current backend flows produce.

    Bay data comes from the facts `ReturnCaseWorkflow` already writes -- there is
    no separate bay service to read, and inventing one here would put a field on
    the contract that nothing can ever fill. `assembly.project_warehouse` names
    the producing fact for every field it fills and names the absence for every
    field it does not; the seven receipt fields below currently have **no
    case-keyed producer anywhere in the platform** and are therefore always
    `None` on an assembled case. They stay declared because the contract is the
    place a producer will land, and a field the model cannot express is a field
    a writer cannot deliver.

    **A case with no bay is a normal state.** Bay placement is advisory and
    best-effort by declared policy (`CaseBayPlacement`), and it runs before the
    goods exist, so "no bay" is the ordinary answer for most of a case's life.
    `bayReason` is what makes that readable: it carries the state the placement
    engine named -- `RECOMMENDED`, `NO_ELIGIBLE_BAY`, `PRE_ARRIVAL_NOT_ALLOWED`,
    `WAREHOUSE_ABSENT_NO_WAREHOUSE_REFERENCE`, `BAY_PLACEMENT_NOT_CONFIGURED`
    and their siblings -- so a reader has an explanation rather than a blank
    where a bay would be. **It is not an error field**, and a `bayReason` with
    no `bayId` beside it is the platform working, not failing.

    `bayReason` is deliberately a field of its own rather than folded into
    `warehouseStatus`. `has_receipt` reads `warehouseStatus`, so writing a
    placement reason there would make every case that merely *asked* for a bay
    report goods booked in and jump the Copilot to `WAREHOUSE_RECEIVING` -- a
    receiving pane lit by a recommendation, which is the fabrication this block
    exists to prevent.
    """

    facilityId: Reference | None = None
    facilityName: str | None = Field(default=None, max_length=512)
    bayId: Reference | None = None
    #: Why placement answered as it did, whether or not it found a bay. An
    #: explanation, never an error.
    bayReason: Reference | None = None
    receivedAt: AwareDatetime | None = None
    receivedQuantity: int | None = Field(default=None, ge=0)
    inspectionStatus: Reference | None = None
    condition: Reference | None = None
    disposition: Reference | None = None
    qaStatus: Reference | None = None
    warehouseStatus: Reference | None = None

    @property
    def has_receipt(self) -> bool:
        """Whether the warehouse has recorded anything about these goods.

        A block that exists with every field null is not a receipt. The
        projection is supposed to omit the block entirely in that case, and this
        makes the stage rule correct even when it does not.

        **Bay placement is not a receipt**, so `facilityId`, `bayId` and
        `bayReason` are all absent from the test. A recommended bay is where the
        goods are *to be* put; a receipt is somebody saying they arrived. The
        four fields below are the ones that can only be written by an actual
        receiving event, which is exactly why the stage rule reads them and not
        the placement fields.
        """
        return (
            self.receivedAt is not None
            or self.warehouseStatus is not None
            or self.receivedQuantity is not None
            or self.inspectionStatus is not None
        )


class SettlementProjection(ProjectionModel):
    """The credit. `NOT_INTEGRATED`, because there is no settlement producer.

    Not `NOT_STARTED`. That would say a producer exists and has not run yet,
    which is a false statement about the system: nothing in this platform issues
    a credit memo, computes a settled amount or records a settlement date. The
    audit found a pane displaying `249.99`, `18.75` and `CM-2026-88192` with no
    backend contract behind any of it; the honest contract for those three fields
    is this block saying, positively, that the integration does not exist.

    `status` is required and always populated, so this block is **not** the
    "every field null" shape the module docstring forbids -- `NOT_INTEGRATED` is
    an answer, not an absence. The three fields beside it stay `None` under the
    ordinary rule, and nothing may ever fill them until a producer does.

    Three consequences, and each is asserted in the projection tests:

    * Settlement never enters `awaiting` and never blocks `businessComplete`.
      `AwaitingDimension` has no settlement member, so it cannot.
    * `businessComplete` means **completion within configured platform
      responsibility** -- every requirement the return method and policy place
      on *this* platform is satisfied. Settlement is outside that boundary, so a
      return can be business-complete with its credit unissued. That boundary is
      the load-bearing assumption of the whole completion rule.
    * A case whose settlement is `NOT_INTEGRATED` reaches
      `COMPLETED_EXTERNAL_SETTLEMENT`, never plain `COMPLETED`, so a
      completed-return count can never be read as a settled-return count.
    """

    status: SettlementStatus
    creditMemoReference: Reference | None = None
    settledAmount: MonetaryAmount | None = None
    settledAt: AwareDatetime | None = None


class CaseProjectionState(ProjectionModel):
    """Everything the platform has computed about a case, before deriving anything.

    The input to `derive_copilot_stage` and `resolve_completion`, and the half of
    `CaseProjection` a repository fills in. Split from the derived block so that
    the derivation has a type that cannot accidentally read its own output --
    a stage rule that consulted `stage` would be a lookup table wearing a
    function's clothes.
    """

    caseId: Reference
    tenantId: Reference
    principalId: Reference
    conversationId: Reference | None = None
    status: ReturnCaseStatus
    revision: int = Field(ge=0)
    updatedAt: AwareDatetime

    customer: CustomerProjection | None = None
    confirmedOrder: ConfirmedOrderProjection | None = None
    selectedItems: tuple[SelectedItemProjection, ...] | None = None
    facts: tuple[CaseFactProjection, ...] | None = None
    policyEvaluation: PolicyEvaluationProjection | None = None
    support: SupportProjection | None = None
    returnRecords: tuple[ReturnRecordProjection, ...] | None = None
    pickup: PickupProjection | None = None
    warehouse: WarehouseProjection | None = None
    settlement: SettlementProjection | None = None

    def records(self) -> tuple[ReturnRecordProjection, ...]:
        """The RMAs, or none. `None` and `()` mean different things to a reader
        and the same thing to a loop, and only the loop is here."""
        return self.returnRecords if self.returnRecords is not None else ()

    def all_shipments(self) -> tuple[ShipmentProjection, ...]:
        """Every non-cancelled package across every RMA on the case."""
        return tuple(
            shipment for record in self.records() for shipment in record.active_shipments()
        )


class CaseProjection(CaseProjectionState):
    """The wire contract: the state above plus the four derived values.

    Build it with `project_case`. Constructing one directly is legal -- a test
    fixture and the mock server both need to -- but nothing in the platform
    should, because the four fields below are a function of the ones above and a
    hand-written pair that disagree is exactly the class of defect this contract
    was written to end.
    """

    stage: CopilotStage
    awaiting: tuple[AwaitingDimension, ...]
    businessComplete: bool
    isTerminal: bool
