"""Agent-assisted, transactionally enforced warehouse bay placement.

**Candidates come from the graph when one is reachable (W2.7).** They used to
come from `SQLBusinessStateRepository.list_bay_candidates` on every call, which
is the direct source read R2 forbids from an agent path, and which answered a
return with no warehouse reference by offering every bay in the estate. The
graph read is an *observation* with three outcomes, and a warehouse nobody
observed yields no candidates rather than all of them.

Bay placement is `best_effort` by declared policy, so a graph that cannot be
reached does not fail the return: `bay_observation` on the result records which
of the three readings applied, and the caller decides. It is never a reason to
fall back to the SQL bypass -- silently reading the source the step removed would
make the removal a comment.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from return_platform.agents.contracts import (
    BayAssessment,
    BayAssessmentRequest,
    BayCandidateInput,
    NormalizedReturnMethod,
)
from return_platform.agents.registry import AgentRegistry
from return_platform.configuration.return_configuration import ReturnPlatformConfiguration
from return_platform.operations.repository import OperationalRepository
from return_platform.operations.sql_business_state import SQLBusinessStateRepository
from return_platform.operations.warehouse.observations import (
    BayEvidence,
    CapacityEvidence,
    LiveBayCapacityPort,
    WarehouseBayObservationPort,
    WarehouseObservation,
)

logger = logging.getLogger("return_platform.operations.warehouse.service")


def normalize_method(value: str | None) -> NormalizedReturnMethod:
    aliases = {
        "PPL": NormalizedReturnMethod.BRANCH_UPS,
        "BOL": NormalizedReturnMethod.BRANCH_LTL,
        "CUSTOMER_SHIP": NormalizedReturnMethod.OFFSITE_PARCEL,
        "NO_LABEL": NormalizedReturnMethod.NO_PHYSICAL_RETURN,
    }
    if value in aliases:
        return aliases[value]
    try:
        return NormalizedReturnMethod(value or "UNKNOWN")
    except ValueError:
        return NormalizedReturnMethod.UNKNOWN


def _flag(value: Any) -> bool:
    """A SQL Server `bit` as the graph hands it back.

    pymssql yields `0`/`1` for a bit and Neo4j stores whatever the projector
    wrote, so a value arrives as `True`, `1` or `"1"` depending on the path it
    took. `bool("0")` is `True`, which would make every bay hazmat-capable, so
    the string spellings are enumerated rather than coerced.
    """
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return bool(value)


def _string_list(value: Any) -> tuple[str, ...]:
    """`supported_shipping_paths` and `supported_product_types`.

    Both are `nvarchar` columns holding a JSON array, so the graph holds the raw
    text -- the descriptor declares what the source declares. Unparseable text
    yields an empty tuple, which the eligibility test below reads as "this bay
    states no restriction", exactly as the SQL path did.
    """
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, ValueError):
        return ()
    return tuple(str(item) for item in parsed) if isinstance(parsed, list) else ()


def _permits(paths: tuple[str, ...], return_method: str) -> bool:
    """The shipping-path aliases the SQL query carried, kept verbatim.

    `PPL` and `BOL` are the codes bay configuration was written with and
    `NormalizedReturnMethod` is the vocabulary the platform moved to; a bay row
    declaring the old code still means the new method. Re-deriving this would be
    a second answer to a question the source already answers one way.
    """
    if not paths or return_method in paths:
        return True
    if return_method == "BRANCH_UPS" and "PPL" in paths:
        return True
    return return_method in {"BRANCH_LTL", "OFFSITE_LTL"} and "BOL" in paths


def _supports_product(product_types: tuple[str, ...], product_type: str) -> bool:
    return not product_types or product_type in product_types


def _candidate_of(row: dict[str, Any], return_method: str) -> dict[str, Any]:
    """One graph row in the shape `recommend` already returns to its callers.

    `capacityAvailable` is the bay's **declared maximum**, not its free capacity.
    The SQL query this replaces subtracted `SUM(reserved_capacity)` over
    reservations with `expires_at > SYSUTCDATETIME()` -- a figure that changes
    with the clock rather than with any source write, so no sync makes a graph
    node current for it. Over-recommending is caught where it has to be anyway:
    `reserve_and_assign_handling_unit` takes the reservation transactionally and
    refuses a bay that cannot take the unit, and that is the only place holding a
    lock over the decision.
    """
    maximum = row.get("max_handling_unit_count")
    if maximum is None:
        maximum = row.get("max_package_count")
    return {
        "bayId": str(row.get("bay_id", "")),
        "bayType": str(row.get("bay_type", "")),
        "warehouseId": str(row.get("warehouse_id", "")),
        "active": _flag(row.get("active")),
        "priority": int(row.get("priority") or 0),
        "capacityAvailable": max(0, int(maximum or 0)),
        "supportsHazardous": _flag(row.get("hazardous_allowed")),
        "supportsOversized": _flag(row.get("oversized_allowed")),
        "supportedReturnMethods": [return_method],
    }


async def _apply_live_capacity(
    candidates: list[dict[str, Any]],
    live_capacity: LiveBayCapacityPort | None,
) -> CapacityEvidence:
    """Replace each bay's declared maximum with what is actually free (BAY-02).

    The graph holds `max_handling_unit_count`; it cannot hold the reservation
    aggregate, so ranking on the graph figure alone offers every concurrent
    return the same tightest-fit bay and lets the transaction refuse all but
    one of them. That is the reservation churn BAY-02 names.

    Best-effort like the rest of placement: a live read that fails leaves the
    declared figures in place and reports `DECLARED`, so a SQL outage degrades
    the recommendation's odds rather than removing bay placement entirely. The
    caller carries that verdict onto the result, because an operator watching
    reservations get refused has to be able to tell which reading produced
    them.
    """
    if live_capacity is None or not candidates:
        return CapacityEvidence.DECLARED
    try:
        reserved = await live_capacity.reserved_capacity_by_bay(
            [str(item["bayId"]) for item in candidates]
        )
    except asyncio.CancelledError:
        raise
    except Exception as error:  # noqa: BLE001 - best_effort by declared policy
        logger.warning(
            "live_bay_capacity_unavailable",
            extra={"error_code": type(error).__name__},
            exc_info=True,
        )
        return CapacityEvidence.DECLARED
    for item in candidates:
        taken = int(reserved.get(str(item["bayId"]), 0))
        item["capacityAvailable"] = max(0, int(item["capacityAvailable"]) - taken)
    return CapacityEvidence.LIVE


async def observe_eligible_bays(
    *,
    observations: WarehouseBayObservationPort | None,
    sql: SQLBusinessStateRepository | None,
    warehouse_id: str | None,
    return_method: str,
    product_type: str,
    live_capacity: LiveBayCapacityPort | None = None,
) -> tuple[WarehouseObservation | None, list[dict[str, Any]], CapacityEvidence]:
    """Bay configuration for one warehouse, from the graph where there is one.

    Lifted out of `WarehousePlacementService._candidates` unchanged when the
    case flow needed the same reading (BAY-01). The session path and the case
    path differ only in how they *arrive* at a warehouse reference; what
    counts as an eligible bay for a return method and a product type is one
    answer, and two copies of it would drift.

    With no observation port configured this is the pre-W2.7 SQL read, so a
    deployment that has not yet built the targeted-graph stack keeps working.
    With one, the graph is the only source consulted: an `ABSENT` or
    `UNAVAILABLE` reading yields no candidates and the agent reports no
    eligible bay, which is the honest answer and the one the `best_effort`
    policy is written for.
    """
    if observations is None:
        if sql is None:
            # Neither source. Not "no bay" -- nothing was consulted, and a
            # caller must not read this as an empty warehouse.
            return (
                WarehouseObservation(
                    warehouse_reference=warehouse_id,
                    evidence=BayEvidence.UNAVAILABLE,
                    unavailable_reason="NO_PLACEMENT_SOURCE",
                ),
                [],
                CapacityEvidence.DECLARED,
            )
        # The pre-W2.7 read already subtracts unexpired reservations in the
        # same statement, so its capacity is live by construction.
        return (
            None,
            await sql.list_bay_candidates(
                warehouse_id=warehouse_id,
                return_method=return_method,
                product_type=product_type,
            ),
            CapacityEvidence.LIVE,
        )
    try:
        observation = await observations.observe(warehouse_id)
    except asyncio.CancelledError:
        raise
    except Exception as error:  # noqa: BLE001 - best_effort by declared policy
        # The port raises only when the graph cannot be read at all, and this
        # is where that becomes a *state* rather than a 500. Bay placement is
        # `best_effort`: the case must never park over it, and an operator
        # reading `WAREHOUSE_UNAVAILABLE:CONNECTIONREFUSEDERROR` on the
        # recommendation knows both that no bay was offered and why. Letting
        # it propagate would take the endpoint down over a stage the policy
        # says may be skipped.
        logger.warning(
            "warehouse_bay_observation_failed",
            extra={"warehouse_id": warehouse_id, "error_code": type(error).__name__},
            exc_info=True,
        )
        return (
            WarehouseObservation(
                warehouse_reference=warehouse_id,
                evidence=BayEvidence.UNAVAILABLE,
                unavailable_reason=type(error).__name__.upper(),
            ),
            [],
            CapacityEvidence.DECLARED,
        )
    if observation.evidence is not BayEvidence.OBSERVED:
        return observation, [], CapacityEvidence.DECLARED
    eligible = [
        _candidate_of(row, return_method)
        for row in observation.candidates
        if _permits(_string_list(row.get("supported_shipping_paths")), return_method)
        and _supports_product(_string_list(row.get("supported_product_types")), product_type)
    ]
    # The same order the SQL query returned: priority ascending, then bay id.
    # The agent ranks on capability and capacity, and a stable order is what
    # makes two identical requests produce the same recommendation.
    ordered = sorted(eligible, key=lambda item: (item["priority"], item["bayId"]))
    # After eligibility and before ranking: the agent must weigh what is free,
    # not what the bay was built to hold.
    capacity_evidence = await _apply_live_capacity(ordered, live_capacity)
    return observation, ordered, capacity_evidence


class WarehousePlacementService:
    def __init__(
        self,
        *,
        repository: OperationalRepository,
        sql: SQLBusinessStateRepository,
        configuration: ReturnPlatformConfiguration,
        observations: WarehouseBayObservationPort | None = None,
    ) -> None:
        self._repository = repository
        # Still held: `assign` reserves and assigns transactionally in SQL, which
        # is the authoritative write and is not what W2.7 removes. What moved to
        # the graph is the *candidate listing* the agent ranks.
        self._sql = sql
        self._observations = observations
        self._agent = AgentRegistry.build(configuration).bay_assignment

    async def recommend(
        self,
        session_id: str,
        *,
        handling_unit_id: str,
        required_capacity: int,
        oversized: bool,
        hazardous: bool,
        actor_id: str,
    ) -> tuple[BayAssessment, list[dict[str, Any]]]:
        assessment, candidates, _ = await self.recommend_with_evidence(
            session_id,
            handling_unit_id=handling_unit_id,
            required_capacity=required_capacity,
            oversized=oversized,
            hazardous=hazardous,
            actor_id=actor_id,
        )
        return assessment, candidates

    async def recommend_with_evidence(
        self,
        session_id: str,
        *,
        handling_unit_id: str,
        required_capacity: int,
        oversized: bool,
        hazardous: bool,
        actor_id: str,
    ) -> tuple[BayAssessment, list[dict[str, Any]], WarehouseObservation | None]:
        session = await self._repository.get_return(session_id)
        if session is None:
            raise KeyError(session_id)
        handling = await self._repository.get_handling_unit(handling_unit_id)
        if handling is None or handling.get("sessionId") != session_id:
            raise KeyError(handling_unit_id)
        events = await self._repository.list_events(session_id)
        receipt_confirmed = any(event.eventType == "RECEIPT_CONFIRMED" for event in events)
        physical_status = (
            "WAREHOUSE_RECEIVED"
            if receipt_confirmed
            else str(handling.get("physicalStatus", "UNKNOWN"))
        )
        return_method = normalize_method(
            session.approvedReturnMethod or session.shippingPathExpectation
        )
        observation, raw_candidates = await self._candidates(
            warehouse_id=session.processingWarehouseReference,
            return_method=return_method.value,
            product_type=session.productType or "STANDARD",
        )
        candidates = tuple(
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
        )
        result = self._agent.assess(
            BayAssessmentRequest(
                physicalStatus=physical_status,
                returnMethod=return_method,
                requiredCapacity=required_capacity,
                oversized=oversized,
                hazardous=hazardous,
                candidates=candidates,
            )
        )
        await self._repository.persist_agent_decision(
            aggregate_id=handling_unit_id,
            session_id=session_id,
            decision=result.decision.model_dump(mode="json"),
            decision_key=(
                f"bay-recommendation:{handling_unit_id}:{physical_status}:"
                f"{required_capacity}:{oversized}:{hazardous}"
            ),
            actor_id=actor_id,
        )
        return result, raw_candidates, observation

    async def _candidates(
        self,
        *,
        warehouse_id: str | None,
        return_method: str,
        product_type: str,
    ) -> tuple[WarehouseObservation | None, list[dict[str, Any]]]:
        """This session's warehouse, read through the shared bay pipeline."""
        observation, candidates, _ = await observe_eligible_bays(
            observations=self._observations,
            sql=self._sql,
            warehouse_id=warehouse_id,
            return_method=return_method,
            product_type=product_type,
            # The same repository that will take the reservation, so the
            # ranking is filtered by the figure the reservation is tested
            # against. Only used on the graph path -- `list_bay_candidates`
            # subtracts reservations in its own statement.
            live_capacity=self._sql,
        )
        return observation, candidates

    async def assign(
        self,
        session_id: str,
        *,
        handling_unit_id: str,
        bay_id: str,
        required_capacity: int,
        oversized: bool,
        hazardous: bool,
        expected_handling_unit_version: int,
        actor_id: str,
    ) -> dict[str, Any]:
        assessment, candidates = await self.recommend(
            session_id,
            handling_unit_id=handling_unit_id,
            required_capacity=required_capacity,
            oversized=oversized,
            hazardous=hazardous,
            actor_id=actor_id,
        )
        if bay_id not in assessment.eligibleBayIds:
            raise ValueError("The selected bay is not eligible for this handling unit.")
        candidate = next(item for item in candidates if item["bayId"] == bay_id)
        session = await self._repository.get_return(session_id)
        assert session is not None
        if session.returnReference is None:
            raise ValueError("An authoritative return reference is required before bay assignment.")
        reservation_id, assignment_id = await self._sql.reserve_and_assign_handling_unit(
            session,
            handling_unit_id=handling_unit_id,
            return_reference=session.returnReference,
            bay_id=bay_id,
            warehouse_id=str(candidate["warehouseId"]),
            required_capacity=required_capacity,
            actor_id=actor_id,
        )
        handling = await self._repository.update_handling_unit(
            handling_unit_id,
            {
                "physicalStatus": "WAREHOUSE_STAGED",
                "bayId": bay_id,
                "warehouseId": candidate["warehouseId"],
                "reservationId": reservation_id,
                "assignmentId": assignment_id,
            },
            expected_version=expected_handling_unit_version,
        )
        await self._repository.update_return(
            session_id,
            {
                "bayReference": bay_id,
                "warehouseStatus": "STAGED",
            },
        )
        await self._repository.append_event(
            session_id,
            event_type="WAREHOUSE_BAY_ASSIGNED",
            actor_type="USER",
            actor_id=actor_id,
            payload={
                "handlingUnitId": handling_unit_id,
                "bayId": bay_id,
                "warehouseId": candidate["warehouseId"],
                "reservationId": reservation_id,
                "assignmentId": assignment_id,
            },
            deduplication_key=f"warehouse-bay:{handling_unit_id}:{bay_id}",
        )
        return {
            "assessment": assessment.model_dump(mode="json"),
            "handlingUnit": {key: value for key, value in handling.items() if key != "_id"},
            "reservationId": reservation_id,
            "assignmentId": assignment_id,
        }
