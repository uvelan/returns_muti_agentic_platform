"""Evidence and Support-draft responsibilities of the Return Workflow Agent."""

from __future__ import annotations

from return_platform.agents.contracts import (
    AgentDecisionView,
    NormalizedReturnMethod,
    ProductPresence,
    ReturnWorkflowAssessment,
    ReturnWorkflowAssessmentRequest,
)
from return_platform.configuration.return_configuration import ReturnPlatformConfiguration


class ReturnWorkflowAgent:
    """This agent does not directly invoke another agent."""

    def __init__(self, configuration: ReturnPlatformConfiguration) -> None:
        self._root = configuration
        self._config = configuration.agents["return_workflow"]

    @staticmethod
    def _recommended_method(request: ReturnWorkflowAssessmentRequest) -> NormalizedReturnMethod:
        if request.proposedReturnMethod is not NormalizedReturnMethod.UNKNOWN:
            return request.proposedReturnMethod
        presence = request.productPresence
        if presence is ProductPresence.PRESENT_AT_BRANCH:
            return NormalizedReturnMethod.BRANCH_UPS
        if presence in {
            ProductPresence.OFFSITE_CUSTOMER_RESIDENCE,
            ProductPresence.OFFSITE_CUSTOMER_JOBSITE,
            ProductPresence.OFFSITE_COMMERCIAL_SITE,
            ProductPresence.OFFSITE_VENDOR_SITE,
            ProductPresence.OFFSITE_OTHER,
        }:
            return NormalizedReturnMethod.OFFSITE_LTL
        if presence is ProductPresence.NOT_APPLICABLE:
            return NormalizedReturnMethod.NO_PHYSICAL_RETURN
        return NormalizedReturnMethod.UNKNOWN

    def assess(self, request: ReturnWorkflowAssessmentRequest) -> ReturnWorkflowAssessment:
        missing: list[str] = []
        if request.productPresence is None:
            missing.append("product_presence")
        if not request.associateId:
            missing.append("associate_id")
        if request.productPresence is ProductPresence.PRESENT_AT_BRANCH and not request.branchId:
            missing.append("branch_id")
        for item in request.items:
            if item.shippedQuantity is not None and item.requestedQuantity > item.shippedQuantity:
                missing.append(f"eligible_quantity:{item.orderLineId}")
        photo_required = any(
            item.reasonCode.upper() in self._root.return_policy.photo_required_reason_codes
            for item in request.items
        )
        if photo_required and any(not item.attachmentIds for item in request.items):
            missing.append("required_photo_evidence")
        if request.productPresence in {
            ProductPresence.OFFSITE_CUSTOMER_RESIDENCE,
            ProductPresence.OFFSITE_CUSTOMER_JOBSITE,
            ProductPresence.OFFSITE_COMMERCIAL_SITE,
            ProductPresence.OFFSITE_VENDOR_SITE,
            ProductPresence.OFFSITE_OTHER,
        }:
            pickup = request.pickupAssessment or {}
            for field in self._root.return_policy.heavy_pickup_required_fields:
                if pickup.get(field) in (None, "", [], ()):  # fail closed
                    missing.append(f"pickup.{field}")
        method = self._recommended_method(request)
        if method is NormalizedReturnMethod.UNKNOWN:
            missing.append("approved_return_method")
        item_lines = "\n".join(
            f"- {item.orderLineId}: product {item.productId}, quantity {item.requestedQuantity}, "
            f"reason {item.reasonCode}"
            for item in request.items
        )
        support_draft = (
            f"Return request {request.sessionId}\n"
            f"Order source: {request.orderSource.value}\n"
            "Product presence: "
            f"{request.productPresence.value if request.productPresence else 'UNKNOWN'}\n"
            f"Proposed method: {method.value}\n"
            f"Items:\n{item_lines}\n"
            f"Missing evidence: {', '.join(missing) if missing else 'None'}"
        )
        evidence = tuple(f"ORDER_LINE:{item.orderLineId}" for item in request.items)
        return ReturnWorkflowAssessment(
            complete=not missing,
            missingFields=tuple(dict.fromkeys(missing)),
            photoEvidenceRequired=photo_required,
            recommendedReturnMethod=method,
            supportDraft=support_draft,
            decision=AgentDecisionView(
                agent=self._config.name,
                agentVersion=self._config.version,
                configurationVersion=self._root.assumption_set_version,
                decisionType="RETURN_REQUEST_COMPLETENESS",
                decision="READY_FOR_HUMAN_CONFIRMATION" if not missing else "MISSING_EVIDENCE",
                explanation="The agent drafts and validates; an Associate must confirm submission.",
                confidenceMillionths=950_000 if not missing else 500_000,
                evidenceReferences=evidence,
                warnings=tuple(f"MISSING:{item}" for item in missing),
            ),
        )
