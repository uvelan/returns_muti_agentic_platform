"""Evidence-bound Order Analysis Agent with AI Assistance."""

from __future__ import annotations

import json
from typing import Any

from return_platform.agents.contracts import (
    AgentDecisionView,
    OrderAnalysisAssessment,
    OrderAnalysisRequest,
)
from return_platform.ai.gateway.service import AIGatewayService
from return_platform.configuration.return_configuration import ReturnPlatformConfiguration


def _question_from_explanation(explanation: str) -> str | None:
    """The clarification question, when the model returned one as JSON.

    The task prompts ask for compact JSON inside the envelope's explanation
    field; a model that answered with plain prose instead is not an error, it is
    an explanation with no extractable question -- so only a syntactically
    JSON-shaped explanation is parsed, and a malformed one falls back to `None`
    rather than raising.
    """
    if not explanation.startswith("{"):
        return None
    try:
        parsed = json.loads(explanation)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    question = parsed.get("question") or parsed.get("smartQuestion")
    return question if isinstance(question, str) and question.strip() else None


def _distinct_candidate_values(candidates: list[Any], candidate_field: str) -> set[str]:
    """Every distinct value the candidates carry for one field.

    Duck-typed on purpose, and in one place on purpose. The candidates are the
    dynamic-knowledge order shapes, whose property bags are release-defined --
    and this module may not import `dynamic_knowledge` (agents/README.md), so a
    shared type cannot express them here. The lookup order is: an explicit model
    attribute, then the customer/order property bags, then each line's product
    properties.
    """
    values: set[str] = set()
    for candidate in candidates:
        if hasattr(candidate, candidate_field):
            value = getattr(candidate, candidate_field)
            if value:
                values.add(str(value))
            continue
        for bag_name in ("customerProperties", "orderProperties"):
            bag = getattr(candidate, bag_name, None)
            if bag and candidate_field in bag:
                value = bag[candidate_field]
                if value:
                    values.add(str(value))
                break
        else:
            for line in getattr(candidate, "lines", None) or ():
                properties = getattr(line, "productProperties", None)
                if properties and candidate_field in properties:
                    value = properties[candidate_field]
                    if value:
                        values.add(str(value))
    return values


class OrderAnalysisAgent:
    """This agent does not directly invoke another agent."""

    def __init__(self, configuration: ReturnPlatformConfiguration) -> None:
        self._root = configuration
        self._config = configuration.agents["order_analysis"]
        #: The analysis task, from configuration where the release names one --
        #: `ai_route_ref` existed in every release and was read by nothing, so
        #: the code and the configuration could name different tasks with
        #: neither being wrong. The literal remains only as the fallback for a
        #: release cut before the field was honoured.
        self._analysis_task_id = self._config.ai_route_ref or "ORDER_CANDIDATE_ANALYSIS_V1"

    async def analyze(
        self, request: OrderAnalysisRequest, ai_gateway: AIGatewayService
    ) -> OrderAnalysisAssessment:
        if not self._config.ai_assisted:
            return OrderAnalysisAssessment(
                smartQuestion=None,
                analysisExplanation="AI Assistance is disabled.",
                decision=AgentDecisionView(
                    agent=self._config.name,
                    agentVersion=self._config.version,
                    configurationVersion=self._root.assumption_set_version,
                    decisionType="ORDER_ANALYSIS",
                    decision="DETERMINISTIC_FALLBACK",
                    explanation="AI Assistance is disabled.",
                    confidenceMillionths=0,
                    evidenceReferences=("DISCOVERY:NO_CANDIDATE",),
                ),
            )

        payload = {
            "candidates": [candidate.model_dump(mode="json") for candidate in request.candidates],
            "evidence": request.suppliedEvidence,
            "conflicts": [anchor for c in request.candidates for anchor in c.conflictingAnchors],
            "knownFacts": [
                f"Candidate {c.candidateId} source is {c.orderSource}" for c in request.candidates
            ],
        }

        evaluation = await ai_gateway.evaluate(
            session_id=request.sessionId,
            redacted_input=payload,
            task_id=self._analysis_task_id,
        )

        trace = evaluation.trace
        explanation = trace.explanation or ""
        smart_question = _question_from_explanation(explanation)

        return OrderAnalysisAssessment(
            smartQuestion=smart_question,
            analysisExplanation=explanation,
            decision=AgentDecisionView(
                agent=self._config.name,
                agentVersion=self._config.version,
                configurationVersion=self._root.assumption_set_version,
                decisionType="ORDER_ANALYSIS",
                decision=trace.decision.value if trace.decision else "UNKNOWN",
                explanation=trace.explanation,
                confidenceMillionths=trace.confidenceMillionths,
                evidenceReferences=tuple(
                    {ref for c in request.candidates for ref in c.evidenceReferences}
                )
                or ("DISCOVERY:NO_CANDIDATE",),
            ),
        )

    async def disambiguate(
        self,
        candidates: list[Any],
        user_response: str,
        allowed_fields: list[dict[str, Any]],
        ai_gateway: AIGatewayService,
        session_id: str,
    ) -> tuple[str | None, str | None]:
        """
        Aggregate data and ask AI to select candidate or pick best question.
        Returns: (candidate_id, smart_question)
        """
        if not self._config.ai_assisted:
            return None, None

        max_distinct = self._root.clarification_policy.max_distinct_values_for_ai or 5

        # Aggregate distinct values for all allowed fields
        summary: dict[str, list[str] | dict[str, int]] = {}
        for field_config in allowed_fields:
            field_name = field_config["field"]
            candidate_field = field_config.get("candidate_field")
            if not candidate_field:
                continue
            distinct_values = _distinct_candidate_values(candidates, candidate_field)
            if distinct_values:
                if len(distinct_values) <= max_distinct:
                    summary[field_name] = sorted(distinct_values)
                else:
                    summary[field_name] = {"count": len(distinct_values)}

        payload = {
            "aggregatedSummary": summary,
            "userMessage": user_response,
            "allowedFields": [f["field"] for f in allowed_fields],
        }

        evaluation = await ai_gateway.evaluate(
            session_id=session_id,
            redacted_input=payload,
            task_id="ORDER_CANDIDATE_DISAMBIGUATION_V1",
        )

        trace = evaluation.trace
        explanation = trace.explanation or ""
        smart_question = _question_from_explanation(explanation)

        if trace.decision and trace.decision.value not in {"AMBIGUOUS", "UNKNOWN"}:
            # AI identified a specific candidate
            return trace.decision.value, None

        return None, smart_question or explanation
