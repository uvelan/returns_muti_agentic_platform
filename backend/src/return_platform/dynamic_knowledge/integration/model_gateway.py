"""Branch adapter from the shared AI route pool to typed Order Agent actions.

Everything provider-facing -- route selection, failover, rate limiting, tier
escalation, safety inspection, attempt logging -- lives in
`ai_gateway/structured.py` and is shared with every other structured-output
caller. What remains here is only what is genuinely Order-Agent-shaped: the
three call modes, how a turn context becomes a payload, and mapping the parsed
`AgentAction` onto `ModelInvocationResult`.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from return_platform.ai.gateway.interception_policy import (
    AIGatewaySettingsSource,
    build_interception_policy,
)
from return_platform.ai.gateway.structured_invocation import (
    StructuredInvocationUnavailable,
    StructuredOutputInvoker,
)
from return_platform.ai.interception.store import InterceptionStore
from return_platform.ai.gateway.telemetry import AIAttemptRecorder, InvocationCorrelation
from return_platform.ai.routing.selection import AIRoutePool
from return_platform.ai.routing.tasks import AIGatewayConfiguration
from return_platform.configuration.settings import Settings
from return_platform.dynamic_knowledge.knowledge.evidence import StructuredAgentResponse
from return_platform.dynamic_knowledge.order_agent.contracts import (
    AgentAction,
    AgentTurnContext,
    ModelInvocationResult,
)
from return_platform.dynamic_knowledge.order_agent.temporal_grounding import (
    temporal_grounding_prompt,
)

logger = logging.getLogger("return_platform.dynamic_knowledge.model_gateway")


class StandardReasoningUnavailable(StructuredInvocationUnavailable):
    """Raised when every configured standard-reasoning route fails.

    Subclasses the shared error so callers already catching this specific name
    keep working, while a handler written against the shared base also sees it.
    """


class RoutePoolReasoningModelGateway:
    """Invoke only STANDARD routes and require a strict ``AgentAction`` response."""

    def __init__(
        self,
        *,
        settings: Settings,
        configuration: AIGatewayConfiguration,
        route_pool: AIRoutePool,
        task_id: str = "ORDER_AGENT_REASONING_V1",
        recorder: AIAttemptRecorder | None = None,
        # AI-01. This is the path the audit found bypassing interception, and
        # the reason it did was that nothing here ever mentioned it. A store and
        # a settings source produce a real gate; their absence produces a logged
        # `ALLOW_ALL` rather than a silent one.
        interception_store: InterceptionStore | None = None,
        gateway_settings: AIGatewaySettingsSource | None = None,
    ) -> None:
        self._invoker: StructuredOutputInvoker[AgentAction] = StructuredOutputInvoker(
            settings=settings,
            configuration=configuration,
            route_pool=route_pool,
            task_id=task_id,
            response_model=AgentAction,
            logger=logger,
            event_prefix="order_agent",
            subject="Order Agent",
            unavailable_error=StandardReasoningUnavailable,
            recorder=recorder,
            interception=build_interception_policy(
                store=interception_store,
                settings_source=gateway_settings,
                subject="order_agent_reasoning",
            ),
        )
        self._settings = settings
        self._task_id = task_id

    async def decide(self, context: AgentTurnContext) -> ModelInvocationResult:
        return await self._invoke(mode="DECIDE", context=context)

    async def correct_action(
        self,
        *,
        context: AgentTurnContext,
        invalid_action: AgentAction | None,
        validation_error: str,
    ) -> ModelInvocationResult:
        return await self._invoke(
            mode="CORRECT_ACTION",
            context=context,
            invalid_action=invalid_action,
            validation_error=validation_error,
        )

    async def correct_response(
        self,
        *,
        context: AgentTurnContext,
        invalid_response: StructuredAgentResponse,
        validation_error: str,
    ) -> ModelInvocationResult:
        return await self._invoke(
            mode="CORRECT_RESPONSE",
            context=context,
            invalid_response=invalid_response,
            validation_error=validation_error,
        )

    async def _invoke(
        self,
        *,
        mode: str,
        context: AgentTurnContext,
        invalid_action: AgentAction | None = None,
        invalid_response: StructuredAgentResponse | None = None,
        validation_error: str | None = None,
    ) -> ModelInvocationResult:
        context_json = json.dumps(
            context.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        payload: dict[str, Any] = {
            "mode": mode,
            "contextJson": context_json,
            "invalidActionJson": (
                json.dumps(
                    invalid_action.model_dump(mode="json"),
                    separators=(",", ":"),
                    sort_keys=True,
                )
                if invalid_action is not None
                else ""
            ),
            "invalidResponseJson": (
                json.dumps(
                    invalid_response.model_dump(mode="json"),
                    separators=(",", ":"),
                    sort_keys=True,
                )
                if invalid_response is not None
                else ""
            ),
            "validationError": (validation_error or "")[:2_000],
        }
        invocation = await self._invoker.invoke(
            payload=payload,
            size_probe=context_json,
            log_context={
                "conversation_id": context.conversation_id,
                "client_turn_id": context.client_turn_id,
                "mode": mode,
            },
            # The turn's as-of has to be *stated*, not merely present somewhere
            # inside `contextJson`. A model that has to find the date in a
            # sorted JSON blob to know what "yesterday" means will sometimes not
            # look, and the packaged task prompt cannot carry it because it is
            # one immutable string per configuration release and this changes
            # every turn.
            prompt_addendum=temporal_grounding_prompt(context.as_of, context.session_timezone),
            # W4.12. The business dimension the metrics were blind on. All five
            # are platform-issued identifiers -- nothing the associate typed and
            # nothing retrieved from the graph travels with them.
            correlation=InvocationCorrelation(
                correlation_id=context.correlation_id,
                case_id=context.case_id,
                conversation_id=context.conversation_id,
                agent_id=context.agent_id,
            ),
        )
        return ModelInvocationResult(
            action=invocation.value,
            provider=invocation.provider,
            model=invocation.model,
            prompt_tokens=invocation.prompt_tokens,
            completion_tokens=invocation.completion_tokens,
        )
