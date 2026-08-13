"""The eligibility *decision* contract, over the shared dispatch boundary.

This module owns what is specific to the decision path: the task input
allowlist, the `{decision, explanation, confidenceMillionths}` response
contract, the persistent `AITrace` an operator inspects, the per-session quota,
and the deterministic manual-review answer its callers must always receive.

It no longer owns a provider execution loop. Route selection, failover, retry,
recursive redaction, the `provider.generate` call, pricing and attempt telemetry
all live in `final_dispatch.FinalDispatcher`, which is the single boundary every
AI request crosses -- including the structured-output path this one used to run
beside rather than through.

**Interception moved without changing behaviour.** The `interceptMode` gate that
used to be an `if` in the middle of this function is now
`GatewaySettingsInterceptionPolicy`, consulted by the dispatcher before it looks
at a route. That is the same decision at the same point in the sequence; what
changed is that it is now the boundary's decision rather than this caller's, so
a second caller cannot reach a provider without one.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Protocol

from return_platform.ai.gateway.final_dispatch import (
    DispatchDecision,
    DispatchObserver,
    DispatchRequest,
    FinalDispatcher,
    InterceptionPolicy,
    InterceptionVerdict,
)
from return_platform.ai.gateway.telemetry import (
    InvocationCorrelation,
    RepositoryAIAttemptRecorder,
)
from return_platform.ai.pricing import AICostEstimate
from return_platform.ai.providers import ProviderError, ProviderResponse
from return_platform.ai.routing.routes import AIRoute, build_routes
from return_platform.ai.routing.selection import AIRoutePool
from return_platform.ai.routing.tasks import (
    AIGatewayConfiguration,
    LoadedAIGatewayConfiguration,
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
from return_platform.platform.redaction.sensitive_keys import is_sensitive_key


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

    The estimate is the dispatcher's -- the same object it writes to the attempt
    row -- rather than a second call to the catalog, so the trace and its own
    attempts cannot disagree about what a call cost.
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


class GatewaySettingsInterceptionPolicy(InterceptionPolicy):
    """Holds a request for a human when the operator has enabled interception.

    Reads the same `AIGatewaySettingsView.interceptMode` the AI Control Center
    writes, and honours the same exemption: an explicitly forced provider is a
    replay or a comparison an operator has already chosen to run, so holding it
    again would deadlock the console against itself.
    """

    def __init__(self, repository: AIGatewayRepository) -> None:
        self._repository = repository

    async def decide(self, request: DispatchRequest) -> InterceptionVerdict:
        settings = await self._repository.get_ai_settings()
        if getattr(settings, "interceptMode", False) and request.force_provider is None:
            return InterceptionVerdict(
                decision=DispatchDecision.HUMAN_RESPONSE,
                reason=AIRequestStatus.INTERCEPTION_PENDING.value,
            )
        return InterceptionVerdict(decision=DispatchDecision.ALLOW_PROVIDER)


class _TraceObserver(DispatchObserver):
    """Keeps the persistent `AITrace` in step with the attempt loop.

    The trace is the decision path's operator-facing contract -- the thing the
    AI Control Center reads -- and it must record each state transition as it
    happens rather than one summary at the end, because an operator looking at a
    hung request needs to see which provider it is hung on.
    """

    def __init__(self, repository: AIGatewayRepository, trace: AITraceView) -> None:
        self._repository = repository
        self.trace = trace

    async def _update(self, updates: dict[str, Any], *, versioned: bool = False) -> None:
        self.trace = await self._repository.update_ai_trace(
            self.trace.id,
            updates,
            expected_version=self.trace.version if versioned else None,
        )

    async def on_attempt_started(self, *, route: AIRoute, attempt: int) -> None:
        await self._update(
            {
                "status": AIRequestStatus.DISPATCHED.value,
                "provider": route.provider_name,
                "model": route.model,
                "credentialId": route.credential_id,
                "routeId": route.route_id,
                "selectedTier": route.tier.value,
                "selectionReason": "HEALTHY_ROUTE_SELECTED",
                "attempts": attempt,
                "errorCode": None,
            }
        )

    async def on_response(
        self,
        *,
        route: AIRoute,
        attempt: int,
        response: ProviderResponse,
        latency_ms: int,
        cost: AICostEstimate,
    ) -> None:
        del route, attempt
        await self._update(
            {
                "status": AIRequestStatus.RESPONSE_RECEIVED.value,
                "responseText": response.text,
                "responseDigest": hashlib.sha256(response.text.encode("utf-8")).hexdigest(),
                "latencyMs": latency_ms,
                "inputTokens": response.input_tokens,
                "cachedInputTokens": response.cached_input_tokens,
                "outputTokens": response.output_tokens,
                "totalTokens": response.total_tokens,
                **_priced_trace_fields(cost),
            }
        )

    async def on_attempt_succeeded(
        self, *, route: AIRoute, attempt: int, response: ProviderResponse, latency_ms: int
    ) -> None:
        del route, attempt, response, latency_ms
        await self._update({"status": AIRequestStatus.RESPONSE_VALIDATED.value}, versioned=True)

    async def on_attempt_failed(
        self,
        *,
        route: AIRoute,
        attempt: int,
        error_code: str,
        latency_ms: int,
        error: BaseException | None,
    ) -> None:
        del route, latency_ms, error
        await self._update(
            {
                "status": AIGatewayService._trace_status_for_error(error_code).value,
                "errorCode": error_code,
                "attempts": attempt,
            }
        )


class AIGatewayService:
    def __init__(
        self,
        repository: AIGatewayRepository,
        settings: Settings,
        *,
        loaded_configuration: LoadedAIGatewayConfiguration | None = None,
        route_pool: AIRoutePool | None = None,
        dispatcher: FinalDispatcher | None = None,
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
        # A repository that cannot store attempts records none, which is the
        # state a caller passing a minimal stand-in was already in.
        recorder = (
            RepositoryAIAttemptRecorder(repository)
            if callable(getattr(repository, "insert_ai_attempt_metric", None))
            else None
        )
        self._dispatcher = dispatcher or FinalDispatcher(
            settings=settings,
            configuration=self._configuration,
            route_pool=self._route_pool,
            recorder=recorder,
            interception=GatewaySettingsInterceptionPolicy(repository),
        )

    @property
    def route_pool(self) -> AIRoutePool:
        return self._route_pool

    @property
    def configuration(self) -> AIGatewayConfiguration:
        return self._configuration

    @property
    def dispatcher(self) -> FinalDispatcher:
        return self._dispatcher

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
                if is_sensitive_key(raw_key):
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

    async def _manual_review(
        self,
        trace: AITraceView,
        *,
        request: DispatchRequest,
        task: TaskConfiguration,
        status: AIRequestStatus,
        error_code: str,
        explanation: str,
        safety: SafetyInspection,
        attempts: int = 0,
    ) -> GatewayEvaluation:
        """The deterministic answer this path's callers must always receive.

        `evaluate` is a business decision, so it cannot raise the way the
        structured path does: a caller with no outcome would have to invent one.
        """
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
        # Recorded through the dispatcher's single writer even though no
        # provider ran: an attempt that never left the platform is still an
        # attempt, and a second writer is how one collection comes to hold two
        # column vocabularies.
        await self._dispatcher.record_attempt(
            trace_id=trace.id,
            request=request,
            route=None,
            attempt_number=attempts,
            status="SAFETY_BLOCKED" if not safety.allowed else "FALLBACK",
            selection_reason="DETERMINISTIC_FALLBACK",
            safety_status=safety.status.value,
            response_digest=trace.responseDigest,
            error_code=error_code,
            fallback_reason=error_code,
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
            digest = self._digest(prompt, empty_payload)
            trace = await self._repository.create_ai_trace(
                session_id=session_id,
                status=AIRequestStatus.CREATED,
                prompt_version=task.promptVersion,
                redacted_input=empty_payload,
                system_prompt=prompt,
                request_digest=digest,
                original_request_digest=original_request_digest,
                task_id=task_id,
                configured_tier=task.tier.value,
                safety_status=safety.status.value,
                safety_signals=list(safety.signals),
            )
            return await self._manual_review(
                trace,
                request=self._dispatch_request(
                    task_id=task_id,
                    task=task,
                    prompt=prompt,
                    payload=empty_payload,
                    digest=digest,
                    safety=safety,
                    session_id=session_id,
                    force_provider=force_provider,
                ),
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

        # The quota is a precondition of *dispatching*, not of asking: a request
        # a human intercepted must not have spent the session's budget, because
        # the operator's answer is not a provider call.
        quota_bucket = f"session:{session_id}" if session_id is not None else "standalone"

        async def quota() -> str | None:
            if await self._repository.consume_ai_quota(quota_bucket):
                return None
            return AIRequestStatus.RATE_LIMITED.value

        request = self._dispatch_request(
            task_id=task_id,
            task=task,
            prompt=prompt,
            payload=payload,
            digest=digest,
            safety=safety,
            session_id=session_id,
            force_provider=force_provider,
            precondition=quota,
        )
        observer = _TraceObserver(self._repository, trace)
        outcome = await self._dispatcher.dispatch(
            request,
            trace_id=trace.id,
            validate=lambda response: self._parse_response(response.text),
            observer=observer,
        )
        trace = observer.trace

        if outcome.decision is DispatchDecision.HUMAN_RESPONSE:
            trace = await self._repository.update_ai_trace(
                trace.id,
                {"status": AIRequestStatus.INTERCEPTION_PENDING.value},
                expected_version=trace.version,
            )
            return GatewayEvaluation(trace=trace, pending_interception=True)

        if outcome.decision is DispatchDecision.REJECT:
            return await self._manual_review(
                trace,
                request=request,
                task=task,
                status=AIRequestStatus.RATE_LIMITED,
                error_code=outcome.reason or AIRequestStatus.RATE_LIMITED.value,
                explanation="AI session quota was exhausted; manual review is required.",
                safety=safety,
            )

        if outcome.value is None:
            return await self._manual_review(
                trace,
                request=request,
                task=task,
                status=AIRequestStatus.DECISION_PERSISTED,
                error_code=outcome.last_error or AIRequestStatus.PROVIDER_UNAVAILABLE.value,
                explanation=(
                    "All permitted AI routes were unavailable, rate-limited, unsafe, or returned "
                    "an untrusted response; manual review is required."
                ),
                safety=safety,
                attempts=outcome.attempts,
            )

        decision, explanation, confidence = outcome.value
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
        return GatewayEvaluation(trace=trace, pending_interception=False)

    def _dispatch_request(
        self,
        *,
        task_id: str,
        task: TaskConfiguration,
        prompt: str,
        payload: dict[str, Any],
        digest: str,
        safety: SafetyInspection,
        session_id: str | None,
        force_provider: str | None,
        precondition: Any = None,
    ) -> DispatchRequest:
        return DispatchRequest(
            task_id=task_id,
            task=task,
            system_prompt=prompt,
            payload=payload,
            request_digest=digest,
            estimated_tokens=max(1, len(json.dumps(payload, sort_keys=True)) // 4),
            # This path serves the eligibility decision, which is scoped to a
            # session rather than a conversation or a case. The other three ids
            # are genuinely absent here, and are recorded as absent.
            correlation=InvocationCorrelation(session_id=session_id),
            safety_status=safety.status.value,
            max_output_tokens=task.maximumOutputTokens,
            temperature=0.0,
            force_provider=force_provider,
            precondition=precondition,
        )
