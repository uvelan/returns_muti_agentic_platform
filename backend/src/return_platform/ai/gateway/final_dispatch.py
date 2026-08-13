"""The one boundary an AI provider call crosses.

Three execution loops used to call `AIProvider.generate`: the eligibility
decision path (`service.py`), the structured-output path
(`structured_invocation.py`) and the dependency simulator's narrative path
(`dependency_simulation/ai.py`). They shared the route *pool* and the safety
*functions*, which is why the platform could describe itself as having one AI
path -- but each carried its own copy of the machinery around the call: its own
candidate loop, its own failover bookkeeping, its own retry policy, its own
attempt telemetry, and in one case its own cost arithmetic.

That is the structural defect, not a stylistic one. **A control attached to one
loop is absent from the other two.** Interception was real, audited and
operator-visible on the decision path and did not exist on the path the Order
Agent and the Graph Analyzer actually use. Recursive redaction had to be added
to each loop separately, and the simulator's loop never got it. Cost was priced
from the released catalog on two loops and computed from a local table that
returned `0` for an unpriced model on the third. None of those are bugs anyone
introduced; they are what having three loops *means*.

So this module owns everything that happens between "a caller has a payload" and
"a provider answered":

  interception decision -> precondition -> route selection -> acquire ->
  recursive redaction -> **the single `provider.generate` call** -> output
  safety -> caller validation -> failover bookkeeping -> priced telemetry

and callers own only what genuinely differs between them: how the payload is
built, how the response is parsed, and what else must be persisted alongside.
Those arrive as `DispatchRequest`, a `validate` callable and a
`DispatchObserver`. A fourth caller should need no edit to this file.

**Why the interception decision lives here rather than in each caller.** C7
requires exactly one of `ALLOW_PROVIDER`, `HUMAN_RESPONSE` or `REJECT` before any
external call. A decision point that each caller opts into is a decision point
each caller can forget -- which is precisely the state that produced AI-01. Here
it is unconditional: `dispatch()` asks the policy before it looks at a route, so
a caller cannot reach a provider without a verdict. What a caller supplies is
*which* policy, and the default (`ALLOW_ALL`) is an explicit, greppable choice
rather than an omission.

**`DurableInterceptionProvider` is not this.** It is a MANUAL provider, gated to
development and test, that *replaces* the model with a human. It is reached
through a route like any other provider, and it reports `MANUAL` so a human
answer is never laundered as model output. Gating dispatch and substituting the
thing dispatched to are different mechanisms; conflating them is how "we have
interception" came to be believed about paths that had none.
"""

from __future__ import annotations

import asyncio
import hashlib
import random
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from return_platform.ai.gateway.redaction import redact_payload
from return_platform.ai.gateway.telemetry import (
    AIAttemptRecord,
    AIAttemptRecorder,
    InvocationCorrelation,
)
from return_platform.ai.pricing import AICostEstimate
from return_platform.ai.providers import ProviderError, ProviderRequest, ProviderResponse
from return_platform.ai.routing.routes import AIRoute
from return_platform.ai.routing.selection import AIRoutePool
from return_platform.ai.routing.tasks import (
    AIGatewayConfiguration,
    ModelTier,
    TaskConfiguration,
)
from return_platform.ai.safety import SafetyStatus, inspect_output
from return_platform.configuration.settings import Settings

__all__ = [
    "ALLOW_ALL",
    "DispatchDecision",
    "DispatchObserver",
    "DispatchOutcome",
    "DispatchRequest",
    "FinalDispatcher",
    "InterceptionPolicy",
    "InterceptionVerdict",
]


class DispatchDecision(StrEnum):
    """The three outcomes C7 permits, and no fourth.

    `REJECT` covers every refusal that is not a human answering in the model's
    place -- an interception policy declining the call, and a caller-supplied
    precondition (a quota) declining it. They are distinguishable by
    `DispatchOutcome.reason`, which carries the error code, rather than by a
    proliferation of decision values that callers would have to enumerate.
    """

    ALLOW_PROVIDER = "ALLOW_PROVIDER"
    HUMAN_RESPONSE = "HUMAN_RESPONSE"
    REJECT = "REJECT"


@dataclass(frozen=True, slots=True)
class InterceptionVerdict:
    """One policy answer. `reason` is recorded, so it must be an error code."""

    decision: DispatchDecision
    response_text: str | None = None
    reason: str | None = None


class InterceptionPolicy:
    """Consulted once per invocation, before a route is even selected.

    A class with a permissive default rather than a Protocol so that
    `ALLOW_ALL` is a real object a test can assert identity against, and so a
    caller that wants interception overrides one method.
    """

    async def decide(self, request: DispatchRequest) -> InterceptionVerdict:
        return _ALLOW_VERDICT


_ALLOW_VERDICT = InterceptionVerdict(decision=DispatchDecision.ALLOW_PROVIDER)

#: The explicit "this path is not intercepted yet" marker. AI-01 replaces these
#: with real policies; until it does, `grep ALLOW_ALL` enumerates exactly which
#: callers still bypass operator inspection.
ALLOW_ALL = InterceptionPolicy()


@dataclass(frozen=True, slots=True)
class DispatchRequest:
    """Everything the boundary needs, and nothing about who is asking.

    `payload` is the *unredacted* payload. Redaction is deliberately not the
    caller's job: a caller that forgot would be indistinguishable from one that
    had nothing to redact, and that is the mistake `structured_invocation` made
    for its whole life. It happens once, here, at `ProviderRequest`
    construction.
    """

    task_id: str
    task: TaskConfiguration
    system_prompt: str
    payload: Mapping[str, Any]
    request_digest: str
    estimated_tokens: int
    correlation: InvocationCorrelation = InvocationCorrelation()
    #: The input-safety verdict the caller already computed. Recorded on every
    #: attempt so a row says what was known about the request, not only about
    #: the response.
    safety_status: str = SafetyStatus.SAFE.value
    max_output_tokens: int | None = None
    temperature: float = 0.0
    response_schema: dict[str, Any] | None = None
    force_provider: str | None = None
    #: Restricts candidates further than the task's own `allowedProviders`. The
    #: dependency simulator narrows to its configured provider order; nothing
    #: else uses it. `None` means "whatever the task permits".
    provider_allowlist: frozenset[str] | None = None
    #: A per-attempt ceiling tighter than `ai_timeout_seconds`, for a caller
    #: whose work is optional and must not hold a request open.
    attempt_timeout_seconds: float | None = None
    #: Retry the whole candidate set at LIGHTWEIGHT after STANDARD is exhausted.
    #: Explicit rather than read from `task.allowTierEscalation` because the
    #: decision path has never escalated and silently gaining it here would be a
    #: behaviour change smuggled in by a refactor.
    allow_tier_escalation: bool = False
    #: Runs after ALLOW_PROVIDER and before any route is selected. Returns an
    #: error code to abort as `REJECT`, or `None` to proceed. This is where a
    #: quota lives: it must not be spent on a request a human intercepted.
    precondition: Callable[[], Awaitable[str | None]] | None = None


@dataclass(frozen=True, slots=True)
class DispatchOutcome[ValueT]:
    """What happened, in enough detail that no caller re-derives it."""

    decision: DispatchDecision
    value: ValueT | None = None
    response: ProviderResponse | None = None
    route: AIRoute | None = None
    latency_ms: int = 0
    attempts: int = 0
    last_error: str | None = None
    failure_summary: Mapping[str, int] = field(default_factory=dict)
    fallback_reason: str | None = None
    reason: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.decision is DispatchDecision.ALLOW_PROVIDER and self.response is not None


class DispatchObserver:
    """Caller-side effects around each attempt, with no-op defaults.

    The decision path persists an `AITrace` at four points inside one attempt;
    the structured path writes six log lines; the simulator writes a domain
    metric row. None of that belongs in the boundary, and none of it may be
    duplicated *as* the boundary. Overriding a hook is how a caller keeps its
    own bookkeeping without owning a provider loop.
    """

    async def on_route_skipped(self, *, route: AIRoute, attempt: int, reason: str) -> None:
        """A route the pool declined to lend -- rate limit, circuit, concurrency."""

    async def on_attempt_started(self, *, route: AIRoute, attempt: int) -> None: ...

    async def on_response(
        self,
        *,
        route: AIRoute,
        attempt: int,
        response: ProviderResponse,
        latency_ms: int,
        cost: AICostEstimate,
    ) -> None:
        """A provider answered. Fires before validation, because a response that
        fails validation still happened and still cost money."""

    async def on_attempt_succeeded(
        self, *, route: AIRoute, attempt: int, response: ProviderResponse, latency_ms: int
    ) -> None: ...

    async def on_attempt_failed(
        self,
        *,
        route: AIRoute,
        attempt: int,
        error_code: str,
        latency_ms: int,
        error: BaseException | None,
    ) -> None: ...

    async def on_tier_escalation(self, *, attempts: int, last_error: str) -> None: ...

    async def on_exhausted(self, *, attempts: int, last_error: str, breakdown: Mapping[str, int]) -> None: ...

    async def on_record_failed(self, *, trace_id: str, error: BaseException) -> None:
        """Telemetry could not be written. Never fails the caller's work, but a
        silently absent metrics stream is how a cost report comes to cover a
        third of traffic and nobody notices for a quarter."""


#: Errors that mean "this route will fail the same way if asked again". Retrying
#: the same route on any of them spends the global deadline to learn nothing.
_TERMINAL_FOR_ROUTE = frozenset(
    {"AUTH_FAILED", "RATE_LIMITED", "POLICY_BLOCKED", "RESPONSE_INVALID"}
)


class FinalDispatcher:
    """The single implementation of route selection, failover, pricing and
    telemetry, and the single caller of `AIProvider.generate`.

    One instance may serve many tasks and many callers; it holds no per-request
    state. `route_pool` is shared deliberately -- circuit state learned from a
    reasoning call must be visible to the next eligibility call, or a dead
    credential is rediscovered once per caller.
    """

    def __init__(
        self,
        *,
        settings: Settings,
        configuration: AIGatewayConfiguration,
        route_pool: AIRoutePool,
        recorder: AIAttemptRecorder | None = None,
        interception: InterceptionPolicy = ALLOW_ALL,
    ) -> None:
        self._settings = settings
        self._configuration = configuration
        self._route_pool = route_pool
        self._recorder = recorder
        self._interception = interception

    @property
    def route_pool(self) -> AIRoutePool:
        return self._route_pool

    @property
    def interception(self) -> InterceptionPolicy:
        return self._interception

    def price(
        self,
        *,
        route: AIRoute | None,
        input_tokens: int,
        cached_input_tokens: int,
        output_tokens: int,
    ) -> AICostEstimate:
        """The only place an invocation is costed.

        Priced against the release's own catalog at the moment of the call, so a
        vendor changing a rate in June cannot re-cost a March invocation. A
        provider or model the catalog does not cover yields `UNKNOWN` with
        `amount_micros=None` -- never `0`, which sums and charts and makes real
        spend look free.
        """
        return self._configuration.pricing.estimate(
            provider=route.provider_name if route else None,
            model=route.model if route else None,
            at=datetime.now(UTC),
            input_tokens=max(0, input_tokens),
            cached_input_tokens=max(0, cached_input_tokens),
            output_tokens=max(0, output_tokens),
        )

    async def record_attempt(
        self,
        *,
        trace_id: str,
        request: DispatchRequest,
        route: AIRoute | None,
        attempt_number: int,
        status: str,
        selection_reason: str,
        safety_status: str | None = None,
        latency_ms: int = 0,
        response: ProviderResponse | None = None,
        response_digest: str | None = None,
        error_code: str | None = None,
        fallback_reason: str | None = None,
        cost: AICostEstimate | None = None,
        observer: DispatchObserver | None = None,
    ) -> None:
        """The only writer of an AI attempt row.

        Public because attempts that never reach a provider -- a safety
        rejection, an exhausted quota, a deterministic fallback -- are still
        attempts, and recording them through a second code path is how the two
        halves of one collection come to disagree about column names. A caller
        that has no provider to name passes `route=None`.
        """
        if self._recorder is None:
            return
        input_tokens = max(0, int(response.input_tokens or 0)) if response else 0
        cached_input_tokens = response.cached_input_tokens if response else None
        output_tokens = max(0, int(response.output_tokens or 0)) if response else 0
        record = AIAttemptRecord(
            trace_id=trace_id,
            task_id=request.task_id,
            prompt_version=request.task.promptVersion,
            attempt_number=attempt_number,
            status=status,
            configured_tier=request.task.tier.value,
            selected_tier=route.tier.value if route else None,
            provider=route.provider_name if route else None,
            model=route.model if route else None,
            credential_id=route.credential_id if route else None,
            route_id=route.route_id if route else None,
            selection_reason=selection_reason,
            fallback_used=fallback_reason is not None,
            fallback_reason=fallback_reason,
            safety_status=safety_status or request.safety_status,
            latency_ms=latency_ms,
            rate_limit_wait_ms=0,
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            output_tokens=output_tokens,
            total_tokens=(max(0, int(response.total_tokens or 0)) if response else 0)
            or (input_tokens + output_tokens),
            cost=cost
            or self.price(
                route=route,
                input_tokens=input_tokens,
                cached_input_tokens=int(cached_input_tokens or 0),
                output_tokens=output_tokens,
            ),
            correlation=request.correlation,
            request_digest=request.request_digest,
            response_digest=response_digest,
            error_code=error_code,
        )
        try:
            await self._recorder.record(record)
        except Exception as error:  # Telemetry is not the work.
            if observer is not None:
                await observer.on_record_failed(trace_id=trace_id, error=error)

    async def dispatch[ValueT](
        self,
        request: DispatchRequest,
        *,
        trace_id: str,
        validate: Callable[[ProviderResponse], ValueT],
        observer: DispatchObserver | None = None,
    ) -> DispatchOutcome[ValueT]:
        """Take one invocation as far as the policy and the routes allow.

        `validate` turns a provider response into the caller's own result type
        and raises to reject it. Raising `ProviderError` names the failure;
        raising `ValueError`/`TypeError` is recorded as `RESPONSE_INVALID`. A
        rejected response fails *this attempt*, so the failover loop tries the
        next route rather than handing the caller something unparsed.
        """
        watcher = observer or _NULL_OBSERVER

        verdict = await self._interception.decide(request)
        if verdict.decision is not DispatchDecision.ALLOW_PROVIDER:
            return DispatchOutcome(
                decision=verdict.decision,
                response=(
                    _human_response(request, verdict.response_text)
                    if verdict.decision is DispatchDecision.HUMAN_RESPONSE
                    and verdict.response_text is not None
                    else None
                ),
                reason=verdict.reason,
            )

        if request.precondition is not None:
            refusal = await request.precondition()
            if refusal is not None:
                return DispatchOutcome(decision=DispatchDecision.REJECT, reason=refusal)

        deadline = time.monotonic() + self._settings.ai_global_timeout_seconds
        failures: Counter[str] = Counter()
        state = _LoopState(attempts=0, last_error="PROVIDER_UNAVAILABLE")

        candidates = await self._candidates(request, tier=None)
        outcome = await self._attempt_routes(
            candidates,
            request=request,
            trace_id=trace_id,
            validate=validate,
            observer=watcher,
            deadline=deadline,
            state=state,
            failures=failures,
            fallback_reason=None,
        )
        if outcome is not None:
            return outcome

        if request.allow_tier_escalation:
            escalated = await self._candidates(request, tier=ModelTier.LIGHTWEIGHT)
            if escalated:
                await watcher.on_tier_escalation(
                    attempts=state.attempts, last_error=state.last_error
                )
                outcome = await self._attempt_routes(
                    escalated,
                    request=request,
                    trace_id=trace_id,
                    validate=validate,
                    observer=watcher,
                    deadline=deadline,
                    state=state,
                    failures=failures,
                    # Every attempt past this line is the escalation, and the
                    # reason it happened is the error that exhausted STANDARD.
                    fallback_reason=state.last_error,
                )
                if outcome is not None:
                    return outcome

        await watcher.on_exhausted(
            attempts=state.attempts, last_error=state.last_error, breakdown=dict(failures)
        )
        return DispatchOutcome(
            decision=DispatchDecision.ALLOW_PROVIDER,
            attempts=state.attempts,
            last_error=state.last_error,
            failure_summary=dict(failures),
        )

    async def _candidates(
        self, request: DispatchRequest, *, tier: ModelTier | None
    ) -> tuple[AIRoute, ...]:
        """The only call to `AIRoutePool.candidates` on any dispatch path."""
        task = request.task if tier is None else request.task.model_copy(update={"tier": tier})
        routes = await self._route_pool.candidates(
            task,
            task_id=request.task_id,
            force_provider=(
                request.force_provider.strip().upper() if request.force_provider else None
            ),
        )
        if request.provider_allowlist is None:
            return routes
        return tuple(route for route in routes if route.provider_name in request.provider_allowlist)

    async def _attempt_routes[ValueT](
        self,
        candidates: tuple[AIRoute, ...],
        *,
        request: DispatchRequest,
        trace_id: str,
        validate: Callable[[ProviderResponse], ValueT],
        observer: DispatchObserver,
        deadline: float,
        state: _LoopState,
        failures: Counter[str],
        fallback_reason: str | None,
    ) -> DispatchOutcome[ValueT] | None:
        """The only failover loop. Returns an outcome, or `None` if exhausted."""
        retry = self._configuration.retry
        for route in candidates:
            for route_attempt in range(retry.maximumAttemptsPerRoute):
                if state.attempts >= retry.maximumTotalAttempts:
                    return None
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    state.last_error = "TIMEOUT"
                    failures[state.last_error] += 1
                    return None

                acquired = await self._route_pool.try_acquire(
                    route, estimated_tokens=request.estimated_tokens
                )
                if not acquired.acquired:
                    state.last_error = acquired.reason
                    await observer.on_route_skipped(
                        route=route, attempt=state.attempts, reason=acquired.reason
                    )
                    await self.record_attempt(
                        trace_id=trace_id,
                        request=request,
                        route=route,
                        attempt_number=state.attempts,
                        status="SKIPPED",
                        selection_reason=acquired.reason,
                        error_code=acquired.reason,
                        fallback_reason=fallback_reason,
                        observer=observer,
                    )
                    # Move to the next route rather than re-asking this one:
                    # a circuit or a per-minute counter will not have changed
                    # in the microseconds an inner retry would take.
                    break

                state.attempts += 1
                started = time.monotonic()
                await observer.on_attempt_started(route=route, attempt=state.attempts)

                error: BaseException | None = None
                # Bound only on the success path, and read only there. A tuple
                # rather than four separately-initialised locals so "the attempt
                # produced a usable answer" is one condition a reader can see.
                produced: tuple[ProviderResponse, ValueT, AICostEstimate, str, int] | None = None
                try:
                    attempt_timeout = min(
                        self._settings.ai_timeout_seconds,
                        max(0.05, remaining),
                        request.attempt_timeout_seconds or self._settings.ai_timeout_seconds,
                    )
                    response = await asyncio.wait_for(
                        # THE dispatch. Every AI request this platform makes
                        # passes through this expression; an architecture test
                        # enumerates `AIProvider.generate` call sites to keep it
                        # that way.
                        route.provider.generate(
                            ProviderRequest(
                                system_prompt=request.system_prompt,
                                # Recursive redaction, at the last point before
                                # the request leaves the platform. Applied here
                                # rather than by each caller because a caller
                                # that forgot would look exactly like a caller
                                # with nothing to redact.
                                user_payload=redact_payload(dict(request.payload)),
                                max_output_tokens=request.max_output_tokens,
                                temperature=request.temperature,
                                response_schema=request.response_schema,
                            )
                        ),
                        timeout=attempt_timeout,
                    )
                    latency = _elapsed_ms(started)
                    cost = self.price(
                        route=route,
                        input_tokens=int(response.input_tokens or 0),
                        cached_input_tokens=int(response.cached_input_tokens or 0),
                        output_tokens=int(response.output_tokens or 0),
                    )
                    await observer.on_response(
                        route=route,
                        attempt=state.attempts,
                        response=response,
                        latency_ms=latency,
                        cost=cost,
                    )
                    output_safety = inspect_output(response.text)
                    if not output_safety.allowed:
                        raise ProviderError("POLICY_BLOCKED")
                    produced = (
                        response,
                        validate(response),
                        cost,
                        output_safety.status.value,
                        latency,
                    )
                except TimeoutError as exc:
                    state.last_error, error = "TIMEOUT", exc
                except ProviderError as exc:
                    state.last_error, error = exc.code, exc
                except (ValueError, TypeError) as exc:
                    state.last_error, error = "RESPONSE_INVALID", exc
                except Exception as exc:  # Defensive provider boundary.
                    state.last_error, error = "PROVIDER_UNAVAILABLE", exc

                await self._route_pool.release(route)

                if produced is not None:
                    response, value, cost, safety_value, latency = produced
                    await self._route_pool.record_success(route)
                    await observer.on_attempt_succeeded(
                        route=route,
                        attempt=state.attempts,
                        response=response,
                        latency_ms=latency,
                    )
                    await self.record_attempt(
                        trace_id=trace_id,
                        request=request,
                        route=route,
                        attempt_number=state.attempts,
                        status="SUCCESS",
                        selection_reason="HEALTHY_ROUTE_SELECTED",
                        safety_status=safety_value,
                        latency_ms=latency,
                        response=response,
                        response_digest=hashlib.sha256(response.text.encode("utf-8")).hexdigest(),
                        fallback_reason=fallback_reason,
                        cost=cost,
                        observer=observer,
                    )
                    return DispatchOutcome(
                        decision=DispatchDecision.ALLOW_PROVIDER,
                        value=value,
                        response=response,
                        route=route,
                        latency_ms=latency,
                        attempts=state.attempts,
                        failure_summary=dict(failures),
                        fallback_reason=fallback_reason,
                    )

                latency = _elapsed_ms(started)
                await self._route_pool.record_failure(route, state.last_error)
                failures[state.last_error] += 1
                await observer.on_attempt_failed(
                    route=route,
                    attempt=state.attempts,
                    error_code=state.last_error,
                    latency_ms=latency,
                    error=error,
                )
                await self.record_attempt(
                    trace_id=trace_id,
                    request=request,
                    route=route,
                    attempt_number=state.attempts,
                    status="FAILED",
                    selection_reason="ROUTE_FAILED",
                    latency_ms=latency,
                    error_code=state.last_error,
                    fallback_reason=fallback_reason,
                    observer=observer,
                )
                if state.last_error in _TERMINAL_FOR_ROUTE:
                    break
                if route_attempt + 1 < retry.maximumAttemptsPerRoute:
                    await asyncio.sleep(self._backoff_seconds(route_attempt))
        return None

    def _backoff_seconds(self, route_attempt: int) -> float:
        retry = self._configuration.retry
        base_ms = min(
            retry.maximumBackoffMilliseconds,
            retry.initialBackoffMilliseconds * (2**route_attempt),
        )
        jitter = random.randint(0, max(1, base_ms // 4)) if retry.jitter else 0
        return (base_ms + jitter) / 1000


@dataclass(slots=True)
class _LoopState:
    """Attempt count and last error, carried across a tier escalation.

    Mutable and passed by reference so the escalated pass continues the same
    attempt budget rather than starting a second one -- `maximumTotalAttempts`
    is a per-invocation ceiling, not a per-tier one.
    """

    attempts: int
    last_error: str


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1_000))


def _human_response(request: DispatchRequest, text: str) -> ProviderResponse:
    """A human answer, reported as MANUAL.

    Never attributed to the provider the route would have used: an evaluation
    set built from these rows would otherwise be measuring human text as though
    a model had produced it.
    """
    del request
    return ProviderResponse(provider="MANUAL", model="manual-human-v1", text=text)


_NULL_OBSERVER = DispatchObserver()
