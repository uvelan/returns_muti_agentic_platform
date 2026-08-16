"""The reason and condition catalogues, and why they are where they are.

Plan sect. 12.4 puts both vocabularies in the return configuration. Three things
about that decision are easy to undo later and are pinned here:

* the block is **top-level**, because `bootstrap_graph_configuration` merges the
  packaged file underneath an active release one top-level key at a time -- a
  key added inside `return_policy` would be dropped by the release's own
  `return_policy` and could never reach a deployment (defect D11);
* an **empty** catalogue publishes nothing and therefore refuses nothing, which
  is what lets a release cut before the block keep working;
* `reasons` is checked against `policy.vocabulary.ReturnReason`, the closed
  vocabulary `case_policy_facts` maps a stored `return_reason` onto. A release
  free to publish a reason outside it would look correct, load, and send every
  return using it to review with nothing saying why.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from return_platform.configuration.return_configuration import (
    ReturnPlatformConfiguration,
    SelectionVocabularyConfiguration,
    load_return_configuration,
)
from return_platform.configuration.settings import DEFAULT_RETURN_CONFIGURATION_PATH
from return_platform.policy.vocabulary import ReturnReason

PACKAGED = load_return_configuration(DEFAULT_RETURN_CONFIGURATION_PATH).configuration


def test_the_packaged_release_publishes_both_catalogues() -> None:
    """A file that declared neither would leave the check inert."""
    vocabulary = PACKAGED.selection_vocabulary

    assert vocabulary.reasons, "the packaged release publishes no reasons"
    assert vocabulary.conditions, "the packaged release publishes no conditions"


def test_every_published_reason_is_one_the_evaluator_can_read() -> None:
    """The property the load-time check exists to guarantee, asserted on the
    file that actually ships."""
    known = {member.value for member in ReturnReason}
    assert set(PACKAGED.selection_vocabulary.reasons) <= known


def test_unknown_is_not_offered_to_an_associate() -> None:
    """`UNKNOWN` is the absence of an answer, not something anyone chooses."""
    assert ReturnReason.UNKNOWN.value not in PACKAGED.selection_vocabulary.reasons


def test_the_block_is_top_level_and_not_nested_in_the_return_policy() -> None:
    """The D11 shape. Nested, this key could never reach a live deployment.

    Asserted on the model rather than on the YAML text so a later move of the
    field is caught wherever the file happens to be formatted.
    """
    assert "selection_vocabulary" in ReturnPlatformConfiguration.model_fields
    assert "selection_vocabulary" not in type(PACKAGED.return_policy).model_fields


def test_a_release_predating_the_block_still_loads_and_publishes_nothing() -> None:
    """The default is empty, and empty is "unpublished", not "reject everything"."""
    vocabulary = SelectionVocabularyConfiguration()

    assert vocabulary.reasons == ()
    assert vocabulary.conditions == ()
    assert vocabulary.unknown_reasons(["ANYTHING_AT_ALL"]) == ()
    assert vocabulary.unknown_conditions(["ANYTHING_AT_ALL"]) == ()


def test_a_reason_the_evaluator_cannot_read_is_refused_at_load() -> None:
    """`DAMAGED_IN_TRANSIT` is a plausible-looking reason and is not a
    `ReturnReason`, so every return using it would silently resolve to
    `UNKNOWN` and route to a human."""
    with pytest.raises(ValidationError) as invalid:
        SelectionVocabularyConfiguration(reasons=("SHIPPING_DAMAGE", "DAMAGED_IN_TRANSIT"))

    assert "DAMAGED_IN_TRANSIT" in str(invalid.value)


def test_a_repeated_entry_is_refused() -> None:
    with pytest.raises(ValidationError) as invalid:
        SelectionVocabularyConfiguration(conditions=("USED", "used"))

    assert "twice" in str(invalid.value)


def test_conditions_are_not_constrained_against_a_code_vocabulary() -> None:
    """Deliberate. There is no item-condition enum, and inventing one to check
    against would be the hardcoded catalogue this block removes."""
    vocabulary = SelectionVocabularyConfiguration(conditions=("SOMETHING_THE_OPERATOR_CHOSE",))

    assert vocabulary.unknown_conditions(["SOMETHING_THE_OPERATOR_CHOSE"]) == ()


def test_admission_is_case_and_whitespace_insensitive() -> None:
    vocabulary = SelectionVocabularyConfiguration(
        reasons=("SHIPPING_DAMAGE",), conditions=("USED",)
    )

    assert vocabulary.unknown_reasons([" shipping_damage "]) == ()
    assert vocabulary.unknown_conditions(["Used"]) == ()
    assert vocabulary.unknown_reasons(["SHORTAGE"]) == ("SHORTAGE",)


def test_every_unpublished_term_is_named_once_and_in_order() -> None:
    """The refusal is a list a client can render, not the first offender."""
    vocabulary = SelectionVocabularyConfiguration(reasons=("SHIPPING_DAMAGE",))

    assert vocabulary.unknown_reasons(["ZED", "SHIPPING_DAMAGE", "ALPHA", "ZED"]) == (
        "ZED",
        "ALPHA",
    )
