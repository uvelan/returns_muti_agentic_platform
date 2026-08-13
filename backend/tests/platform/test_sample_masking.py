"""W4.6's mask: the value is gone and the structure it carried is not.

These are the properties the clause names -- field names, types, shape,
cardinality, distribution metadata -- tested one at a time, because a mask that
satisfies four of the five is not a partial success. Losing cardinality in
particular is silent: the analyzer still produces a schema, and the schema is
wrong in a way that only shows up as a graph whose keys do not join.
"""

from __future__ import annotations

from return_platform.ai.gateway.redaction import redact_payload
from return_platform.platform.redaction.sample_masking import MASK_PREFIX, SampleMasker
from return_platform.platform.redaction.sensitive_keys import is_sensitive_key

SALT = b"deterministic-for-tests"


def _masker() -> SampleMasker:
    """A fixed salt, so a failure reports the same token twice rather than a
    fresh random one each run."""
    return SampleMasker(salt=SALT)


def test_the_sensitive_value_does_not_survive_in_any_form() -> None:
    """The point of the exercise. Everything below is about what is *kept*, and
    none of it matters if the value itself leaks through."""
    masked = _masker().mask_row({"customer_name": "Jane Doe", "email": "jane@example.com"})
    rendered = repr(masked)
    assert "Jane Doe" not in rendered
    assert "jane@example.com" not in rendered
    assert "example.com" not in rendered


def test_field_names_and_shape_are_untouched() -> None:
    """Names and nesting are what the model reasons over first, and they are
    already visible through `describe_object` -- masking them would remove
    signal without removing disclosure."""
    row = {
        "order_id": "SO-1",
        "customer_name": "Jane Doe",
        "shipping_address": {"line1": "1 High St", "postcode": "AB1 2CD"},
        "tags": ["a", "b", "c"],
    }
    masked = _masker().mask_row(row)
    assert list(masked) == list(row)
    assert isinstance(masked["shipping_address"], dict)
    assert list(masked["shipping_address"]) == ["line1", "postcode"]
    assert len(masked["tags"]) == 3


def test_equal_values_mask_to_equal_tokens_so_cardinality_survives() -> None:
    """The property the constant `[REDACTED]` destroys, and the reason this
    module exists rather than reusing the provider-boundary redactor.

    Three rows holding two distinct names must still look like two distinct
    names, or the analyzer concludes the column is a constant.
    """
    masker = _masker()
    rows = masker.mask_rows(
        [
            {"customer_name": "Jane Doe"},
            {"customer_name": "John Roe"},
            {"customer_name": "Jane Doe"},
        ]
    )
    values = [row["customer_name"] for row in rows]
    assert values[0] == values[2]
    assert values[0] != values[1]
    assert len(set(values)) == 2


def test_the_same_value_in_two_objects_masks_alike_so_a_join_stays_inferable() -> None:
    """Why the masker is per analysis rather than per call: a foreign key is
    only visible as the same value appearing on both sides of it."""
    masker = _masker()
    order = masker.mask_row({"account_name": "ACME LTD"})
    customer = masker.mask_row({"account_name": "ACME LTD"})
    assert order["account_name"] == customer["account_name"]


def test_two_analyses_produce_unrelated_tokens() -> None:
    """The other half of the salt's job. Tokens line up for as long as one
    analysis needs them to and mean nothing outside it, so a token that escapes
    into a log cannot be matched against a token from anywhere else -- or
    against a hash of a guessed name.
    """
    first = SampleMasker().mask_row({"customer_name": "Jane Doe"})
    second = SampleMasker().mask_row({"customer_name": "Jane Doe"})
    assert first["customer_name"] != second["customer_name"]


def test_the_surrogate_states_the_type_and_size_it_replaced() -> None:
    """Distribution metadata as a stated fact rather than as content: a
    five-character code column and a forty-character free-text one are different
    fields, and the model can still tell which is which."""
    masked = _masker().mask_row({"customer_name": "Jane Doe", "card_number": 4111111111111111})
    assert masked["customer_name"].startswith(f"{MASK_PREFIX}str:8:")
    assert masked["card_number"].startswith(f"{MASK_PREFIX}int:16:")


def test_a_masked_number_is_not_a_number_that_looks_real() -> None:
    """Preserving the JSON type here would mean emitting a different sixteen
    digit integer, which nothing downstream could distinguish from a card
    number. The type is reported instead of imitated."""
    masked = _masker().mask_row({"card_number": 4111111111111111})
    assert isinstance(masked["card_number"], str)
    assert "4111" not in masked["card_number"]


def test_values_that_are_not_sensitive_are_left_exactly_as_they_were() -> None:
    """The analyzer infers structure from these. A mask that touched them would
    be removing the signal it exists to protect."""
    row = {"order_id": "SO-1", "quantity": 3, "unit_price": 19.99, "is_returnable": True}
    assert _masker().mask_row(row) == row


def test_absent_stays_absent_and_a_flag_stays_a_flag() -> None:
    """`None` masked would tell the model a value exists where none does, which
    is a false statement about nullability -- the same rule the provider
    boundary already applies. A boolean holds one bit and identifies nobody."""
    masked = _masker().mask_row({"email": None, "name_verified": False})
    assert masked["email"] is None
    assert masked["name_verified"] is False


def test_a_container_under_a_sensitive_key_is_masked_all_the_way_down() -> None:
    """A rule that only inspected the key it found would mask `address` and let
    `address.line1` through one level below it."""
    masked = _masker().mask_row(
        {"shipping_address": {"line1": "1 High St", "nested": {"postcode": "AB1 2CD"}}}
    )
    assert masked["shipping_address"]["line1"].startswith(MASK_PREFIX)
    assert masked["shipping_address"]["nested"]["postcode"].startswith(MASK_PREFIX)


def test_a_list_of_sensitive_values_keeps_its_length_and_its_distinctness() -> None:
    """Cardinality inside a container is cardinality too: a repeated element and
    a distinct one must not come out the same."""
    masked = _masker().mask_row({"emails": ["a@x.com", "b@x.com", "a@x.com"]})
    values = masked["emails"]
    assert len(values) == 3
    assert values[0] == values[2] != values[1]


def test_masking_twice_changes_nothing() -> None:
    """Load-bearing rather than tidy: a sample is masked at the inspection port
    and again where a prompt block is assembled. Without idempotence the second
    pass would report every field as a string of the surrogate's length, so the
    type and size the surrogate carries would describe the surrogate."""
    masker = _masker()
    once = masker.mask_row({"customer_name": "Jane Doe", "shipping_address": {"line1": "1 High"}})
    twice = masker.mask_row(once)
    assert twice == once


def test_a_surrogate_from_a_different_masker_is_still_recognised() -> None:
    """Idempotence has to hold by shape, not by memory -- the row may have been
    masked in another process, or by the analysis's masker and re-checked by the
    prompt builder's."""
    first = SampleMasker().mask_row({"customer_name": "Jane Doe"})
    second = SampleMasker().mask_row(first)
    assert second == first


def test_both_boundaries_ask_the_same_question_about_a_key() -> None:
    """One policy, two masking strategies. The strategies differ on purpose; the
    list of names they apply to must not, because the entry point with the
    shorter list is the one that stops recognising a field as sensitive.
    """
    for key in ("customer_name", "customerEmail", "SHIP-TO-ADDRESS", "cvv", "api_token"):
        assert is_sensitive_key(key)
        assert redact_payload({key: "value"})[key] == "[REDACTED]"
        assert _masker().mask_row({key: "value"})[key].startswith(MASK_PREFIX)


def test_the_two_strategies_disagree_only_where_they_are_meant_to() -> None:
    """A regression net for the temptation to "simplify" by pointing the
    analyzer at `redact_payload`. If this ever passes, cardinality is gone."""
    rows = [{"customer_name": "Jane Doe"}, {"customer_name": "John Roe"}]
    redacted = [redact_payload(dict(row))["customer_name"] for row in rows]
    masked = [row["customer_name"] for row in _masker().mask_rows(rows)]
    assert redacted[0] == redacted[1], "the provider boundary is expected to collapse values"
    assert masked[0] != masked[1], "the analyzer boundary must not"
