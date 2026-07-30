"""Reusable deterministic progressive-conversation policy engine."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TypeVar

CandidateT = TypeVar("CandidateT")


@dataclass(frozen=True, slots=True)
class DisambiguationRule:
    """One safe candidate attribute that may be requested to resolve ambiguity."""

    slot: str
    candidate_field: str
    label: str
    priority: int


@dataclass(frozen=True, slots=True)
class ConversationStatePolicy:
    """Domain-supplied state names used by the reusable conversation engine."""

    no_candidates: str
    single_candidate: str
    slot_disambiguation: str
    generic_disambiguation: str

    def __post_init__(self) -> None:
        values = (
            self.no_candidates,
            self.single_candidate,
            self.slot_disambiguation,
            self.generic_disambiguation,
        )
        if any(not value.strip() for value in values):
            raise ValueError("Conversation state names must not be blank")


@dataclass(frozen=True, slots=True)
class DialogueDecision[CandidateT]:
    """Deterministic state projection produced from a candidate set."""

    state: str
    candidates: tuple[CandidateT, ...]
    requested_slots: tuple[str, ...]
    candidate_set_id: str | None
    candidate_set_expires_at: datetime | None
    question: str | None


class ProgressiveConversationEngine[CandidateT]:
    """Domain-neutral candidate disambiguation and requested-slot matching."""

    def __init__(
        self,
        *,
        rules: Sequence[DisambiguationRule],
        candidate_ttl_seconds: int,
        states: ConversationStatePolicy,
        max_clarification_options: int = 6,
    ) -> None:
        if candidate_ttl_seconds <= 0:
            raise ValueError("candidate_ttl_seconds must be positive")
        if max_clarification_options < 2:
            raise ValueError("max_clarification_options must be at least 2")
        normalized_rules = tuple(sorted(rules, key=lambda item: -item.priority))
        slots = tuple(item.slot for item in normalized_rules)
        if not normalized_rules or len(slots) != len(set(slots)):
            raise ValueError("Disambiguation rules must be non-empty and use unique slots")
        self._rules = normalized_rules
        self._candidate_ttl_seconds = candidate_ttl_seconds
        self._states = states
        self._max_clarification_options = max_clarification_options

    @property
    def rules(self) -> tuple[DisambiguationRule, ...]:
        return self._rules

    @staticmethod
    def response_matches(candidate_value: str | None, response: str) -> bool:
        """Match one short human response without regex or model inference."""

        if not candidate_value:
            return False
        candidate_normalized = " ".join(candidate_value.casefold().split())
        response_normalized = " ".join(response.casefold().split())
        if not response_normalized:
            return False
        return (
            candidate_normalized == response_normalized
            or candidate_normalized in response_normalized
            or response_normalized in candidate_normalized
        )

    def select_rule(
        self,
        candidates: Sequence[CandidateT],
        *,
        value_for: Callable[[CandidateT, str], str | None],
        excluded_slots: set[str] | None = None,
    ) -> DisambiguationRule | None:
        excluded = excluded_slots or set()
        ranked_rules: list[tuple[int, int, int, DisambiguationRule]] = []
        for rule in self._rules:
            if rule.slot in excluded:
                continue
            values = [
                normalized.casefold()
                for candidate in candidates
                if (value := value_for(candidate, rule.candidate_field))
                if (normalized := " ".join(value.split()).strip())
            ]
            distinct_values = set(values)
            if (
                len(values) != len(candidates)
                or len(distinct_values) <= 1
                or len(distinct_values) > self._max_clarification_options
            ):
                continue
            largest_partition = max(values.count(value) for value in distinct_values)
            ranked_rules.append(
                (
                    largest_partition,
                    len(distinct_values),
                    -rule.priority,
                    rule,
                )
            )
        if not ranked_rules:
            return None
        return min(ranked_rules, key=lambda item: item[:3])[3]

    def project(
        self,
        candidates: Sequence[CandidateT],
        *,
        value_for: Callable[[CandidateT, str], str | None],
        default_ambiguity_question: str,
        now: datetime | None = None,
    ) -> DialogueDecision[CandidateT]:
        """Project candidates to one state without allowing AI to choose transitions."""

        frozen_candidates = tuple(candidates)
        if not frozen_candidates:
            return DialogueDecision(
                state=self._states.no_candidates,
                candidates=(),
                requested_slots=(),
                candidate_set_id=None,
                candidate_set_expires_at=None,
                question=None,
            )
        if len(frozen_candidates) == 1:
            return DialogueDecision(
                state=self._states.single_candidate,
                candidates=frozen_candidates,
                requested_slots=(),
                candidate_set_id=None,
                candidate_set_expires_at=None,
                question=None,
            )

        instant = now or datetime.now(UTC)
        candidate_set_id = str(uuid.uuid4())
        expires_at = instant + timedelta(seconds=self._candidate_ttl_seconds)
        rule = self.select_rule(frozen_candidates, value_for=value_for)
        if rule is not None:
            return DialogueDecision(
                state=self._states.slot_disambiguation,
                candidates=frozen_candidates,
                requested_slots=(rule.slot,),
                candidate_set_id=candidate_set_id,
                candidate_set_expires_at=expires_at,
                question=f"Which {rule.label} matches the order?",
            )
        return DialogueDecision(
            state=self._states.generic_disambiguation,
            candidates=frozen_candidates,
            requested_slots=(),
            candidate_set_id=candidate_set_id,
            candidate_set_expires_at=expires_at,
            question=default_ambiguity_question,
        )
