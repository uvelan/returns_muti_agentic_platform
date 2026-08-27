"""The associate's answer is this turn's message, and the transcript says so.

Resuming discards `initial_state`: the paused checkpoint already holds the real
accumulated state, so anything the new request brings has to travel in the
update. For a long time only the correlation id and the grounding did.

The answer itself reaches the graph as `Command(resume=...)` and lands in
`clarification_exchanges` -- so it was *reachable*, and nothing else was true.
`user_message` still named the associate's previous sentence and `transcript`
was frozen at the moment the pause began, while the prompt directs the model to
"look for the answer in this turn's message and in contextJson.transcript" and
to read the transcript "before deciding anything". Both of the fields it is
pointed at were stale on every resumed turn.

Observed end to end against the running platform: "Confirm the customer MERIDIAN
HEATING & COOLING on account ORL" arrived as `user_message: "find order for
ALEX"` with `transcript: []`, and the agent re-asked the question it had just
been answered. A turn that cannot see the answer cannot act on it, however the
prompt is worded.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from return_platform.dynamic_knowledge.order_agent.coordinator import _resume_update

AS_OF = datetime(2026, 8, 27, 6, 30, tzinfo=UTC)
ANSWER = "Confirm the customer MERIDIAN HEATING & COOLING on account ORL."


def _request(message: str = ANSWER, turn_id: str = "ui-turn-2") -> SimpleNamespace:
    return SimpleNamespace(message=message, client_turn_id=turn_id)


def _conversation(*exchanges: tuple[str, str]) -> dict[str, Any]:
    return {
        "transcript": [{"role": role, "text": text} for role, text in exchanges],
    }


def _update(paused: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    return _resume_update(
        request=kwargs.pop("request", _request()),
        conversation_state=kwargs.pop("conversation_state", _conversation()),
        paused_values=paused if paused is not None else {},
        as_of=AS_OF,
        session_timezone="America/New_York",
        correlation_id="corr-2",
        **kwargs,
    )


def test_the_answer_becomes_this_turns_message() -> None:
    """The defect, stated as the thing that was false."""
    assert _update()["user_message"] == ANSWER


def test_the_transcript_carries_what_was_already_said() -> None:
    """Frozen at the pause, it showed the model an empty conversation.

    The pausing turn records both halves -- the associate's message and the
    question it stopped on -- so by the time the answer arrives there is a real
    exchange to read. It simply never reached the resumed state.
    """
    state = _conversation(
        ("associate", "find order for ALEX"),
        ("agent", "Is this the Alex on GARDEN or the one on ORL?"),
    )

    transcript = _update(conversation_state=state)["transcript"]

    assert [entry["role"] for entry in transcript] == ["associate", "agent"]
    assert "GARDEN" in transcript[-1]["text"]


def test_the_turn_id_moves_with_the_message() -> None:
    """A fact reported on this turn cites `source_message_id`.

    Left at the paused turn's id, the associate's answer would be attributed to
    the message before it -- provenance pointing at the wrong sentence, which is
    worse than none.
    """
    assert _update(request=_request(turn_id="ui-turn-7"))["client_turn_id"] == "ui-turn-7"


def test_a_pinned_grounding_is_not_moved() -> None:
    """The as-of is the instant the checkpoint's evidence was filtered against.

    Moving it would leave that evidence and the next date filter meaning
    different days -- unchanged behaviour, asserted here because the message
    fields now travel beside it and must not drag it along.
    """
    update = _update(paused={"as_of": "2026-08-20T00:00:00+00:00"})

    assert "as_of" not in update
    assert "session_timezone" not in update
    assert update["user_message"] == ANSWER


def test_an_ungrounded_checkpoint_is_given_this_turns_instant() -> None:
    """A checkpoint written before the field existed resumes with no grounding."""
    update = _update(paused={})

    assert update["as_of"] == AS_OF.isoformat()
    assert update["session_timezone"] == "America/New_York"
