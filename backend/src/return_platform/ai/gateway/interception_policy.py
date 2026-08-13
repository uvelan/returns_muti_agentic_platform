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
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from return_platform.ai.gateway.final_dispatch import (
    ALLOW_ALL,
    DispatchDecision,
    DispatchRequest,
    InterceptionPolicy,
    InterceptionVerdict,
)
from return_platform.ai.interception.records import InterceptionStatus, ResumeCommand
from return_platform.ai.interception.store import InterceptionStore

__all__ = [
    "DEFAULT_INTERCEPTION_TTL_SECONDS",
    "AIGatewaySettingsSource",
    "DurableInterceptionPolicy",
    "build_interception_policy",
    "interception_id_for",
]

logger = logging.getLogger("return_platform.ai.gateway.interception_policy")

#: An interception nobody answers must not hold a run forever. One hour is the
#: operator console's working span; the expiry sweep turns anything older into
#: `EXPIRED`, which is deliberately distinct from `CANCELLED` so "nobody got to
#: it" is not read as ordinary churn.
DEFAULT_INTERCEPTION_TTL_SECONDS = 3_600


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
) -> InterceptionPolicy:
    """The policy for a caller, or an explicit, logged `ALLOW_ALL`.

    A process with no interception store genuinely cannot hold a request, and
    failing startup over it would take the platform down to protect a feature
    that deployment does not use. But it must not be *quiet*: an operator who
    enables interception and sees reasoning traffic sail past deserves a line in
    the log naming the path that could not be gated, which is the diagnosis
    nobody had for AI-01.
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

    return DurableInterceptionPolicy(store=store, enabled=enabled, ttl_seconds=ttl_seconds)


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
    ) -> None:
        self._store = store
        self._enabled = enabled
        self._ttl_seconds = ttl_seconds

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
