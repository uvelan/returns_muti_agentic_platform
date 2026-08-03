"""Evidence-bound Order Analysis Agent with AI Assistance."""

from __future__ import annotations

import json

from return_platform.agents.contracts import (
    AgentDecisionView,
    OrderAnalysisAssessment,
    OrderAnalysisRequest,
)
from return_platform.configuration.return_configuration import ReturnPlatformConfiguration
from return_platform.operations.models import AIDecision


class OrderAnalysisAgent:
    def __init__(self, configuration: ReturnPlatformConfiguration) -> None:
        self._root = configuration
        self._config = configuration.agents["order_analysis"]

    async def analyze(
        self, request: OrderAnalysisRequest, ai_gateway: "AIGatewayService"
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
            "candidates": [
                candidate.model_dump(mode="json") for candidate in request.candidates
            ],
            "evidence": request.suppliedEvidence,
            "conflicts": [
                anchor for c in request.candidates for anchor in c.conflictingAnchors
            ],
            "knownFacts": [
                f"Candidate {c.candidateId} source is {c.orderSource}"
                for c in request.candidates
            ],
        }

        evaluation = await ai_gateway.evaluate(
            session_id=request.sessionId,
            redacted_input=payload,
            task_id="ORDER_CANDIDATE_ANALYSIS_V1",
        )
        
        trace = evaluation.trace
        
        explanation = trace.explanation
        smart_question = None
        
        try:
            # We expect the explanation might have a smart question or just be text.
            # If the gateway is configured properly, it should return a JSON explanation.
            # Let's see if we can parse it.
            if explanation.startswith("{"):
                parsed = json.loads(explanation)
                smart_question = parsed.get("question") or parsed.get("smartQuestion")
        except Exception:
            pass
            
        if not smart_question:
            # If the AI failed to return JSON, we can assume the whole text is the question,
            # or fallback. But the prompt said "Return exactly the configured structured gateway JSON envelope"
            pass

        return OrderAnalysisAssessment(
            smartQuestion=smart_question or explanation,
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
