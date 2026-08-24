"""A value the graph does not contain must not anchor the rest of the conversation.

Observed live: "find order for TAYLOR" returned nothing and asked for another
identifier; the next turn, "find order for BLUEFIN", answered "I checked for
orders under TAYLOR, but didn't find any". The search had re-run on the dead
name from the previous turn.

`capture` decides status from what the associate said, and nothing there knows
whether the value matched. So TAYLOR was recorded `USABLE` -- neither
`REASKABLE` (the agent never raises it again) nor `SUPERSEDABLE` (a later name
merges against it as a rival rather than replacing it).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from return_platform.dynamic_knowledge.order_agent.facts import (
    FactCatalogue,
    FactDefinition,
    FactStatus,
)


@dataclass(frozen=True)
class _Stated:
    fact: str
    value: object
    ambiguous: bool = False
    source_message_id: str | None = None
    acquisition: str = "STATED"


@pytest.fixture
def catalogue() -> FactCatalogue:
    return FactCatalogue(
        (
            FactDefinition(
                name="customer_name",
                label="Customer name",
                priority=1,
                field_group="customer",
                answer_ttl_seconds=None,
                confirmation_required=False,
                validation=None,
            ),
        )
    )


@pytest.fixture
def as_of() -> datetime:
    return datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


def _after_failed_search(
    catalogue: FactCatalogue, as_of: datetime, name: str = "TAYLOR"
):
    captured, _ = catalogue.capture(
        (), (_Stated("customer_name", name),), turn_id="turn-1", as_of=as_of
    )
    return catalogue.demote_unmatched(captured, [name])


def test_a_name_that_matched_nothing_is_not_left_usable(catalogue, as_of):
    (fact,) = _after_failed_search(catalogue, as_of)
    assert FactStatus(fact.status) is FactStatus.INVALID
    assert fact.needs_asking_again, "a dead value the agent never raises again"


def test_the_next_name_replaces_it_instead_of_conflicting(catalogue, as_of):
    """The reported symptom: turn 2 must be about BLUEFIN, cleanly."""
    stale = _after_failed_search(catalogue, as_of)
    merged, _ = catalogue.capture(
        stale, (_Stated("customer_name", "BLUEFIN"),), turn_id="turn-2", as_of=as_of
    )
    (fact,) = merged
    assert fact.value == "BLUEFIN"
    assert FactStatus(fact.status) is FactStatus.USABLE
    assert not fact.needs_asking_again


def test_a_silent_turn_leaves_the_dead_value_reaskable(catalogue, as_of):
    """The worse half: the model restating nothing must not resurrect the name.

    Before the fix this returned TAYLOR/`USABLE`, and the next search ran on it.
    """
    stale = _after_failed_search(catalogue, as_of)
    merged, _ = catalogue.capture(stale, (), turn_id="turn-2", as_of=as_of)
    (fact,) = merged
    assert FactStatus(fact.status) is FactStatus.INVALID
    assert fact.needs_asking_again


def test_a_fact_the_search_never_used_is_untouched(catalogue, as_of):
    """Only the values sent to the graph failed to match anything."""
    captured, _ = catalogue.capture(
        (), (_Stated("customer_name", "STANLEY MEDINA"),), turn_id="turn-1", as_of=as_of
    )
    (fact,) = catalogue.demote_unmatched(captured, ["CW273354"])
    assert FactStatus(fact.status) is FactStatus.USABLE


def test_an_already_reaskable_fact_keeps_its_own_status(catalogue, as_of):
    """Demotion reports "did not match"; it must not overwrite a real conflict."""
    first, _ = catalogue.capture(
        (), (_Stated("customer_name", "TAYLOR"),), turn_id="turn-1", as_of=as_of
    )
    conflicted, _ = catalogue.capture(
        first, (_Stated("customer_name", "BLUEFIN"),), turn_id="turn-2", as_of=as_of
    )
    assert FactStatus(conflicted[0].status) is FactStatus.CONFLICTING
    (fact,) = catalogue.demote_unmatched(conflicted, ["BLUEFIN"])
    assert FactStatus(fact.status) is FactStatus.CONFLICTING


def test_list_valued_signals_are_compared_by_element(catalogue, as_of):
    """Configured multiplicity means a signal can arrive as a list of values."""
    captured, _ = catalogue.capture(
        (), (_Stated("customer_name", "TAYLOR"),), turn_id="turn-1", as_of=as_of
    )
    (fact,) = catalogue.demote_unmatched(captured, [["ACME", "TAYLOR"]])
    assert FactStatus(fact.status) is FactStatus.INVALID
