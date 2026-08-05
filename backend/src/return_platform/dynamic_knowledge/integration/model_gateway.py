"""Branch adapter from the shared AI route pool to typed Order Agent actions."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import Counter
from typing import Any

from return_platform.ai_gateway.configuration import AIGatewayConfiguration, ModelTier
from return_platform.ai_gateway.providers import ProviderError, ProviderRequest
from return_platform.ai_gateway.routing import AIRoutePool
from return_platform.ai_gateway.safety import inspect_input, inspect_output
from return_platform.configuration.settings import Settings
from return_platform.dynamic_knowledge.knowledge.evidence import StructuredAgentResponse
from return_platform.dynamic_knowledge.order_agent.contracts import (
    AgentAction,
    AgentTurnContext,
    ModelInvocationResult,
)

logger = logging.getLogger("return_platform.dynamic_knowledge.model_gateway")


class StandardReasoningUnavailable(RuntimeError):
    """Raised when every configured standard-reasoning route fails."""


class RoutePoolReasoningModelGateway:
    """Invoke only STANDARD routes and require a strict ``AgentAction`` response."""

    def __init__(
        self,
        *,
        settings: Settings,
        configuration: AIGatewayConfiguration,
        route_pool: AIRoutePool,
        task_id: str = "ORDER_AGENT_REASONING_V1",
    ) -> None:
        task = configuration.tasks.get(task_id)
        if task is None:
            raise RuntimeError(f"AI task {task_id!r} is not configured")
        if task.tier is not ModelTier.STANDARD:
            raise RuntimeError("Order Agent reasoning task must use the STANDARD tier")
        if "SIMULATOR" in task.allowedProviders:
            raise RuntimeError("Order Agent reasoning must not permit the simulator provider")
        self._settings = settings
        self._configuration = configuration
        self._route_pool = route_pool
        self._task_id = task_id
        self._task = task

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
        if len(context_json.encode("utf-8")) > self._settings.ai_max_payload_bytes:
            raise StandardReasoningUnavailable(
                "Order Agent context exceeds the configured payload limit"
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
        safety = inspect_input(payload)
        if not safety.allowed:
            raise StandardReasoningUnavailable(
                f"Order Agent input rejected: {safety.status.value}"
            )
        estimated_tokens = max(1, len(context_json) // 4)
        candidates = await self._route_pool.candidates(
            self._task,
            task_id=self._task_id,
        )
        if not candidates:
            raise StandardReasoningUnavailable(
                "No healthy standard-reasoning route is available"
            )
        attempts = 0
        last_error = "PROVIDER_UNAVAILABLE"
        failure_summary: Counter[str] = Counter()
        deadline = (
            asyncio.get_running_loop().time()
            + self._settings.ai_global_timeout_seconds
        )
        for route in candidates:
            if route.tier is not ModelTier.STANDARD:
                continue
            for _ in range(self._configuration.retry.maximumAttemptsPerRoute):
                if attempts >= self._configuration.retry.maximumTotalAttempts:
                    break
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    last_error = "TIMEOUT"
                    failure_summary[last_error] += 1
                    break
                acquired = await self._route_pool.try_acquire(
                    route,
                    estimated_tokens=estimated_tokens,
                )
                if not acquired.acquired:
                    last_error = acquired.reason
                    logger.info(
                        "order_agent_model_attempt_skipped",
                        extra={
                            "conversation_id": context.conversation_id,
                            "client_turn_id": context.client_turn_id,
                            "task_id": self._task_id,
                            "provider": route.provider_name,
                            "model": route.model,
                            "credential_id": route.credential_id,
                            "reason": acquired.reason,
                        },
                    )
                    continue
                attempts += 1
                started = time.monotonic()
                logger.info(
                    "order_agent_model_attempt_started",
                    extra={
                        "conversation_id": context.conversation_id,
                        "client_turn_id": context.client_turn_id,
                        "task_id": self._task_id,
                        "mode": mode,
                        "attempt": attempts,
                        "provider": route.provider_name,
                        "model": route.model,
                        "credential_id": route.credential_id,
                    },
                )
                try:
                    response = await asyncio.wait_for(
                        route.provider.generate(
                            ProviderRequest(
                                self._task.systemPrompt,
                                payload,
                                max_output_tokens=self._task.maximumOutputTokens,
                                temperature=0.0,
                            )
                        ),
                        timeout=min(self._settings.ai_timeout_seconds, remaining),
                    )
                    output_safety = inspect_output(response.text)
                    if not output_safety.allowed:
                        raise ProviderError("POLICY_BLOCKED")
                    action = _parse_agent_action(response.text)
                    await self._route_pool.record_success(route)
                    logger.info(
                        "order_agent_model_attempt_succeeded",
                        extra={
                            "conversation_id": context.conversation_id,
                            "client_turn_id": context.client_turn_id,
                            "task_id": self._task_id,
                            "attempt": attempts,
                            "provider": route.provider_name,
                            "model": route.model,
                            "credential_id": route.credential_id,
                            "latency_ms": max(
                                0,
                                int((time.monotonic() - started) * 1_000),
                            ),
                        },
                    )
                    return ModelInvocationResult(
                        action=action,
                        provider=route.provider_name,
                        model=route.model,
                        prompt_tokens=max(0, int(response.input_tokens or 0)),
                        completion_tokens=max(0, int(response.output_tokens or 0)),
                    )
                except TimeoutError:
                    last_error = "TIMEOUT"
                    await self._route_pool.record_failure(route, last_error)
                except ProviderError as exc:
                    last_error = exc.code
                    await self._route_pool.record_failure(route, last_error)
                except (ValueError, TypeError):
                    last_error = "RESPONSE_INVALID"
                    await self._route_pool.record_failure(route, last_error)
                except Exception:
                    last_error = "PROVIDER_UNAVAILABLE"
                    await self._route_pool.record_failure(route, last_error)
                finally:
                    await self._route_pool.release(route)
                failure_summary[last_error] += 1
                logger.warning(
                    "order_agent_model_attempt_failed",
                    extra={
                        "conversation_id": context.conversation_id,
                        "client_turn_id": context.client_turn_id,
                        "task_id": self._task_id,
                        "mode": mode,
                        "attempt": attempts,
                        "provider": route.provider_name,
                        "model": route.model,
                        "credential_id": route.credential_id,
                        "error_code": last_error,
                        "latency_ms": max(
                            0,
                            int((time.monotonic() - started) * 1_000),
                        ),
                    },
                )
        summary = ",".join(
            f"{error_code}:{count}"
            for error_code, count in sorted(failure_summary.items())
        )
        raise StandardReasoningUnavailable(
            "All standard-reasoning routes failed; "
            f"attempts={attempts}; last_error={last_error}; failures={summary or 'none'}"
        )


def _parse_agent_action(text: str) -> AgentAction:
    value = text.strip()
    if value.startswith("```"):
        value = (
            value.removeprefix("```json")
            .removeprefix("```")
            .removesuffix("```")
            .strip()
        )
    try:
        payload = json.loads(value)
    except (TypeError, ValueError):
        start = value.find("{")
        end = value.rfind("}")
        if start < 0 or end <= start:
            raise
        payload = json.loads(value[start : end + 1])
    return AgentAction.model_validate(payload)
