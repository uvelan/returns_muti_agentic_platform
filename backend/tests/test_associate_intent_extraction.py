"""Deterministic extraction safeguards around AI discovery intent."""

from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

from return_platform.configuration.return_configuration import load_return_configuration
from return_platform.operations.associate_flow import (
    AnchorType,
    AssociateConversationService,
    StartAssociateConversationRequest,
)

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def service() -> Any:
    instance = cast(Any, object.__new__(AssociateConversationService))
    instance._return_configuration = load_return_configuration(
        BACKEND_ROOT / "config" / "returns" / "production.yaml"
    ).configuration
    return instance


def test_structured_anchor_normalization_preserves_partial_identifier() -> None:
    extracted = service()._deterministic_intent_fallback("please get orders for cust id cust-10")

    assert extracted.anchorType is AnchorType.CUSTOMER_ID
    assert extracted.anchorValue == "CUST-10"


def test_one_character_anchor_is_rejected() -> None:
    with pytest.raises(ValidationError):
        StartAssociateConversationRequest(
            anchorType=AnchorType.CUSTOMER_NAME,
            anchorValue="A",
        )


def test_unrelated_request_does_not_create_a_strong_identifier() -> None:
    extracted = service()._deterministic_intent_fallback("what is the weather today?")

    assert extracted.anchorType is AnchorType.PRODUCT_DESCRIPTION
