"""Human-governed Feedback Learning Agent."""

from __future__ import annotations

from return_platform.agents.contracts import (
    AgentDecisionView,
    FeedbackAssessment,
    FeedbackAssessmentRequest,
)
from return_platform.configuration.return_configuration import ReturnPlatformConfiguration


class FeedbackLearningAgent:
    def __init__(self, configuration: ReturnPlatformConfiguration) -> None:
        self._root = configuration
        self._config = configuration.agents["feedback_learning"]

    def assess(self, request: FeedbackAssessmentRequest) -> FeedbackAssessment:
        recommendations: list[str] = []
        clarification_count = int(request.metrics.get("supportClarificationCount", 0))
        pickup_failures = int(request.metrics.get("pickupFailureCount", 0))
        ambiguity = int(request.metrics.get("discoveryAmbiguityCount", 0))
        assumption_mismatch = bool(request.metrics.get("assumptionMismatch", False))
        if clarification_count >= 2:
            recommendations.append("Review required-field questions and Support request template.")
        if pickup_failures:
            recommendations.append("Review pickup-site access and equipment validation policy.")
        if ambiguity >= 2:
            recommendations.append("Add stronger order-discovery anchors or scoring evidence.")
        if assumption_mismatch:
            recommendations.append(
                "Create a reviewed replacement for the active assumption policy."
            )
        return FeedbackAssessment(
            recommendations=tuple(recommendations),
            reviewRequired=bool(recommendations),
            decision=AgentDecisionView(
                agent=self._config.name,
                agentVersion=self._config.version,
                configurationVersion=self._root.assumption_set_version,
                decisionType="PROCESS_RECOMMENDATION",
                decision="REVIEW_REQUIRED" if recommendations else "NO_CHANGE",
                explanation="Recommendations are advisory and require governance approval.",
                confidenceMillionths=850_000 if recommendations else 700_000,
                evidenceReferences=request.evidenceReferences,
            ),
        )
