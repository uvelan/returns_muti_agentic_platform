from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast

from return_platform.conversation.progressive import (
    ConversationStatePolicy,
    DisambiguationRule,
    ProgressiveConversationEngine,
)


@dataclass(frozen=True)
class Candidate:
    city: str | None
    postal: str | None


def value_for(candidate: Candidate, field: str) -> str | None:
    return cast(str | None, getattr(candidate, field))


STATES = ConversationStatePolicy(
    no_candidates="LOOKUP_REQUIRED",
    single_candidate="CANDIDATE_READY",
    slot_disambiguation="ATTRIBUTE_REQUIRED",
    generic_disambiguation="SELECTION_REQUIRED",
)


def test_progressive_engine_requests_highest_priority_distinguishing_slot() -> None:
    engine = ProgressiveConversationEngine[Candidate](
        rules=(
            DisambiguationRule("city", "city", "Which city?", 100),
            DisambiguationRule("postal", "postal", "Which postal code?", 50),
        ),
        candidate_ttl_seconds=300,
        states=STATES,
    )
    now = datetime(2026, 7, 27, tzinfo=UTC)
    decision = engine.project(
        [Candidate("Dallas", "75001"), Candidate("Austin", "75001")],
        value_for=value_for,
        default_ambiguity_question="Select an order.",
        now=now,
    )
    assert decision.state == "ATTRIBUTE_REQUIRED"
    assert decision.requested_slots == ("city",)
    assert decision.question == "Which city?"
    assert decision.candidate_set_id is not None
    assert decision.candidate_set_expires_at == now + timedelta(seconds=300)


def test_progressive_engine_never_invents_a_state_from_one_candidate() -> None:
    engine = ProgressiveConversationEngine[Candidate](
        rules=(DisambiguationRule("city", "city", "Which city?", 100),),
        candidate_ttl_seconds=300,
        states=STATES,
    )
    decision = engine.project(
        [Candidate("Dallas", "75001")],
        value_for=value_for,
        default_ambiguity_question="Select an order.",
    )
    assert decision.state == "CANDIDATE_READY"
    assert decision.candidate_set_id is None


def test_response_matching_rejects_empty_and_accepts_bounded_natural_answer() -> None:
    assert not ProgressiveConversationEngine.response_matches("Dallas", "")
    assert ProgressiveConversationEngine.response_matches("Dallas", "the Dallas account")


def test_progressive_engine_uses_candidate_values_not_a_fixed_field_order() -> None:
    engine = ProgressiveConversationEngine[Candidate](
        rules=(
            DisambiguationRule("postal", "postal", "Which postal code?", 100),
            DisambiguationRule("city", "city", "Which city?", 50),
        ),
        candidate_ttl_seconds=300,
        states=STATES,
        max_clarification_options=3,
    )
    candidates = [
        Candidate("Dallas", "75001"),
        Candidate("Dallas", "75002"),
        Candidate("Austin", "75003"),
        Candidate("Austin", "75004"),
    ]

    selected = engine.select_rule(candidates, value_for=value_for)

    assert selected is not None
    assert selected.slot == "city"
