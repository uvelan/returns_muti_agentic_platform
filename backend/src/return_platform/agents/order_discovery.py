"""Evidence-bound Order Discovery Agent."""

from __future__ import annotations

import re

from return_platform.agents.contracts import (
    AgentDecisionView,
    DiscoveryAssessment,
    DiscoveryAssessmentRequest,
    OrderSource,
    RankedDiscoveryCandidate,
)
from return_platform.configuration.return_configuration import ReturnPlatformConfiguration


class OrderDiscoveryAgent:
    def __init__(self, configuration: ReturnPlatformConfiguration) -> None:
        self._root = configuration
        self._config = configuration.agents["order_discovery"]
        self._web_order = re.compile(configuration.discovery.web_order_pattern)

    def _source(self, evidence: dict[str, str]) -> OrderSource:
        order = evidence.get("order_number", "").strip().upper()
        source = evidence.get("source", "").strip().upper()
        if order and self._web_order.fullmatch(order):
            return OrderSource.FERGUSONHOME_WEB
        if source in {"WEB", "FERGUSONHOME", "FERGUSONHOME_WEB"}:
            return OrderSource.FERGUSONHOME_WEB
        if source in {"SHOWROOM", "FERGUSON_SHOWROOM"}:
            return OrderSource.FERGUSON_SHOWROOM
        if source in {"BRANCH", "FERGUSON_BRANCH"}:
            return OrderSource.FERGUSON_BRANCH
        if source in {"OTHER_DIGITAL_BRAND", "PED", "ACWHOLESALERS", "SUPPLY"}:
            return OrderSource.OTHER_DIGITAL_BRAND
        return OrderSource.UNKNOWN

    def assess(self, request: DiscoveryAssessmentRequest) -> DiscoveryAssessment:
        ranked: list[RankedDiscoveryCandidate] = []
        for candidate in request.candidates:
            score = 0
            for anchor in candidate.matchedAnchors:
                score += self._root.discovery.anchor_weights.get(anchor.upper(), 0)
            score -= (
                len(candidate.conflictingAnchors) * self._root.discovery.conflict_penalty_millionths
            )
            score = max(0, min(1_000_000, score))
            ranked.append(
                RankedDiscoveryCandidate(
                    candidateId=candidate.candidateId,
                    scoreMillionths=score,
                    explanation=(
                        f"Matched {len(candidate.matchedAnchors)} evidence anchor(s); "
                        f"{len(candidate.conflictingAnchors)} conflict(s)."
                    ),
                    evidenceReferences=candidate.evidenceReferences,
                )
            )
        ranked.sort(key=lambda item: (-item.scoreMillionths, item.candidateId))
        gap = (
            ranked[0].scoreMillionths - ranked[1].scoreMillionths if len(ranked) > 1 else 1_000_000
        )
        ambiguous = not ranked or (
            len(ranked) > 1 and gap < self._root.discovery.ambiguity_gap_millionths
        )
        supplied = {key for key, value in request.suppliedEvidence.items() if value.strip()}
        question = next(
            (
                f"Could you provide the {item.label}?"
                for item in sorted(
                    self._root.clarification_policy.fields,
                    key=lambda value: -value.priority,
                )
                if item.customer_answerable and item.field not in supplied
            ),
            None,
        )
        refs = tuple(
            dict.fromkeys(ref for candidate in ranked[:3] for ref in candidate.evidenceReferences)
        ) or ("DISCOVERY:NO_CANDIDATE",)
        return DiscoveryAssessment(
            orderSource=self._source(request.suppliedEvidence),
            rankedCandidates=tuple(ranked),
            confirmationRequired=True,
            ambiguous=ambiguous,
            nextQuestion=question if ambiguous or not ranked else None,
            decision=AgentDecisionView(
                agent=self._config.name,
                agentVersion=self._config.version,
                configurationVersion=self._root.assumption_set_version,
                decisionType="ORDER_DISCOVERY_ASSESSMENT",
                decision="AMBIGUOUS" if ambiguous else "CANDIDATES_RANKED",
                explanation=(
                    "Human confirmation is required; the agent only ranks source-backed candidates."
                ),
                confidenceMillionths=ranked[0].scoreMillionths if ranked else 0,
                evidenceReferences=refs,
                warnings=("AUTO_CONFIRMATION_DISABLED",),
            ),
        )
