from pathlib import Path
from typing import Any, cast

from return_platform.configuration.return_configuration import load_return_configuration
from return_platform.operations.associate_flow import (
    AnchorType,
    AssociateConversationService,
)

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def service() -> Any:
    instance = cast(Any, object.__new__(AssociateConversationService))
    instance._return_configuration = load_return_configuration(
        BACKEND_ROOT / "config" / "returns" / "production.yaml"
    ).configuration
    return instance


def test_chat_extracts_configured_strong_anchor_from_raw_sentence() -> None:
    extracted = service()._extract_anchor(
        "The customer says order SO-00010001 arrived damaged yesterday."
    )
    assert extracted.anchorType is AnchorType.ORDER_NUMBER
    assert extracted.anchorValue == "SO-00010001"


def test_chat_prefers_configured_strong_anchor_when_message_has_multiple_clues() -> None:
    extracted = service()._extract_anchor(
        "SKU-123456 is on order ORD-100001 and needs to be returned."
    )
    assert extracted.anchorType is AnchorType.ORDER_NUMBER
    assert extracted.anchorValue == "ORD-100001"


def test_chat_uses_product_description_when_no_structured_anchor_is_present() -> None:
    message = "The brass faucet from the showroom is leaking."
    extracted = service()._extract_anchor(message)
    assert extracted.anchorType is AnchorType.PRODUCT_DESCRIPTION
    assert extracted.anchorValue == message
