from __future__ import annotations

import pytest

from return_platform.configuration.return_configuration import (
    load_return_configuration,
)
from return_platform.configuration.settings import (
    DEFAULT_RETURN_CONFIGURATION_PATH,
)
from return_platform.operations.associate_flow import (
    AnchorType,
    AssociateConversationService,
)


def _service() -> AssociateConversationService:
    service = object.__new__(AssociateConversationService)
    service._return_configuration = load_return_configuration(
        DEFAULT_RETURN_CONFIGURATION_PATH
    ).configuration
    return service


@pytest.mark.parametrize(
    ("message", "expected_name"),
    (
        ("i want to get the oders list from Amara", "Amara"),
        ("show me all orders from Amara please", "Amara"),
        ("find orders for customer amara", "amara"),
        ("display orders under client Amara", "Amara"),
        ("Amara's orders", "Amara"),
        ("show orders for Acme Supply", "Acme Supply"),
    ),
)
def test_extract_anchor_recognizes_customer_order_intent(
    message: str,
    expected_name: str,
) -> None:
    lookup = _service()._extract_anchor(message)

    assert lookup.anchorType is AnchorType.CUSTOMER_NAME
    assert lookup.anchorValue == expected_name


def test_strong_order_number_still_has_priority() -> None:
    lookup = _service()._extract_anchor("show orders from Amara for SO-2026-000001")

    assert lookup.anchorType is AnchorType.ORDER_NUMBER
    assert lookup.anchorValue == "SO-2026-000001"


def test_lowercase_multiword_product_query_keeps_product_fallback() -> None:
    message = "show orders for pressure valve"
    lookup = _service()._extract_anchor(message)

    assert lookup.anchorType is AnchorType.PRODUCT_DESCRIPTION
    assert lookup.anchorValue == message
