"""What happens to an answer the ladder produced (contracts.md sect. 9, brief 4).

> *`support_resolver.reply_gate{default: review_required, per_intent{}}`; gated
> replies are `SUPPORT_REPLY` reviews on the S2 aggregate. Sub-threshold /
> conflicting sources / missing entity -> escalate, never answer.*

Two outcomes, and **one composed message between them**. That is the load-
bearing decision in this module: `compose_reply` runs *once*, before the gate is
consulted, and the resulting text is what a `SUPPORT_REPLY` review carries as
its draft payload **and** what an `auto_reply` intent posts directly. Composing
separately per path would give the reviewed message and the unreviewed message
two different spellings of the disclosure, the neutralisation and the framing --
and the unreviewed one is precisely the path with nobody looking at it.

## The delivery identity, without a store

Sect. 7 wants `logical_operation_id` -> `delivery_id` (generated once, stored,
reused on retry) -> receiver dedupe. A gated reply gets its logical operation id
from the approving command, which is V1's endpoint. An **auto** reply has no
approving command, so its identity is derived instead:
`logical_operation_id = support-reply:{case_id}:{support_event_id}` and
`delivery_id = uuid5(namespace, logical_operation_id)`.

Deriving rather than generating-and-storing is deliberate and is *not* a
weakening. The property the contract asks for is observable -- "a retry reuses
the same delivery identity, and the receiver absorbs it" -- and a pure function
of two ids satisfies it without a row that could itself be lost between the
generate and the store. A stored id has a window where it has been generated and
not yet persisted; a derived one has none.

## Provenance

An auto reply posts with `actor_id=SYSTEM` (`review_aggregate.SYSTEM_ACTOR`,
which sect. 6 reserves and makes non-assignable) and carries the released
disclosure line, so a message the platform sent on its own can never be read as
an associate's or as Support's own. Both are asserted, and the disclosure is
asserted on the *composed text*, not on the flag.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, Protocol

from return_platform.configuration.support_resolver_configuration import (
    SupportResolverConfiguration,
)
from return_platform.operations.fact_names import SUPPORT_REPLY_DRAFT
from return_platform.operations.models import FactAcquisition, FactChannel
from return_platform.operations.return_support.outbound_composition import (
    ComposedMessage,
    DisclosureLike,
    compose_reply,
)
from return_platform.operations.return_support.resolution_ladder import AGENT_ID
from return_platform.operations.review_aggregate import SYSTEM_ACTOR, ReviewKind

__all__ = [
    "REPLY_DELIVERY_NAMESPACE",
    "GatedReply",
    "ReplyGateOutcome",
    "ReviewStorePort",
    "SupportThreadPort",
    "gate_reply",
    "reply_delivery_identity",
]

#: A fixed namespace, so `uuid5` over the same logical operation id gives the
#: same delivery id in every process and every replay. A generated namespace
#: would make the derivation depend on when the module was imported.
REPLY_DELIVERY_NAMESPACE: Final = uuid.UUID("6f3d1a52-4b7e-5c8a-9d0e-1f2a3b4c5d6e")

_LOGICAL_OPERATION_PREFIX: Final = "support-reply"


class ReplyGateOutcome:
    """What the gate did. Two values; there is no third."""

    REVIEW_OPENED: Final = "review_opened"
    AUTO_REPLIED: Final = "auto_replied"


def reply_delivery_identity(*, case_id: str, support_event_id: str) -> tuple[str, str]:
    """`(logical_operation_id, delivery_id)` for an auto reply. Pure.

    Purity is the guarantee: a retry of the same support event computes the same
    pair without reading anything, so the receiver's unique index on
    `businessPayload.deliveryId` absorbs the second post.
    """
    logical_operation_id = f"{_LOGICAL_OPERATION_PREFIX}:{case_id}:{support_event_id}"
    delivery_id = str(uuid.uuid5(REPLY_DELIVERY_NAMESPACE, logical_operation_id))
    return logical_operation_id, delivery_id


@dataclass(frozen=True, slots=True)
class GatedReply:
    """What the gate came to. Returned so a test can assert, not narrate."""

    outcome: str
    #: The text that was reviewed, or the text that was sent. The same string in
    #: both cases -- see the module docstring.
    message_text: str
    discloses_agent: bool
    review_id: str | None = None
    scope_id: str | None = None
    logical_operation_id: str | None = None
    delivery_id: str | None = None
    message_id: str | None = None
    absorbed: bool = False


class ReviewStorePort(Protocol):
    """`ReviewAggregateStore.create_review`, structurally."""

    async def create_review(
        self,
        *,
        case_id: str,
        request_id: str,
        review_kind: ReviewKind,
        draft_payload: Mapping[str, Any],
        scope_id: str | None = None,
        review_id: str | None = None,
    ) -> dict[str, Any]: ...


class SupportThreadPort(Protocol):
    """The two S2 delivery operations, structurally (contracts.md sect. 7)."""

    async def ensure_case_support_thread(
        self,
        *,
        case_id: str,
        tenant_id: str,
        principal_id: str,
        support_draft: str,
        idempotency_key: str,
        business_payload: Mapping[str, Any] | None = None,
        subject: str | None = None,
        work_item_id: str | None = None,
    ) -> Any: ...

    async def post_support_message(
        self,
        *,
        work_item_id: str,
        message_text: str,
        delivery_id: str | None = None,
        business_payload: Mapping[str, Any] | None = None,
        actor_id: str = ...,
        actor_role: str = ...,
    ) -> Any: ...


class ScopedFactWriterPort(Protocol):
    async def __call__(self, *, record_scope: str | None, **fact: Any) -> bool: ...


async def gate_reply(
    resolution: Mapping[str, Any],
    *,
    case_id: str,
    support_event_id: str,
    intent: str,
    question_text: str,
    tenant_id: str,
    principal_id: str,
    configuration: SupportResolverConfiguration,
    disclosure: DisclosureLike | None,
    reviews: ReviewStorePort,
    threads: SupportThreadPort,
    append_scoped_fact_once: ScopedFactWriterPort,
) -> GatedReply:
    """Route one resolution through the released gate.

    `resolution` is the ladder's own terminal value -- so this function is only
    ever reached for an answer that cleared its rung's threshold. It does not
    re-check the threshold, and deliberately does not: a second, independent
    reading of "is this good enough" is a second place for the two readings to
    disagree, and the ladder's routers already refuse to produce a resolution
    that has not cleared one.

    It **does** re-read the gate from the configuration rather than trusting
    `resolution["requiresReview"]`, because the two are written at different
    times and the release is the authority.
    """
    composed: ComposedMessage = compose_reply(
        answer_text=str(resolution.get("answerText", "")),
        verbatim_question=question_text,
        disclosure=disclosure,
    )
    await _record_draft_fact(
        resolution,
        case_id=case_id,
        support_event_id=support_event_id,
        intent=intent,
        composed=composed,
        append_scoped_fact_once=append_scoped_fact_once,
    )

    if configuration.reply_gate.requires_review(intent):
        review = await reviews.create_review(
            case_id=case_id,
            # One request per support event: sect. 6's unit is the support
            # request, and the event that raised the question *is* the request
            # this reply answers. Deterministic, so a retried gating returns the
            # already-open review rather than opening a second one.
            request_id=f"{_LOGICAL_OPERATION_PREFIX}:{support_event_id}",
            review_kind=ReviewKind.SUPPORT_REPLY,
            draft_payload={
                "messageText": composed.text,
                "disclosesAgent": composed.discloses_agent,
                "supportEventId": support_event_id,
                "intent": intent,
                "confidenceMillionths": resolution.get("confidenceMillionths"),
                "resolvedByRung": resolution.get("resolvedByRung"),
                "citedFactIds": list(resolution.get("citedFactIds") or ()),
                "consumedFactIds": list(resolution.get("consumedFactIds") or ()),
                "contextHash": resolution.get("contextHash"),
            },
            # Not supplied: sect. 6 mints a SUPPORT_REPLY review's `scope_id`
            # server-side, and passing one here would be this module deciding a
            # thing the aggregate owns.
        )
        return GatedReply(
            outcome=ReplyGateOutcome.REVIEW_OPENED,
            message_text=composed.text,
            discloses_agent=composed.discloses_agent,
            review_id=str(review["_id"]),
            scope_id=str(review["scopeId"]),
        )

    logical_operation_id, delivery_id = reply_delivery_identity(
        case_id=case_id, support_event_id=support_event_id
    )
    thread = await threads.ensure_case_support_thread(
        case_id=case_id,
        tenant_id=tenant_id,
        principal_id=principal_id,
        support_draft=composed.text,
        idempotency_key=logical_operation_id,
    )
    post = await threads.post_support_message(
        work_item_id=thread.workItemId,
        message_text=composed.text,
        delivery_id=delivery_id,
        business_payload={
            "schemaVersion": "support-reply-v1",
            "caseId": case_id,
            "supportEventId": support_event_id,
            "intent": intent,
            "logicalOperationId": logical_operation_id,
            "disclosesAgent": composed.discloses_agent,
        },
        # Reserved and non-assignable (sect. 6). An automatic send is the
        # platform acting on its own, and saying so is the whole point of the
        # reservation.
        actor_id=SYSTEM_ACTOR,
        actor_role="AGENT",
    )
    return GatedReply(
        outcome=ReplyGateOutcome.AUTO_REPLIED,
        message_text=composed.text,
        discloses_agent=composed.discloses_agent,
        logical_operation_id=logical_operation_id,
        delivery_id=delivery_id,
        message_id=str(post.messageId),
        absorbed=bool(post.absorbed),
    )


async def _record_draft_fact(
    resolution: Mapping[str, Any],
    *,
    case_id: str,
    support_event_id: str,
    intent: str,
    composed: ComposedMessage,
    append_scoped_fact_once: ScopedFactWriterPort,
) -> None:
    """`support_reply_draft`, written on **both** paths.

    Both, because the fact is the record that the platform composed an answer to
    this support question -- which is equally true whether an associate approved
    it or the release let it go straight out. A fact written only on the review
    path would leave the auto path with no case-side trace at all, which is the
    path with the least human attention on it.
    """
    await append_scoped_fact_once(
        record_scope=None,
        fact_id=f"{SUPPORT_REPLY_DRAFT}-{support_event_id}",
        case_id=case_id,
        fact_name=SUPPORT_REPLY_DRAFT,
        value={
            "supportEventId": support_event_id,
            "intent": intent,
            "messageText": composed.text,
            "disclosesAgent": composed.discloses_agent,
            "confidenceMillionths": resolution.get("confidenceMillionths"),
            "resolvedByRung": resolution.get("resolvedByRung"),
            "consumedFactIds": list(resolution.get("consumedFactIds") or ()),
        },
        agent_id=AGENT_ID,
        channel=FactChannel.CHANNEL_A,
        # A model wrote the sentence. `DERIVED` would claim it was computed
        # from other facts, which is the trust distinction FactAcquisition keeps.
        acquisition_method=FactAcquisition.INFERRED,
        source_system="RETURN_SUPPORT",
        source_path="SUPPORT_REPLY_GATE",
    )
