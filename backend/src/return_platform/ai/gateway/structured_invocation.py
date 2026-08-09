"""The one provider-agnostic structured-output execution path.

`AIGatewayService.evaluate` is the *decision* path: its parser requires exactly
`{decision, explanation, confidenceMillionths}`, so it cannot carry a richer
typed result. Everything that needs a real pydantic model back from a model call
runs through here instead -- and there is deliberately only one such place, so
routing, failover, rate limits, circuit breakers, tier escalation, safety
inspection and attempt logging are shared rather than reimplemented per caller
(design doc, "Both adapters wrap the same gateway").

What varies between callers is only the three things `invoke()` takes: the
payload to send, the response model to parse back, and the log fields that
identify the caller's unit of work. What does not vary -- and therefore lives
here -- is the retry/deadline/escalation machinery around them.

This module knows nothing about order agents or schema analysis. Callers own
their own payload construction and their own result types; adding a third caller
should not require editing this file.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from return_platform.ai.providers import ProviderError, ProviderRequest
from return_platform.ai.providers.schema_cleaner import clean_gemini_schema
from return_platform.ai.routing.routes import AIRoute
from return_platform.ai.routing.selection import AIRoutePool
from return_platform.ai.routing.tasks import (
    AIGatewayConfiguration,
    ModelTier,
    TaskConfiguration,
)
from return_platform.ai.safety.inspection import inspect_input, inspect_output
from return_platform.configuration.settings import Settings

__all__ = [
    "StructuredInvocation",
    "StructuredInvocationUnavailable",
    "StructuredOutputInvoker",
    "parse_structured_response",
]


class StructuredInvocationUnavailable(RuntimeError):
    """Raised when every candidate route failed to produce a valid response.

    Callers subclass this to keep their own domain-named exception (the Order
    Agent's `StandardReasoningUnavailable`), so an `except` clause written
    against the specific name keeps working while a handler written against this
    base catches every structured-output failure.
    """


@dataclass(frozen=True, slots=True)
class StructuredInvocation[ResponseT: BaseModel]:
    """A parsed response plus the route that actually produced it."""

    value: ResponseT
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int


class StructuredOutputInvoker[ResponseT: BaseModel]:
    """Runs one structured-output task over the shared route pool.

    One instance is bound to one configured task and one response model, so the
    tier/provider checks below happen once at construction rather than on every
    call -- a misconfigured task fails at wiring time, not on a user's request.
    """

    def __init__(
        self,
        *,
        settings: Settings,
        configuration: AIGatewayConfiguration,
        route_pool: AIRoutePool,
        task_id: str,
        response_model: type[ResponseT],
        logger: logging.Logger,
        event_prefix: str,
        subject: str,
        required_tier: ModelTier | None = ModelTier.STANDARD,
        forbid_simulator: bool = True,
        unavailable_error: type[StructuredInvocationUnavailable] = (
            StructuredInvocationUnavailable
        ),
    ) -> None:
        task = configuration.tasks.get(task_id)
        if task is None:
            raise RuntimeError(f"AI task {task_id!r} is not configured")
        if required_tier is not None and task.tier is not required_tier:
            raise RuntimeError(f"AI task {task_id!r} must use the {required_tier.value} tier")
        if forbid_simulator and "SIMULATOR" in task.allowedProviders:
            raise RuntimeError(f"AI task {task_id!r} must not permit the simulator provider")
        self._settings = settings
        self._configuration = configuration
        self._route_pool = route_pool
        self._task_id = task_id
        self._task: TaskConfiguration = task
        self._response_model = response_model
        self._logger = logger
        self._event_prefix = event_prefix
        self._subject = subject
        self._unavailable_error = unavailable_error

    @property
    def task(self) -> TaskConfiguration:
        return self._task

    async def invoke(
        self,
        *,
        payload: Mapping[str, Any],
        size_probe: str,
        log_context: Mapping[str, Any],
    ) -> StructuredInvocation[ResponseT]:
        """Send `payload`, returning the parsed `response_model`.

        `size_probe` is the caller's own measure of how large this request is --
        the text whose length decides both the payload-limit rejection and the
        rate limiter's token estimate. It is a separate argument rather than
        derived from `payload` because only the caller knows which part of its
        payload is the bulk (for a turn context, the serialised context; for a
        schema analysis, the rendered source blocks).

        `log_context` is merged into every attempt event so each log line carries
        the caller's unit-of-work identifiers.
        """
        if len(size_probe.encode("utf-8")) > self._settings.ai_max_payload_bytes:
            raise self._unavailable_error(
                f"{self._subject} input exceeds the configured payload limit"
            )

        safety = inspect_input(dict(payload))
        if not safety.allowed:
            raise self._unavailable_error(f"{self._subject} input rejected: {safety.status.value}")

        estimated_tokens = max(1, len(size_probe) // 4)
        candidates = await self._route_pool.candidates(self._task, task_id=self._task_id)
        if not candidates and not self._task.allowTierEscalation:
            raise self._unavailable_error(
                f"No healthy {self._task.tier.value} route is available for {self._task_id}"
            )

        # The response schema travels in the prompt rather than only in the
        # provider's structured-output field: not every configured provider
        # honours a native schema parameter, and the prompt copy is what makes
        # the contract identical across all of them.
        schema_str = json.dumps(clean_gemini_schema(self._response_model.model_json_schema()))
        full_prompt = (
            f"{self._task.systemPrompt}\n\n"
            "REQUIRED RESPONSE SCHEMA (Output exactly this JSON structure):\n"
            f"```json\n{schema_str}\n```"
        )
        deadline = asyncio.get_running_loop().time() + self._settings.ai_global_timeout_seconds
        attempts = 0
        last_error = "PROVIDER_UNAVAILABLE"
        failure_summary: Counter[str] = Counter()

        result, attempts, last_error = await self._attempt_routes(
            candidates,
            payload=payload,
            full_prompt=full_prompt,
            estimated_tokens=estimated_tokens,
            deadline=deadline,
            attempts=attempts,
            last_error=last_error,
            failure_summary=failure_summary,
            log_context=log_context,
        )
        if result is not None:
            return result

        if self._task.allowTierEscalation:
            lightweight_task = self._task.model_copy(update={"tier": ModelTier.LIGHTWEIGHT})
            lightweight_candidates = await self._route_pool.candidates(
                lightweight_task,
                task_id=self._task_id,
            )
            if lightweight_candidates:
                self._logger.warning(
                    f"{self._event_prefix}_tier_escalation_started",
                    extra={
                        **log_context,
                        "task_id": self._task_id,
                        "standard_attempts": attempts,
                        "standard_last_error": last_error,
                    },
                )
                result, attempts, last_error = await self._attempt_routes(
                    lightweight_candidates,
                    payload=payload,
                    full_prompt=full_prompt,
                    estimated_tokens=estimated_tokens,
                    deadline=deadline,
                    attempts=attempts,
                    last_error=last_error,
                    failure_summary=failure_summary,
                    log_context=log_context,
                )
                if result is not None:
                    return result

        summary = ",".join(
            f"{error_code}:{count}" for error_code, count in sorted(failure_summary.items())
        )
        raise self._unavailable_error(
            f"All {self._task_id} routes and lightweight fallbacks failed; "
            f"attempts={attempts}; last_error={last_error}; failures={summary or 'none'}"
        )

    async def _attempt_routes(
        self,
        candidates: tuple[AIRoute, ...],
        *,
        payload: Mapping[str, Any],
        full_prompt: str,
        estimated_tokens: int,
        deadline: float,
        attempts: int,
        last_error: str,
        failure_summary: Counter[str],
        log_context: Mapping[str, Any],
    ) -> tuple[StructuredInvocation[ResponseT] | None, int, str]:
        for route in candidates:
            for _ in range(self._configuration.retry.maximumAttemptsPerRoute):
                if attempts >= self._configuration.retry.maximumTotalAttempts:
                    return None, attempts, last_error
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    last_error = "TIMEOUT"
                    failure_summary[last_error] += 1
                    return None, attempts, last_error
                acquired = await self._route_pool.try_acquire(
                    route,
                    estimated_tokens=estimated_tokens,
                )
                if not acquired.acquired:
                    last_error = acquired.reason
                    self._logger.info(
                        f"{self._event_prefix}_model_attempt_skipped",
                        extra={
                            **log_context,
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
                self._logger.info(
                    f"{self._event_prefix}_model_attempt_started",
                    extra={
                        **log_context,
                        "task_id": self._task_id,
                        "attempt": attempts,
                        "tier": route.tier.value,
                        "provider": route.provider_name,
                        "model": route.model,
                        "credential_id": route.credential_id,
                    },
                )
                try:
                    response = await asyncio.wait_for(
                        route.provider.generate(
                            ProviderRequest(
                                system_prompt=full_prompt,
                                user_payload=dict(payload),
                                max_output_tokens=self._task.maximumOutputTokens,
                                temperature=0.0,
                                response_schema=self._response_model.model_json_schema(),
                            )
                        ),
                        timeout=min(self._settings.ai_timeout_seconds, remaining),
                    )
                    output_safety = inspect_output(response.text)
                    if not output_safety.allowed:
                        raise ProviderError("POLICY_BLOCKED")
                    value = parse_structured_response(response.text, self._response_model)
                    await self._route_pool.record_success(route)
                    self._logger.info(
                        f"{self._event_prefix}_model_attempt_succeeded",
                        extra={
                            **log_context,
                            "task_id": self._task_id,
                            "attempt": attempts,
                            "tier": route.tier.value,
                            "provider": route.provider_name,
                            "model": route.model,
                            "credential_id": route.credential_id,
                            "latency_ms": max(
                                0,
                                int((time.monotonic() - started) * 1_000),
                            ),
                        },
                    )
                    return (
                        StructuredInvocation(
                            value=value,
                            provider=route.provider_name,
                            model=route.model,
                            prompt_tokens=max(0, int(response.input_tokens or 0)),
                            completion_tokens=max(0, int(response.output_tokens or 0)),
                        ),
                        attempts,
                        last_error,
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
                    # Unexpected provider/transport failure: keep the failover loop
                    # moving to the next route, but log the full traceback so it's
                    # not silently lost.
                    last_error = "PROVIDER_UNAVAILABLE"
                    self._logger.exception(
                        f"{self._event_prefix}_model_attempt_unexpected_error",
                        extra={
                            **log_context,
                            "task_id": self._task_id,
                            "provider": route.provider_name,
                            "model": route.model,
                        },
                    )
                    await self._route_pool.record_failure(route, last_error)
                finally:
                    await self._route_pool.release(route)
                failure_summary[last_error] += 1
                self._logger.warning(
                    f"{self._event_prefix}_model_attempt_failed",
                    extra={
                        **log_context,
                        "task_id": self._task_id,
                        "attempt": attempts,
                        "tier": route.tier.value,
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
        return None, attempts, last_error


def parse_structured_response[ResponseT: BaseModel](text: str, model: type[ResponseT]) -> ResponseT:
    """Parse a model's text into `model`, tolerating fenced or wrapped JSON.

    Providers that ignore the response-schema hint still tend to return correct
    JSON inside a Markdown fence or with a sentence around it, so a brace-span
    fallback recovers a response that would otherwise be discarded as invalid.
    Raises `ValueError`/`TypeError` on failure, which the attempt loop maps to
    `RESPONSE_INVALID` and retries on the next route.
    """
    value = text.strip()
    if value.startswith("```"):
        value = value.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        payload = json.loads(value)
    except (TypeError, ValueError):
        start = value.find("{")
        end = value.rfind("}")
        if start < 0 or end <= start:
            raise
        payload = json.loads(value[start : end + 1])
    return model.model_validate(payload)
