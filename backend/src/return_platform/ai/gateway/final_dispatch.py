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
  recursive redaction -> **the single `provider.generate` call** -> response
  review -> output safety -> caller validation -> failover bookkeeping ->
  priced telemetry

and callers own only what genuinely differs between them: how the payload is
built, how the response is parsed, and what else must be persisted alongside.
Those arrive as `DispatchRequest`, a `validate` callable and a
`DispatchObserver`. A fourth caller should need no edit to this file.

**There are two interception points, and they answer different questions.** The
first, before the call, decides whether the provider may be reached at all --
and if not, a human may answer in its place. The second, after the call and
before anything consumes the reply, decides what happens to what came back:
accept it, edit it, or reject it. The ordering above is the whole design: review
sits *before* output safety and caller validation, so a human's edit is checked
by exactly the machinery that checks a model's answer.

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
import logging
import random
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
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
from return_platform.ai.providers import (
    ProviderError,
    ProviderRequest,
    ProviderResponse,
)
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
    "RESPONSE_REJECTED",
    "RESPONSE_REVIEW_EXPIRED",
    "DispatchDecision",
    "DispatchObserver",
    "DispatchOutcome",
    "DispatchRequest",
    "FinalDispatcher",
    "InterceptionPolicy",
    "InterceptionVerdict",
    "ResponseDecision",
    "ResponseVerdict",
]

#: The boundary's own logger, used for exactly one thing: a caller-supplied
#: callback that raised. Every *narrative* event here belongs to the caller and
#: goes through `DispatchObserver`, because the same dispatch serves callers
#: whose log context is different. A hook that threw is not narrative -- it is a
#: bug in code this module called, with nowhere else to surface.
logger = logging.getLogger("return_platform.ai.gateway.final_dispatch")

#: An operator read the model's answer and refused it. An attempt error code
#: rather than a dispatch-level decision, because that is what it is: a
#: statement about *this* answer from *this* route, not about the request. The
#: request point already has a way to refuse a request (cancel), and conflating
#: the two would let one rejected answer suppress a healthy fallback route that
#: might have answered correctly.
RESPONSE_REJECTED = "RESPONSE_REJECTED"

#: Nobody reviewed the held response before its deadline. Distinct from a
#: rejection for the same reason `EXPIRED` is distinct from `CANCELLED` in the
#: store: "nobody got to it" is a staffing fact and "we looked and said no" is a
#: quality one, and one reported as the other hides a rota problem inside what
#: looks like ordinary model failure.
RESPONSE_REVIEW_EXPIRED = "RESPONSE_REVIEW_EXPIRED"


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


class ResponseDecision(StrEnum):
    """The three outcomes of reviewing a response, and no fourth.

    Deliberately parallel to `DispatchDecision` without being it. The request
    point decides whether a model may be *called*; this one decides what happens
    to what it *said*. `ALLOW_PROVIDER` has no meaning after the provider has
    already answered, and `HUMAN_RESPONSE` cannot express "a human changed the
    model's words" -- which is the whole reason this point exists.
    """

    ACCEPTED = "ACCEPTED"
    EDITED = "EDITED"
    REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True)
class ResponseVerdict:
    """One review answer.

    `response` is populated only for `EDITED`, and it is a *replacement*
    `ProviderResponse` carrying the third identity -- `HUMAN_EDITED` with a
    `HumanEdit` naming the model it came from -- rather than the edited text on
    its own. Handing back bare text here would leave it to this module to
    reattach provenance, which is exactly the step a future edit forgets.
    """

    decision: ResponseDecision
    response: ProviderResponse | None = None
    reason: str | None = None


class InterceptionPolicy:
    """Consulted once per invocation, before a route is even selected -- and,
    if it says so, once more after the provider has answered.

    A class with a permissive default rather than a Protocol so that
    `ALLOW_ALL` is a real object a test can assert identity against, and so a
    caller that wants interception overrides one method.

    **Two points, one object.** `review` lives here rather than on a second
    constructor argument because a process that can hold a request is exactly a
    process that can hold a response: it has the store, the queue, the operator
    endpoints and the sealing. A separate `response_interception=` parameter
    would have been a second thing every one of the platform's dispatcher
    construction sites had to remember, and "a control each caller opts into is
    a control each caller can forget" is the finding this whole boundary exists
    to answer.
    """

    #: Whether `review` does anything. Read once per attempt, and the entire
    #: cost of this feature when it is off.
    #:
    #: A flag rather than "just call `review` and let the base class return
    #: immediately" because the requirement is that a deployment with response
    #: interception disabled behaves *identically to today* -- no extra await,
    #: no extra store write, no extra latency. One attribute read is a claim a
    #: test can make; "an await that usually returns fast" is not.
    reviews_responses: bool = False

    async def decide(self, request: DispatchRequest) -> InterceptionVerdict:
        return _ALLOW_VERDICT

    async def review(self, request: DispatchRequest, response: ProviderResponse) -> ResponseVerdict:
        """What to do with what the provider said. Never reached unless
        `reviews_responses` is true."""
        del request, response
        return _ACCEPT_UNCHANGED


_ALLOW_VERDICT = InterceptionVerdict(decision=DispatchDecision.ALLOW_PROVIDER)
_ACCEPT_UNCHANGED = ResponseVerdict(decision=ResponseDecision.ACCEPTED)

#: The explicit "this path is not intercepted yet" marker. AI-01 replaces these
#: with real policies; until it does, `grep ALLOW_ALL` enumerates exactly which
#: callers still bypass operator inspection.
ALLOW_ALL = InterceptionPolicy()

#: Shared because `InvocationCorrelation` is frozen: a caller with no business
#: identifiers gets the same empty record rather than a fresh allocation.
_NO_CORRELATION = InvocationCorrelation()


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
    correlation: InvocationCorrelation = field(default=_NO_CORRELATION)
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
    #: Rebuilds the payload after `validate` rejected a response, given the
    #: payload that produced it and the exception that rejected it. What comes
    #: back is what every *subsequent* attempt sends.
    #:
    #: This exists because retrying an identical payload on the next route makes
    #: the second route repeat the first one's mistake with no way of knowing
    #: there was one -- and when the next route is MANUAL, the "next route" is a
    #: person. A human answering a hold that carries no diagnosis is being asked
    #: to correct a response they were never shown, which is what
    #: `breakdown=RESPONSE_INVALIDx2` looked like from the operator console.
    #:
    #: Caller-supplied because the boundary must not learn any caller's payload
    #: vocabulary; the boundary owns *when* it fires, that the result is
    #: redacted before it is sent or sealed, and that a hook which raises cannot
    #: take the failover loop down with it.
    #:
    #: `request_digest` is deliberately *not* recomputed when this fires. It
    #: identifies the invocation -- it is what ties every attempt row of one
    #: dispatch together and what `interception_id_for` derives a resumable
    #: identity from -- so a diagnosis appended between attempts must not split
    #: one turn into two identities, which would have the resumed call open a
    #: second interception and ask a human the same question twice.
    on_response_invalid: Callable[[Mapping[str, Any], BaseException], Mapping[str, Any]] | None = (
        None
    )


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

    async def on_exhausted(
        self, *, attempts: int, last_error: str, breakdown: Mapping[str, int]
    ) -> None: ...

    async def on_record_failed(self, *, trace_id: str, error: BaseException) -> None:
        """Telemetry could not be written. Never fails the caller's work, but a
        silently absent metrics stream is how a cost report comes to cover a
        third of traffic and nobody notices for a quarter."""


#: Errors that mean "this route will fail the same way if asked again". Retrying
#: the same route on any of them spends the global deadline to learn nothing.
#:
#: The two review codes are here for a sharper version of the same reason. Asking
#: the same model the same question again would produce the same answer and put
#: it in front of the same operator a second time -- so a retry does not spend
#: the deadline to learn nothing, it spends a *human's* time to learn nothing.
#: Moving to the next route is what the loop already does for `RESPONSE_INVALID`,
#: which is the same category of event with a worse judge.
_TERMINAL_FOR_ROUTE = frozenset(
    {
        "AUTH_FAILED",
        "RATE_LIMITED",
        "POLICY_BLOCKED",
        "RESPONSE_INVALID",
        RESPONSE_REJECTED,
        RESPONSE_REVIEW_EXPIRED,
    }
)


class FinalDispatcher:
    """The single implementation of route selection, failover, pricing and
    telemetry, and the single caller of `AIProvider.generate`.

    One instance may serve many tasks and many callers; it holds no per-request
    state. `route_pool` is shared deliberately -- circuit state learned from a
    reasoning call must be visible to the next eligibility call, or a dead
    credential is rediscovered once per caller.

    **There is deliberately no `configuration` argument.** The active document is
    read from the route pool on every dispatch, because `replace_routes` swaps
    routes and configuration together under the pool's own lock -- it is the one
    object in the process that is already atomically updated when a release is
    activated. A dispatcher holding its own copy would answer "which
    configuration is in force" differently from the pool it dispatches over, and
    a constructor copy is how task-level settings came to be pinned for the
    lifetime of a process while route changes hot-applied around them.
    """

    def __init__(
        self,
        *,
        settings: Settings,
        route_pool: AIRoutePool,
        recorder: AIAttemptRecorder | None = None,
        # No default. AI-01 was not a missing mechanism -- it was a mechanism two
        # of three callers never opted into, and a defaulted parameter is what
        # made not opting in the silent option. `ALLOW_ALL` is still available
        # and is still the right answer for a process with no interception store,
        # but it now has to be typed out at the call site.
        interception: InterceptionPolicy,
    ) -> None:
        self._settings = settings
        self._route_pool = route_pool
        self._recorder = recorder
        self._interception = interception

    @property
    def route_pool(self) -> AIRoutePool:
        return self._route_pool

    @property
    def configuration(self) -> AIGatewayConfiguration:
        """The document in force *now*, not the one this object was built with."""
        return self._route_pool.configuration

    def task(self, task_id: str) -> TaskConfiguration | None:
        """The task as currently released.

        Callers resolve their task through here on every invocation rather than
        caching it, so promptVersion, tier, token ceilings, allowed providers,
        retry limits and pricing all follow an activated release the same way
        routes already did.
        """
        return self.configuration.tasks.get(task_id)

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
        return self.configuration.pricing.estimate(
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
            # Carried from the response rather than passed separately, so a row
            # cannot report a provider's answer while the object it was built
            # from says a human rewrote it.
            human_edit=response.human_edit if response else None,
            # The bodies behind the digests. The recorder decides whether these
            # persist anywhere: the telemetry row never carries them, and only a
            # recorder wired with a trace sink writes them to `ai_traces`.
            system_prompt=request.system_prompt,
            payload=dict(request.payload),
            response_text=response.text if response else None,
        )
        try:
            await self._recorder.record(record)
        except Exception as error:  # Telemetry is not the work.
            if observer is not None:
                await observer.on_record_failed(trace_id=trace_id, error=error)

    async def _answered_outcome[ValueT](
        self,
        request: DispatchRequest,
        *,
        trace_id: str,
        response: ProviderResponse,
        validate: Callable[[ProviderResponse], ValueT],
        reason: str | None,
        observer: DispatchObserver,
    ) -> DispatchOutcome[ValueT]:
        """An operator's answer, carried to the caller as a usable result.

        Before this existed, an answered interception was a dead end: `decide`
        returned the operator's text, `dispatch` wrapped it in a `ProviderResponse`
        and returned -- with `value` unset, because nothing had parsed it. Every
        structured caller then refused the outcome on the decision alone, so a
        held request could be answered but the answer could never be acted on.
        Interception could stop work and never resume it.

        The answer goes through **the same two gates a model's answer does**, and
        in the same order: `inspect_output` then `validate`. That is the rule the
        response-review point already states -- a human's words are not trusted
        more than a model's -- and it is what stops an operator putting unparsed
        text into a caller's result type. A malformed answer fails here as
        `RESPONSE_INVALID`, exactly as a malformed model reply does, and the
        caller sees an outcome with no value rather than a broken one.

        No route is named and no cost is recorded, because no provider was
        called. The attempt is recorded so an operator-answered request is
        visible in the trace rather than being the one dispatch that left no row.
        """
        try:
            safety = inspect_output(response.text)
            if not safety.allowed:
                raise ProviderError("POLICY_BLOCKED")
            value = validate(response)
        except (ProviderError, ValueError, TypeError) as error:
            error_code = error.code if isinstance(error, ProviderError) else "RESPONSE_INVALID"
            await self.record_attempt(
                trace_id=trace_id,
                request=request,
                route=None,
                attempt_number=1,
                status="FAILED",
                selection_reason="HUMAN_RESPONSE",
                error_code=error_code,
                response=response,
                observer=observer,
            )
            return DispatchOutcome(
                decision=DispatchDecision.HUMAN_RESPONSE,
                response=response,
                reason=reason,
                last_error=error_code,
                attempts=1,
                failure_summary={error_code: 1},
            )

        await self.record_attempt(
            trace_id=trace_id,
            request=request,
            route=None,
            attempt_number=1,
            status="SUCCESS",
            selection_reason="HUMAN_RESPONSE",
            safety_status=safety.status.value,
            response=response,
            observer=observer,
        )
        return DispatchOutcome(
            decision=DispatchDecision.HUMAN_RESPONSE,
            value=value,
            response=response,
            reason=reason,
            attempts=1,
        )

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

        # Redacted here, before anything else looks at the payload, and carried
        # for the rest of the invocation. This is a *sequence* requirement, not
        # a tidiness one: an interception policy persists the held request, and
        # masking after that point would seal customer data into a store whose
        # whole purpose is to be opened by an operator. Everything downstream --
        # the policy, the `ProviderRequest`, the telemetry digest -- now sees the
        # same masked payload, so "nothing unredacted leaves the platform" is one
        # claim provable in one place rather than one claim per path.
        request = replace(request, payload=redact_payload(dict(request.payload)))

        verdict = await self._interception.decide(request)
        if verdict.decision is not DispatchDecision.ALLOW_PROVIDER:
            if (
                verdict.decision is DispatchDecision.HUMAN_RESPONSE
                and verdict.response_text is not None
            ):
                return await self._answered_outcome(
                    request,
                    trace_id=trace_id,
                    response=_human_response(request, verdict.response_text),
                    validate=validate,
                    reason=verdict.reason,
                    observer=watcher,
                )
            return DispatchOutcome(decision=verdict.decision, reason=verdict.reason)

        if request.precondition is not None:
            refusal = await request.precondition()
            if refusal is not None:
                return DispatchOutcome(decision=DispatchDecision.REJECT, reason=refusal)

        deadline = time.monotonic() + self._settings.ai_global_timeout_seconds
        failures: Counter[str] = Counter()
        state = _LoopState(attempts=0, last_error="PROVIDER_UNAVAILABLE", payload=request.payload)

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
            # After a timeout, do not re-queue a route that just timed out.
            #
            # A tier is a request for a *different* model, not a second go at the
            # same one. Where both tiers resolve to one route -- which is every
            # MANUAL deployment, because the manual provider offers its single
            # model at both -- escalating re-queued the identical route and paid
            # the full per-attempt timeout again. That is how one Order Discovery
            # turn reached roughly fourteen minutes: three serial 280-second
            # waits on one provider, and nothing about the second or third ask
            # differed from the first.
            #
            # **Only for timeouts, and the distinction is the whole point.** A
            # timeout means nobody answered, so asking the same route again is
            # asking an unchanged question of a silent respondent. A rejected
            # answer is the opposite: on a keyless deployment the "next route"
            # is the same person, and the second hold is how they are told what
            # was wrong with the first answer -- `validationError` is carried
            # into it. Suppressing that would trade a long wait for an operator
            # who cannot learn why their answer was refused, which is the defect
            # `test_the_retry_after_a_bad_answer_tells_the_operator_what_was_wrong`
            # exists to hold shut.
            #
            # Compared by `route_id` -- provider/model/credential, the identity
            # of the thing that would be called. Comparing by tier would be
            # circular, since the tier is what changed.
            if state.last_error == "TIMEOUT":
                attempted = {route.route_id for route in candidates}
                escalated = tuple(route for route in escalated if route.route_id not in attempted)
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
        retry = self.configuration.retry
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
                                # Already recursively redacted, at the top of
                                # `dispatch` -- before the interception policy
                                # could persist it. Masking here instead would
                                # leave the held-request store richer than the
                                # provider, which is the one place it must never
                                # be. Read off the loop state rather than the
                                # request because a rejected response may have
                                # added a diagnosis for this attempt to carry;
                                # `_diagnosed` redacts that addition the same
                                # way before it lands here.
                                user_payload=dict(state.payload),
                                max_output_tokens=request.max_output_tokens,
                                temperature=request.temperature,
                                response_schema=request.response_schema,
                                # Per-invocation, because one route can serve
                                # several tasks and a provider built at
                                # `build_routes` time cannot know which.
                                task_id=request.task_id,
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
                    # The second interception point. Placed here, after the
                    # provider answered and *before* `inspect_output` and
                    # `validate`, so an edited response is not trusted more than
                    # the model's was: whatever comes back from the review goes
                    # through the identical safety inspection and the identical
                    # schema parse, and a malformed edit fails as
                    # `RESPONSE_INVALID` exactly as a malformed model answer
                    # does. Reviewing *after* validation would have made a human
                    # the one contributor who could put unparsed text into a
                    # caller's result type.
                    #
                    # It is also outside the `wait_for` above, which times the
                    # provider and not the operator, and outside the pricing
                    # call, because the call happened and is billable however
                    # the review goes.
                    if getattr(self._interception, "reviews_responses", False):
                        response = await self._reviewed(request, response)
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
                # After the row is written, so the attempt that failed is
                # recorded against the payload that actually produced it, and
                # before the loop moves on, so whoever answers next -- the next
                # provider, or the operator holding the next MANUAL request --
                # is told what was wrong with the last answer.
                if state.last_error == "RESPONSE_INVALID" and error is not None:
                    state.payload = self._diagnosed(request, state.payload, error)

                if state.last_error in _TERMINAL_FOR_ROUTE:
                    break
                if route_attempt + 1 < retry.maximumAttemptsPerRoute:
                    await asyncio.sleep(self._backoff_seconds(route_attempt))
        return None

    def _diagnosed(
        self, request: DispatchRequest, payload: Mapping[str, Any], error: BaseException
    ) -> Mapping[str, Any]:
        """The payload the next attempt sends, carrying why the last one failed.

        Redacted on the way out, exactly like the original: the diagnosis is
        built from a rejected *response*, and a response is derived from a prompt
        that can carry rows read out of a customer's database. Running it through
        the same masker keeps "nothing unredacted leaves the platform, and
        nothing unredacted is sealed into the interception store" one claim
        provable in one place rather than one claim per contributor.

        A hook that raises is swallowed with its traceback logged. Failover is
        the work; annotating it is not, and a caller whose diagnosis builder has
        a bug must still get its next route rather than an exception thrown from
        inside the retry loop.
        """
        if request.on_response_invalid is None:
            return payload
        try:
            return redact_payload(dict(request.on_response_invalid(payload, error)))
        except Exception:  # noqa: BLE001 - a diagnosis must never break failover
            logger.exception("ai_dispatch_diagnosis_failed", extra={"task_id": request.task_id})
            return payload

    async def _reviewed(
        self, request: DispatchRequest, response: ProviderResponse
    ) -> ProviderResponse:
        """Hold the response for a human, and return what the caller gets.

        Blocking, unlike the request point, and the asymmetry is forced rather
        than chosen. A held *request* can be released and resumed because
        re-entering `dispatch()` costs nothing -- the provider was never called.
        A held *response* has already been paid for; releasing the turn and
        letting it re-enter would call the provider a second time to obtain a
        reply the store is already holding, so the coroutine waits.

        The cost of waiting is real and is named here rather than hidden: the
        route stays acquired for the duration, so a pool with a small
        concurrency ceiling can be occupied by operators rather than by work.
        That is survivable only because this point is refused outside
        development and test by the same two mechanisms that refuse MANUAL.

        `REJECTED` raises, which fails *this attempt* and -- because the code is
        terminal for the route -- moves the loop to the next candidate. If none
        remains the dispatch exhausts and the caller sees its ordinary
        unavailability, which is the honest report: nothing usable was produced.
        """
        verdict = await self._interception.review(request, response)
        if verdict.decision is ResponseDecision.REJECTED:
            raise ProviderError(verdict.reason or RESPONSE_REJECTED)
        if verdict.decision is ResponseDecision.EDITED:
            if verdict.response is None:
                # A policy that reports EDITED without a replacement has lost
                # the edit. Failing the attempt is the only safe reading:
                # falling through would deliver the model's original text as
                # though the operator had approved it.
                raise ProviderError(RESPONSE_REJECTED)
            return verdict.response
        return response

    def _backoff_seconds(self, route_attempt: int) -> float:
        retry = self.configuration.retry
        base_ms = min(
            retry.maximumBackoffMilliseconds,
            retry.initialBackoffMilliseconds * (2**route_attempt),
        )
        jitter = random.randint(0, max(1, base_ms // 4)) if retry.jitter else 0
        return float(base_ms + jitter) / 1000


@dataclass(slots=True)
class _LoopState:
    """Attempt count, last error and current payload, carried across a tier
    escalation.

    Mutable and passed by reference so the escalated pass continues the same
    attempt budget rather than starting a second one -- `maximumTotalAttempts`
    is a per-invocation ceiling, not a per-tier one.
    """

    attempts: int
    last_error: str
    #: What the next attempt sends. Starts as the request's own redacted payload
    #: and is replaced only by `on_response_invalid`, so a dispatch with no hook
    #: sends the identical bytes on every attempt exactly as it always did. It
    #: lives here rather than on the frozen `DispatchRequest` for the same reason
    #: the attempt count does: the escalated pass must continue the diagnosis the
    #: standard pass accumulated, not start again from the original question.
    payload: Mapping[str, Any]


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
