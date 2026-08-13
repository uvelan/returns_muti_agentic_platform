"""Task-bound, observable, rate-limited, provider/model/key failover AI Gateway."""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from return_platform.ai.gateway.redaction import redact_payload
from return_platform.ai.gateway.telemetry import AIAttemptRecord, InvocationCorrelation
from return_platform.ai.pricing import AICostEstimate
from return_platform.ai.providers import ProviderError, ProviderRequest
from return_platform.ai.routing.routes import AIRoute, build_routes
from return_platform.ai.routing.selection import AIRoutePool
from return_platform.ai.routing.tasks import (
    AIGatewayConfiguration,
    LoadedAIGatewayConfiguration,
    ModelTier,
    TaskConfiguration,
    load_ai_gateway_configuration,
)
from return_platform.ai.safety import (
    SafetyInspection,
    inspect_input,
    inspect_output,
)
from return_platform.configuration.settings import Settings
from return_platform.operations.models import AIDecision, AIRequestStatus, AITraceView

_SENSITIVE_KEY_FRAGMENTS = (
    "name",
    "email",
    "phone",
    "address",
    "password",
    "secret",
    "token",
    "ssn",
    "aadhaar",
    "pan",
    "card",
    "cvv",
)


class AIGatewayRepository(Protocol):
    async def create_ai_trace(self, **kwargs: Any) -> AITraceView: ...
    async def update_ai_trace(
        self, trace_id: str, updates: dict[str, Any], *, expected_version: int | None = None
    ) -> AITraceView: ...
    async def get_ai_settings(self) -> Any: ...
    async def consume_ai_quota(self, bucket: str) -> bool: ...
    async def insert_ai_attempt_metric(self, document: dict[str, Any]) -> Any: ...


@dataclass(frozen=True, slots=True)
class GatewayEvaluation:
    trace: AITraceView
    pending_interception: bool


def _priced_trace_fields(cost: AICostEstimate) -> dict[str, Any]:
    """One estimate, spread across the trace's four pricing columns.

    Written once rather than at each update site so a future column cannot be
    added to the record and forgotten at one of them, which is how the trace and
    its own attempt rows would come to disagree about what a call cost.
    """
    return {
        "estimatedCostMicros": cost.amount_micros,
        "pricingCurrency": cost.currency,
        "pricingStatus": cost.status.value,
        "pricingVersion": cost.pricing_version,
    }


class _PayloadPolicyError(ValueError):
    def __init__(self, code: AIRequestStatus, message: str) -> None:
        self.code = code
        super().__init__(message)


class AIGatewayService:
    def __init__(
        self,
        repository: AIGatewayRepository,
        settings: Settings,
        *,
        loaded_configuration: LoadedAIGatewayConfiguration | None = None,
        route_pool: AIRoutePool | None = None,
    ) -> None:
        self._repository = repository
        self._settings = settings
        if loaded_configuration is None:
            if settings.environment not in {"development", "test"}:
                raise RuntimeError(
                    "Production AI gateway configuration must come from the active graph release"
                )
            loaded_configuration = load_ai_gateway_configuration(
                settings.ai_gateway_configuration_path
            )
        self._loaded_configuration = loaded_configuration
        self._configuration: AIGatewayConfiguration = self._loaded_configuration.configuration
        self._route_pool = route_pool or AIRoutePool(build_routes(settings), self._configuration)

    @property
    def route_pool(self) -> AIRoutePool:
        return self._route_pool

    @property
    def configuration(self) -> AIGatewayConfiguration:
        return self._configuration

    @staticmethod
    def _digest(system_prompt: str, payload: dict[str, Any]) -> str:
        canonical = json.dumps(
            {"systemPrompt": system_prompt, "payload": payload},
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _validate_scalar(value: Any, *, depth: int = 0) -> Any:
        if depth > 4:
            raise _PayloadPolicyError(
                AIRequestStatus.POLICY_BLOCKED, "AI input nesting exceeds policy limit."
            )
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            normalized = value.strip()
            if len(normalized) > 2_000:
                raise _PayloadPolicyError(
                    AIRequestStatus.POLICY_BLOCKED, "AI input string exceeds policy limit."
                )
            return normalized
        if isinstance(value, list):
            if len(value) > 100:
                raise _PayloadPolicyError(
                    AIRequestStatus.POLICY_BLOCKED, "AI input list exceeds policy limit."
                )
            return [AIGatewayService._validate_scalar(item, depth=depth + 1) for item in value]
        if isinstance(value, dict):
            if len(value) > 100:
                raise _PayloadPolicyError(
                    AIRequestStatus.POLICY_BLOCKED, "AI input object exceeds policy limit."
                )
            result: dict[str, Any] = {}
            for raw_key, nested in value.items():
                if not isinstance(raw_key, str):
                    raise _PayloadPolicyError(
                        AIRequestStatus.POLICY_BLOCKED, "AI input keys must be strings."
                    )
                normalized_key = raw_key.lower().replace("_", "").replace("-", "")
                if any(fragment in normalized_key for fragment in _SENSITIVE_KEY_FRAGMENTS):
                    raise _PayloadPolicyError(
                        AIRequestStatus.REDACTION_FAILED,
                        "Sensitive field was blocked by redaction policy.",
                    )
                result[raw_key] = AIGatewayService._validate_scalar(nested, depth=depth + 1)
            return result
        raise _PayloadPolicyError(
            AIRequestStatus.POLICY_BLOCKED, "AI input contains an unsupported value type."
        )

    def _redact_and_validate(
        self,
        payload: dict[str, Any],
        task: TaskConfiguration,
    ) -> dict[str, Any]:
        unknown = sorted(set(payload) - set(task.allowedInputKeys))
        if unknown:
            raise _PayloadPolicyError(
                AIRequestStatus.POLICY_BLOCKED,
                f"AI input contains fields outside the task allowlist: {', '.join(unknown)}.",
            )
        sanitized = {key: self._validate_scalar(value) for key, value in payload.items()}
        encoded = json.dumps(sanitized, separators=(",", ":"), sort_keys=True).encode("utf-8")
        if len(encoded) > self._settings.ai_max_payload_bytes:
            raise _PayloadPolicyError(
                AIRequestStatus.POLICY_BLOCKED, "AI input exceeds the maximum payload size."
            )
        estimated_tokens = max(1, len(encoded) // 4)
        if estimated_tokens > task.maximumInputTokens:
            raise _PayloadPolicyError(
                AIRequestStatus.POLICY_BLOCKED, "AI input exceeds the task token budget."
            )
        return sanitized

    @staticmethod
    def _trace_status_for_error(error_code: str) -> AIRequestStatus:
        try:
            return AIRequestStatus(error_code)
        except ValueError:
            return AIRequestStatus.PROVIDER_UNAVAILABLE

    @staticmethod
    def _parse_response(text: str) -> tuple[AIDecision, str, int]:
        stripped = text.strip()
        if stripped.startswith("```"):
            stripped = (
                stripped.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            )
        try:
            data = json.loads(stripped)
        except (ValueError, TypeError) as error:
            raise ProviderError("RESPONSE_INVALID") from error
        if not isinstance(data, dict) or set(data) != {
            "decision",
            "explanation",
            "confidenceMillionths",
        }:
            raise ProviderError("RESPONSE_INVALID")
        try:
            decision = AIDecision(data["decision"])
        except (ValueError, TypeError) as error:
            raise ProviderError("RESPONSE_INVALID") from error
        explanation = data["explanation"]
        confidence = data["confidenceMillionths"]
        if not isinstance(explanation, str) or not 1 <= len(explanation.strip()) <= 1_024:
            raise ProviderError("RESPONSE_INVALID")
        if not isinstance(confidence, int) or not 0 <= confidence <= 1_000_000:
            raise ProviderError("RESPONSE_INVALID")
        output_safety = inspect_output(explanation)
        if not output_safety.allowed:
            raise ProviderError("POLICY_BLOCKED")
        return decision, explanation.strip(), confidence

    async def _attempt_metric(
        self,
        *,
        trace_id: str,
        session_id: str | None,
        task_id: str,
        configured_tier: ModelTier,
        selected_tier: ModelTier | None,
        route: AIRoute | None,
        attempt_number: int,
        selection_reason: str,
        status: str,
        fallback_used: bool,
        safety: SafetyInspection,
        latency_ms: int,
        rate_limit_wait_ms: int,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        request_digest: str,
        response_digest: str | None,
        error_code: str | None,
        cached_input_tokens: int | None = None,
        fallback_reason: str | None = None,
        prompt_version: str = "",
    ) -> None:
        method = getattr(self._repository, "insert_ai_attempt_metric", None)
        if not callable(method):
            return
        # Priced here, at the moment of the attempt, against the release's own
        # catalog -- not derived when a dashboard is opened. A cost recomputed
        # at read time silently rewrites every historical figure the day a
        # vendor changes a rate.
        cost = self._configuration.pricing.estimate(
            provider=route.provider_name if route else None,
            model=route.model if route else None,
            at=datetime.now(UTC),
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens or 0,
            output_tokens=output_tokens,
        )
        # Built through the shared record so this path and `structured_invocation`
        # write the same columns under the same names. Two hand-written dict
        # literals for one collection is how a metrics query silently comes to
        # cover only half the traffic.
        record = AIAttemptRecord(
            trace_id=trace_id,
            task_id=task_id,
            prompt_version=prompt_version,
            attempt_number=attempt_number,
            status=status,
            configured_tier=configured_tier.value,
            selected_tier=selected_tier.value if selected_tier else None,
            provider=route.provider_name if route else None,
            model=route.model if route else None,
            credential_id=route.credential_id if route else None,
            route_id=route.route_id if route else None,
            selection_reason=selection_reason,
            fallback_used=fallback_used,
            fallback_reason=fallback_reason,
            safety_status=safety.status.value,
            latency_ms=latency_ms,
            rate_limit_wait_ms=rate_limit_wait_ms,
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cost=cost,
            # This path serves the eligibility decision, which is scoped to a
            # session rather than a conversation or a case. The other three ids
            # are genuinely absent here, and are recorded as absent.
            correlation=InvocationCorrelation(session_id=session_id),
            request_digest=request_digest,
            response_digest=response_digest,
            error_code=error_code,
        )
        await method({"_id": str(uuid.uuid4()), **record.to_document()})

    async def _manual_review(
        self,
        trace: AITraceView,
        *,
        task_id: str,
        task: TaskConfiguration,
        status: AIRequestStatus,
        error_code: str,
        explanation: str,
        safety: SafetyInspection,
        attempts: int = 0,
    ) -> GatewayEvaluation:
        fallback_text = json.dumps(
            {
                "decision": AIDecision.REVIEW_REQUIRED.value,
                "explanation": explanation,
                "confidenceMillionths": 0,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        trace = await self._repository.update_ai_trace(
            trace.id,
            {
                "status": status.value,
                "provider": "DETERMINISTIC",
                "model": task.fallbackTemplate,
                "selectedTier": task.tier.value,
                "responseText": fallback_text,
                "responseDigest": hashlib.sha256(fallback_text.encode("utf-8")).hexdigest(),
                "decision": AIDecision.REVIEW_REQUIRED.value,
                "explanation": explanation,
                "confidenceMillionths": 0,
                "attempts": attempts,
                "fallbackUsed": True,
                "safetyStatus": safety.status.value,
                "safetySignals": list(safety.signals),
                "selectionReason": "DETERMINISTIC_FALLBACK",
                "errorCode": error_code,
            },
        )
        await self._attempt_metric(
            trace_id=trace.id,
            session_id=trace.sessionId,
            task_id=task_id,
            configured_tier=task.tier,
            selected_tier=task.tier,
            route=None,
            attempt_number=attempts,
            selection_reason="DETERMINISTIC_FALLBACK",
            status="SAFETY_BLOCKED" if not safety.allowed else "FALLBACK",
            fallback_used=True,
            safety=safety,
            latency_ms=0,
            rate_limit_wait_ms=0,
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            request_digest=trace.requestDigest,
            response_digest=trace.responseDigest,
            error_code=error_code,
            fallback_reason=error_code,
            prompt_version=task.promptVersion,
        )
        return GatewayEvaluation(trace=trace, pending_interception=False)

    async def evaluate(
        self,
        *,
        session_id: str | None,
        redacted_input: dict[str, Any],
        requested_decision: AIDecision | None = None,
        force_provider: str | None = None,
        system_prompt: str | None = None,
        original_request_digest: str | None = None,
        task_id: str = "RETURN_ELIGIBILITY_V1",
    ) -> GatewayEvaluation:
        task = self._configuration.tasks.get(task_id)
        if task is None:
            raise ValueError(f"Unknown AI task: {task_id}")
        candidate = dict(redacted_input)
        if requested_decision is not None:
            candidate["requestedDecision"] = requested_decision.value

        if system_prompt is not None and self._settings.environment not in {"development", "test"}:
            raise _PayloadPolicyError(
                AIRequestStatus.POLICY_BLOCKED,
                "Custom system prompts are forbidden outside development and test.",
            )
        prompt = system_prompt or task.systemPrompt

        try:
            payload = self._redact_and_validate(candidate, task)
            safety = inspect_input(payload)
            if not safety.allowed:
                raise _PayloadPolicyError(
                    AIRequestStatus.POLICY_BLOCKED,
                    f"AI input was blocked: {safety.status.value}.",
                )
        except _PayloadPolicyError as error:
            safety = inspect_input(candidate)
            empty_payload: dict[str, Any] = {}
            trace = await self._repository.create_ai_trace(
                session_id=session_id,
                status=AIRequestStatus.CREATED,
                prompt_version=task.promptVersion,
                redacted_input=empty_payload,
                system_prompt=prompt,
                request_digest=self._digest(prompt, empty_payload),
                original_request_digest=original_request_digest,
                task_id=task_id,
                configured_tier=task.tier.value,
                safety_status=safety.status.value,
                safety_signals=list(safety.signals),
            )
            return await self._manual_review(
                trace,
                task_id=task_id,
                task=task,
                status=error.code,
                error_code=safety.status.value if not safety.allowed else error.code.value,
                explanation=str(error),
                safety=safety,
            )

        digest = self._digest(prompt, payload)
        trace = await self._repository.create_ai_trace(
            session_id=session_id,
            status=AIRequestStatus.CREATED,
            prompt_version=task.promptVersion,
            redacted_input=payload,
            system_prompt=prompt,
            request_digest=digest,
            original_request_digest=original_request_digest,
            task_id=task_id,
            configured_tier=task.tier.value,
            safety_status=safety.status.value,
            safety_signals=list(safety.signals),
        )
        trace = await self._repository.update_ai_trace(
            trace.id,
            {"status": AIRequestStatus.REDACTED.value},
            expected_version=trace.version,
        )
        trace = await self._repository.update_ai_trace(
            trace.id,
            {"status": AIRequestStatus.POLICY_CHECKED.value},
            expected_version=trace.version,
        )
        gateway_settings = await self._repository.get_ai_settings()
        if gateway_settings.interceptMode and force_provider is None:
            trace = await self._repository.update_ai_trace(
                trace.id,
                {"status": AIRequestStatus.INTERCEPTION_PENDING.value},
                expected_version=trace.version,
            )
            return GatewayEvaluation(trace=trace, pending_interception=True)

        quota_bucket = f"session:{session_id}" if session_id is not None else "standalone"
        if not await self._repository.consume_ai_quota(quota_bucket):
            return await self._manual_review(
                trace,
                task_id=task_id,
                task=task,
                status=AIRequestStatus.RATE_LIMITED,
                error_code=AIRequestStatus.RATE_LIMITED.value,
                explanation="AI session quota was exhausted; manual review is required.",
                safety=safety,
            )

        normalized_force = force_provider.strip().upper() if force_provider else None
        candidates = await self._route_pool.candidates(
            task,
            task_id=task_id,
            force_provider=normalized_force,
        )
        attempts = 0
        last_error = AIRequestStatus.PROVIDER_UNAVAILABLE.value
        deadline = time.monotonic() + self._settings.ai_global_timeout_seconds
        estimated_tokens = max(1, len(json.dumps(payload, sort_keys=True)) // 4)

        for route in candidates:
            for route_attempt in range(self._configuration.retry.maximumAttemptsPerRoute):
                if attempts >= self._configuration.retry.maximumTotalAttempts:
                    break
                if time.monotonic() >= deadline:
                    last_error = AIRequestStatus.TIMEOUT.value
                    break
                acquired = await self._route_pool.try_acquire(
                    route,
                    estimated_tokens=estimated_tokens,
                )
                if not acquired.acquired:
                    await self._attempt_metric(
                        trace_id=trace.id,
                        session_id=session_id,
                        task_id=task_id,
                        configured_tier=task.tier,
                        selected_tier=route.tier,
                        route=route,
                        attempt_number=attempts,
                        selection_reason=acquired.reason,
                        status="SKIPPED",
                        fallback_used=False,
                        safety=safety,
                        latency_ms=0,
                        rate_limit_wait_ms=0,
                        input_tokens=0,
                        output_tokens=0,
                        total_tokens=0,
                        request_digest=digest,
                        response_digest=None,
                        error_code=acquired.reason,
                        prompt_version=task.promptVersion,
                    )
                    break

                attempts += 1
                started = time.monotonic()
                trace = await self._repository.update_ai_trace(
                    trace.id,
                    {
                        "status": AIRequestStatus.DISPATCHED.value,
                        "provider": route.provider_name,
                        "model": route.model,
                        "credentialId": route.credential_id,
                        "routeId": route.route_id,
                        "selectedTier": route.tier.value,
                        "selectionReason": "HEALTHY_ROUTE_SELECTED",
                        "attempts": attempts,
                        "errorCode": None,
                    },
                )
                try:
                    remaining = max(0.05, deadline - time.monotonic())
                    response = await asyncio.wait_for(
                        route.provider.generate(
                            ProviderRequest(
                                prompt,
                                # `_redact_and_validate` already rejected a
                                # sensitive *top-level key*. This catches the
                                # same policy applied to values nested inside
                                # containers and inside JSON-encoded strings,
                                # which that check cannot see.
                                redact_payload(payload),
                                max_output_tokens=task.maximumOutputTokens,
                                temperature=0.0,
                            )
                        ),
                        timeout=min(self._settings.ai_timeout_seconds, remaining),
                    )
                    latency = max(0, int((time.monotonic() - started) * 1000))
                    response_digest = hashlib.sha256(response.text.encode("utf-8")).hexdigest()
                    trace = await self._repository.update_ai_trace(
                        trace.id,
                        {
                            "status": AIRequestStatus.RESPONSE_RECEIVED.value,
                            "responseText": response.text,
                            "responseDigest": response_digest,
                            "latencyMs": latency,
                            "inputTokens": response.input_tokens,
                            "cachedInputTokens": response.cached_input_tokens,
                            "outputTokens": response.output_tokens,
                            "totalTokens": response.total_tokens,
                            **_priced_trace_fields(
                                self._configuration.pricing.estimate(
                                    provider=route.provider_name,
                                    model=route.model,
                                    at=datetime.now(UTC),
                                    input_tokens=int(response.input_tokens or 0),
                                    cached_input_tokens=int(response.cached_input_tokens or 0),
                                    output_tokens=int(response.output_tokens or 0),
                                )
                            ),
                        },
                    )
                    decision, explanation, confidence = self._parse_response(response.text)
                    trace = await self._repository.update_ai_trace(
                        trace.id,
                        {"status": AIRequestStatus.RESPONSE_VALIDATED.value},
                        expected_version=trace.version,
                    )
                    trace = await self._repository.update_ai_trace(
                        trace.id,
                        {
                            "status": AIRequestStatus.DECISION_PERSISTED.value,
                            "decision": decision.value,
                            "explanation": explanation,
                            "confidenceMillionths": confidence,
                            "fallbackUsed": False,
                            "errorCode": None,
                        },
                    )
                    await self._route_pool.record_success(route)
                    await self._attempt_metric(
                        trace_id=trace.id,
                        session_id=session_id,
                        task_id=task_id,
                        configured_tier=task.tier,
                        selected_tier=route.tier,
                        route=route,
                        attempt_number=attempts,
                        selection_reason="HEALTHY_ROUTE_SELECTED",
                        status="SUCCESS",
                        fallback_used=False,
                        safety=safety,
                        latency_ms=latency,
                        rate_limit_wait_ms=0,
                        input_tokens=int(response.input_tokens or 0),
                        cached_input_tokens=response.cached_input_tokens,
                        output_tokens=int(response.output_tokens or 0),
                        total_tokens=int(
                            response.total_tokens
                            or int(response.input_tokens or 0) + int(response.output_tokens or 0)
                        ),
                        request_digest=digest,
                        response_digest=response_digest,
                        error_code=None,
                        prompt_version=task.promptVersion,
                    )
                    return GatewayEvaluation(trace=trace, pending_interception=False)
                except TimeoutError:
                    last_error = AIRequestStatus.TIMEOUT.value
                except ProviderError as error:
                    last_error = error.code
                except Exception as error:  # Defensive provider boundary.
                    last_error = type(error).__name__
                finally:
                    await self._route_pool.release(route)

                latency = max(0, int((time.monotonic() - started) * 1000))
                await self._route_pool.record_failure(route, last_error)
                trace = await self._repository.update_ai_trace(
                    trace.id,
                    {
                        "status": self._trace_status_for_error(last_error).value,
                        "errorCode": last_error,
                        "attempts": attempts,
                    },
                )
                await self._attempt_metric(
                    trace_id=trace.id,
                    session_id=session_id,
                    task_id=task_id,
                    configured_tier=task.tier,
                    selected_tier=route.tier,
                    route=route,
                    attempt_number=attempts,
                    selection_reason="ROUTE_FAILED",
                    status="FAILED",
                    fallback_used=False,
                    safety=safety,
                    latency_ms=latency,
                    rate_limit_wait_ms=0,
                    input_tokens=0,
                    output_tokens=0,
                    total_tokens=0,
                    request_digest=digest,
                    response_digest=None,
                    error_code=last_error,
                    prompt_version=task.promptVersion,
                )
                if last_error in {
                    AIRequestStatus.AUTH_FAILED.value,
                    AIRequestStatus.RATE_LIMITED.value,
                    AIRequestStatus.POLICY_BLOCKED.value,
                    AIRequestStatus.RESPONSE_INVALID.value,
                }:
                    break
                if route_attempt + 1 < self._configuration.retry.maximumAttemptsPerRoute:
                    base_ms = min(
                        self._configuration.retry.maximumBackoffMilliseconds,
                        self._configuration.retry.initialBackoffMilliseconds * (2**route_attempt),
                    )
                    jitter = (
                        random.randint(0, max(1, base_ms // 4))
                        if self._configuration.retry.jitter
                        else 0
                    )
                    await asyncio.sleep((base_ms + jitter) / 1000)

        return await self._manual_review(
            trace,
            task_id=task_id,
            task=task,
            status=AIRequestStatus.DECISION_PERSISTED,
            error_code=last_error,
            explanation=(
                "All permitted AI routes were unavailable, rate-limited, unsafe, or returned an "
                "untrusted response; manual review is required."
            ),
            safety=safety,
            attempts=attempts,
        )
