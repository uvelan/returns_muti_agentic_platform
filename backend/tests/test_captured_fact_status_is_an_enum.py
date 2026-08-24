"""A fact's status must be one of six codes, never whatever the model said.

`FactStatus` has always had six members, and both ingestion points accepted
anything: `status=str(entry.get("status", "USABLE"))`. So a model that answered
with prose had that prose stored verbatim, and the copilot rendered it as the
fact's unsettled-reason badge -- immediately after the value, inside a
`truncate`. On screen `TAYLOR` and `Completing observation...` ran together as
`TAYLORCompleting ob...`, which reads like a string-concatenation bug in the
frontend and is nothing of the sort: it is an enum nobody enforced.
"""

from __future__ import annotations

import pytest

from return_platform.dynamic_knowledge.order_agent.facts import (
    FactStatus,
    coerce_fact_status,
)


@pytest.mark.parametrize("member", list(FactStatus))
def test_every_declared_status_survives_unchanged(member: FactStatus) -> None:
    assert coerce_fact_status(member.value) == member.value
    assert coerce_fact_status(member) == member.value


def test_prose_from_a_model_does_not_reach_the_panel() -> None:
    # The exact shape observed: a sentence where a code belongs.
    assert coerce_fact_status("Completing observation of the order") == FactStatus.USABLE.value


def test_case_and_padding_are_forgiven() -> None:
    # "usable" is the same answer as "USABLE"; refusing it would invent a defect.
    assert coerce_fact_status("  conflicting ") == FactStatus.CONFLICTING.value
    assert coerce_fact_status("Ambiguous") == FactStatus.AMBIGUOUS.value


def test_absent_status_still_means_usable() -> None:
    # The behaviour before this change, for a status that was simply not sent.
    assert coerce_fact_status(None) == FactStatus.USABLE.value
    assert coerce_fact_status("") == FactStatus.USABLE.value


def test_a_near_miss_is_not_silently_accepted() -> None:
    # `CONFLICTED` is not a member. Accepting near-misses by prefix would let the
    # next unenumerated string through, which is how this started.
    assert coerce_fact_status("CONFLICTED") == FactStatus.USABLE.value
