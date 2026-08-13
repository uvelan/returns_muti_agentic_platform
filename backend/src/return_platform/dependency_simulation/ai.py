"""Optional lightweight AI enrichment with mandatory deterministic fallback.

The narrative is wording only -- the deterministic operation facts are already
final before this module is asked anything, and every failure path returns the
template. That is why this path may fail freely and must never block a
simulation.

**It no longer owns a provider loop.** This was the third copy of the
platform's provider execution machinery: its own candidate loop, its own
failover bookkeeping, its own attempt rows, its own `provider.generate` call.
Being a copy is what made it the only path with *no* redaction at all -- the
recursive redactor was added to the other two loops individually, and a third
loop nobody was looking at simply did not get it. Route selection, failover,
retry, redaction, the dispatch itself and the interception decision now come
from `ai.gateway.final_dispatch.FinalDispatcher`.

What stays here is the simulator's own contract: the `{message, summary,
nextAction}` response shape, and the `SimulationAIUsageMetric` row that the
simulator console reads and that `aiMetricId` links a narrative to. That row is
domain reporting over the simulator's own configured price table, which is not
the AI gateway's released pricing catalog; see the module note on `_cost`.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from return_platform.ai.gateway.final_dispatch import (
    DispatchObserver,
    DispatchRequest,
    FinalDispatcher,
)
from return_platform.ai.providers import ProviderResponse
from return_platform.ai.routing.routes import AIRoute, build_routes
from return_platform.ai.routing.selection import AIRoutePool
from return_platform.ai.routing.tasks import (
    LoadedAIGatewayConfiguration,
    load_ai_gateway_configuration,
)
from return_platform.ai.safety import inspect_input, inspect_output
from return_platform.configuration.settings import Settings
from return_platform.dependency_simulation.configuration import DependencySimulationConfiguration
from return_platform.dependency_simulation.models import DependencyKind, SimulationNarrative
from return_platform.dependency_simulation.repository import SimulationRepository
from return_platform.dependency_simulation.templates import default_narrative

#: What the original loop reported when no lightweight route was configured at
#: all. Kept as a distinct code because "nothing to try" and "everything tried
#: and failed" are different operator problems.
_NO_ROUTE = "NO_CONFIGURED_LIGHTWEIGHT_ROUTE"


@dataclass(frozen=True, slots=True)
class NarrativeResult:
    narrative: SimulationNarrative


class SimulationNarrativeService:
    """AI improves wording only; deterministic operation facts are already final."""

    def __init__(
        self,
        repository: SimulationRepository,
        settings: Settings,
        configuration: DependencySimulationConfiguration,
        *,
        loaded_ai_gateway: LoadedAIGatewayConfiguration | None = None,
        route_pool: AIRoutePool | None = None,
        dispatcher: FinalDispatcher | None = None,
    ) -> None:
        self._repository = repository
        self._settings = settings
        self._configuration = configuration
        if loaded_ai_gateway is None:
            if settings.environment not in {"development", "test"}:
                raise RuntimeError(
                    "Production simulation AI behavior must come from the active graph release"
                )
            loaded_ai_gateway = load_ai_gateway_configuration(
                settings.ai_gateway_configuration_path
            )
        loaded = loaded_ai_gateway
        self._gateway_configuration = loaded.configuration
        self._task = self._gateway_configuration.tasks[configuration.ai.taskId]
        if self._task.tier.value != "LIGHTWEIGHT" or self._task.allowTierEscalation:
            raise ValueError("Dependency simulator narratives must remain lightweight-only.")
        self._route_pool = route_pool or AIRoutePool(
            build_routes(settings), self._gateway_configuration
        )
        # No `AIAttemptRecorder`: the simulator has never written to the
        # platform's `ai_usage_attempts` collection and giving it one here would
        # mix simulated traffic into real cost reporting. Its own metric rows
        # are written by the observer below.
        self._dispatcher = dispatcher or FinalDispatcher(
            settings=settings,
            configuration=self._gateway_configuration,
            route_pool=self._route_pool,
        )

    @staticmethod
    def _digest(value: object) -> str:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
        return hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _parse(text: str, fallback: SimulationNarrative) -> SimulationNarrative:
        stripped = (
            text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        )
        data = json.loads(stripped)
        if not isinstance(data, dict):
            raise ValueError("AI narrative must be a JSON object")
        keys = {"message", "summary", "nextAction"}
        if set(data) != keys or not all(
            isinstance(data[key], str) and data[key].strip() for key in keys
        ):
            raise ValueError("AI narrative schema is invalid")
        for key in keys:
            if not inspect_output(data[key]).allowed:
                raise ValueError("AI narrative failed output safety validation")
        return fallback.model_copy(
            update={
                "source": "LIGHTWEIGHT_AI",
                "message": data["message"].strip()[:1_000],
                "summary": data["summary"].strip()[:1_000],
                "nextAction": data["nextAction"].strip()[:1_000],
            }
        )

    def _cost(self, provider: str, input_tokens: int, output_tokens: int) -> int:
        """The simulator's own report figure, over its own configured rates.

        Deliberately *not* the AI gateway's released pricing catalog: this row
        describes simulated traffic and is summed into `SimulationAISummary`,
        which the platform's real cost reporting must not include.

        It still carries the defect the gateway's catalog was built to remove --
        an unpriced provider reports `0` rather than "unknown" -- because
        `SimulationAIUsageMetric.estimatedCostMicrousd` is `int Field(ge=0)` and
        is summed unconditionally in `repository.py`. Making it honest means
        making that field nullable, teaching the two summaries to exclude and
        count unknown rows, and regenerating the frontend contract. Tracked as
        an external item; not fixable from inside the AI dispatch boundary.
        """
        pricing = self._configuration.ai.pricingMicrousdPerMillionTokens.get(provider)
        if pricing is None:
            return 0
        return int((input_tokens * pricing.input + output_tokens * pricing.output) / 1_000_000)

    async def _metric(
        self,
        *,
        operation_id: str,
        session_id: str,
        dependency: DependencyKind,
        operation: str,
        provider: str,
        model: str,
        credential_id: str | None,
        route_id: str | None,
        selection_reason: str,
        status: str,
        fallback_used: bool,
        attempt: int,
        latency_ms: int,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        request_digest: str,
        response_digest: str | None,
        error_code: str | None,
    ) -> str:
        metric_id = f"SIM-AI-{uuid.uuid4()}"
        await self._repository.insert_ai_metric(
            {
                "_id": metric_id,
                "id": metric_id,
                "operationId": operation_id,
                "sessionId": session_id,
                "dependency": dependency.value,
                "operation": operation,
                "provider": provider,
                "model": model,
                "credentialId": credential_id,
                "routeId": route_id,
                "modelTier": self._task.tier.value,
                "selectionReason": selection_reason,
                "status": status,
                "fallbackUsed": fallback_used,
                "attempt": attempt,
                "latencyMs": latency_ms,
                "inputTokens": input_tokens,
                "outputTokens": output_tokens,
                "totalTokens": total_tokens,
                "estimatedCostMicrousd": self._cost(provider, input_tokens, output_tokens),
                "requestDigest": request_digest,
                "responseDigest": response_digest,
                "errorCode": error_code,
                "createdAt": datetime.now(UTC),
            }
        )
        return metric_id

    async def _fallback(
        self,
        *,
        operation_id: str,
        session_id: str,
        dependency: DependencyKind,
        operation: str,
        fallback: SimulationNarrative,
        request_digest: str,
        error_code: str,
        attempt: int,
        status: str = "FALLBACK",
    ) -> NarrativeResult:
        metric_id = await self._metric(
            operation_id=operation_id,
            session_id=session_id,
            dependency=dependency,
            operation=operation,
            provider="DEFAULT_TEMPLATE",
            model=self._configuration.templateVersion,
            credential_id=None,
            route_id=None,
            selection_reason="DETERMINISTIC_TEMPLATE",
            status=status,
            fallback_used=True,
            attempt=attempt,
            latency_ms=0,
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            request_digest=request_digest,
            response_digest=self._digest(fallback.model_dump(mode="json")),
            error_code=error_code,
        )
        return NarrativeResult(fallback.model_copy(update={"aiMetricId": metric_id}))

    async def generate(
        self,
        *,
        operation_id: str,
        session_id: str,
        dependency: DependencyKind,
        operation: str,
        result: dict[str, Any],
        enabled: bool,
    ) -> NarrativeResult:
        fallback = default_narrative(
            dependency,
            operation,
            result,
            template_version=self._configuration.templateVersion,
        )
        request_payload = {
            "dependency": dependency.value,
            "operation": operation,
            "simulatedResult": result,
            "rules": [
                (
                    "Do not add identifiers, statuses, amounts, dates, or facts not "
                    "present in simulatedResult."
                ),
                "Return JSON with exactly message, summary, and nextAction.",
                "Keep each value below 120 words.",
            ],
        }
        request_digest = self._digest(request_payload)
        safety = inspect_input(request_payload)
        if not safety.allowed:
            return await self._fallback(
                operation_id=operation_id,
                session_id=session_id,
                dependency=dependency,
                operation=operation,
                fallback=fallback,
                request_digest=request_digest,
                error_code=safety.status.value,
                attempt=0,
                status="SAFETY_BLOCKED",
            )
        if not enabled or not self._configuration.ai.enabled:
            return await self._fallback(
                operation_id=operation_id,
                session_id=session_id,
                dependency=dependency,
                operation=operation,
                fallback=fallback,
                request_digest=request_digest,
                error_code="AI_DISABLED",
                attempt=0,
                status="SKIPPED",
            )

        observer = _NarrativeObserver(
            service=self,
            operation_id=operation_id,
            session_id=session_id,
            dependency=dependency,
            operation=operation,
            request_digest=request_digest,
        )
        outcome = await self._dispatcher.dispatch(
            DispatchRequest(
                task_id=self._configuration.ai.taskId,
                task=self._task,
                system_prompt=self._task.systemPrompt,
                payload=request_payload,
                request_digest=request_digest,
                estimated_tokens=max(1, len(json.dumps(request_payload, sort_keys=True)) // 4),
                safety_status=safety.status.value,
                max_output_tokens=min(
                    self._configuration.ai.maxOutputTokens,
                    self._task.maximumOutputTokens,
                ),
                temperature=self._configuration.ai.temperature,
                # The simulator's narrative is optional work behind a user
                # request, so it holds a route for less time than the platform's
                # global ceiling allows.
                attempt_timeout_seconds=self._configuration.ai.timeoutSeconds,
                provider_allowlist=frozenset(self._configuration.ai.providerOrder),
            ),
            trace_id=operation_id,
            validate=lambda response: self._parse(response.text, fallback),
            observer=observer,
        )

        if outcome.value is not None and observer.metric_id is not None:
            return NarrativeResult(
                outcome.value.model_copy(update={"aiMetricId": observer.metric_id})
            )

        # "Nothing to try" and "everything tried and failed" are different
        # operator problems, so an empty candidate set keeps its own code.
        error_code = outcome.last_error if outcome.attempts or outcome.failure_summary else _NO_ROUTE
        return await self._fallback(
            operation_id=operation_id,
            session_id=session_id,
            dependency=dependency,
            operation=operation,
            fallback=fallback,
            request_digest=request_digest,
            error_code=(error_code or _NO_ROUTE)[:128],
            attempt=outcome.attempts,
        )


class _NarrativeObserver(DispatchObserver):
    """Writes the simulator's own metric rows around the shared dispatch.

    `metric_id` is read back by `generate` because `SimulationNarrative.
    aiMetricId` links a narrative to the row that describes how it was
    produced -- the one piece of the old loop that genuinely had to survive the
    consolidation.
    """

    def __init__(
        self,
        *,
        service: SimulationNarrativeService,
        operation_id: str,
        session_id: str,
        dependency: DependencyKind,
        operation: str,
        request_digest: str,
    ) -> None:
        self._service = service
        self._operation_id = operation_id
        self._session_id = session_id
        self._dependency = dependency
        self._operation = operation
        self._request_digest = request_digest
        self.metric_id: str | None = None

    async def _write(self, **fields: Any) -> str:
        return await self._service._metric(
            operation_id=self._operation_id,
            session_id=self._session_id,
            dependency=self._dependency,
            operation=self._operation,
            request_digest=self._request_digest,
            **fields,
        )

    async def on_route_skipped(self, *, route: AIRoute, attempt: int, reason: str) -> None:
        await self._write(
            provider=route.provider_name,
            model=route.model,
            credential_id=route.credential_id,
            route_id=route.route_id,
            selection_reason=reason,
            status="SKIPPED",
            fallback_used=False,
            attempt=attempt,
            latency_ms=0,
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            response_digest=None,
            error_code=reason,
        )

    async def on_attempt_succeeded(
        self, *, route: AIRoute, attempt: int, response: ProviderResponse, latency_ms: int
    ) -> None:
        input_tokens = int(response.input_tokens or 0)
        output_tokens = int(response.output_tokens or 0)
        self.metric_id = await self._write(
            # The provider that *answered*, not the one the route names. A
            # durable interception a human answered reports MANUAL, and this row
            # must say so rather than crediting the model it replaced.
            provider=response.provider,
            model=response.model,
            credential_id=route.credential_id,
            route_id=route.route_id,
            selection_reason="LIGHTWEIGHT_ROUTE_SELECTED",
            status="SUCCESS",
            fallback_used=False,
            attempt=attempt,
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=int(response.total_tokens or input_tokens + output_tokens),
            response_digest=SimulationNarrativeService._digest(response.text),
            error_code=None,
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
        del error
        await self._write(
            provider=route.provider_name,
            model=route.model,
            credential_id=route.credential_id,
            route_id=route.route_id,
            selection_reason="LIGHTWEIGHT_ROUTE_FAILED",
            status="FAILED",
            fallback_used=False,
            attempt=attempt,
            latency_ms=latency_ms,
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            response_digest=None,
            error_code=error_code[:128],
        )
