"""Holding a request for a human, on every path rather than one.

AI-01. Interception was real, audited and operator-visible -- and it was an `if`
in the middle of `AIGatewayService.evaluate`'s provider loop. The Order Agent and
the Graph Analyzer run on `StructuredOutputInvoker`, which had its own loop and
therefore no gate at all: turning interception on stopped eligibility decisions
and did nothing whatsoever to the reasoning traffic that carries the most
customer data. Nothing was misconfigured. The control was attached to a loop
instead of to a boundary.

AI-02 gave the platform one boundary, and `FinalDispatcher` asks an
`InterceptionPolicy` before it looks at a route. This module supplies the policy
that makes that gate mean something off the decision path.

**Held, then resumed -- not blocked.** A policy that awaited an operator inside
`dispatch()` would hold a Temporal activity open for however long a human takes.
The platform already has the durable alternative: `ai/interception/store.py`
persists the held request sealed, `InterceptionResumeDispatcher` turns a decided
interception into a `reasoning_resume_commands` row, and
`platform/reasoning/resume_worker.py` delivers it as a signal. So a first call
persists `PENDING` and reports `HUMAN_RESPONSE`; the operator decides; the run
resumes and re-enters `dispatch()`, where this policy finds the decision and
returns it. One provider call, after the approval, and none before.

**The identity has to survive the gap.** The resumed call must find the record
the held call opened, and two different turns must not share one. The id is
derived from the task, the request digest and the caller's correlation -- all
values the request already carries, none of them random -- so a retry after a
crash re-finds its own interception instead of opening a second one.

**Nothing unredacted is persisted.** `FinalDispatcher` applies the recursive
redactor before it consults this policy, so the payload sealed here is the same
one a provider would have received. That ordering is the whole reason redaction
moved to the top of the boundary; it is asserted in
`tests/test_ai_interception_covers_every_path.py`.

THE SECOND POINT: HOLDING THE RESPONSE
--------------------------------------
`review` is the other half. The request point can only ever *replace* the model
-- a human writes the answer and the model is never called -- which is the wrong
control for the most common operator need, which is to look at what the model
actually said before anything acts on it. So after the provider answers and
before the reply is inspected, parsed or returned, the same store holds it for a
human who may accept it, edit it, or reject it.

**Blocking, and the asymmetry is forced.** A held request is released and
resumed because re-entering `dispatch()` costs nothing: no provider was called.
A held response has already been paid for, so releasing the turn would mean
buying the same answer a second time to find out what the store already knows.
The coroutine therefore waits, which is affordable only because the point is
refused outside development and test.

**An edited response is a third thing, and it is labelled as one.** Not the
model (an evaluation set would absorb a person's edits as model quality) and not
`MANUAL` (that would lose that a model produced the substance). It comes back as
`HUMAN_EDITED` / `human-edited-v1` carrying a `HumanEdit` that names the
originating provider and model, who edited it, and the digest of the text before
the edit alongside the digest of the text delivered. See
`ai/providers/contracts.py` for why the *edit* rather than the origin is the
half that occupies `provider`.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from return_platform.ai.gateway.final_dispatch import (
    ALLOW_ALL,
    RESPONSE_REJECTED,
    RESPONSE_REVIEW_EXPIRED,
    DispatchDecision,
    DispatchRequest,
    InterceptionPolicy,
    InterceptionVerdict,
    ResponseDecision,
    ResponseVerdict,
)
from return_platform.ai.interception.records import (
    Interception,
    InterceptionPoint,
    InterceptionStatus,
    ResumeCommand,
    is_terminal,
)
from return_platform.ai.interception.store import InterceptionStore
from return_platform.ai.providers.contracts import (
    HUMAN_EDITED_MODEL,
    HUMAN_EDITED_PROVIDER,
    HumanEdit,
    ProviderError,
    ProviderResponse,
)
from return_platform.configuration.settings import Settings

__all__ = [
    "DEFAULT_INTERCEPTION_TTL_SECONDS",
    "DEFAULT_RESPONSE_POLL_SECONDS",
    "DEFAULT_RESPONSE_REVIEW_TIMEOUT_SECONDS",
    "AIGatewaySettingsSource",
    "DurableInterceptionPolicy",
    "build_interception_policy",
    "human_edited_response",
    "interception_id_for",
    "response_interception_id_for",
]

logger = logging.getLogger("return_platform.ai.gateway.interception_policy")

#: An interception nobody answers must not hold a run forever. One hour is the
#: operator console's working span; the expiry sweep turns anything older into
#: `EXPIRED`, which is deliberately distinct from `CANCELLED` so "nobody got to
#: it" is not read as ordinary churn.
DEFAULT_INTERCEPTION_TTL_SECONDS = 3_600

#: How long a caller waits for a human to review a response before giving up.
#: Ten minutes, matching `DurableInterceptionProvider`'s own wait, because it is
#: the same trade: a coroutine is parked on a person, and the ceiling is what
#: stops an unattended console from becoming an outage.
DEFAULT_RESPONSE_REVIEW_TIMEOUT_SECONDS = 600.0

#: Poll interval while waiting. Polling rather than a change stream for the same
#: reason `DurableInterceptionProvider` polls: it needs nothing of the store but
#: `get`, so an in-memory implementation and Mongo behave identically.
DEFAULT_RESPONSE_POLL_SECONDS = 1.0


class AIGatewaySettingsSource(Protocol):
    """Whatever can answer "is interception on right now".

    A Protocol rather than the operational repository so a worker with a
    different settings surface can supply one, and so this module does not
    depend on Mongo to express a boolean.
    """

    async def get_ai_settings(self) -> Any: ...


def build_interception_policy(
    *,
    store: InterceptionStore | None,
    settings_source: AIGatewaySettingsSource | None,
    subject: str,
    ttl_seconds: int = DEFAULT_INTERCEPTION_TTL_SECONDS,
    settings: Settings | None = None,
) -> InterceptionPolicy:
    """The policy for a caller, or an explicit, logged `ALLOW_ALL`.

    A process with no interception store genuinely cannot hold a request, and
    failing startup over it would take the platform down to protect a feature
    that deployment does not use. But it must not be *quiet*: an operator who
    enables interception and sees reasoning traffic sail past deserves a line in
    the log naming the path that could not be gated, which is the diagnosis
    nobody had for AI-01.

    `settings` is what turns the *response* point on, and it is optional for a
    reason: a caller that does not pass it gets today's behaviour exactly, which
    is the requirement that response interception change nothing when it is off.
    A caller that does pass it still gets today's behaviour unless
    `ai_response_interception` is set, and that setting is refused in production
    by `Settings.validate_relationships`.
    """
    if store is None or settings_source is None:
        logger.warning(
            "ai_interception_ungated",
            extra={
                "subject": subject,
                "reason": "no interception store" if store is None else "no settings source",
            },
        )
        return ALLOW_ALL

    async def enabled() -> bool:
        record = await settings_source.get_ai_settings()
        return bool(getattr(record, "interceptMode", False))

    return DurableInterceptionPolicy(
        store=store, enabled=enabled, ttl_seconds=ttl_seconds, settings=settings
    )


def interception_id_for(request: DispatchRequest) -> str:
    """A stable id for this request, derived rather than generated.

    Derivation is the correctness mechanism, not a convenience. The call that
    opens the interception and the call that resumes after it is decided are
    separate invocations, possibly in separate processes after a crash; a random
    id would make the second one open a *second* interception and wait for an
    operator to answer the same question twice, forever.

    Built from the task, the digest of what would have been sent, and the
    caller's correlation identifiers -- so two turns of the same conversation are
    distinct, and one turn retried is not.
    """
    correlation = request.correlation
    material = "\x1f".join(
        (
            request.task_id,
            request.request_digest,
            correlation.correlation_id or "",
            correlation.conversation_id or "",
            correlation.case_id or "",
            correlation.session_id or "",
        )
    )
    return f"aiq-{hashlib.sha256(material.encode('utf-8')).hexdigest()[:32]}"


def response_interception_id_for(request: DispatchRequest, response_text: str) -> str:
    """A stable id for *this answer* to this request.

    Derived from everything `interception_id_for` uses **plus the digest of the
    response**, and both halves are load-bearing.

    The request half is why a crash mid-review does not lose the decision: the
    re-run asks the same model the same question, gets the same text, derives
    the same id, finds the operator's verdict already recorded and honours it
    without holding a second time.

    The response half is why a rejection does not cascade. `REJECTED` is
    terminal for its route, so the loop moves to the next candidate -- which
    produces a *different* answer. Keyed on the request alone, that next answer
    would find the first one's rejection waiting for it and be thrown away
    unread, and one operator's judgement about one reply would silently become a
    judgement about every reply. Two answers are two things to look at.

    A distinct `air-` prefix rather than a shared namespace so the two points
    cannot collide on the same primary key, and so an id read out of a log says
    which kind it is without a lookup.
    """
    digest = hashlib.sha256(response_text.encode("utf-8")).hexdigest()
    material = "\x1f".join((interception_id_for(request), InterceptionPoint.RESPONSE.value, digest))
    return f"air-{hashlib.sha256(material.encode('utf-8')).hexdigest()[:32]}"


class DurableInterceptionPolicy(InterceptionPolicy):
    """Holds a request until an operator decides, on whichever path asked.

    `enabled` is a callable rather than a flag because the switch is operator
    state that changes while the process runs -- reading it once at construction
    would make "turn interception on" require a restart, which is the failure
    this whole task exists to remove one instance of.
    """

    def __init__(
        self,
        *,
        store: InterceptionStore,
        enabled: Callable[[], Awaitable[bool]],
        ttl_seconds: int = DEFAULT_INTERCEPTION_TTL_SECONDS,
        settings: Settings | None = None,
        response_poll_seconds: float = DEFAULT_RESPONSE_POLL_SECONDS,
        response_timeout_seconds: float = DEFAULT_RESPONSE_REVIEW_TIMEOUT_SECONDS,
    ) -> None:
        self._store = store
        self._enabled = enabled
        self._ttl_seconds = ttl_seconds
        self._poll_seconds = response_poll_seconds
        self._response_timeout_seconds = response_timeout_seconds
        # Read once, at construction, and *not* through the operator settings
        # record the request point uses. Response review is process
        # configuration, not a live toggle: flipping it mid-flight would park
        # in-flight callers on a console nobody has opened yet.
        self.reviews_responses = settings is not None and settings.ai_response_interception
        # The same gate `DurableInterceptionProvider` applies to MANUAL, kept
        # separate from the switch above so an environment that should never
        # have reached here is *refused* rather than quietly downgraded to
        # "review nothing". A production process that somehow carries the flag
        # -- only reachable by bypassing `Settings.validate_relationships`, which
        # rejects it -- fails its AI calls instead of pretending the control is
        # on.
        self._non_production = settings is not None and settings.environment in {
            "development",
            "test",
        }

    async def decide(self, request: DispatchRequest) -> InterceptionVerdict:
        interception_id = interception_id_for(request)

        # An already-decided interception is honoured even if interception has
        # since been switched off. A run held while the switch was on must not
        # resume by silently bypassing the decision an operator already made --
        # and a request an operator *cancelled* must stay cancelled.
        existing = await self._store.get(interception_id)
        if existing is not None:
            return await self._verdict_for(existing.status, interception_id)

        if not await self._enabled():
            return InterceptionVerdict(decision=DispatchDecision.ALLOW_PROVIDER)

        # `request.payload` is already redacted: `FinalDispatcher` masks before
        # it consults any policy, precisely so that what is sealed here can never
        # be richer than what a provider would have seen.
        await self._store.open(
            interception_id=interception_id,
            task_id=request.task_id,
            request_payload={
                "systemPrompt": request.system_prompt,
                "payload": dict(request.payload),
                "requestDigest": request.request_digest,
            },
            resume=ResumeCommand(
                run_id=request.correlation.correlation_id or interception_id,
                thread_id=request.correlation.conversation_id or interception_id,
                workflow_id=request.correlation.case_id,
            ),
            expires_at=datetime.now(UTC) + timedelta(seconds=self._ttl_seconds),
        )
        logger.info(
            "ai_request_intercepted",
            extra={
                "interception_id": interception_id,
                "task_id": request.task_id,
                "correlation_id": request.correlation.correlation_id,
                "conversation_id": request.correlation.conversation_id,
            },
        )
        return InterceptionVerdict(
            decision=DispatchDecision.HUMAN_RESPONSE,
            reason=InterceptionStatus.PENDING.value,
        )

    async def _verdict_for(
        self, status: InterceptionStatus, interception_id: str
    ) -> InterceptionVerdict:
        """The five stored states, mapped onto exactly the three C7 outcomes.

        `PENDING` and `ANSWERED` are both `HUMAN_RESPONSE`: in each the provider
        will not be called and a human is in the loop. They differ only in
        whether the answer has arrived yet, which `response_text` carries -- the
        caller does not need a fourth decision value to tell them apart, and
        adding one would put a state outside the contract into every consumer.
        """
        if status is InterceptionStatus.ALLOWED:
            return InterceptionVerdict(decision=DispatchDecision.ALLOW_PROVIDER)
        if status in {InterceptionStatus.CANCELLED, InterceptionStatus.EXPIRED}:
            return InterceptionVerdict(decision=DispatchDecision.REJECT, reason=status.value)
        if status is InterceptionStatus.ANSWERED:
            # Unsealed only here, and only for a request that is actually
            # resuming. Decrypting on every poll would defeat sealing it.
            payload = await self._store.request_payload(interception_id)
            text = (payload or {}).get("responseText")
            return InterceptionVerdict(
                decision=DispatchDecision.HUMAN_RESPONSE,
                reason=status.value,
                response_text=str(text) if text is not None else None,
            )
        return InterceptionVerdict(
            decision=DispatchDecision.HUMAN_RESPONSE, reason=InterceptionStatus.PENDING.value
        )

    # ------------------------------------------------------------------
    # The response point
    # ------------------------------------------------------------------

    async def review(self, request: DispatchRequest, response: ProviderResponse) -> ResponseVerdict:
        """Hold the model's reply until an operator accepts, edits or rejects it.

        Reached only when `reviews_responses` is true -- `FinalDispatcher` checks
        that attribute before awaiting anything, so a deployment with the point
        off performs no store read, no store write and no extra await.
        """
        if not self.reviews_responses:
            return ResponseVerdict(decision=ResponseDecision.ACCEPTED)
        if not self._non_production:
            # Structurally impossible to deploy, not merely discouraged -- the
            # same refusal `DurableInterceptionProvider.generate` makes.
            raise ProviderError("POLICY_BLOCKED")

        interception_id = response_interception_id_for(request, response.text)
        record = await self._store.get(interception_id)
        if record is None:
            record = await self._open_response_hold(request, response, interception_id)

        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._response_timeout_seconds
        while True:
            if record is None:
                # The record vanished under us. Not treated as an acceptance:
                # "the store lost the review" and "a human approved this" are
                # very different claims to make about text a caller is about to
                # act on.
                raise ProviderError("INTERCEPTION_LOST")
            if is_terminal(record.status):
                return await self._response_verdict_for(record, response, interception_id)
            if loop.time() >= deadline:
                # Close the record on the way out, exactly as the MANUAL
                # provider does: leaving it PENDING shows an operator work whose
                # caller has already gone, and they would review into nothing.
                await self._store.cancel(
                    interception_id=interception_id, status=InterceptionStatus.EXPIRED
                )
                return ResponseVerdict(
                    decision=ResponseDecision.REJECTED, reason=RESPONSE_REVIEW_EXPIRED
                )
            await asyncio.sleep(self._poll_seconds)
            record = await self._store.get(interception_id)

    async def _open_response_hold(
        self, request: DispatchRequest, response: ProviderResponse, interception_id: str
    ) -> Interception | None:
        """Seal the reply -- with the question it answers -- and queue it.

        The response is sealed for the same reason the request is: it is derived
        from a prompt that can carry rows read out of a customer's database, so
        an answer summarising those rows carries them too. Sealing the prompt and
        leaving the completion in the clear would be theatre.

        The request travels with it because a reviewer cannot judge an answer
        without the question, and fetching the two separately would mean two
        unseal operations for one decision.
        """
        record = await self._store.open(
            interception_id=interception_id,
            task_id=request.task_id,
            request_payload={
                "systemPrompt": request.system_prompt,
                "payload": dict(request.payload),
                "requestDigest": request.request_digest,
                "modelResponse": {
                    "provider": response.provider,
                    "model": response.model,
                    "text": response.text,
                    "digest": _digest(response.text),
                },
            },
            resume=ResumeCommand(
                run_id=request.correlation.correlation_id or interception_id,
                thread_id=request.correlation.conversation_id or interception_id,
                workflow_id=request.correlation.case_id,
            ),
            expires_at=datetime.now(UTC) + timedelta(seconds=self._ttl_seconds),
            point=InterceptionPoint.RESPONSE,
        )
        logger.info(
            "ai_response_intercepted",
            extra={
                "interception_id": interception_id,
                "task_id": request.task_id,
                "provider": response.provider,
                "model": response.model,
                "correlation_id": request.correlation.correlation_id,
                "conversation_id": request.correlation.conversation_id,
            },
        )
        return record

    async def _response_verdict_for(
        self, record: Interception, response: ProviderResponse, interception_id: str
    ) -> ResponseVerdict:
        """The four terminal statuses, read at the response point.

        The same statuses the request point uses, deliberately. `ALLOWED` there
        means "the model may proceed"; here it means "the model's answer stands"
        -- in both cases the human looked and did not substitute anything, and in
        both cases the result is reported as the model. `ANSWERED` there means a
        human wrote the answer; here it means a human rewrote it, which is why
        this is the one branch that changes the response's identity.
        """
        if record.status is InterceptionStatus.ALLOWED:
            return ResponseVerdict(decision=ResponseDecision.ACCEPTED)
        if record.status is InterceptionStatus.CANCELLED:
            return ResponseVerdict(decision=ResponseDecision.REJECTED, reason=RESPONSE_REJECTED)
        if record.status is InterceptionStatus.EXPIRED:
            return ResponseVerdict(
                decision=ResponseDecision.REJECTED, reason=RESPONSE_REVIEW_EXPIRED
            )

        # ANSWERED: unsealed only here, and only for a review that is actually
        # concluding. Decrypting on every poll would defeat sealing it.
        payload = await self._store.request_payload(interception_id)
        text = (payload or {}).get("responseText")
        if not isinstance(text, str) or not text:
            # An edit that recorded no text is not an approval of the original.
            # Rejecting is the only reading that cannot launder the model's
            # answer as something a human signed off.
            return ResponseVerdict(decision=ResponseDecision.REJECTED, reason=RESPONSE_REJECTED)
        return ResponseVerdict(
            decision=ResponseDecision.EDITED,
            response=human_edited_response(
                response,
                text=text,
                edited_by=record.answered_by or "unknown",
                interception_id=interception_id,
            ),
        )


def human_edited_response(
    original: ProviderResponse,
    *,
    text: str,
    edited_by: str,
    interception_id: str,
) -> ProviderResponse:
    """The third identity, built in one place.

    `provider`/`model` become `HUMAN_EDITED`/`human-edited-v1`, and the model
    that produced the substance moves into `human_edit` alongside who changed it
    and the digests of both versions. One function so that no caller can
    construct half of it -- a `ProviderResponse` carrying edited text under the
    origin provider's name is precisely the misattribution the type exists to
    make impossible.

    Token counts are copied unchanged. They describe what the provider billed
    for, and a human rewriting the text afterwards does not alter the invoice.
    """
    return ProviderResponse(
        provider=HUMAN_EDITED_PROVIDER,
        model=HUMAN_EDITED_MODEL,
        text=text,
        input_tokens=original.input_tokens,
        cached_input_tokens=original.cached_input_tokens,
        output_tokens=original.output_tokens,
        total_tokens=original.total_tokens,
        human_edit=HumanEdit(
            origin_provider=original.provider,
            origin_model=original.model,
            origin_digest=_digest(original.text),
            delivered_digest=_digest(text),
            edited_by=edited_by,
            interception_id=interception_id,
        ),
    )


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
