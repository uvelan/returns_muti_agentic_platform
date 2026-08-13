"""Bay placement keyed by CASE rather than by session (BAY-01).

`WarehousePlacementService` was a real, graph-oriented, deterministic bay
engine that the case flow never called: its every reference was
`api/warehouse_placement.py`, and `request_bay_assignment` -- the one activity
the case workflow runs for a bay -- wrote a single `bay_assignment_requested`
fact and returned. It queried no graph, ranked no bay and resolved no location.
The workflow then waited `bay_wait_seconds` for a `bay_result` signal whose
only sender in the repository was a test.

So this is a re-keying, not a second engine. The observation port, the
candidate pipeline (`observe_eligible_bays`) and the ranking agent
(`BayAssignmentAgent`, whose confidence is already computed from the winner's
margin over the runner-up) are the ones the session path uses. What differs is
where the *inputs* come from, and that is the whole of the difference between a
session and a case:

* a session carries `processingWarehouseReference`, `productType`,
  `approvedReturnMethod` and a handling unit with a physical status;
* a case, at the moment Bay runs, carries a confirmed order and its fact log,
  and no handling unit exists yet because nothing has been received.

Inputs are therefore read from the case's fact projection, falling back to the
confirmed order's own shipping warehouse in the graph. Neither is guessed: an
unresolvable warehouse yields `ABSENT / NO_WAREHOUSE_REFERENCE`, which is a
real answer the observation model already distinguishes from "this warehouse
has no eligible bay".

**One atomic result.** `CaseBayRecommendation` carries warehouse, bay, return
location, computed confidence, reason, explanation and the evidence reference
together. A partial result is not a result -- a caller that had to join a bay
id to a confidence from somewhere else would be the shape BAY-01 is closing.

**Best-effort, and that stays.** Nothing here raises for a missing warehouse,
an unreadable graph or an ineligible estate; each is a recommendation with a
reason. The only exception that escapes is one the caller's activity is
expected to turn into `REQUEST_FAILED`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

from return_platform.agents.contracts import (
    BayAssessment,
    BayAssessmentRequest,
    BayCandidateInput,
)
from return_platform.agents.registry import AgentRegistry
from return_platform.configuration.return_configuration import ReturnPlatformConfiguration
from return_platform.operations.warehouse.observations import (
    BayEvidence,
    WarehouseBayObservationPort,
    WarehouseObservation,
)
from return_platform.operations.warehouse.service import normalize_method, observe_eligible_bays

__all__ = [
    "CaseBayPlacement",
    "CaseBayRecommendation",
    "CasePlacementRepositoryPort",
    "OrderPlacementObservationPort",
    "return_location_of",
]

logger = logging.getLogger("return_platform.operations.warehouse.case_placement")

#: Fact names the case's placement inputs are read from. Every one mirrors the
#: `ReturnSessionView` field the session path reads, so an operator or an agent
#: recording one on a case moves the same lever it moves on a session.
FACT_WAREHOUSE = "processing_warehouse_reference"
FACT_PRODUCT_TYPE = "product_type"
FACT_RETURN_METHOD = "return_method"
FACT_REQUIRED_CAPACITY = "required_capacity"
FACT_OVERSIZED = "oversized"
FACT_HAZARDOUS = "hazardous"
FACT_PHYSICAL_STATUS = "physical_status"

#: What a case's physical status is before anything has been received. Named
#: rather than blank so `bay.eligible_statuses` can include or exclude it as
#: configuration, which is what decides whether a pre-arrival recommendation is
#: allowed at all (`bay.allow_prearrival_reservation`).
PRE_ARRIVAL_STATUS = "AWAITING_RECEIPT"

#: One handling unit's worth, when the case has not said otherwise. The session
#: path takes this from the caller; a case at Bay time has no handling unit to
#: measure, and asking for zero capacity would make every bay eligible.
DEFAULT_REQUIRED_CAPACITY = 1


class CasePlacementRepositoryPort(Protocol):
    """The slice of `OperationalRepository` this reads and writes."""

    async def get_case(self, case_id: str) -> dict[str, Any] | None: ...

    async def latest_case_facts(self, case_id: str) -> dict[str, dict[str, Any]]: ...

    async def persist_agent_decision(
        self,
        *,
        aggregate_id: str,
        session_id: str,
        decision: dict[str, Any],
        decision_key: str,
        actor_id: str,
    ) -> Any: ...


class OrderPlacementObservationPort(Protocol):
    """Where the confirmed order says it shipped from.

    Structural, so `operations` neither imports `dynamic_knowledge` nor learns
    what a generation lease is -- the adapter is
    `dynamic_knowledge/integration/order_placement_observations.py`. Returns
    `None` when the order carries no shipping warehouse or could not be read;
    the distinction is logged there and does not change what placement does,
    because both mean the same thing here: no warehouse to observe.
    """

    async def observe_shipping_warehouse(self, order_reference: str) -> str | None: ...


def _flag_fact(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return bool(value)


def _int_fact(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def return_location_of(
    candidate: dict[str, Any] | None, observation: WarehouseObservation | None
) -> str | None:
    """Where the goods are to be sent, as one addressable reference.

    Composed from the recommendation rather than stored beside it: the return
    location IS the chosen bay in the chosen warehouse, and holding it as an
    independent field is how a case ends up with a bay in one warehouse and a
    location in another. `SupportReturnRecord.return_location` stays the
    authority for what Support actually instructed -- this is the platform's
    recommendation, and the two are allowed to differ.

    `None` when no bay was recommended. A location without a bay would be a
    partial result, and a partial result is not a result.
    """
    if candidate is None:
        return None
    warehouse = str(candidate.get("warehouseId") or "") or (
        observation.warehouse_reference if observation is not None else None
    )
    bay = str(candidate.get("bayId") or "")
    if not bay:
        return None
    return f"{warehouse}/{bay}" if warehouse else bay


@dataclass(frozen=True, slots=True)
class CaseBayRecommendation:
    """One coherent placement answer for one case (contract C2).

    Every field is populated from the same reading. `confidence_millionths` is
    `BayAssignmentAgent`'s computed margin over the runner-up -- never a
    constant -- and is `None` only when no recommendation was produced at all,
    which is different from a recommendation made with low confidence.
    """

    case_id: str
    warehouse_reference: str | None
    bay_reference: str | None
    return_location: str | None
    confidence_millionths: int | None
    reason: str
    explanation: str
    evidence_reference: str | None
    graph_generation_id: str | None
    eligible_bay_ids: tuple[str, ...] = ()

    @property
    def recommended(self) -> bool:
        return self.bay_reference is not None


class CaseBayPlacement:
    """Recommend a bay for a confirmed case. Advisory, and never raising for a state."""

    def __init__(
        self,
        *,
        repository: CasePlacementRepositoryPort,
        configuration: ReturnPlatformConfiguration,
        observations: WarehouseBayObservationPort | None = None,
        order_observations: OrderPlacementObservationPort | None = None,
    ) -> None:
        self._repository = repository
        self._configuration = configuration
        self._observations = observations
        self._order_observations = order_observations
        self._agent = AgentRegistry.build(configuration).bay_assignment

    async def recommend(self, case_id: str) -> CaseBayRecommendation:
        case = await self._repository.get_case(case_id)
        if case is None:
            raise KeyError(case_id)
        facts = {
            name: fact.get("value")
            for name, fact in (await self._repository.latest_case_facts(case_id)).items()
        }

        physical_status = _text(facts.get(FACT_PHYSICAL_STATUS))
        if physical_status is None and not self._configuration.bay.allow_prearrival_reservation:
            # The configured answer, given before anything is read. Bay runs
            # concurrently with the support conversation, so a case reaches
            # here before any goods exist to receive; `allow_prearrival_
            # reservation` is the flag that decides whether that may produce a
            # recommendation, and it was declared and read by nothing until
            # this path needed it. Refusing here rather than after a targeted
            # warehouse sync avoids paying for a read whose answer
            # configuration has already given.
            return CaseBayRecommendation(
                case_id=case_id,
                warehouse_reference=None,
                bay_reference=None,
                return_location=None,
                confidence_millionths=None,
                reason="PRE_ARRIVAL_NOT_ALLOWED",
                explanation=(
                    "Bay placement is configured to wait for physical receipt; "
                    "no bay is recommended before the goods arrive."
                ),
                evidence_reference=None,
                graph_generation_id=None,
            )
        physical_status = physical_status or PRE_ARRIVAL_STATUS

        warehouse_reference = await self._warehouse_reference(case, facts)
        return_method = normalize_method(_text(facts.get(FACT_RETURN_METHOD)))
        product_type = _text(facts.get(FACT_PRODUCT_TYPE)) or "STANDARD"
        required_capacity = _int_fact(
            facts.get(FACT_REQUIRED_CAPACITY), DEFAULT_REQUIRED_CAPACITY
        )

        observation, raw_candidates = await observe_eligible_bays(
            observations=self._observations,
            # Never the SQL bypass. The session path keeps it for deployments
            # that predate the targeted-graph stack; the case path is new and
            # has no such deployment, so reading the source directly here would
            # reintroduce exactly what W2.7 removed.
            sql=None,
            warehouse_id=warehouse_reference,
            return_method=return_method.value,
            product_type=product_type,
        )
        assessment = self._agent.assess(
            BayAssessmentRequest(
                physicalStatus=physical_status,
                returnMethod=return_method,
                requiredCapacity=required_capacity,
                oversized=_flag_fact(facts.get(FACT_OVERSIZED)),
                hazardous=_flag_fact(facts.get(FACT_HAZARDOUS)),
                candidates=tuple(
                    BayCandidateInput(
                        bayId=str(item["bayId"]),
                        bayType=str(item["bayType"]),
                        active=bool(item["active"]),
                        capacityAvailable=int(item["capacityAvailable"]),
                        supportsOversized=bool(item["supportsOversized"]),
                        supportsHazardous=bool(item["supportsHazardous"]),
                        supportedReturnMethods=(return_method,),
                    )
                    for item in raw_candidates
                ),
            )
        )
        recommendation = self._compose(
            case_id=case_id,
            assessment=assessment,
            observation=observation,
            raw_candidates=raw_candidates,
            warehouse_reference=warehouse_reference,
        )
        await self._record(case_id, assessment, required_capacity, physical_status)
        return recommendation

    async def _warehouse_reference(
        self, case: dict[str, Any], facts: dict[str, Any]
    ) -> str | None:
        """The case's processing warehouse, from the case or from its order.

        An explicitly recorded fact wins: somebody stating where this return is
        being processed outranks anything inferred. Otherwise the confirmed
        order's own shipping warehouse is the honest default -- goods came from
        it, and returning them to it is the platform's recommendation, not a
        guess about an unrelated site.

        `None` when neither answers. `observe_eligible_bays` turns that into
        `ABSENT / NO_WAREHOUSE_REFERENCE` rather than into every bay in the
        estate, which is the defect `bay_observations` was written to close.
        """
        recorded = _text(facts.get(FACT_WAREHOUSE))
        if recorded:
            return recorded
        order_reference = _text(case.get("confirmedOrderReference"))
        if not order_reference or self._order_observations is None:
            return None
        try:
            return await self._order_observations.observe_shipping_warehouse(order_reference)
        except Exception:  # noqa: BLE001 - advisory; absence is a real answer
            logger.warning(
                "case_placement_order_warehouse_unreadable",
                extra={"order_reference": order_reference},
                exc_info=True,
            )
            return None

    def _compose(
        self,
        *,
        case_id: str,
        assessment: BayAssessment,
        observation: WarehouseObservation | None,
        raw_candidates: list[dict[str, Any]],
        warehouse_reference: str | None,
    ) -> CaseBayRecommendation:
        bay_reference = assessment.recommendedBayId
        candidate = next(
            (item for item in raw_candidates if str(item.get("bayId")) == bay_reference),
            None,
        )
        decision = assessment.decision
        # The reason names the *state*, not merely the decision: an operator
        # looking at a case with no bay has to be able to tell "no such
        # warehouse" from "we could not look" from "every bay is full", and
        # `NO_ELIGIBLE_BAY` alone says none of the three.
        reason = str(decision.decision)
        if bay_reference is None and decision.decision == "NOT_READY":
            # `require_physical_receipt` refused even though pre-arrival
            # placement is allowed -- two configured gates that contradict each
            # other. Surfaced by its own name rather than folded into
            # `NO_ELIGIBLE_BAY`, because the fix is a configuration change and
            # nothing about the estate.
            reason = "PHYSICAL_RECEIPT_REQUIRED"
        elif bay_reference is None and observation is not None:
            if observation.evidence is BayEvidence.ABSENT:
                reason = f"WAREHOUSE_ABSENT_{observation.absent_reason or 'UNKNOWN'}"
            elif observation.evidence is BayEvidence.UNAVAILABLE:
                reason = f"WAREHOUSE_UNAVAILABLE_{observation.unavailable_reason or 'UNKNOWN'}"
        return CaseBayRecommendation(
            case_id=case_id,
            warehouse_reference=(
                str(candidate["warehouseId"])
                if candidate is not None and candidate.get("warehouseId")
                else warehouse_reference
            ),
            bay_reference=bay_reference,
            return_location=return_location_of(candidate, observation),
            # Reported only when there is a recommendation to be confident
            # about. The agent returns 0 for "nothing eligible", and carrying
            # that forward as a confidence would read as a confident refusal.
            confidence_millionths=(
                int(decision.confidenceMillionths) if bay_reference is not None else None
            ),
            reason=reason,
            explanation=str(decision.explanation),
            evidence_reference=(
                observation.evidence_reference if observation is not None else None
            ),
            graph_generation_id=(
                observation.graph_generation_id if observation is not None else None
            ),
            eligible_bay_ids=tuple(assessment.eligibleBayIds),
        )

    async def _record(
        self,
        case_id: str,
        assessment: BayAssessment,
        required_capacity: int,
        physical_status: str,
    ) -> None:
        """The decision log entry, keyed by case instead of by handling unit.

        `decision_key` is derived from the inputs the agent actually weighed,
        so re-running placement for an unchanged case rewrites one record
        rather than appending a second opinion.
        """
        try:
            await self._repository.persist_agent_decision(
                aggregate_id=case_id,
                session_id=case_id,
                decision=assessment.decision.model_dump(mode="json"),
                decision_key=(
                    f"case-bay-recommendation:{case_id}:{physical_status}:{required_capacity}"
                ),
                actor_id="return-case-workflow",
            )
        except Exception:  # noqa: BLE001 - advisory: the recommendation stands
            logger.warning(
                "case_placement_decision_not_recorded",
                extra={"case_id": case_id},
                exc_info=True,
            )


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
