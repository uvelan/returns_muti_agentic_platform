"""Task → route selection, health, quota, concurrency, and circuit state.

`routes.py` builds the immutable set of routes a configuration permits; this module
owns everything that changes at runtime about them. Splitting the two keeps route
construction free of mutable state, so a configuration reload can hand
:meth:`AIRoutePool.replace_routes` a fresh tuple without reasoning about counters.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum

from return_platform.ai.routing.routes import AIRoute
from return_platform.ai.routing.tasks import (
    AIGatewayConfiguration,
    ModelTier,
    TaskConfiguration,
)

logger = logging.getLogger("return_platform.ai.routing.selection")


class CircuitState(StrEnum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


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
        self._report_context_shortfalls()

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
        self._report_context_shortfalls()

    def _report_context_shortfalls(self) -> None:
        """Say at build time which route cannot serve which task, and why.

        A model whose context window is smaller than a task's
        `maximumInputTokens` cannot serve that task and never could:
        `nvidia/nemotron-mini-4b-instruct` answered every
        `ORDER_AGENT_REASONING_V1` call with `HTTP 400 -- maximum context length
        is 4096 tokens, however you requested 24014`. Nothing in the platform
        knew that before the call, so the only place it surfaced was one failed
        provider round trip per turn, indistinguishable in the health view from a
        model having a bad afternoon.

        Logged here rather than raised. Refusing to *build* the route would be
        wrong on its face -- a 4k model is perfectly capable of
        `RETURN_STATUS_SUMMARY_V1`, and the incompatibility is a property of the
        pair, not of the route -- and refusing to build the *pool* would take a
        process down over a configuration line an operator can fix. `candidates`
        enforces the same rule per selection; this exists so the operator learns
        it at startup and from a release activation, rather than from a metric.
        """
        for route in self.routes:
            for task_id, task in sorted(self.configuration.tasks.items()):
                if route.provider_name not in task.allowedProviders or route.tier is not task.tier:
                    continue
                shortfall = self.configuration.context_shortfall(
                    provider=route.provider_name, model=route.model, task=task
                )
                if shortfall is None:
                    continue
                window, required = shortfall
                logger.warning(
                    "ai_route_context_window_too_small route_id=%s task=%s window=%d required=%d",
                    route.route_id,
                    task_id,
                    window,
                    required,
                    extra={
                        "route_id": route.route_id,
                        "provider": route.provider_name,
                        "model": route.model,
                        "task_id": task_id,
                        "maximum_context_tokens": window,
                        "maximum_input_tokens": required,
                    },
                )

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
            available = []
            for route in self.routes:
                reason = "allowed"
                if route.tier is not task.tier:
                    reason = f"tier mismatch: {route.tier} != {task.tier}"
                elif route.provider_name not in task.allowedProviders:
                    reason = f"provider not allowed: {route.provider_name}"
                elif forced is not None and route.provider_name != forced:
                    reason = "forced mismatch"
                elif (
                    route.allowed_task_keys
                    and task_id is not None
                    and task_id not in route.allowed_task_keys
                ):
                    reason = "task not in allowed_task_keys"
                elif (
                    shortfall := self.configuration.context_shortfall(
                        provider=route.provider_name, model=route.model, task=task
                    )
                ) is not None:
                    # Refused before the request rather than after the provider's
                    # 400. A model that cannot read the prompt is not a route
                    # having a bad minute, so it must not open a circuit, consume
                    # an attempt from the retry budget, or record a failure
                    # against a credential that is working perfectly well.
                    reason = (
                        f"model context {shortfall[0]} < task maximumInputTokens {shortfall[1]}"
                    )
                elif not self._is_available(route, now):
                    reason = f"not available (configured={route.provider.configured})"
                logger.debug(
                    "ai_route_candidate_evaluated",
                    extra={"route_id": route.route_id, "reason": reason},
                )
                if reason == "allowed":
                    available.append(route)
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
                cursor = self._state.provider_route_cursors[provider_name] % len(provider_routes)
                rotated_groups[provider_name] = provider_routes[cursor:] + provider_routes[:cursor]
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
