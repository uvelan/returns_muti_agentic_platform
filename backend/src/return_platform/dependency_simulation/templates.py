"""Deterministic narratives that keep the main flow available without AI."""

from __future__ import annotations

from typing import Any

from return_platform.dependency_simulation.models import DependencyKind, SimulationNarrative

_DEFAULTS: dict[tuple[str, str], tuple[str, str, str]] = {
    ("OMC", "CREATE_RMA"): (
        "The simulated OMC service created and read back a v2 RMA.",
        "RMA creation confirmed by the deterministic dependency simulator.",
        "Continue with the approved physical-return method.",
    ),
    ("OMC", "SET_CUSTOMER_RESOLUTION"): (
        "The simulated OMC service recorded the customer resolution.",
        "Customer resolution updated independently of product disposition.",
        "Verify whether physical and vendor-recovery work remains.",
    ),
    ("PARCEL", "CREATE_RETURN_LABEL"): (
        "The simulated parcel service created a label for one handling unit.",
        "A label exists, but carrier custody has not yet been confirmed.",
        "Confirm package-to-label mapping and physical handoff.",
    ),
    ("PARCEL", "ADVANCE_TRACKING"): (
        "The simulated parcel service emitted the next tracking event.",
        "Parcel state advanced using the configured deterministic sequence.",
        "Review the tracking event and continue when authoritative evidence is sufficient.",
    ),
    ("FREIGHT", "REQUEST_QUOTES"): (
        "The simulated freight service returned deterministic LTL quotes.",
        "Quotes are available for Logistics review.",
        "Approve one quote before creating and tendering a BOL.",
    ),
    ("FREIGHT", "TENDER_SHIPMENT"): (
        "The simulated TMS accepted the tender request for processing.",
        "Tendering is recorded; carrier booking is still unconfirmed.",
        "Wait for or manually trigger booking confirmation.",
    ),
    ("FREIGHT", "CONFIRM_BOOKING"): (
        "The simulated TMS confirmed carrier booking.",
        "A carrier is booked, but physical pickup has not occurred.",
        "Complete site readiness and wait for pickup evidence.",
    ),
    ("FREIGHT", "CONFIRM_PICKUP"): (
        "The simulated freight carrier confirmed physical pickup.",
        "Carrier custody is established.",
        "Track the shipment until receiving evidence arrives.",
    ),
    ("LSI", "RECORD_RECEIPT"): (
        "The simulated LSI service recorded physical receipt.",
        "The returned item was reconciled to the customer return and cart item.",
        "Assign the LSI license plate and record product disposition.",
    ),
    ("LSI", "ASSIGN_LICENSE_PLATE"): (
        "The simulated LSI service assigned a license plate after receipt.",
        "Physical inventory identity is now available.",
        "Continue with product disposition and warehouse processing.",
    ),
    ("LSI", "CREATE_RGA"): (
        "The simulated LSI service created a downstream vendor RGA.",
        "Vendor recovery started after the customer return and RTV disposition.",
        "Wait for vendor debit or credit evidence.",
    ),
    ("LSI", "RECORD_VENDOR_CREDIT"): (
        "The simulated LSI service recorded vendor-credit evidence.",
        "Downstream vendor recovery is complete.",
        "Close the case when all other applicable dimensions are terminal.",
    ),
}


def default_narrative(
    dependency: DependencyKind,
    operation: str,
    result: dict[str, Any],
    *,
    template_version: str,
) -> SimulationNarrative:
    message, summary, next_action = _DEFAULTS.get(
        (dependency.value, operation),
        (
            f"The simulated {dependency.value} service completed {operation}.",
            "The deterministic simulator returned a schema-valid response.",
            "Review the result and continue the configured return workflow.",
        ),
    )
    reference = result.get("externalReference") or result.get("rmaId") or result.get("rgaId")
    if reference:
        summary = f"{summary} Reference: {reference}."
    return SimulationNarrative(
        source="DEFAULT_TEMPLATE",
        message=message,
        summary=summary,
        nextAction=next_action,
        templateVersion=template_version,
    )
