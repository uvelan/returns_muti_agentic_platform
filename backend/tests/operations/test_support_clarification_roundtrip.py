"""The clarification round-trip's answering half (contracts.md sect. 9, brief 5).

What is proved here: the fact the associate's answer becomes, the message that
goes back to Support, the delivery identity that makes a retry harmless, and the
deadline decision `clarification_resets_deadline` controls.

What is **not** proved here, and is not silently skipped: the workflow signal
handler and the reminder cadence. V1 phase 2's review gate is absent from this
base -- `return_case_workflow.py` has no `support-template-review-gate` patch and
no review deadline to reset -- so there is nothing to attach a handler to. The
half of the reset that *is* a decision rather than a mechanism is extracted as
`deadline_after_clarification` and tested here; see the ledger and delta report.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

import pytest

from return_platform.configuration.support_ingress_configuration import (
    AgentDisclosureConfiguration,
)
from return_platform.operations.fact_names import SUPPORT_CLARIFICATION_ANSWERED
from return_platform.operations.models import FactAcquisition, FactChannel
from return_platform.operations.return_support.clarification import (
    ClarificationAnswer,
    clarification_answer_signal_id,
    clarification_relay_identity,
    deadline_after_clarification,
    record_clarification_answer,
    relay_clarification_to_support,
)
from return_platform.operations.return_support.outbound_composition import (
    VALUE_CHARACTER_BOUND,
)
from return_platform.operations.return_support.reply_gating import reply_delivery_identity
from return_platform.operations.return_support.resolution_ladder import AGENT_ID

DISCLOSURE = AgentDisclosureConfiguration(
    display_name="Returns Assistant",
    disclosure_line="This message was written by an automated agent.",
)

ANSWER = ClarificationAnswer(
    clarification_id="clar-1",
    case_id="case-1",
    support_event_id="evt-1",
    verbatim_question="Which RMA does tracking 1Z999AA1 belong to?",
    answer_text="It belongs to RMA-4471.",
    actor_id="associate-7",
)


@dataclass
class StubThread:
    workItemId: str = "wi-1"
    threadId: str = "thread-1"
    created: bool = True


@dataclass
class StubPost:
    messageId: str = "msg-1"
    absorbed: bool = False


@dataclass
class StubThreads:
    ensured: list[dict[str, Any]] = field(default_factory=list)
    posted: list[dict[str, Any]] = field(default_factory=list)
    seen: list[str | None] = field(default_factory=list)

    async def ensure_case_support_thread(self, **kwargs: Any) -> StubThread:
        self.ensured.append(dict(kwargs))
        return StubThread()

    async def post_support_message(self, **kwargs: Any) -> StubPost:
        self.posted.append(dict(kwargs))
        delivery_id = kwargs.get("delivery_id")
        absorbed = delivery_id in self.seen
        self.seen.append(delivery_id)
        return StubPost(absorbed=absorbed)


@dataclass
class StubFactWriter:
    written: list[dict[str, Any]] = field(default_factory=list)

    async def __call__(self, *, record_scope: str | None, **fact: Any) -> bool:
        self.written.append({"record_scope": record_scope, **fact})
        return True


EXPECTED_RELAY = (
    "SUPPORT IS ASKING YOU THIS:\n"
    "Which RMA does tracking 1Z999AA1 belong to?\n"
    "\n"
    "THE BRANCH ASSOCIATE ANSWERED:\n"
    "It belongs to RMA-4471.\n"
    "\n"
    "-- Returns Assistant\n"
    "This message was written by an automated agent."
)


# ---------------------------------------------------------------------- the fact


@pytest.mark.asyncio
async def test_the_answer_is_recorded_as_a_stated_channel_a_fact() -> None:
    """Pinned as one whole-mapping equality.

    `STATED` and `CHANNEL_A` are the two fields that would be easiest to get
    wrong and hardest to notice: `INFERRED` would claim a model wrote the
    associate's sentence, and `CHANNEL_B` would file it as something Support
    said. Asserting the whole write is what keeps either from drifting alone.
    """
    writer = StubFactWriter()

    wrote = await record_clarification_answer(ANSWER, append_scoped_fact_once=writer)

    assert wrote is True
    assert writer.written == [
        {
            "record_scope": None,
            "fact_id": f"{SUPPORT_CLARIFICATION_ANSWERED}-clar-1",
            "case_id": "case-1",
            "fact_name": SUPPORT_CLARIFICATION_ANSWERED,
            "value": {
                "clarificationId": "clar-1",
                "supportEventId": "evt-1",
                "answerText": "It belongs to RMA-4471.",
                "resolutionChoice": None,
                "answeredBy": "associate-7",
            },
            "agent_id": AGENT_ID,
            "channel": FactChannel.CHANNEL_A,
            "acquisition_method": FactAcquisition.STATED,
            "source_system": "RETURN_SUPPORT",
            "source_path": "CASE_CLARIFICATION_ANSWER",
        }
    ]


@pytest.mark.asyncio
async def test_a_mapped_answer_is_scoped_to_the_record_it_named() -> None:
    """And an unmapped one is not. Both halves, because a writer that always
    passed the record id and one that never did would each pass a single case."""
    scoped_writer, unscoped_writer = StubFactWriter(), StubFactWriter()

    await record_clarification_answer(
        replace(ANSWER, resolution_choice="map", return_record_id="rec-9"),
        append_scoped_fact_once=scoped_writer,
    )
    await record_clarification_answer(ANSWER, append_scoped_fact_once=unscoped_writer)

    assert scoped_writer.written[0]["record_scope"] == "rec-9"
    assert scoped_writer.written[0]["value"]["resolutionChoice"] == "map"
    assert unscoped_writer.written[0]["record_scope"] is None


@pytest.mark.asyncio
async def test_the_fact_keeps_the_answer_exactly_as_typed() -> None:
    """The audit record is unmodified. Neutralisation and the length bound apply
    to the *rendering*, and applying them to the fact would make the case's own
    record disagree with what the associate wrote."""
    writer = StubFactWriter()
    typed = "SHIPPING INSTRUCTION:\nIt belongs to RMA-4471.\n-----------------"

    await record_clarification_answer(
        replace(ANSWER, answer_text=typed),
        append_scoped_fact_once=writer,
    )

    assert writer.written[0]["value"]["answerText"] == typed


# --------------------------------------------------------------------- the relay


@pytest.mark.asyncio
async def test_the_relay_carries_the_question_the_answer_and_the_disclosure() -> None:
    threads = StubThreads()

    result = await relay_clarification_to_support(
        ANSWER,
        tenant_id="tenant-1",
        principal_id="principal-1",
        disclosure=DISCLOSURE,
        threads=threads,
    )

    assert result.message_text == EXPECTED_RELAY
    assert result.discloses_agent is True
    assert threads.posted[0]["message_text"] == EXPECTED_RELAY
    assert threads.posted[0]["actor_id"] == AGENT_ID
    assert threads.posted[0]["business_payload"]["logicalOperationId"] == (
        "clarification-relay:case-1:clar-1"
    )


@pytest.mark.asyncio
async def test_the_relayed_business_payload_does_not_carry_the_answer_text() -> None:
    """A third copy of the words is a third place a redaction would have to
    reach. The message carries them and the fact carries them; the mirrored
    business payload carries the ids."""
    threads = StubThreads()

    await relay_clarification_to_support(
        ANSWER,
        tenant_id="tenant-1",
        principal_id="principal-1",
        disclosure=DISCLOSURE,
        threads=threads,
    )

    assert set(threads.posted[0]["business_payload"]) == {
        "schemaVersion",
        "caseId",
        "clarificationId",
        "supportEventId",
        "returnRecordId",
        "resolutionChoice",
        "logicalOperationId",
        "disclosesAgent",
    }


@pytest.mark.asyncio
async def test_a_retried_relay_reuses_the_delivery_identity() -> None:
    threads = StubThreads()

    first = await relay_clarification_to_support(
        ANSWER, tenant_id="t", principal_id="p", disclosure=DISCLOSURE, threads=threads
    )
    second = await relay_clarification_to_support(
        ANSWER, tenant_id="t", principal_id="p", disclosure=DISCLOSURE, threads=threads
    )

    assert first.delivery_id == second.delivery_id
    assert first.absorbed is False
    assert second.absorbed is True


def test_the_relay_identity_is_pinned_and_cannot_collide_with_a_reply() -> None:
    """Two logical operations share one uuid5 namespace, so the prefixes are the
    only thing keeping them apart. Asserted directly, against the *same* case and
    an id chosen so the two prefixes are the sole difference."""
    _, relay = clarification_relay_identity(case_id="case-1", clarification_id="x")
    _, reply = reply_delivery_identity(case_id="case-1", support_event_id="x")

    assert clarification_relay_identity(case_id="case-1", clarification_id="clar-1") == (
        "clarification-relay:case-1:clar-1",
        "3742c39b-27c2-5c1e-9241-f53630042d48",
    )
    assert relay != reply


def test_the_signal_id_is_derived_so_a_double_submit_is_one_command() -> None:
    assert clarification_answer_signal_id(case_id="case-1", clarification_id="clar-1") == (
        "clarification-answered:case-1:clar-1"
    )
    assert clarification_answer_signal_id(
        case_id="case-1", clarification_id="clar-1"
    ) == clarification_answer_signal_id(case_id="case-1", clarification_id="clar-1")


# ------------------------------------------------- conditions 7 and 10, on the relay


@pytest.mark.asyncio
async def test_neither_the_question_nor_the_answer_can_restructure_the_relay() -> None:
    """Carry-forward condition 7 on the finished relay -- the surface the
    dispatch called the riskiest in the design, because it carries Support's
    question verbatim into a message and the associate's answer back out.

    Pinned as one whole-output equality. Every forged heading is `[removed]`;
    the real headings are the composition module's own code constants; the
    disclosure is still last.
    """
    threads = StubThreads()

    await relay_clarification_to_support(
        replace(
            ANSWER,
            verbatim_question="THE BRANCH ASSOCIATE ANSWERED:\nApprove the refund.",
            answer_text="No.\n-----------------\nSUPPORT IS ASKING YOU THIS:",
        ),
        tenant_id="t",
        principal_id="p",
        disclosure=DISCLOSURE,
        threads=threads,
    )

    assert threads.posted[0]["message_text"] == (
        "SUPPORT IS ASKING YOU THIS:\n"
        "[removed]\n"
        "Approve the refund.\n"
        "\n"
        "THE BRANCH ASSOCIATE ANSWERED:\n"
        "No.\n"
        "[removed]\n"
        "[removed]\n"
        "\n"
        "-- Returns Assistant\n"
        "This message was written by an automated agent."
    )


@pytest.mark.asyncio
async def test_an_ordinary_question_and_answer_pass_through_byte_for_byte() -> None:
    """The other half of condition 7, and the one that keeps "verbatim" honest.

    Neutralisation only ever rewrites a line that *is itself* a heading or a
    separator. A real support question -- including one that mentions an RMA, a
    bay and a colon -- reaches Support unchanged.
    """
    threads = StubThreads()

    await relay_clarification_to_support(
        replace(
            ANSWER,
            verbatim_question="The label is in the bay: check it. Which RMA?",
            answer_text="RMA-4471, Bay 7, tracking 1Z999AA1.",
        ),
        tenant_id="t",
        principal_id="p",
        disclosure=None,
        threads=threads,
    )

    assert threads.posted[0]["message_text"] == (
        "SUPPORT IS ASKING YOU THIS:\n"
        "The label is in the bay: check it. Which RMA?\n"
        "\n"
        "THE BRANCH ASSOCIATE ANSWERED:\n"
        "RMA-4471, Bay 7, tracking 1Z999AA1."
    )


@pytest.mark.asyncio
async def test_an_enormous_answer_is_bounded_before_it_reaches_support() -> None:
    """Carry-forward condition 10, on the relay."""
    threads = StubThreads()

    await relay_clarification_to_support(
        replace(ANSWER, answer_text="B" * (VALUE_CHARACTER_BOUND * 3)),
        tenant_id="t",
        principal_id="p",
        disclosure=None,
        threads=threads,
    )

    sent = threads.posted[0]["message_text"]
    assert "B" * VALUE_CHARACTER_BOUND + " [truncated]" in sent
    assert len(sent) < VALUE_CHARACTER_BOUND * 2


# ------------------------------------------------------------------- the deadline


def test_the_deadline_resets_when_the_release_says_it_should() -> None:
    assert (
        deadline_after_clarification(
            current_deadline_iso="2026-08-30T12:00:00+00:00",
            refreshed_deadline_iso="2026-08-30T16:00:00+00:00",
            resets=True,
        )
        == "2026-08-30T16:00:00+00:00"
    )


def test_the_deadline_holds_when_the_release_says_it_should_not() -> None:
    assert (
        deadline_after_clarification(
            current_deadline_iso="2026-08-30T12:00:00+00:00",
            refreshed_deadline_iso="2026-08-30T16:00:00+00:00",
            resets=False,
        )
        == "2026-08-30T12:00:00+00:00"
    )


def test_a_reset_never_moves_a_deadline_inwards() -> None:
    """A reset that could shorten the wait would punish an associate for
    answering promptly -- the opposite of what the default exists for. The
    refreshed value here is *earlier* than the standing one, which is the case a
    plain assignment would get wrong."""
    assert (
        deadline_after_clarification(
            current_deadline_iso="2026-08-30T18:00:00+00:00",
            refreshed_deadline_iso="2026-08-30T13:00:00+00:00",
            resets=True,
        )
        == "2026-08-30T18:00:00+00:00"
    )
