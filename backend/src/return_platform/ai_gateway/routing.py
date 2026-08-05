"""Health-aware provider, model, and credential routing for the AI Gateway."""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum

from pydantic import SecretStr

from return_platform.ai_gateway.configuration import (
    AIGatewayConfiguration,
    ModelTier,
    TaskConfiguration,
)
from return_platform.ai_gateway.providers.anthropic import AnthropicProvider
from return_platform.ai_gateway.providers.contracts import AIProvider
from return_platform.ai_gateway.providers.google import GeminiProvider
from return_platform.ai_gateway.providers.nvidia import NvidiaProvider
from return_platform.ai_gateway.providers.ollama import OllamaProvider
from return_platform.ai_gateway.providers.openai import OpenAIResponsesProvider
from return_platform.ai_gateway.providers.simulator import SimulatorProvider
from return_platform.configuration.settings import Settings


class CircuitState(StrEnum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


@dataclass(frozen=True, slots=True)
class AIRoute:
    route_id: str
    provider_name: str
    model: str
    credential_id: str
    credential_fingerprint: str | None
    tier: ModelTier
    provider: AIProvider
    provider_priority: int
    model_priority: int
    credential_priority: int
    allowed_task_keys: frozenset[str] = frozenset()


@dataclass(slots=True)
class _Circuit:
    consecutive_failures: int = 0
    open_until: float = 0.0
    last_error: str | None = None
    last_success_at: float | None = None
    last_failure_at: float | None = None

    def state(self, now: float) -> CircuitState:
        if self.open_until <= 0:
            return CircuitState.CLOSED
        if now < self.open_until:
            return CircuitState.OPEN
        return CircuitState.HALF_OPEN


@dataclass(slots=True)
class _MinuteCounter:
    minute: int = -1
    requests: int = 0
    tokens: int = 0

    def reset_if_needed(self, now: float) -> None:
        minute = int(now // 60)
        if self.minute != minute:
            self.minute = minute
            self.requests = 0
            self.tokens = 0


@dataclass(slots=True)
class _RuntimeState:
    route_circuits: dict[str, _Circuit] = field(default_factory=dict)
    credential_circuits: dict[str, _Circuit] = field(default_factory=dict)
    model_circuits: dict[str, _Circuit] = field(default_factory=dict)
    provider_circuits: dict[str, _Circuit] = field(default_factory=dict)
    route_counters: dict[str, _MinuteCounter] = field(default_factory=dict)
    model_counters: dict[str, _MinuteCounter] = field(default_factory=dict)
    credential_counters: dict[str, _MinuteCounter] = field(default_factory=dict)
    provider_counters: dict[str, _MinuteCounter] = field(default_factory=dict)
    tier_counters: dict[str, _MinuteCounter] = field(default_factory=dict)
    application_counter: _MinuteCounter = field(default_factory=_MinuteCounter)
    active_routes: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    active_models: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    active_credentials: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    active_providers: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    active_tiers: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    provider_route_cursors: dict[str, int] = field(default_factory=lambda: defaultdict(int))


@dataclass(frozen=True, slots=True)
class RouteHealth:
    routeId: str
    provider: str
    model: str
    credentialId: str
    tier: ModelTier
    configured: bool
    circuitState: CircuitState
    activeRequests: int
    requestsThisMinute: int
    tokensThisMinute: int
    lastError: str | None
    lastSuccessAtEpochMs: int | None
    lastFailureAtEpochMs: int | None


@dataclass(frozen=True, slots=True)
class RouteAcquireResult:
    acquired: bool
    reason: str


def _fingerprint(secret: SecretStr | None) -> str | None:
    if secret is None:
        return None
    raw = secret.get_secret_value().encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


def _provider(
    provider_name: str,
    settings: Settings,
    *,
    key: SecretStr | None,
    model: str,
) -> AIProvider:
    if provider_name == "GOOGLE":
        return GeminiProvider(
            settings.model_copy(update={"google_api_key": key, "google_model": model})
        )
    if provider_name == "NVIDIA":
        return NvidiaProvider(
            settings.model_copy(update={"nvidia_api_key": key, "nvidia_model": model})
        )
    if provider_name == "OPENAI":
        return OpenAIResponsesProvider(
            settings.model_copy(update={"openai_api_key": key, "openai_model": model})
        )
    if provider_name == "ANTHROPIC":
        return AnthropicProvider(
            settings.model_copy(update={"anthropic_api_key": key, "anthropic_model": model})
        )
    if provider_name == "OLLAMA":
        return OllamaProvider(settings.model_copy(update={"ollama_model": model}))
    if provider_name == "SIMULATOR":
        return SimulatorProvider(settings)
    raise ValueError(f"Unsupported AI provider: {provider_name}")


def _provider_credentials(settings: Settings, provider_name: str) -> tuple[SecretStr | None, ...]:
    if provider_name == "GOOGLE":
        return tuple(settings.resolved_google_api_keys)
    if provider_name == "NVIDIA":
        return tuple(settings.resolved_nvidia_api_keys)
    if provider_name == "OPENAI":
        return tuple(settings.resolved_openai_api_keys)
    if provider_name == "ANTHROPIC":
        return tuple(settings.resolved_anthropic_api_keys)
    if provider_name in {"OLLAMA", "SIMULATOR"}:
        return (None,)
    return ()


def _provider_models(
    settings: Settings,
    provider_name: str,
    tier: ModelTier,
) -> tuple[str, ...]:
    if provider_name == "GOOGLE":
        return (
            tuple(settings.google_lightweight_models)
            if tier is ModelTier.LIGHTWEIGHT
            else tuple(settings.resolved_google_standard_models)
        )
    if provider_name == "NVIDIA":
        return (
            tuple(settings.nvidia_lightweight_models)
            if tier is ModelTier.LIGHTWEIGHT
            else tuple(settings.resolved_nvidia_standard_models)
        )
    if provider_name == "OPENAI":
        return (
            tuple(settings.openai_lightweight_models)
            if tier is ModelTier.LIGHTWEIGHT
            else tuple(settings.resolved_openai_standard_models)
        )
    if provider_name == "ANTHROPIC":
        return (
            tuple(settings.anthropic_lightweight_models)
            if tier is ModelTier.LIGHTWEIGHT
            else tuple(settings.resolved_anthropic_standard_models)
        )
    if provider_name == "OLLAMA":
        return (
            tuple(settings.ollama_lightweight_models)
            if tier is ModelTier.LIGHTWEIGHT
            else tuple(settings.resolved_ollama_standard_models)
        )
    if provider_name == "SIMULATOR":
        return ("deterministic-eligibility-simulator-v1",) if tier is ModelTier.LIGHTWEIGHT else ()
    return ()



def _validated_route_bindings(
    settings: Settings,
) -> dict[tuple[str, int, str], frozenset[str]]:
    bindings: dict[tuple[str, int, str], set[str]] = defaultdict(set)
    for raw in settings.ai_validated_route_bindings:
        provider_name, credential_index_text, model, task_key = raw.split("|", maxsplit=3)
        bindings[(provider_name, int(credential_index_text), model)].add(task_key)
    return {key: frozenset(value) for key, value in bindings.items()}

def build_routes(settings: Settings) -> tuple[AIRoute, ...]:
    provider_order = tuple(p.strip() for p in settings.ai_provider_order.split(","))
    validated_bindings = _validated_route_bindings(settings)
    restrict_to_validated_pairs = bool(validated_bindings)
    routes: list[AIRoute] = []
    for provider_priority, provider_name in enumerate(provider_order):
        credentials = _provider_credentials(settings, provider_name)
        for tier in ModelTier:
            models = _provider_models(settings, provider_name, tier)
            for model_priority, model in enumerate(models):
                for credential_priority, credential in enumerate(credentials):
                    binding_key = (provider_name, credential_priority, model)
                    if restrict_to_validated_pairs and binding_key not in validated_bindings:
                        continue
                    credential_id = (
                        f"{provider_name.lower()}-local"
                        if credential is None
                        else f"{provider_name.lower()}-key-{credential_priority + 1}"
                    )
                    adapter = _provider(provider_name, settings, key=credential, model=model)
                    route_id = f"{provider_name.lower()}/{model}/{credential_id}"
                    routes.append(
                        AIRoute(
                            route_id=route_id,
                            provider_name=provider_name,
                            model=model,
                            credential_id=credential_id,
                            credential_fingerprint=_fingerprint(credential),
                            tier=tier,
                            provider=adapter,
                            provider_priority=provider_priority,
                            model_priority=model_priority,
                            credential_priority=credential_priority,
                            allowed_task_keys=validated_bindings.get(binding_key, frozenset()),
                        )
                    )
    return tuple(routes)


class AIRoutePool:
    """Process-shared route health, quota, concurrency, and circuit state."""

    def __init__(
        self,
        routes: Iterable[AIRoute],
        configuration: AIGatewayConfiguration,
    ) -> None:
        self.routes = tuple(routes)
        self.configuration = configuration
        self._state = _RuntimeState()
        self._lock = asyncio.Lock()

    async def replace_routes(
        self,
        routes: Iterable[AIRoute],
        configuration: AIGatewayConfiguration,
    ) -> None:
        """Atomically replace graph-validated routes for existing consumers."""

        async with self._lock:
            self.routes = tuple(routes)
            self.configuration = configuration
            self._state = _RuntimeState()

    @staticmethod
    def _model_key(route: AIRoute) -> str:
        return f"{route.provider_name}:{route.model}"

    @staticmethod
    def _credential_key(route: AIRoute) -> str:
        return f"{route.provider_name}:{route.credential_id}"

    def _circuit(self, collection: dict[str, _Circuit], key: str) -> _Circuit:
        return collection.setdefault(key, _Circuit())

    def _counter(self, collection: dict[str, _MinuteCounter], key: str) -> _MinuteCounter:
        return collection.setdefault(key, _MinuteCounter())

    def _group_circuits(self, route: AIRoute) -> tuple[_Circuit, ...]:
        return (
            self._circuit(self._state.route_circuits, route.route_id),
            self._circuit(self._state.credential_circuits, self._credential_key(route)),
            self._circuit(self._state.model_circuits, self._model_key(route)),
            self._circuit(self._state.provider_circuits, route.provider_name),
        )

    def _is_available(self, route: AIRoute, now: float) -> bool:
        return route.provider.configured and all(
            circuit.state(now) is not CircuitState.OPEN for circuit in self._group_circuits(route)
        )

    async def candidates(
        self,
        task: TaskConfiguration,
        *,
        task_id: str | None = None,
        force_provider: str | None = None,
    ) -> tuple[AIRoute, ...]:
        now = time.monotonic()
        forced = force_provider.upper() if force_provider else None
        async with self._lock:
            available = [
                route
                for route in self.routes
                if route.tier is task.tier
                and route.provider_name in task.allowedProviders
                and (forced is None or route.provider_name == forced)
                and (
                    not route.allowed_task_keys
                    or task_id is None
                    or task_id in route.allowed_task_keys
                )
                and self._is_available(route, now)
            ]
            grouped: dict[str, list[AIRoute]] = defaultdict(list)
            for route in available:
                grouped[route.provider_name].append(route)
            provider_names = sorted(
                grouped,
                key=lambda provider_name: min(
                    route.provider_priority for route in grouped[provider_name]
                ),
            )
            rotated_groups: dict[str, list[AIRoute]] = {}
            for provider_name in provider_names:
                provider_routes = grouped[provider_name]
                provider_routes.sort(
                    key=lambda route: (
                        self._state.active_routes[route.route_id],
                        self._circuit(
                            self._state.route_circuits, route.route_id
                        ).consecutive_failures,
                        route.model_priority,
                        route.credential_priority,
                    )
                )
                cursor = self._state.provider_route_cursors[provider_name] % len(
                    provider_routes
                )
                rotated_groups[provider_name] = (
                    provider_routes[cursor:] + provider_routes[:cursor]
                )
                self._state.provider_route_cursors[provider_name] = cursor + 1
            balanced: list[AIRoute] = []
            maximum_provider_routes = max(
                (len(routes) for routes in rotated_groups.values()),
                default=0,
            )
            for route_index in range(maximum_provider_routes):
                for provider_name in provider_names:
                    provider_routes = rotated_groups[provider_name]
                    if route_index < len(provider_routes):
                        balanced.append(provider_routes[route_index])
            return tuple(balanced)

    async def try_acquire(
        self,
        route: AIRoute,
        *,
        estimated_tokens: int,
    ) -> RouteAcquireResult:
        now = time.monotonic()
        async with self._lock:
            if not self._is_available(route, now):
                return RouteAcquireResult(False, "CIRCUIT_OPEN")

            app_limit = self.configuration.rateLimits.application
            tier_limit = (
                self.configuration.rateLimits.lightweight
                if route.tier is ModelTier.LIGHTWEIGHT
                else self.configuration.rateLimits.standard
            )
            provider_limit = self.configuration.providerLimits[route.provider_name]

            counters = (
                (self._state.application_counter, app_limit, "APPLICATION_RATE_LIMIT"),
                (
                    self._counter(self._state.tier_counters, route.tier.value),
                    tier_limit,
                    "TIER_RATE_LIMIT",
                ),
                (
                    self._counter(self._state.provider_counters, route.provider_name),
                    provider_limit,
                    "PROVIDER_RATE_LIMIT",
                ),
                (
                    self._counter(self._state.model_counters, self._model_key(route)),
                    provider_limit,
                    "MODEL_RATE_LIMIT",
                ),
                (
                    self._counter(self._state.credential_counters, self._credential_key(route)),
                    provider_limit,
                    "CREDENTIAL_RATE_LIMIT",
                ),
                (
                    self._counter(self._state.route_counters, route.route_id),
                    provider_limit,
                    "ROUTE_RATE_LIMIT",
                ),
            )
            for counter, limit, reason in counters:
                counter.reset_if_needed(now)
                if counter.requests >= limit.requestsPerMinute:
                    return RouteAcquireResult(False, reason)
                if counter.tokens + estimated_tokens > limit.tokensPerMinute:
                    return RouteAcquireResult(False, reason.replace("RATE", "TOKEN"))

            if self._state.active_tiers[route.tier.value] >= tier_limit.maximumConcurrency:
                return RouteAcquireResult(False, "TIER_CONCURRENCY_LIMIT")
            provider_concurrency = (
                provider_limit.maximumConcurrency or tier_limit.maximumConcurrency
            )
            if self._state.active_providers[route.provider_name] >= provider_concurrency:
                return RouteAcquireResult(False, "PROVIDER_CONCURRENCY_LIMIT")

            for counter, _limit, _reason in counters:
                counter.requests += 1
                counter.tokens += estimated_tokens
            self._state.active_routes[route.route_id] += 1
            self._state.active_models[self._model_key(route)] += 1
            self._state.active_credentials[self._credential_key(route)] += 1
            self._state.active_providers[route.provider_name] += 1
            self._state.active_tiers[route.tier.value] += 1
            return RouteAcquireResult(True, "ACQUIRED")

    async def release(self, route: AIRoute) -> None:
        async with self._lock:
            for collection, key in (
                (self._state.active_routes, route.route_id),
                (self._state.active_models, self._model_key(route)),
                (self._state.active_credentials, self._credential_key(route)),
                (self._state.active_providers, route.provider_name),
                (self._state.active_tiers, route.tier.value),
            ):
                collection[key] = max(0, collection[key] - 1)

    async def record_success(self, route: AIRoute) -> None:
        now_epoch = time.time()
        async with self._lock:
            for circuit in self._group_circuits(route):
                circuit.consecutive_failures = 0
                circuit.open_until = 0.0
                circuit.last_error = None
                circuit.last_success_at = now_epoch

    async def record_failure(self, route: AIRoute, error_code: str) -> None:
        now = time.monotonic()
        now_epoch = time.time()
        cfg = self.configuration.circuitBreaker
        async with self._lock:
            route_circuit = self._circuit(self._state.route_circuits, route.route_id)
            route_circuit.consecutive_failures += 1
            route_circuit.last_error = error_code
            route_circuit.last_failure_at = now_epoch

            targets: list[_Circuit] = [route_circuit]
            open_seconds = cfg.openSeconds
            if error_code == "AUTH_FAILED":
                targets.append(
                    self._circuit(self._state.credential_circuits, self._credential_key(route))
                )
                open_seconds = cfg.authFailureOpenSeconds
            elif error_code == "RATE_LIMITED":
                targets.append(
                    self._circuit(self._state.credential_circuits, self._credential_key(route))
                )
                open_seconds = cfg.rateLimitCooldownSeconds
            elif error_code in {"MODEL_UNAVAILABLE", "CONTEXT_LIMIT_EXCEEDED"}:
                targets.append(self._circuit(self._state.model_circuits, self._model_key(route)))
            elif error_code == "PROVIDER_UNAVAILABLE":
                targets.append(self._circuit(self._state.provider_circuits, route.provider_name))

            for circuit in targets:
                circuit.consecutive_failures += 1 if circuit is not route_circuit else 0
                circuit.last_error = error_code
                circuit.last_failure_at = now_epoch
                if (
                    error_code
                    in {
                        "AUTH_FAILED",
                        "RATE_LIMITED",
                        "MODEL_UNAVAILABLE",
                        "CONTEXT_LIMIT_EXCEEDED",
                    }
                    or circuit.consecutive_failures >= cfg.failureThreshold
                ):
                    circuit.open_until = max(circuit.open_until, now + open_seconds)

    async def health(self) -> tuple[RouteHealth, ...]:
        now = time.monotonic()
        async with self._lock:
            values: list[RouteHealth] = []
            for route in self.routes:
                counter = self._counter(self._state.route_counters, route.route_id)
                counter.reset_if_needed(now)
                circuit = self._circuit(self._state.route_circuits, route.route_id)
                group_state = max(
                    (item.state(now) for item in self._group_circuits(route)),
                    key=lambda state: {
                        CircuitState.CLOSED: 0,
                        CircuitState.HALF_OPEN: 1,
                        CircuitState.OPEN: 2,
                    }[state],
                )
                values.append(
                    RouteHealth(
                        routeId=route.route_id,
                        provider=route.provider_name,
                        model=route.model,
                        credentialId=route.credential_id,
                        tier=route.tier,
                        configured=route.provider.configured,
                        circuitState=group_state,
                        activeRequests=self._state.active_routes[route.route_id],
                        requestsThisMinute=counter.requests,
                        tokensThisMinute=counter.tokens,
                        lastError=circuit.last_error,
                        lastSuccessAtEpochMs=(
                            int(circuit.last_success_at * 1000)
                            if circuit.last_success_at is not None
                            else None
                        ),
                        lastFailureAtEpochMs=(
                            int(circuit.last_failure_at * 1000)
                            if circuit.last_failure_at is not None
                            else None
                        ),
                    )
                )
            return tuple(values)
