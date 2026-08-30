"""S2: the same facts and the same policy make the same context, always.

Contracts.md sect. 10. `assemble_case_context` is pure, and the tests that
matter most here are the ones that would pass anyway if it were not: identical
inputs hashing identically, and a shuffled input hashing the same as a sorted
one. A context whose bytes depend on which document Mongo happened to return
first is not reproducible, and nothing downstream could tell.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from return_platform.configuration.context_assembly_configuration import (
    ContextAssemblyConfiguration,
    ContextCompactionConfiguration,
)
from return_platform.configuration.return_configuration import (
    ReturnPlatformConfiguration,
    load_return_configuration,
)
from return_platform.configuration.settings import DEFAULT_RETURN_CONFIGURATION_PATH
from return_platform.operations.fact_names import CONTEXT_SUMMARY
from return_platform.platform.reasoning.case_context import (
    UnknownTokenizerError,
    assemble_case_context,
)

BASE = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)


def _fact(
    fact_id: str,
    fact_name: str,
    value: Any,
    *,
    minute: int = 0,
    record_scope: str | None = None,
) -> dict[str, Any]:
    return {
        "factId": fact_id,
        "caseId": "case-9400",
        "factName": fact_name,
        "value": value,
        "recordedAt": BASE + timedelta(minutes=minute),
        "record_scope": record_scope,
    }


def _policy(**overrides: Any) -> ContextAssemblyConfiguration:
    base: dict[str, Any] = {
        "pinned_fact_names": (),
        "token_budget": 8_000,
        "tokenizer_version": "wordpiece-approx.v1",
        "compaction": ContextCompactionConfiguration(),
    }
    base.update(overrides)
    return ContextAssemblyConfiguration(**base)


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #


def test_identical_inputs_are_byte_identical() -> None:
    """Definition of done, stated as a hash compare."""
    facts = [
        _fact("f1", "return_reason", "damaged", minute=1),
        _fact("f2", "carrier", "FEDEX", minute=2),
    ]

    first = assemble_case_context(facts, _policy())
    second = assemble_case_context(list(facts), _policy())

    assert first.content_hash == second.content_hash
    assert first.payload() == second.payload()


def test_input_order_does_not_reach_the_output() -> None:
    """The canonical order is `(recordedAt, factId)` and nothing else.

    Mongo returns documents in whatever order it likes; if that reached the
    hash, two identical cases would look different and no cache or audit
    downstream could compare them.
    """
    facts = [_fact(f"f{index}", f"name_{index}", index, minute=index) for index in range(12)]
    sorted_hash = assemble_case_context(facts, _policy()).content_hash

    shuffled = list(facts)
    random.Random(7).shuffle(shuffled)

    assert assemble_case_context(shuffled, _policy()).content_hash == sorted_hash


def test_facts_sharing_an_instant_are_tie_broken_by_fact_id() -> None:
    """`recordedAt` alone is not a total order: one transaction, one instant."""
    same_instant = [
        _fact("f-b", "beta", 2, minute=5),
        _fact("f-a", "alpha", 1, minute=5),
    ]

    assembled = assemble_case_context(same_instant, _policy())
    assert [entry.fact_id for entry in assembled.entries] == ["f-a", "f-b"]


# --------------------------------------------------------------------------- #
# The scoped-latest projection
# --------------------------------------------------------------------------- #


def test_the_projection_is_latest_per_scope_and_name() -> None:
    """Two records each carry a tracking number; neither shadows the other."""
    facts = [
        _fact("f1", "tracking_number", "OLD-A", minute=1, record_scope="record-a"),
        _fact("f2", "tracking_number", "NEW-A", minute=5, record_scope="record-a"),
        _fact("f3", "tracking_number", "B-1", minute=2, record_scope="record-b"),
        _fact("f4", "case_status", "AWAITING_SUPPORT", minute=3),
    ]

    assembled = assemble_case_context(facts, _policy())
    by_scope = {(entry.record_scope, entry.fact_name): entry.value for entry in assembled.entries}

    assert by_scope[("record-a", "tracking_number")] == "NEW-A"
    assert by_scope[("record-b", "tracking_number")] == "B-1"
    assert by_scope[(None, "case_status")] == "AWAITING_SUPPORT"
    assert len(assembled.entries) == 3


def test_a_superseded_fact_is_not_in_the_context_but_is_not_a_loss() -> None:
    """The projection collapses the log; the log itself is untouched."""
    facts = [
        _fact("f1", "return_reason", "wrong_item", minute=1),
        _fact("f2", "return_reason", "damaged", minute=4),
    ]

    assembled = assemble_case_context(facts, _policy())
    assert [entry.value for entry in assembled.entries] == ["damaged"]
    assert assembled.consumed_fact_ids == ("f2",)


# --------------------------------------------------------------------------- #
# Pinned names
# --------------------------------------------------------------------------- #


def test_a_pinned_name_survives_a_budget_that_fits_nothing_else() -> None:
    """The failure the pin exists to prevent: the model reasoning without the
    one fact the operator was certain it had seen."""
    facts = [
        _fact("f1", "return_reason", "x" * 400, minute=1),
        _fact("f2", "carrier", "y" * 400, minute=2),
        _fact("f3", "case_status", "z" * 400, minute=3),
    ]

    assembled = assemble_case_context(
        facts, _policy(pinned_fact_names=("return_reason",), token_budget=140)
    )

    kept = {entry.fact_name for entry in assembled.entries}
    assert "return_reason" in kept
    assert assembled.omitted_fact_ids


def test_caller_pins_add_to_the_configured_ones() -> None:
    """A resolver pinning the fact it is reasoning about adds, never replaces."""
    facts = [
        _fact("f1", "return_reason", "a" * 200, minute=1),
        _fact("f2", "carrier", "b" * 200, minute=2),
        _fact("f3", "noise", "c" * 200, minute=3),
    ]

    assembled = assemble_case_context(
        facts,
        _policy(pinned_fact_names=("return_reason",), token_budget=120),
        extra_pinned_fact_names=("carrier",),
    )

    assert assembled.pinned_fact_names == ("return_reason", "carrier")
    kept = {entry.fact_name for entry in assembled.entries}
    assert {"return_reason", "carrier"} <= kept


# --------------------------------------------------------------------------- #
# The budget
# --------------------------------------------------------------------------- #


def test_everything_fits_under_a_generous_budget() -> None:
    facts = [_fact(f"f{index}", f"name_{index}", index, minute=index) for index in range(20)]

    assembled = assemble_case_context(facts, _policy(token_budget=100_000))

    assert assembled.omitted_fact_ids == ()
    assert len(assembled.entries) == 20
    assert assembled.compaction_recommended is False


def test_what_the_budget_leaves_out_is_named_never_dropped() -> None:
    """Compaction never discards a fact. It can leave one out of *this*
    rendering, and then it has to say which."""
    facts = [_fact(f"f{index}", f"name_{index}", "v" * 200, minute=index) for index in range(10)]

    assembled = assemble_case_context(facts, _policy(token_budget=200))

    assert assembled.omitted_fact_ids
    kept = {entry.fact_id for entry in assembled.entries}
    assert kept.isdisjoint(assembled.omitted_fact_ids)
    assert kept | set(assembled.omitted_fact_ids) == {f"f{index}" for index in range(10)}


def test_the_budget_keeps_the_newest_when_it_has_to_choose() -> None:
    """The oldest unpinned fact is the one whose absence costs least, and the
    summary is what stands in for it."""
    facts = [_fact(f"f{index}", f"name_{index}", "v" * 200, minute=index) for index in range(10)]

    assembled = assemble_case_context(facts, _policy(token_budget=300))

    assert "f9" in {entry.fact_id for entry in assembled.entries}
    assert "f0" in assembled.omitted_fact_ids


def test_crossing_the_trigger_fraction_recommends_compaction() -> None:
    """The recommendation is all this function does -- the summary itself is a
    separate write-once step."""
    facts = [_fact(f"f{index}", f"name_{index}", "v" * 40, minute=index) for index in range(10)]

    roomy = assemble_case_context(facts, _policy(token_budget=100_000))
    tight = assemble_case_context(
        facts,
        _policy(
            token_budget=200,
            compaction=ContextCompactionConfiguration(trigger_fraction_millionths=500_000),
        ),
    )

    assert roomy.compaction_recommended is False
    assert tight.compaction_recommended is True


def test_the_estimate_is_reported_with_the_version_that_produced_it() -> None:
    assembled = assemble_case_context([_fact("f1", "a", "b")], _policy())
    assert assembled.tokenizer_version == "wordpiece-approx.v1"
    assert assembled.token_budget == 8_000
    assert assembled.estimated_tokens > 0


def test_an_unpinnable_tokenizer_version_is_refused_not_approximated() -> None:
    """A pin whose unknown values fall back silently is a default with extra
    steps, and the budget it produces is measured in units nobody declared."""
    with pytest.raises(UnknownTokenizerError):
        assemble_case_context([_fact("f1", "a", "b")], _policy(tokenizer_version="tiktoken.v9"))


# --------------------------------------------------------------------------- #
# The persisted summary
# --------------------------------------------------------------------------- #


def test_a_persisted_summary_is_consumed_not_regenerated() -> None:
    """Definition of done. The summary is read, included, and its fact id
    recorded; nothing here writes one."""
    facts = [
        _fact("f1", "return_reason", "damaged", minute=1),
        _fact("sum-1", CONTEXT_SUMMARY, {"text": "Earlier: the RMA was issued."}, minute=2),
        _fact("f2", "carrier", "FEDEX", minute=3),
    ]

    assembled = assemble_case_context(facts, _policy())

    assert assembled.summary == {"text": "Earlier: the RMA was issued."}
    assert assembled.summary_fact_id == "sum-1"
    assert "sum-1" in assembled.consumed_fact_ids
    # And it is not also an ordinary entry -- it would be counted twice.
    assert CONTEXT_SUMMARY not in {entry.fact_name for entry in assembled.entries}
    assert assembled.payload()["summary"] == {"text": "Earlier: the RMA was issued."}


def test_the_newest_summary_wins() -> None:
    facts = [
        _fact("sum-1", CONTEXT_SUMMARY, {"text": "older"}, minute=1),
        _fact("sum-2", CONTEXT_SUMMARY, {"text": "newer"}, minute=6),
    ]

    assembled = assemble_case_context(facts, _policy())
    assert assembled.summary == {"text": "newer"}
    assert assembled.summary_fact_id == "sum-2"


def test_a_case_with_no_summary_assembles_cleanly() -> None:
    assembled = assemble_case_context([_fact("f1", "a", "b")], _policy())
    assert assembled.summary is None
    assert assembled.summary_fact_id is None


def test_the_summary_is_counted_against_the_budget() -> None:
    """It is in the payload the model receives, so it is in the measurement."""
    facts = [_fact("f1", "a", "b", minute=1)]
    without = assemble_case_context(facts, _policy())
    with_summary = assemble_case_context(
        [*facts, _fact("sum-1", CONTEXT_SUMMARY, {"text": "x" * 400}, minute=2)],
        _policy(),
    )

    assert with_summary.estimated_tokens > without.estimated_tokens


# --------------------------------------------------------------------------- #
# consumed_fact_ids
# --------------------------------------------------------------------------- #


def test_consumed_fact_ids_names_exactly_what_the_model_received() -> None:
    """Recorded per invocation (contracts.md sect. 10)."""
    facts = [
        _fact("f1", "return_reason", "v" * 200, minute=1),
        _fact("f2", "carrier", "v" * 200, minute=2),
        _fact("sum-1", CONTEXT_SUMMARY, {"text": "s"}, minute=3),
    ]

    assembled = assemble_case_context(facts, _policy(token_budget=100))

    rendered = {entry.fact_id for entry in assembled.entries}
    assert set(assembled.consumed_fact_ids) == rendered | {"sum-1"}
    assert set(assembled.consumed_fact_ids).isdisjoint(assembled.omitted_fact_ids)


def test_an_empty_case_assembles_to_an_empty_context() -> None:
    assembled = assemble_case_context([], _policy())
    assert assembled.entries == ()
    assert assembled.consumed_fact_ids == ()
    assert assembled.content_hash


# --------------------------------------------------------------------------- #
# The released block
# --------------------------------------------------------------------------- #


def test_the_shipped_configuration_carries_the_block() -> None:
    configuration: ReturnPlatformConfiguration = load_return_configuration(
        DEFAULT_RETURN_CONFIGURATION_PATH
    ).configuration
    block = configuration.context_assembly

    assert block.tokenizer_version == "wordpiece-approx.v1"
    assert block.token_budget == 8_000
    assert block.compaction.trigger_fraction_millionths == 800_000
    assert block.compaction.summary_task_id == "support.context.summarize.v1"

    # And it is usable *as* a policy. `assemble_case_context` types its policy
    # against a structural protocol rather than this class -- `platform/*` names
    # no type a domain module owns -- so the thing worth asserting is that the
    # released block still satisfies the shape.
    assembled = assemble_case_context([_fact("f1", "return_reason", "damaged")], block)
    assert assembled.tokenizer_version == block.tokenizer_version
    assert assembled.consumed_fact_ids == ("f1",)


def test_a_release_without_the_block_still_loads() -> None:
    """Defaulted, like every other block added after the first release."""
    assert ContextAssemblyConfiguration().token_budget == 8_000
    assert ContextAssemblyConfiguration().pinned_fact_names == ()
