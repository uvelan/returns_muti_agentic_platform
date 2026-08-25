"""Deterministic Support Response Agent.

Plans what Support would issue for a handed-off return: the RMA reference, the
tracking and label references where the method needs them, and the handling
instructions for the product that is coming back. The plan is derived from the
released `return_policy.return_method_requirements` table, so the agent issues
exactly the artifacts the confirmed return method requires -- no label for a
freight pickup, no BOL for a parcel -- and asks for clarification instead of
inventing anything the handoff did not carry.

The agent decides; `operations/return_support/auto_responder.py` executes --
posts to the Support thread and records the outcome the workflow consumes.
"""

from __future__ import annotations

from return_platform.agents.contracts import (
    AgentDecisionView,
    SupportResponseAssessment,
    SupportResponseRequest,
    SupportRmaPlan,
)
from return_platform.configuration.return_configuration import ReturnPlatformConfiguration

#: Methods whose parcels travel on the platform's parcel account. The carrier is
#: stated only where the method implies one; freight methods leave it to the
#: carrier booking that follows, and `None` never erases a carrier already
#: recorded (see `support_return_record`).
_PARCEL_CARRIER: dict[str, str] = {
    "PREPAID_PARCEL": "UPS",
    "BRANCH_UPS": "UPS",
    "OFFSITE_PARCEL": "UPS",
}


def _reference(prefix: str, seed: str) -> str:
    """A deterministic reference, so a retry plans the same artifact."""
    cleaned = "".join(character for character in seed.upper() if character.isalnum())
    return f"{prefix}-{cleaned[:40]}" if cleaned else f"{prefix}-UNKNOWN"


class SupportResponseAgent:
    """This agent does not directly invoke another agent."""

    def __init__(self, configuration: ReturnPlatformConfiguration) -> None:
        self._root = configuration
        # A release cut before this agent existed carries no entry for it.
        # Constructing from such a release must not fail -- the registry builds
        # every agent -- so the identity falls back to the built-in name and the
        # behaviour stays governed by the release's requirement table either way.
        configured = configuration.agents.get("support_response")
        self._name = configured.name if configured else "Support Response Agent"
        self._version = configured.version if configured else "1.0"

    def _requirements_for(self, method: str | None) -> tuple[str, ...] | None:
        """What the released table says this method needs, or `None` when the
        table does not know it -- and "no row" must never read as "requires
        nothing" (the rule `ReturnMethodRequirementTable` states)."""
        if method is None:
            return None
        key = method.strip().upper()
        for row in self._root.return_policy.return_method_requirements:
            if row.method.upper() == key:
                return tuple(dimension.upper() for dimension in row.requires)
        return None

    def _instructions(self, request: SupportResponseRequest, requires: tuple[str, ...]) -> str:
        lines: list[str] = []
        for item in request.items:
            product = item.productName or item.sku or item.lineReference
            quantity = f"{item.quantity} x " if item.quantity else ""
            condition = (item.condition or "").strip().upper()
            reason = (item.reason or "").strip().upper()
            lines.append(f"- {quantity}{product} (line {item.lineReference})")
            if reason in self._root.return_policy.photo_required_reason_codes:
                lines.append(
                    "  Photograph the item before packing; this reason code requires evidence."
                )
            if condition and condition not in {"NEW", "UNOPENED"}:
                lines.append(
                    f"  Condition reported as {condition.title()}: pack to prevent further damage."
                )
        if "LABEL" in requires:
            lines.append("Attach the return label to each package.")
        if "BOL" in requires:
            lines.append("Keep the bill of lading with the shipment for the carrier.")
        if "PICKUP" in requires:
            lines.append(
                "A carrier pickup will be scheduled; keep the goods staged and accessible."
            )
        if request.bayReference:
            lines.append(f"Stage in bay {request.bayReference}.")
        if request.handlingInstructions:
            lines.append(request.handlingInstructions)
        return "\n".join(lines)

    def assess(self, request: SupportResponseRequest) -> SupportResponseAssessment:
        requires = self._requirements_for(request.returnMethod)
        missing: list[str] = []
        if requires is None:
            missing.append("return_method")
            requires = ()
        return_location = request.returnLocation or request.bayReference
        if "RETURN_LOCATION" in requires and not return_location:
            missing.append("return_location")
        for item in request.items:
            if item.quantity is None:
                missing.append(f"quantity:{item.lineReference}")

        evidence = (
            f"CASE:{request.caseId}",
            *(f"ORDER_LINE:{item.lineReference}" for item in request.items),
        )
        decision_common = {
            "agent": self._name,
            "agentVersion": self._version,
            "configurationVersion": self._root.assumption_set_version,
            "decisionType": "SUPPORT_RESPONSE",
            "evidenceReferences": evidence,
        }

        if missing:
            deduped = tuple(dict.fromkeys(missing))
            question = (
                "Before the RMA can be issued, please confirm: "
                + ", ".join(field.replace("_", " ") for field in deduped)
                + "."
            )
            return SupportResponseAssessment(
                ready=False,
                missingFields=deduped,
                clarificationRequest=question,
                plan=None,
                messageText=question,
                decision=AgentDecisionView(
                    decision="CLARIFICATION_REQUIRED",
                    explanation="The handoff lacks facts the confirmed return method requires.",
                    confidenceMillionths=1_000_000,
                    warnings=tuple(f"MISSING:{field}" for field in deduped),
                    **decision_common,
                ),
            )

        rma = _reference("RMA", request.caseId)
        method = (request.returnMethod or "").strip().upper()
        plan = SupportRmaPlan(
            returnReference=rma,
            trackingReference=_reference("TRK", rma) if "TRACKING" in requires else None,
            labelReference=_reference("LBL", rma) if "LABEL" in requires else None,
            returnLocation=return_location,
            shippingInstructionReference=(
                _reference("SHIP", rma) if requires != ("RMA",) else None
            ),
            returnMethod=method or None,
            carrier=_PARCEL_CARRIER.get(method),
            orderLineReferences=tuple(item.lineReference for item in request.items),
            instructions=self._instructions(request, requires),
        )
        summary = ", ".join(
            part
            for part in (
                f"RMA {plan.returnReference}",
                f"tracking {plan.trackingReference}" if plan.trackingReference else None,
                f"label {plan.labelReference}" if plan.labelReference else None,
            )
            if part
        )
        message = (
            f"Return created for case {request.caseId}: {summary}.\n"
            f"Return method: {method or 'Not stated'}.\n"
            f"Instructions:\n{plan.instructions}"
        )
        return SupportResponseAssessment(
            ready=True,
            missingFields=(),
            clarificationRequest=None,
            plan=plan,
            messageText=message,
            decision=AgentDecisionView(
                decision="RETURN_PLANNED",
                explanation=(
                    "Artifacts follow the released requirement table for the confirmed method; "
                    "recording them is the executor's act, not this assessment's."
                ),
                confidenceMillionths=950_000,
                **decision_common,
            ),
        )
