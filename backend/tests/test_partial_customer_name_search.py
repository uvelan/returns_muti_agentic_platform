"""Bounded customer-name and identifier-prefix search tests."""

from pathlib import Path
from typing import Any, cast

import pytest

from return_platform.configuration.return_configuration import load_return_configuration
from return_platform.operations.associate_flow import AnchorType, AssociateConversationService

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def service() -> Any:
    instance = cast(Any, object.__new__(AssociateConversationService))
    instance._return_configuration = load_return_configuration(
        BACKEND_ROOT / "config" / "returns" / "production.yaml"
    ).configuration
    instance._source_config = instance._return_configuration.source_resolution
    return instance


@pytest.mark.parametrize(
    ("value", "expected"),
    (
        ("Am", "Am*"),
        ("Ama", "Ama*"),
        ("Amar", "(Amar* OR Amar~1)"),
        ("Amara Iy", "(Amara* OR Amara~1) AND Iy*"),
    ),
)
def test_customer_query_uses_bounded_prefix_and_configured_fuzzy_variants(
    value: str,
    expected: str,
) -> None:
    assert service()._customer_name_query(value) == expected


def test_identifier_prefix_regex_is_anchored_and_escaped() -> None:
    query = service()._case_insensitive_query("CUST-10.*", exact=False)

    assert query == {"$regex": r"^CUST\-10\.\*", "$options": "i"}
    assert not query["$regex"].endswith("$")


@pytest.mark.parametrize(
    "anchor_type",
    (
        AnchorType.ORDER_NUMBER,
        AnchorType.CUSTOMER_ID,
        AnchorType.SKU,
    ),
)
def test_direct_identifier_queries_keep_user_input_in_parameters(
    anchor_type: AnchorType,
) -> None:
    matcher = service()._case_insensitive_query("SO-2026-001", exact=False)
    query = service()._direct_source_query(anchor_type, matcher)

    assert "$or" in query
    regexes = [next(iter(clause.values()))["$regex"] for clause in query["$or"]]
    assert regexes
    assert set(regexes) == {r"^SO\-2026\-001"}
