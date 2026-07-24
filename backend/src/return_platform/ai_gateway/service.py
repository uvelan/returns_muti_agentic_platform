"""Observable, interceptable, provider-failover AI Gateway."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

from return_platform.ai_gateway.providers import ProviderError, ProviderRequest, build_providers
from return_platform.configuration.settings import Settings
from return_platform.operations.models import AIDecision, AIRequestStatus, AITraceView
from return_platform.operations.repository import OperationalRepository

_SYSTEM_PROMPT = """You are the Return Platform eligibility policy evaluator.
Use only the supplied redacted operational facts. Never infer identity or protected attributes.
Return exactly one JSON object with keys decision, explanation, confidenceMillionths.
decision must be APPROVE, REJECT, or REVIEW_REQUIRED. confidenceMillionths must be 0..1000000.
Use REVIEW_REQUIRED when evidence is incomplete, conflicting, or policy confidence is insufficient."""
_ALLOWED_INPUT_KEYS = frozenset(
    {
        "requestReference",
        "customerReference",
        "orderReferences",
        "itemReferences",
        "reasonCode",
        "orderStatus",
        "daysSinceDelivery",
        "requestedDecision",
    }
)
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


@dataclass(frozen=True, slots=True)
class GatewayEvaluation:
    trace: AITraceView
    pending_interception: bool


class _PayloadPolicyError(ValueError):
    def __init__(self, code: AIRequestStatus, message: str) -> None:
        self.code = code
        super().__init__(message)


class AIGatewayService:
    def __init__(self, repository: OperationalRepository, settings: Settings) -> None:
        self._repository = repository
        self._settings = settings
        self._providers = build_providers(settings)
        self._semaphore = asyncio.Semaphore(settings.ai_max_concurrency)

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
            raise _PayloadPolicyError(AIRequestStatus.POLICY_BLOCKED, "AI input nesting exceeds policy limit.")
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            normalized = value.strip()
            if len(normalized) > 512:
                raise _PayloadPolicyError(AIRequestStatus.POLICY_BLOCKED, "AI input string exceeds policy limit.")
            return normalized
        if isinstance(value, list):
            if len(value) > 50:
                raise _PayloadPolicyError(AIRequestStatus.POLICY_BLOCKED, "AI input list exceeds policy limit.")
            return [AIGatewayService._validate_scalar(item, depth=depth + 1) for item in value]
        if isinstance(value, dict):
            if len(value) > 50:
                raise _PayloadPolicyError(AIRequestStatus.POLICY_BLOCKED, "AI input object exceeds policy limit.")
            result: dict[str, Any] = {}
            for raw_key, nested in value.items():
                if not isinstance(raw_key, str):
                    raise _PayloadPolicyError(AIRequestStatus.POLICY_BLOCKED, "AI input keys must be strings.")
                normalized_key = raw_key.lower().replace("_", "").replace("-", "")
                if any(fragment in normalized_key for fragment in _SENSITIVE_KEY_FRAGMENTS):
                    raise _PayloadPolicyError(AIRequestStatus.REDACTION_FAILED, "Sensitive field was blocked by redaction policy.")
                result[raw_key] = AIGatewayService._validate_scalar(nested, depth=depth + 1)
            return result
        raise _PayloadPolicyError(AIRequestStatus.POLICY_BLOCKED, "AI input contains an unsupported value type.")

    def _redact_and_validate(self, payload: dict[str, Any]) -> dict[str, Any]:
        unknown = sorted(set(payload) - _ALLOWED_INPUT_KEYS)
        if unknown:
            raise _PayloadPolicyError(
                AIRequestStatus.POLICY_BLOCKED,
                f"AI input contains fields outside the allowlist: {', '.join(unknown)}.",
            )
        sanitized = {key: self._validate_scalar(value) for key, value in payload.items()}
        encoded = json.dumps(sanitized, separators=(",", ":"), sort_keys=True).encode("utf-8")
        if len(encoded) > self._settings.ai_max_payload_bytes:
            raise _PayloadPolicyError(AIRequestStatus.POLICY_BLOCKED, "AI input exceeds the maximum payload size.")
        return sanitized

    @staticmethod
    def _parse_response(text: str) -> tuple[AIDecision, str, int]:
        stripped = text.strip()
        if stripped.startswith("```"):
            stripped = stripped.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        try:
            data = json.loads(stripped)
        except (ValueError, TypeError) as error:
            raise ProviderError("RESPONSE_INVALID") from error
        if not isinstance(data, dict) or set(data) != {"decision", "explanation", "confidenceMillionths"}:
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
        return decision, explanation.strip(), confidence

    async def _manual_review(
        self,
        trace: AITraceView,
        *,
        status: AIRequestStatus,
        error_code: str,
        explanation: str,
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
                "model": "eligibility-fallback-v1",
                "responseText": fallback_text,
                "responseDigest": hashlib.sha256(fallback_text.encode("utf-8")).hexdigest(),
                "decision": AIDecision.REVIEW_REQUIRED.value,
                "explanation": explanation,
                "confidenceMillionths": 0,
                "attempts": attempts,
                "errorCode": error_code,
            },
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
    ) -> GatewayEvaluation:
        candidate = dict(redacted_input)
        if requested_decision is not None:
            candidate["requestedDecision"] = requested_decision.value
        prompt = system_prompt or _SYSTEM_PROMPT
        try:
            payload = self._redact_and_validate(candidate)
        except _PayloadPolicyError as error:
            empty_payload: dict[str, Any] = {}
            trace = await self._repository.create_ai_trace(
                session_id=session_id,
                status=AIRequestStatus.CREATED,
                prompt_version=self._settings.ai_prompt_version,
                redacted_input=empty_payload,
                system_prompt=prompt,
                request_digest=self._digest(prompt, empty_payload),
                original_request_digest=original_request_digest,
            )
            return await self._manual_review(
                trace,
                status=error.code,
                error_code=error.code.value,
                explanation=str(error),
            )

        digest = self._digest(prompt, payload)
        trace = await self._repository.create_ai_trace(
            session_id=session_id,
            status=AIRequestStatus.CREATED,
            prompt_version=self._settings.ai_prompt_version,
            redacted_input=payload,
            system_prompt=prompt,
            request_digest=digest,
            original_request_digest=original_request_digest,
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
                status=AIRequestStatus.RATE_LIMITED,
                error_code=AIRequestStatus.RATE_LIMITED.value,
                explanation="AI request quota was exhausted; manual review is required.",
            )

        normalized_force = force_provider.strip().upper() if force_provider else None
        provider_order = [normalized_force] if normalized_force else gateway_settings.providerOrder
        attempts = 0
        last_error = AIRequestStatus.PROVIDER_UNAVAILABLE.value
        deadline = time.monotonic() + self._settings.ai_global_timeout_seconds
        async with self._semaphore:
            for provider_name in provider_order:
                provider = self._providers.get(provider_name)
                if provider is None or not provider.configured:
                    continue
                for attempt_index in range(self._settings.ai_max_attempts_per_provider):
                    if time.monotonic() >= deadline:
                        last_error = AIRequestStatus.TIMEOUT.value
                        break
                    attempts += 1
                    started = time.monotonic()
                    trace = await self._repository.update_ai_trace(
                        trace.id,
                        {
                            "status": AIRequestStatus.DISPATCHED.value,
                            "provider": provider.name,
                            "model": provider.model,
                            "attempts": attempts,
                            "errorCode": None,
                        },
                    )
                    try:
                        remaining = max(0.05, deadline - time.monotonic())
                        response = await asyncio.wait_for(
                            provider.generate(ProviderRequest(prompt, payload)),
                            timeout=min(self._settings.ai_timeout_seconds, remaining),
                        )
                        latency = max(0, int((time.monotonic() - started) * 1000))
                        trace = await self._repository.update_ai_trace(
                            trace.id,
                            {
                                "status": AIRequestStatus.RESPONSE_RECEIVED.value,
                                "responseText": response.text,
                                "responseDigest": hashlib.sha256(response.text.encode("utf-8")).hexdigest(),
                                "latencyMs": latency,
                                "inputTokens": response.input_tokens,
                                "outputTokens": response.output_tokens,
                                "totalTokens": response.total_tokens,
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
                                "errorCode": None,
                            },
                        )
                        return GatewayEvaluation(trace=trace, pending_interception=False)
                    except asyncio.TimeoutError:
                        last_error = AIRequestStatus.TIMEOUT.value
                    except ProviderError as error:
                        last_error = error.code
                    trace = await self._repository.update_ai_trace(
                        trace.id,
                        {"status": last_error, "errorCode": last_error, "attempts": attempts},
                    )
                    if last_error in {
                        AIRequestStatus.AUTH_FAILED.value,
                        AIRequestStatus.POLICY_BLOCKED.value,
                        AIRequestStatus.RESPONSE_INVALID.value,
                    }:
                        break
                    if attempt_index + 1 < self._settings.ai_max_attempts_per_provider:
                        await asyncio.sleep(min(1.0, 0.2 * (2**attempt_index)))

        return await self._manual_review(
            trace,
            status=AIRequestStatus.DECISION_PERSISTED,
            error_code=last_error,
            explanation="All configured AI providers were unavailable or returned an untrusted response; manual review is required.",
            attempts=attempts,
        )
