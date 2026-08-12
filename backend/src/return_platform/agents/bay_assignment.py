"""Deterministic, evidence-bound Bay Assignment Agent."""

from __future__ import annotations

from return_platform.agents.contracts import (
    AgentDecisionView,
    AgentExecutionContext,
    BayAssessment,
    BayAssessmentRequest,
)
from return_platform.agents.contracts.descriptor import AgentDescriptor
from return_platform.configuration.return_configuration import ReturnPlatformConfiguration


def _confidence_millionths(scored: list[tuple[int, str]], required_capacity: int) -> int:
    """How clear the winning bay was, in millionths.

    Zero when nothing is eligible -- there is no recommendation to be confident
    about. Certain when exactly one bay qualifies, because no other candidate
    could have been preferred. Otherwise the margin between the winner's spare
    capacity and the runner-up's, scaled by what the return actually needs: a
    two-pallet edge means a great deal for a two-pallet return and very little
    for a fifty-pallet one.
    """
    if not scored:
        return 0
    if len(scored) == 1:
        return 1_000_000
    margin = scored[1][0] - scored[0][0]
    # `scored` holds spare capacity after the return, ascending, so the winner
    # is the tightest fit and the margin is how much slacker the next one is.
    scale = max(1, required_capacity)
    return 500_000 + min(500_000, round(500_000 * min(margin, scale) / scale))


class BayAssignmentAgent:
    """This agent does not directly invoke another agent."""

    def __init__(self, configuration: ReturnPlatformConfiguration) -> None:
        self._root = configuration
        self._config = configuration.agents["bay_assignment"]

    @property
    def descriptor(self) -> AgentDescriptor:
        return AgentDescriptor.from_configuration("bay_assignment", self._config)

    async def execute(
        self, request: BayAssessmentRequest, context: AgentExecutionContext
    ) -> BayAssessment:
        del context
        return self.assess(request)

    def assess(self, request: BayAssessmentRequest) -> BayAssessment:
        if (
            self._root.bay.require_physical_receipt
            and request.physicalStatus not in self._root.bay.eligible_statuses
        ):
            return BayAssessment(
                recommendedBayId=None,
                eligibleBayIds=(),
                excludedReasons={"*": ("PHYSICAL_RECEIPT_REQUIRED",)},
                decision=AgentDecisionView(
                    agent=self._config.name,
                    agentVersion=self._config.version,
                    configurationVersion=self._root.assumption_set_version,
                    decisionType="BAY_RECOMMENDATION",
                    decision="NOT_READY",
                    explanation=(
                        "A warehouse bay cannot be recommended before the configured "
                        "readiness gate."
                    ),
                    confidenceMillionths=1_000_000,
                    evidenceReferences=(f"PHYSICAL_STATUS:{request.physicalStatus}",),
                ),
            )
        eligible: list[str] = []
        excluded: dict[str, tuple[str, ...]] = {}
        scored: list[tuple[int, str]] = []
        for bay in request.candidates:
            reasons: list[str] = []
            if not bay.active:
                reasons.append("INACTIVE")
            if bay.capacityAvailable < request.requiredCapacity:
                reasons.append("INSUFFICIENT_CAPACITY")
            if request.oversized and not bay.supportsOversized:
                reasons.append("OVERSIZED_NOT_SUPPORTED")
            if request.hazardous and not bay.supportsHazardous:
                reasons.append("HAZARDOUS_NOT_SUPPORTED")
            if (
                bay.supportedReturnMethods
                and request.returnMethod not in bay.supportedReturnMethods
            ):
                reasons.append("RETURN_METHOD_NOT_SUPPORTED")
            if reasons:
                excluded[bay.bayId] = tuple(reasons)
                continue
            eligible.append(bay.bayId)
            scored.append((bay.capacityAvailable - request.requiredCapacity, bay.bayId))
        scored.sort(key=lambda item: (item[0], item[1]))
        recommended = scored[0][1] if scored else None
        # Confidence from the margin over the runner-up, not a literal.
        #
        # This was `950_000 if recommended else 800_000` -- two constants
        # labelled "confidence" that said nothing about the decision and could
        # not be wrong, which makes them worse than no confidence at all. The
        # real signal is how clear the winner was: a bay with far more headroom
        # than the next candidate is a confident pick, two near-identical bays
        # are a coin toss, and a sole candidate is certain because there was
        # nothing to weigh it against.
        confidence = _confidence_millionths(scored, request.requiredCapacity)
        return BayAssessment(
            recommendedBayId=recommended,
            eligibleBayIds=tuple(eligible),
            excludedReasons=excluded,
            decision=AgentDecisionView(
                agent=self._config.name,
                agentVersion=self._config.version,
                configurationVersion=self._root.assumption_set_version,
                decisionType="BAY_RECOMMENDATION",
                decision="RECOMMENDED" if recommended else "NO_ELIGIBLE_BAY",
                explanation=(
                    "The recommendation is advisory; atomic capacity reservation "
                    "remains deterministic."
                ),
                confidenceMillionths=confidence,
                evidenceReferences=(f"PHYSICAL_STATUS:{request.physicalStatus}",),
            ),
        )
