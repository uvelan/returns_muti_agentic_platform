"""Branch adapter from the shared AI route pool to typed Order Agent actions.

Everything provider-facing -- route selection, failover, rate limiting, tier
escalation, safety inspection, attempt logging -- lives in
`ai_gateway/structured.py` and is shared with every other structured-output
caller. What remains here is only what is genuinely Order-Agent-shaped: the
three call modes, how a turn context becomes a payload, which stage prompt the
turn gets, and mapping the parsed `AgentAction` onto `ModelInvocationResult`.

**One gateway, several prompts.** The reasoning prompt reached 17,109 characters
and adherence at that size is visibly poor -- see `order_agent/reasoning_stage.py`
for the measurement and the reasoning. A turn now runs against the prompt for
the stage its own state says it is in, which for most turns is a good deal
smaller. That is a routing decision and it lives here, at the seam between the
turn and the gateway, because it is the only place that holds both the
`AgentTurnContext` and the invokers.

**The base task is never optional.** `ORDER_AGENT_REASONING_V1` carries every
rule and is what a turn falls back to whenever its stage's task cannot be
served -- absent from the active release, or bound away from every route. A
deployment that has never published the stage tasks therefore behaves exactly as
it did before they existed, which is the property that lets this ship while
`runtime-configuration-init` still publishes with `--if-missing`.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from return_platform.ai.gateway.final_dispatch import FinalDispatcher
from return_platform.ai.gateway.interception_policy import (
    AIGatewaySettingsSource,
    build_interception_policy,
)
from return_platform.ai.gateway.structured_invocation import (
    StructuredInvocationUnavailable,
    StructuredOutputInvoker,
)
from return_platform.ai.gateway.telemetry import AIAttemptRecorder, InvocationCorrelation
from return_platform.ai.interception.store import InterceptionStore
from return_platform.ai.routing.selection import AIRoutePool
from return_platform.ai.routing.tasks import AIGatewayConfiguration
from return_platform.configuration.settings import Settings
from return_platform.dynamic_knowledge.knowledge.evidence import StructuredAgentResponse
from return_platform.dynamic_knowledge.order_agent.contracts import (
    AgentAction,
    AgentTurnContext,
    ModelInvocationResult,
)
from return_platform.dynamic_knowledge.order_agent.reasoning_stage import (
    STAGE_TASK_IDS,
    ReasoningStage,
    reasoning_stage,
    stage_task_id,
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
        interception = build_interception_policy(
            store=interception_store,
            settings_source=gateway_settings,
            # One subject for every stage. Interception is a rule about *this
            # traffic* -- the reasoning calls that carry the most customer data
            # -- and a per-stage subject would let an operator gate the opening
            # turn and silently miss the four stages that follow it.
            subject="order_agent_reasoning",
            settings=settings,
        )

        def build(
            for_task: str, dispatcher: FinalDispatcher | None
        ) -> StructuredOutputInvoker[AgentAction]:
            return StructuredOutputInvoker(
                settings=settings,
                configuration=configuration,
                route_pool=route_pool,
                task_id=for_task,
                response_model=AgentAction,
                logger=logger,
                event_prefix="order_agent",
                subject="Order Agent",
                unavailable_error=StandardReasoningUnavailable,
                recorder=recorder,
                interception=interception,
                dispatcher=dispatcher,
            )

        self._invoker: StructuredOutputInvoker[AgentAction] = build(task_id, None)
        self._settings = settings
        self._task_id = task_id
        self._route_pool = route_pool
        # Built only for stage tasks the *startup* configuration actually has.
        # A task added by a release activated later falls back until a restart,
        # which is safe by construction: the fallback is the complete prompt.
        #
        # Every stage shares the base invoker's dispatcher, so one process keeps
        # one boundary: circuit state, rate limiting and the active-release view
        # belong to the route pool and must not fork per stage. A stage is a
        # different prompt, not a different provider world.
        self._stage_invokers: dict[str, StructuredOutputInvoker[AgentAction]] = {
            stage_task: build(stage_task, self._invoker.dispatcher)
            for stage_task in STAGE_TASK_IDS.values()
            if stage_task != task_id and stage_task in configuration.tasks
        }
        logger.info(
            "order_agent_stage_prompts_bound",
            extra={
                "base_task_id": task_id,
                "stage_task_ids": sorted(self._stage_invokers),
                "missing_stage_task_ids": sorted(
                    set(STAGE_TASK_IDS.values()) - set(self._stage_invokers) - {task_id}
                ),
            },
        )

    def _servable(self, stage_task: str) -> bool:
        """Whether this release and these routes can actually serve `stage_task`.

        Two structural reasons a stage task cannot be served, both checked here
        rather than discovered as a failed turn:

        The task may be absent from the active release. `runtime-configuration-
        init` in compose.yaml still publishes with `--if-missing`, so a container
        deployment can be running a release cut before these task ids existed.
        The task is re-resolved per call for the same reason `StructuredOutput-
        Invoker.task` is a property: a release activated mid-process must not
        leave a stale answer behind.

        And no route may be allowed to serve it. `AIRoute.allowed_task_keys` is
        built from the operator's live-validation receipts
        (`runtime_integrations.ai_providers[].validated_routes[].task_key`), and
        a deployment that has validated its routes against
        `ORDER_AGENT_REASONING_V1` alone binds them to that id -- so a new task
        id would find no candidates and every turn would fail. An empty
        `allowed_task_keys` means the route is unrestricted, which is the
        packaged configuration's state and why this is invisible in tests that
        do not set it.

        Read without the pool's lock on purpose: `routes` is a tuple replaced
        atomically by `replace_routes`, so this sees one consistent generation
        and costs nothing per turn.
        """
        task = self._invoker.dispatcher.task(stage_task)
        if task is None:
            return False
        return any(
            not route.allowed_task_keys or stage_task in route.allowed_task_keys
            for route in self._route_pool.routes
            if route.tier is task.tier and route.provider_name in task.allowedProviders
        )

    def _select(
        self, context: AgentTurnContext
    ) -> tuple[str, ReasoningStage | None, StructuredOutputInvoker[AgentAction]]:
        """The prompt this turn gets: its stage's, or the complete one."""
        stage = reasoning_stage(
            case_id=context.case_id, conversation_state=dict(context.conversation_state)
        )
        candidate = stage_task_id(stage)
        invoker = self._stage_invokers.get(candidate)
        if invoker is not None and self._servable(candidate):
            return candidate, stage, invoker
        return self._task_id, stage, self._invoker

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
        # Chosen per invocation, not per turn, and the correction modes take the
        # same stage prompt the decision did. A correction has to repair an
        # action produced under a particular set of rules, so handing it a
        # different set is asking it to fix an answer against a standard the
        # answer was never held to.
        task_id, stage, invoker = self._select(context)
        invocation = await invoker.invoke(
            payload=payload,
            size_probe=context_json,
            log_context={
                "conversation_id": context.conversation_id,
                "client_turn_id": context.client_turn_id,
                "mode": mode,
                "reasoning_stage": stage.value if stage is not None else "UNCLASSIFIED",
                "stage_task_id": task_id,
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
