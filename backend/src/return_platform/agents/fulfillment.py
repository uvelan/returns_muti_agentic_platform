"""OMC-aware Return Fulfillment Agent."""

from __future__ import annotations

from return_platform.agents.contracts import (
    AgentDecisionView,
    FulfillmentAssessment,
    FulfillmentAssessmentRequest,
)
from return_platform.configuration.return_configuration import ReturnPlatformConfiguration


class ReturnFulfillmentAgent:
    """This agent does not directly invoke another agent."""

    def __init__(self, configuration: ReturnPlatformConfiguration) -> None:
        self._root = configuration
        self._config = configuration.agents["return_fulfillment"]

    def assess(self, request: FulfillmentAssessmentRequest) -> FulfillmentAssessment:
        raw = (request.rawReturnStatus or "UNKNOWN").strip().upper()
        normalized = self._root.omc.normalized_statuses.get(raw, "UNKNOWN")
        fact_types = {fact.factType.upper(): fact for fact in request.facts}
        tendered = "BOL_TENDERED" in fact_types
        booked = "CARRIER_BOOKED" in fact_types
        picked_up = "PICKUP_CONFIRMED" in fact_types or "CARRIER_ACCEPTED" in fact_types
        received = "LSI_RECEIPT" in fact_types or "WAREHOUSE_RECEIPT" in fact_types
        license_plate = "LICENSE_PLATE" in fact_types
        customer_complete = (
            request.rawCustomerResolution or ""
        ).strip().upper() == "REFUNDED" or normalized == "CUSTOMER_RESOLUTION_COMPLETE"
        physical_complete = received
        warehouse_complete = received and license_plate and bool(request.rawProductResolution)
        vendor_complete = "VENDOR_CREDIT" in fact_types
        warnings: list[str] = []
        if tendered and not booked:
            warnings.append("BOL_TENDERED_NOT_BOOKED")
        if booked and not picked_up:
            warnings.append("BOOKING_IS_NOT_PICKUP")
        if "RGA" in fact_types and not received:
            warnings.append("RGA_BEFORE_RECEIPT_REQUIRES_REVIEW")
        if not received:
            next_event = (
                "CARRIER_BOOKING"
                if tendered and not booked
                else ("PICKUP_CONFIRMATION" if booked and not picked_up else "PHYSICAL_RECEIPT")
            )
        elif not customer_complete:
            next_event = "CUSTOMER_RESOLUTION"
        elif not warehouse_complete:
            next_event = "PRODUCT_RESOLUTION"
        elif not vendor_complete and (request.rawProductResolution or "").upper() == "RTV":
            next_event = "VENDOR_RECOVERY"
        else:
            next_event = None
        evidence = tuple(fact.evidenceReference for fact in request.facts) or ("OMC:STATUS",)
        return FulfillmentAssessment(
            normalizedReturnStatus=normalized,
            customerResolutionComplete=customer_complete,
            physicalReturnComplete=physical_complete,
            warehouseProcessingComplete=warehouse_complete,
            vendorRecoveryComplete=vendor_complete,
            nextExpectedEvent=next_event,
            decision=AgentDecisionView(
                agent=self._config.name,
                agentVersion=self._config.version,
                configurationVersion=self._root.assumption_set_version,
                decisionType="FULFILLMENT_STATE_INTERPRETATION",
                decision=normalized,
                explanation=(
                    "OMC and carrier evidence were normalized without inferring physical events."
                ),
                confidenceMillionths=900_000 if normalized != "UNKNOWN" else 400_000,
                evidenceReferences=evidence,
                warnings=tuple(warnings),
                metadata={"returnVersion": request.returnVersion},
            ),
        )
