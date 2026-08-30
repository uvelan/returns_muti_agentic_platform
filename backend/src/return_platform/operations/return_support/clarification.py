"""The clarification round-trip's answering half (contracts.md sect. 9, brief 5).

V2 writes `support_clarification_requested` when it cannot act on a message
without asking the associate something. This module is what happens when the
associate answers:

    answer endpoint -> command + outbox -> `clarification_answered` signal
        -> record_clarification_answer   (the fact, Channel A, STATED)
        -> relay_clarification_to_support (the message back to Support)
        -> deadline reset per `clarification_resets_deadline`

The two activity bodies live here rather than in `workflows/`, for the reason
`artifact_binding.py` and `message_classification.py` both follow: an activity
should be a thin durable wrapper around a function that can be tested without a
Temporal environment, and the thing worth testing is what gets written.

## One delivery path, not a second one

The relay does **not** get its own posting code. It calls the same
`ensure_case_support_thread` + `post_support_message` pair the reply gate calls,
which is sect. 7's own instruction -- *"kind-agnostic... both review kinds use
this one receiver-deduped path"* -- and the reason is that each additional
posting path is another chance to get the dedupe identity wrong. A relayed
clarification, an approved template and an approved reply are the same act as
far as the receiver is concerned: a message arriving on the case thread with a
delivery id.

## Neutralisation, on both halves

The relay carries **Support's question** and **the associate's answer**, both
typed by a person, into a message shown to the other one. Both go through
`compose_clarification_relay`, which neutralises every value it interpolates and
bounds each one -- carry-forward conditions 7 and 10. The *fact* keeps the
answer as it was typed; only the rendering is neutralised and bounded.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, Protocol

from return_platform.operations.fact_names import SUPPORT_CLARIFICATION_ANSWERED
from return_platform.operations.models import FactAcquisition, FactChannel
from return_platform.operations.return_support.outbound_composition import (
    DisclosureLike,
    compose_clarification_relay,
)
from return_platform.operations.return_support.reply_gating import (
    REPLY_DELIVERY_NAMESPACE,
    ScopedFactWriterPort,
    SupportThreadPort,
)
from return_platform.operations.return_support.resolution_ladder import AGENT_ID

__all__ = [
    "CLARIFICATION_ANSWER_SIGNAL_PREFIX",
    "ClarificationAnswer",
    "ClarificationRelayResult",
    "clarification_answer_signal_id",
    "clarification_relay_identity",
    "deadline_after_clarification",
    "record_clarification_answer",
    "relay_clarification_to_support",
]

CLARIFICATION_ANSWER_SIGNAL_PREFIX: Final = "clarification-answered"
_RELAY_OPERATION_PREFIX: Final = "clarification-relay"


@dataclass(frozen=True, slots=True)
class ClarificationAnswer:
    """What the associate said, and what it was in answer to.

    `verbatim_question` travels with the answer rather than being re-read at
    relay time. A support thread carries several open questions at once, and an
    answer paired with whichever question the reader last remembers is worse
    than no answer -- so the question the associate was actually shown is
    carried alongside what they typed.
    """

    clarification_id: str
    case_id: str
    support_event_id: str
    verbatim_question: str
    answer_text: str
    actor_id: str
    #: `map` or `reject` for an unmatched-artifact clarification; `None` for a
    #: plain question. The choice is a closed set decided by the endpoint, never
    #: derived from the answer text.
    resolution_choice: str | None = None
    return_record_id: str | None = None


def clarification_answer_signal_id(*, case_id: str, clarification_id: str) -> str:
    """The command's signal id. Derived, so a retried answer is one command.

    Sect. 7 deduplicates commands on `(caseId, signalId)`. Deriving the signal
    id from the clarification means a double-submitted answer form records one
    command and signals the workflow once, rather than relying on the client to
    send the same generated id twice.
    """
    return f"{CLARIFICATION_ANSWER_SIGNAL_PREFIX}:{case_id}:{clarification_id}"


def clarification_relay_identity(*, case_id: str, clarification_id: str) -> tuple[str, str]:
    """`(logical_operation_id, delivery_id)` for one relayed clarification. Pure.

    Same construction as `reply_delivery_identity`, and the same namespace: the
    two are different logical operations because their prefixes differ, so
    sharing the namespace cannot collide them, and a second namespace would be a
    second thing to keep in step.
    """
    logical_operation_id = f"{_RELAY_OPERATION_PREFIX}:{case_id}:{clarification_id}"
    return logical_operation_id, str(
        uuid.uuid5(REPLY_DELIVERY_NAMESPACE, logical_operation_id)
    )


def deadline_after_clarification(
    *,
    current_deadline_iso: str,
    refreshed_deadline_iso: str,
    resets: bool,
) -> str:
    """Which deadline the review wait resumes on (`clarification_resets_deadline`).

    Pure, and separate from the workflow, because it is the one part of the
    reset that is a *decision* rather than a mechanism -- and because the
    workflow half cannot be written on this base (V1 phase 2's review gate is
    absent; see the ledger). A workflow that inlined this would make the
    released switch untestable without a Temporal environment.

    Never picks the earlier of the two: a reset that could move a deadline
    *inwards* would punish an associate for answering promptly, which is the
    opposite of what the default `True` exists for. The refreshed deadline is
    computed by the caller from the business calendar; this only decides whether
    it applies.
    """
    if not resets:
        return current_deadline_iso
    return max(current_deadline_iso, refreshed_deadline_iso)


async def record_clarification_answer(
    answer: ClarificationAnswer,
    *,
    append_scoped_fact_once: ScopedFactWriterPort,
) -> bool:
    """Activity body: the associate's answer, on the case (contracts.md sect. 10).

    `STATED` and `CHANNEL_A`, both load-bearing. `STATED` because a person typed
    it -- not `OBSERVED` (nothing watched it happen) and not `INFERRED` (no
    model produced it). `CHANNEL_A` because it came from the branch associate's
    own console; the question it answers is a Channel B fact, and collapsing the
    two would lose which side of the bridge each sentence came from.

    The actor is the server-stamped one from the endpoint's capability check
    (sect. 4: command-originated facts carry server-stamped `actorId`), never
    anything in the request body. It travels as the **`actor_id` parameter**, so
    it lands in the fact document's own `actorId` field and is queryable as
    provenance.

    That parameter did not exist when this module was first written -- S1's
    `append_scoped_case_fact` had no `actor_id`, so the actor was carried inside
    the fact's `value` as `answeredBy`, and the gap was reported rather than
    worked around. S1 phase 1b then shipped the real field, and this adopted it:
    the value-level spelling is **gone**, not joined by a second one. Two
    spellings of one idea is how provenance stops being queryable, which is the
    defect S1 phase 1b existed to end.

    Record-scoped where the clarification named a record, case-scoped where it
    did not -- an unmatched artifact belongs to no record yet, which is the
    whole reason it was asked about.
    """
    return await append_scoped_fact_once(
        record_scope=answer.return_record_id,
        fact_id=f"{SUPPORT_CLARIFICATION_ANSWERED}-{answer.clarification_id}",
        case_id=answer.case_id,
        fact_name=SUPPORT_CLARIFICATION_ANSWERED,
        value={
            "clarificationId": answer.clarification_id,
            "supportEventId": answer.support_event_id,
            # As typed. The audit record is the unmodified answer; the relay's
            # rendering is where neutralisation and the length bound apply.
            "answerText": answer.answer_text,
            "resolutionChoice": answer.resolution_choice,
        },
        agent_id=AGENT_ID,
        channel=FactChannel.CHANNEL_A,
        acquisition_method=FactAcquisition.STATED,
        source_system="RETURN_SUPPORT",
        source_path="CASE_CLARIFICATION_ANSWER",
        # Server-stamped provenance, not a value (S1 phase 1b). `agent_id` says
        # which component wrote the fact; `actor_id` says on whose authority.
        actor_id=answer.actor_id,
    )


@dataclass(frozen=True, slots=True)
class ClarificationRelayResult:
    logical_operation_id: str
    delivery_id: str
    message_id: str
    message_text: str
    discloses_agent: bool
    absorbed: bool


async def relay_clarification_to_support(
    answer: ClarificationAnswer,
    *,
    tenant_id: str,
    principal_id: str,
    disclosure: DisclosureLike | None,
    threads: SupportThreadPort,
) -> ClarificationRelayResult:
    """Activity body: send the answer back to Support (contracts.md sect. 9).

    Both halves of the composed message are a person's typing, and both are
    neutralised and bounded by `compose_clarification_relay`. The disclosure
    rides with it, so an answer the platform relayed cannot be read as one
    Support's own desk wrote.
    """
    composed = compose_clarification_relay(
        verbatim_question=answer.verbatim_question,
        answer_text=answer.answer_text,
        disclosure=disclosure,
    )
    logical_operation_id, delivery_id = clarification_relay_identity(
        case_id=answer.case_id, clarification_id=answer.clarification_id
    )
    thread = await threads.ensure_case_support_thread(
        case_id=answer.case_id,
        tenant_id=tenant_id,
        principal_id=principal_id,
        support_draft=composed.text,
        idempotency_key=logical_operation_id,
    )
    post = await threads.post_support_message(
        work_item_id=thread.workItemId,
        message_text=composed.text,
        delivery_id=delivery_id,
        business_payload=_relay_payload(
            answer, logical_operation_id=logical_operation_id, composed_discloses=composed
        ),
        # The associate answered, but the platform composed and sent the
        # message. Stamping the associate here would claim they wrote a
        # disclosure line they never saw.
        actor_id=AGENT_ID,
        actor_role="AGENT",
    )
    return ClarificationRelayResult(
        logical_operation_id=logical_operation_id,
        delivery_id=delivery_id,
        message_id=str(post.messageId),
        message_text=composed.text,
        discloses_agent=composed.discloses_agent,
        absorbed=bool(post.absorbed),
    )


def _relay_payload(
    answer: ClarificationAnswer, *, logical_operation_id: str, composed_discloses: Any
) -> Mapping[str, Any]:
    return {
        "schemaVersion": "support-clarification-answer-v1",
        "caseId": answer.case_id,
        "clarificationId": answer.clarification_id,
        "supportEventId": answer.support_event_id,
        "returnRecordId": answer.return_record_id,
        "resolutionChoice": answer.resolution_choice,
        "logicalOperationId": logical_operation_id,
        "disclosesAgent": composed_discloses.discloses_agent,
        # Deliberately absent: `answerText`. The words are in the message and in
        # the fact; a third copy in a mirrored business payload is a third place
        # a redaction would have to reach.
    }


class CommandStorePort(Protocol):
    """`DurableCaseCommandStore.record_command`, structurally."""

    async def record_command(self, **kwargs: Any) -> Any: ...
