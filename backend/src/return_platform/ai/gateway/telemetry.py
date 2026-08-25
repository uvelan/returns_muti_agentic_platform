"""Which piece of work a model call belonged to, recorded with the call.

AI metrics were rich on the provider dimension and blind on the business one:
every attempt knew its route, its tier and its token counts, and none of them
knew which conversation, which case, or which associate's request had caused it.
"Why did we spend that" was unanswerable, and so was "show me every model call
this case made".

**Placement is a decision, not an accident.** This lives beside
`structured_invocation.py` rather than inside `ai_gateway/`'s service, because
W5.4 retires that shim and consolidates onto the structured path. Recording
correlation in the module that is going away would mean writing it twice: once
now, once again after the consolidation. `AIGatewayService` feeds the same
recorder, so there is one row shape and one pricing call, not two.

**It is provider-agnostic on purpose.** The record is written around
`provider.generate()`, so a MANUAL file handoff, a durable interception a human
answered, the simulator and the replay provider all produce the same row as a
live HTTP call. A `trace_id` that only exists on the live path is not
correlation -- it is a hole shaped exactly like every interesting incident.

**Identifiers only.** `InvocationCorrelation` carries platform-issued ids and
nothing else. No customer name, email, phone, address or order text goes into a
telemetry row, and the prompt and the response are represented by digests, never
content. That boundary is what makes this safe to fan out to dashboards.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Protocol

from return_platform.ai.pricing import AICostEstimate
from return_platform.ai.providers.contracts import HumanEdit

__all__ = [
    "AIAttemptRecord",
    "AIAttemptRecorder",
    "AITraceDocumentSink",
    "InvocationCorrelation",
    "RepositoryAIAttemptRecorder",
    "payload_digest",
]


@dataclass(frozen=True, slots=True)
class InvocationCorrelation:
    """The unit of work a model call belongs to, as platform identifiers.

    Every field is an id this platform issued: a request correlation id, a case
    id, a conversation id, an agent id, a session id. None of them is
    customer-identifying, and nothing here is derived from the associate's text
    or from anything retrieved out of the graph -- adding such a field would put
    customer data into a telemetry stream whose whole purpose is to be widely
    readable.

    All optional because not every caller has every one: a schema-analysis
    invocation has no conversation, and a turn before `CONFIRM_ORDER` has no
    case. An absent id is recorded as absent rather than as a placeholder,
    because "unknown" and "none" are different queries.
    """

    correlation_id: str | None = None
    case_id: str | None = None
    conversation_id: str | None = None
    agent_id: str | None = None
    session_id: str | None = None
    #: The unit of work that survives a retry, where the caller has one.
    #:
    #: `correlation_id` is per *request*: the API middleware mints a fresh one
    #: for every HTTP call, so two attempts at the same conversational turn carry
    #: different values. That is right for tracing and wrong for anything that
    #: has to recognise the second attempt as the same work -- which is exactly
    #: what `interception_id_for` needs, and why an operator's answer to a held
    #: request was never found by the retry that came looking for it.
    #:
    #: A caller with no such identity leaves it `None` and behaves as before.
    turn_id: str | None = None


@dataclass(frozen=True, slots=True)
class AIAttemptRecord:
    """One attempt against one route, successful or not.

    Failures are recorded, not only successes. A route that fails every request
    costs real latency and is the thing an operator most needs to see; a table
    of successes describes a system that never has incidents.
    """

    trace_id: str
    task_id: str
    prompt_version: str
    attempt_number: int
    status: str
    configured_tier: str
    selected_tier: str | None
    provider: str | None
    model: str | None
    credential_id: str | None
    route_id: str | None
    selection_reason: str
    fallback_used: bool
    fallback_reason: str | None
    safety_status: str
    latency_ms: int
    rate_limit_wait_ms: int
    input_tokens: int
    cached_input_tokens: int | None
    output_tokens: int
    total_tokens: int
    cost: AICostEstimate
    correlation: InvocationCorrelation
    request_digest: str
    response_digest: str | None
    error_code: str | None
    #: Set only when a human edited the model's answer at the response
    #: interception point.
    #:
    #: This is the column that keeps an evaluation set honest. `provider` and
    #: `model` on this row name the route that was *called*, because that is
    #: what was called and what was billed -- so without this field an edited
    #: answer would sit in the metrics store indistinguishable from a pure model
    #: answer, and "model quality" computed from `responseDigest` would be
    #: measuring a person. `responseAttribution` in the document makes the
    #: default reading explicit rather than implied by a null.
    human_edit: HumanEdit | None = None
    #: The bodies behind the digests -- the assembled system prompt, the payload
    #: the provider received, and the text it returned.
    #:
    #: **Never written to the telemetry row.** `to_document` excludes all three
    #: deliberately: the metrics collection is the widely-readable stream and
    #: its boundary stays "identifiers and digests only", exactly as the module
    #: docstring states. They exist on the record so a recorder wired with a
    #: *trace* sink can persist them to `ai_traces` -- the store the Control
    #: Center's request-detail view reads -- via `to_trace_document`. A recorder
    #: without that sink drops them on the floor, which is the old behaviour.
    system_prompt: str | None = None
    payload: dict[str, Any] | None = None
    response_text: str | None = None

    def to_document(self) -> dict[str, Any]:
        """The stored row, in `AIUsageAttemptView`'s field names.

        One conversion, here, so the two dispatch paths cannot drift into
        recording the same attempt under different keys -- which is exactly how
        a metrics query ends up silently covering half the traffic.
        """
        return {
            "traceId": self.trace_id,
            "correlationId": self.correlation.correlation_id,
            "caseId": self.correlation.case_id,
            "conversationId": self.correlation.conversation_id,
            "agentId": self.correlation.agent_id,
            "sessionId": self.correlation.session_id,
            "taskId": self.task_id,
            "promptVersion": self.prompt_version,
            "configuredTier": self.configured_tier,
            "selectedTier": self.selected_tier,
            "provider": self.provider,
            "model": self.model,
            "credentialId": self.credential_id,
            "routeId": self.route_id,
            "attemptNumber": self.attempt_number,
            "selectionReason": self.selection_reason,
            "status": self.status,
            "fallbackUsed": self.fallback_used,
            "fallbackReason": self.fallback_reason,
            "safetyStatus": self.safety_status,
            "latencyMs": self.latency_ms,
            "rateLimitWaitMs": self.rate_limit_wait_ms,
            "inputTokens": self.input_tokens,
            "cachedInputTokens": self.cached_input_tokens,
            "outputTokens": self.output_tokens,
            "totalTokens": self.total_tokens,
            "estimatedCostMicros": self.cost.amount_micros,
            "pricingCurrency": self.cost.currency,
            "pricingStatus": self.cost.status.value,
            "pricingVersion": self.cost.pricing_version,
            "errorCode": self.error_code,
            "requestDigest": self.request_digest,
            "responseDigest": self.response_digest,
            # "MODEL" on every row a provider produced, including every row
            # written before response interception existed. A reader filtering
            # for model output writes one equality, not a null check they have
            # to remember.
            "responseAttribution": "MODEL" if self.human_edit is None else "HUMAN_EDITED",
            "humanEditedBy": None if self.human_edit is None else self.human_edit.edited_by,
            # The model's own output, before the edit. With `responseDigest`
            # (which is what the caller actually received) this proves an edit
            # changed something without either digest carrying any text.
            "originalResponseDigest": (
                None if self.human_edit is None else self.human_edit.origin_digest
            ),
            "humanEditInterceptionId": (
                None if self.human_edit is None else self.human_edit.interception_id
            ),
        }

    def to_trace_document(self) -> dict[str, Any] | None:
        """This attempt as an `ai_traces` document, or `None` without payloads.

        The shape `OperationalRepository._trace_view` parses -- the same
        document `create_ai_trace` writes for the legacy gateway path -- so the
        Control Center's `GET /api/ai/requests/{trace_id}` serves both paths
        from one collection with one reader. The attempt's flat status
        vocabulary is mapped onto `AIRequestStatus`, which is what the view
        validates against. `createdAt`, `updatedAt` and `version` are absent on
        purpose: the upserting repository owns time and revision, exactly as it
        does for every other document it writes.
        """
        if self.system_prompt is None or self.payload is None:
            return None
        status = {
            "SUCCESS": "RESPONSE_VALIDATED",
            "SAFETY_BLOCKED": "POLICY_BLOCKED",
            "SKIPPED": "CANCELLED",
        }.get(self.status, "PROVIDER_UNAVAILABLE")
        return {
            "_id": self.trace_id,
            "sessionId": self.correlation.session_id,
            "status": status,
            "taskId": self.task_id,
            "configuredTier": self.configured_tier,
            "selectedTier": self.selected_tier,
            "provider": self.provider,
            "model": self.model,
            "credentialId": self.credential_id,
            "routeId": self.route_id,
            "promptVersion": self.prompt_version,
            "redactedInput": self.payload,
            "systemPrompt": self.system_prompt,
            "requestDigest": self.request_digest,
            "responseText": self.response_text,
            "decision": None,
            "explanation": None,
            "confidenceMillionths": None,
            "latencyMs": self.latency_ms,
            "rateLimitWaitMs": self.rate_limit_wait_ms,
            "inputTokens": self.input_tokens,
            "cachedInputTokens": self.cached_input_tokens,
            "outputTokens": self.output_tokens,
            "totalTokens": self.total_tokens,
            "estimatedCostMicros": self.cost.amount_micros,
            "pricingCurrency": self.cost.currency,
            "pricingStatus": self.cost.status.value,
            "pricingVersion": self.cost.pricing_version,
            "responseDigest": self.response_digest,
            "attempts": self.attempt_number,
            "fallbackUsed": self.fallback_used,
            "safetyStatus": self.safety_status,
            "safetySignals": [],
            "selectionReason": self.selection_reason,
            "errorCode": self.error_code,
            "interceptedBy": None,
        }


class AIAttemptRecorder(Protocol):
    """Where attempts go.

    A Protocol rather than a concrete repository so the invoker depends on
    "something that can record" and not on Mongo, an operational repository, or
    an HTTP process. It is also why a worker with no operational store can be
    wired with a different sink instead of quietly recording nothing.
    """

    async def record(self, record: AIAttemptRecord) -> None: ...


class _AttemptMetricSink(Protocol):
    async def insert_ai_attempt_metric(self, document: dict[str, Any]) -> Any: ...


class AITraceDocumentSink(Protocol):
    """Where a full invocation trace goes, when payload persistence is on."""

    async def upsert_ai_invocation_trace(self, document: dict[str, Any]) -> Any: ...


class RepositoryAIAttemptRecorder:
    """Writes attempts to the platform's existing `ai_usage_attempts` store.

    Deliberately the same collection the gateway's own attempts already use.
    A second telemetry store for the second dispatch path would make every
    "how much did AI cost this month" query depend on remembering that there
    are two, which nobody does twice in a row.

    `trace_sink` is the payload half, and it is explicit rather than inferred:
    a recorder built without one records digests only -- the historical
    behaviour, and the metrics boundary this module's docstring promises --
    while a recorder built with one also upserts the full trace document the
    Control Center's request-detail view reads. Per-attempt upsert on the shared
    `trace_id` means the last attempt recorded (which the dispatcher orders to
    be the terminal one) is the state the trace shows.
    """

    def __init__(
        self, sink: _AttemptMetricSink, *, trace_sink: AITraceDocumentSink | None = None
    ) -> None:
        self._sink = sink
        self._trace_sink = trace_sink

    async def record(self, record: AIAttemptRecord) -> None:
        await self._sink.insert_ai_attempt_metric(record.to_document())
        if self._trace_sink is None:
            return
        document = record.to_trace_document()
        if document is not None:
            await self._trace_sink.upsert_ai_invocation_trace(document)


def payload_digest(system_prompt: str, payload: dict[str, Any]) -> str:
    """A stable fingerprint of what was sent, holding none of it.

    Enough to prove two invocations sent the same thing -- which is what makes a
    replay comparable to its original -- while keeping the telemetry row free of
    prompt text, retrieved rows and anything an associate typed.
    """
    canonical = json.dumps(
        {"systemPrompt": system_prompt, "payload": payload},
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
