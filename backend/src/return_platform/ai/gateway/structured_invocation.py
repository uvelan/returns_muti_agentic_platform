"""The structured-output *response contract*. The execution is not here.

`AIGatewayService.evaluate` is the *decision* path: its parser requires exactly
`{decision, explanation, confidenceMillionths}`, so it cannot carry a richer
typed result. Everything that needs a real pydantic model back from a model call
runs through here instead.

**What changed, and why it matters.** This module used to own a second copy of
the provider execution machinery -- its own candidate loop, its own failover
bookkeeping, its own attempt telemetry, its own `provider.generate` call. It
shared the route *pool* with the decision path, which made the two look like one
path in a diagram and behave like two in production: interception was wired to
the decision path's loop and therefore absent from this one, and redaction had
to be remembered separately here (it was not, for a long time). All of that now
lives in `final_dispatch.FinalDispatcher`, which is the single boundary every AI
request crosses.

What is left here is the part that is genuinely about *structured output*: the
response schema travels in the prompt as well as in the provider's native field,
the response is parsed into a caller-supplied pydantic model, and exhaustion
raises rather than returning a fabricated result. Callers own their payload and
their result type; adding a third caller should not require editing this file.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from return_platform.ai.gateway.final_dispatch import (
    ALLOW_ALL,
    DispatchDecision,
    DispatchObserver,
    DispatchRequest,
    FinalDispatcher,
    InterceptionPolicy,
)
from return_platform.ai.gateway.telemetry import (
    AIAttemptRecorder,
    InvocationCorrelation,
    payload_digest,
)
from return_platform.ai.providers import ProviderError, ProviderResponse
from return_platform.ai.providers.schema_cleaner import clean_gemini_schema
from return_platform.ai.routing.routes import AIRoute
from return_platform.ai.routing.selection import AIRoutePool
from return_platform.ai.routing.tasks import (
    AIGatewayConfiguration,
    ModelTier,
    TaskConfiguration,
)
from return_platform.ai.safety.inspection import inspect_input
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


class _InvokerObserver(DispatchObserver):
    """The invoker's logging, kept out of the dispatch boundary.

    Every line carries the caller's unit-of-work identifiers, which is why this
    is per-invocation state rather than something the dispatcher could own: the
    boundary is shared by callers whose log context is different.
    """

    def __init__(
        self,
        *,
        logger: logging.Logger,
        event_prefix: str,
        task_id: str,
        log_context: Mapping[str, Any],
    ) -> None:
        self._logger = logger
        self._prefix = event_prefix
        self._task_id = task_id
        self._context = dict(log_context)

    def _extra(self, **fields: Any) -> dict[str, Any]:
        return {**self._context, "task_id": self._task_id, **fields}

    async def on_route_skipped(self, *, route: AIRoute, attempt: int, reason: str) -> None:
        del attempt
        self._logger.info(
            f"{self._prefix}_model_attempt_skipped",
            extra=self._extra(
                provider=route.provider_name,
                model=route.model,
                credential_id=route.credential_id,
                reason=reason,
            ),
        )

    async def on_attempt_started(self, *, route: AIRoute, attempt: int) -> None:
        self._logger.info(
            f"{self._prefix}_model_attempt_started",
            extra=self._extra(
                attempt=attempt,
                tier=route.tier.value,
                provider=route.provider_name,
                model=route.model,
                credential_id=route.credential_id,
            ),
        )

    async def on_attempt_succeeded(
        self, *, route: AIRoute, attempt: int, response: ProviderResponse, latency_ms: int
    ) -> None:
        del response
        self._logger.info(
            f"{self._prefix}_model_attempt_succeeded",
            extra=self._extra(
                attempt=attempt,
                tier=route.tier.value,
                provider=route.provider_name,
                model=route.model,
                credential_id=route.credential_id,
                latency_ms=latency_ms,
            ),
        )

    async def on_attempt_failed(
        self,
        *,
        route: AIRoute,
        attempt: int,
        error_code: str,
        latency_ms: int,
        error: BaseException | None,
    ) -> None:
        # Classified by exception *type*, not by error code: a provider may
        # legitimately report `PROVIDER_UNAVAILABLE`, and treating that as an
        # unexpected error would attach a traceback to ordinary failover.
        if isinstance(error, (ValueError, TypeError)):
            # The reason was being thrown away with the exception. A Pydantic
            # `ValidationError` names the field path, the expected type and what
            # arrived instead -- which is the difference between "the model is
            # chatty" (already handled by the brace-span fallback in
            # `parse_structured_response`) and "the schema and the model
            # disagree about a field", which no amount of retrying will fix.
            # Logged as text rather than as the payload: Pydantic's message
            # carries field names and types, never the values.
            self._logger.warning(
                f"{self._prefix}_response_invalid provider=%s model=%s error_type=%s detail=%s",
                route.provider_name,
                route.model,
                type(error).__name__,
                str(error)[:2000],
                extra=self._extra(
                    provider=route.provider_name,
                    model=route.model,
                    error_type=type(error).__name__,
                ),
            )
        elif error is not None and not isinstance(error, (ProviderError, TimeoutError)):
            # Unexpected provider/transport failure: the failover loop keeps
            # moving, but the traceback must not be silently lost.
            self._logger.exception(
                f"{self._prefix}_model_attempt_unexpected_error provider=%s model=%s error=%s",
                route.provider_name,
                route.model,
                error_code,
                extra=self._extra(provider=route.provider_name, model=route.model),
                exc_info=error,
            )
        self._logger.warning(
            (
                f"{self._prefix}_model_attempt_failed "
                "attempt=%d tier=%s provider=%s model=%s error=%s latency_ms=%d"
            ),
            attempt,
            route.tier.value,
            route.provider_name,
            route.model,
            error_code,
            latency_ms,
            extra=self._extra(
                attempt=attempt,
                tier=route.tier.value,
                provider=route.provider_name,
                model=route.model,
                credential_id=route.credential_id,
                error_code=error_code,
                latency_ms=latency_ms,
            ),
        )

    async def on_tier_escalation(self, *, attempts: int, last_error: str) -> None:
        self._logger.warning(
            f"{self._prefix}_tier_escalation_started",
            extra=self._extra(standard_attempts=attempts, standard_last_error=last_error),
        )

    async def on_exhausted(
        self, *, attempts: int, last_error: str, breakdown: Mapping[str, int]
    ) -> None:
        # Exhaustion was previously silent: the loop returned and the only trace
        # was N separate attempt lines a reader had to count and correlate. A
        # turn that dies here is the caller's whole failure, so it says so once,
        # with the tally of *why* -- the same error repeated N times and N
        # different errors are very different diagnoses.
        self._logger.warning(
            f"{self._prefix}_model_attempts_exhausted attempts=%d last_error=%s breakdown=%s",
            attempts,
            last_error,
            ", ".join(f"{code}x{count}" for code, count in sorted(breakdown.items())),
            extra=self._extra(
                attempts=attempts, error_code=last_error, failure_summary=dict(breakdown)
            ),
        )

    async def on_record_failed(self, *, trace_id: str, error: BaseException) -> None:
        del error
        self._logger.exception(
            f"{self._prefix}_attempt_record_failed",
            extra={"task_id": self._task_id, "trace_id": trace_id},
        )


class StructuredOutputInvoker[ResponseT: BaseModel]:
    """Runs one structured-output task through the shared dispatch boundary.

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
        recorder: AIAttemptRecorder | None = None,
        interception: InterceptionPolicy = ALLOW_ALL,
        dispatcher: FinalDispatcher | None = None,
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
        self._task_id = task_id
        self._task: TaskConfiguration = task
        self._response_model = response_model
        self._logger = logger
        self._event_prefix = event_prefix
        self._subject = subject
        self._unavailable_error = unavailable_error
        # A caller may pass a dispatcher it already owns, so one process has one
        # boundary rather than one per invoker. Constructing a default keeps
        # every existing call site working; it still shares the route pool,
        # which is where the state that must not fork actually lives.
        self._dispatcher = dispatcher or FinalDispatcher(
            settings=settings,
            configuration=configuration,
            route_pool=route_pool,
            recorder=recorder,
            interception=interception,
        )

    @property
    def task(self) -> TaskConfiguration:
        return self._task

    @property
    def dispatcher(self) -> FinalDispatcher:
        return self._dispatcher

    async def invoke(
        self,
        *,
        payload: Mapping[str, Any],
        size_probe: str,
        log_context: Mapping[str, Any],
        prompt_addendum: str | None = None,
        correlation: InvocationCorrelation | None = None,
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

        `prompt_addendum` is appended *after* the configured system prompt and
        the response schema. It exists for facts that are true of one request
        and cannot live in configuration -- the Order Agent's as-of instant is
        the first -- and it goes last so the stable prefix in front of it stays
        byte-identical across requests, which is the precondition for
        provider-side prompt caching.

        `correlation` is which piece of business work this call serves. It is
        recorded, never sent: it does not enter the payload, the prompt or the
        size probe, and no provider ever sees it.
        """
        correlation = correlation or InvocationCorrelation()
        trace_id = str(uuid4())
        if len(size_probe.encode("utf-8")) > self._settings.ai_max_payload_bytes:
            raise self._unavailable_error(
                f"{self._subject} input exceeds the configured payload limit"
            )

        # The prompt is assembled before the safety gate rather than after,
        # because a blocked request still has to record *what* was blocked, and
        # the digest is the only representation of that a telemetry row is
        # allowed to hold. It is pure string building; nothing is sent yet.
        #
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
        if prompt_addendum:
            full_prompt = f"{full_prompt}\n\n{prompt_addendum}"
        request_digest = payload_digest(full_prompt, dict(payload))

        safety = inspect_input(dict(payload))
        observer = _InvokerObserver(
            logger=self._logger,
            event_prefix=self._event_prefix,
            task_id=self._task_id,
            log_context=log_context,
        )
        request = DispatchRequest(
            task_id=self._task_id,
            task=self._task,
            system_prompt=full_prompt,
            payload=payload,
            request_digest=request_digest,
            estimated_tokens=max(1, len(size_probe) // 4),
            correlation=correlation,
            safety_status=safety.status.value,
            max_output_tokens=self._task.maximumOutputTokens,
            temperature=0.0,
            response_schema=self._response_model.model_json_schema(),
            allow_tier_escalation=self._task.allowTierEscalation,
        )

        if not safety.allowed:
            # Recorded, not merely raised. A request the safety inspector
            # rejected never reaches a provider, so without this row the only
            # evidence that it happened at all is a log line.
            await self._dispatcher.record_attempt(
                trace_id=trace_id,
                request=request,
                route=None,
                attempt_number=0,
                status="SAFETY_BLOCKED",
                selection_reason="INPUT_SAFETY_REJECTED",
                error_code=safety.status.value,
                observer=observer,
            )
            raise self._unavailable_error(f"{self._subject} input rejected: {safety.status.value}")

        outcome = await self._dispatcher.dispatch(
            request,
            trace_id=trace_id,
            validate=lambda response: parse_structured_response(
                response.text, self._response_model
            ),
            observer=observer,
        )

        if outcome.decision is not DispatchDecision.ALLOW_PROVIDER:
            # A reasoning loop must not be handed a fabricated answer, so a
            # policy refusal raises like every other unavailability rather than
            # returning something the caller would act on.
            raise self._unavailable_error(
                f"{self._subject} was not dispatched: {outcome.decision.value}"
                f"{f' ({outcome.reason})' if outcome.reason else ''}"
            )
        if outcome.value is None or outcome.response is None or outcome.route is None:
            summary = ",".join(
                f"{error_code}:{count}"
                for error_code, count in sorted(outcome.failure_summary.items())
            )
            raise self._unavailable_error(
                f"All {self._task_id} routes and lightweight fallbacks failed; "
                f"attempts={outcome.attempts}; last_error={outcome.last_error}; "
                f"failures={summary or 'none'}"
            )

        return StructuredInvocation(
            value=outcome.value,
            provider=outcome.route.provider_name,
            model=outcome.route.model,
            prompt_tokens=max(0, int(outcome.response.input_tokens or 0)),
            completion_tokens=max(0, int(outcome.response.output_tokens or 0)),
        )


def parse_structured_response[ResponseT: BaseModel](text: str, model: type[ResponseT]) -> ResponseT:
    """Parse a model's text into `model`, tolerating fenced or wrapped JSON.

    Providers that ignore the response-schema hint still tend to return correct
    JSON inside a Markdown fence or with a sentence around it, so a brace-span
    fallback recovers a response that would otherwise be discarded as invalid.
    Raises `ValueError`/`TypeError` on failure, which the dispatch loop maps to
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
