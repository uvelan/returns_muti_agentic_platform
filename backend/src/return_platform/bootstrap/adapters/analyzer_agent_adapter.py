"""Binds the Analyzer Agent's `AgentReasoningPort` to the shared AI route pool.

Lives here, beside `analyzer_ai_adapter.py`, for the same reason and under the
same rule: this is the only layer permitted to see both an analyzer port and the
gateway's concrete world.

It adds no execution machinery. Routing, failover, rate limits, circuit
breakers, tier escalation, interception, safety inspection and attempt logging
all come from `ai/gateway/structured_invocation.py` -- the same path the schema
proposal and the Order Agent run on.

The prompt arrives pre-framed from `prompt_context.build_prompt_blocks`, with
block delimiters already neutralised. Re-framing it here would mean a second,
divergent copy of the untrusted-input rules.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from typing import Any

from return_platform.ai.gateway.interception_policy import (
    AIGatewaySettingsSource,
    build_interception_policy,
)
from return_platform.ai.gateway.structured_invocation import (
    StructuredInvocationUnavailable,
    StructuredOutputInvoker,
)
from return_platform.ai.gateway.telemetry import AIAttemptRecorder
from return_platform.ai.interception.store import InterceptionStore
from return_platform.ai.routing.selection import AIRoutePool
from return_platform.ai.routing.tasks import AIGatewayConfiguration
from return_platform.configuration.settings import Settings
from return_platform.graph_analyzer.agent_port import AgentAnswer, AgentReasoningPort

__all__ = [
    "AgentAnswerUnavailable",
    "GatewayAgentReasoningAdapter",
    "build_analyzer_agent_adapter",
]

logger = logging.getLogger("return_platform.bootstrap.analyzer_agent_adapter")

GRAPH_SCHEMA_AGENT_TASK_ID = "GRAPH_SCHEMA_AGENT_V1"


class AgentAnswerUnavailable(StructuredInvocationUnavailable):
    """Raised when every route failed to produce a usable agent answer."""


class GatewayAgentReasoningAdapter:
    """Structurally satisfies `AgentReasoningPort`."""

    def __init__(
        self,
        *,
        settings: Settings,
        configuration: AIGatewayConfiguration,
        route_pool: AIRoutePool,
        task_id: str = GRAPH_SCHEMA_AGENT_TASK_ID,
        interception_store: InterceptionStore | None = None,
        gateway_settings: AIGatewaySettingsSource | None = None,
        recorder: AIAttemptRecorder | None = None,
    ) -> None:
        self._invoker: StructuredOutputInvoker[AgentAnswer] = StructuredOutputInvoker(
            settings=settings,
            configuration=configuration,
            route_pool=route_pool,
            task_id=task_id,
            response_model=AgentAnswer,
            logger=logger,
            event_prefix="analyzer_agent_answer",
            subject="Graph schema analyzer answer",
            unavailable_error=AgentAnswerUnavailable,
            # Without this the agent's chat turns, like the proposal path, left
            # no telemetry: no attempt row, no trace, nothing in the Control
            # Center saying the call happened.
            recorder=recorder,
            # Same reasoning as the proposal path: block 5 of this prompt is
            # rows read out of a customer's database, so it is interception's
            # most sensitive payload and must not bypass it.
            interception=build_interception_policy(
                store=interception_store,
                settings_source=gateway_settings,
                subject="analyzer_agent_answer",
                settings=settings,
            ),
        )

    async def answer(
        self,
        *,
        conversation_id: str,
        prompt_blocks: Sequence[Mapping[str, Any]],
    ) -> AgentAnswer:
        blocks_json = json.dumps(
            [dict(block) for block in prompt_blocks],
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        invocation = await self._invoker.invoke(
            payload={
                "analysisId": conversation_id,
                "snapshotContentHash": conversation_id,
                "promptBlocksJson": blocks_json,
            },
            size_probe=blocks_json,
            log_context={"conversation_id": conversation_id},
        )
        answer = invocation.value
        logger.info(
            "analyzer_agent_answer_produced",
            extra={
                "conversation_id": conversation_id,
                "provider": invocation.provider,
                "model": invocation.model,
                "operation_count": len(answer.operations),
            },
        )
        return answer


def build_analyzer_agent_adapter(
    *,
    settings: Settings,
    configuration: AIGatewayConfiguration,
    route_pool: AIRoutePool,
    task_id: str = GRAPH_SCHEMA_AGENT_TASK_ID,
    interception_store: InterceptionStore | None = None,
    gateway_settings: AIGatewaySettingsSource | None = None,
    recorder: AIAttemptRecorder | None = None,
) -> AgentReasoningPort:
    """Typed factory -- the return annotation is what makes mypy prove port
    conformance, rather than a runtime isinstance that only checks method names."""
    return GatewayAgentReasoningAdapter(
        settings=settings,
        configuration=configuration,
        route_pool=route_pool,
        task_id=task_id,
        interception_store=interception_store,
        gateway_settings=gateway_settings,
        recorder=recorder,
    )
