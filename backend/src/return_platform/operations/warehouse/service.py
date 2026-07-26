"""Agent-assisted, transactionally enforced warehouse bay placement."""

from __future__ import annotations

from typing import Any

from return_platform.agents.bay_assignment import BayAssignmentAgent
from return_platform.agents.contracts import (
    BayAssessment,
    BayAssessmentRequest,
    BayCandidateInput,
    NormalizedReturnMethod,
)
from return_platform.configuration.return_configuration import ReturnPlatformConfiguration
from return_platform.operations.repository import OperationalRepository
from return_platform.operations.sql_business_state import SQLBusinessStateRepository


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


class WarehousePlacementService:
    def __init__(
        self,
        *,
        repository: OperationalRepository,
        sql: SQLBusinessStateRepository,
        configuration: ReturnPlatformConfiguration,
    ) -> None:
        self._repository = repository
        self._sql = sql
        self._agent = BayAssignmentAgent(configuration)

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
        session = await self._repository.get_return(session_id)
        if session is None:
            raise KeyError(session_id)
        handling = await self._repository.get_handling_unit(handling_unit_id)
        if handling is None or handling.get("sessionId") != session_id:
            raise KeyError(handling_unit_id)
        events = await self._repository.list_events(session_id)
        receipt_confirmed = any(event.eventType == "RECEIPT_CONFIRMED" for event in events)
        physical_status = "WAREHOUSE_RECEIVED" if receipt_confirmed else str(
            handling.get("physicalStatus", "UNKNOWN")
        )
        return_method = normalize_method(
            session.approvedReturnMethod or session.shippingPathExpectation
        )
        raw_candidates = await self._sql.list_bay_candidates(
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
        return result, raw_candidates

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
