from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from return_platform.configuration.return_configuration import load_return_configuration
from return_platform.operations.associate_flow import (
    AnchorType,
    AssociateConversationService,
    OrderCandidate,
    OrderLineCandidate,
    _is_expired,
    _normalize_utc_datetime,
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


def test_chat_extracts_and_normalizes_lowercase_order_number() -> None:
    extracted = service()._extract_anchor("can you check w000000001 please")
    assert extracted.anchorType is AnchorType.ORDER_NUMBER
    assert extracted.anchorValue == "W000000001"


def test_fuzzy_query_removes_conversation_noise_and_keeps_typo_tolerance() -> None:
    query = service()._fuzzy_query(
        "Find the customer named Jhn Smtih who bought a cordles dril last week.",
        require_all=False,
    )

    assert query == "Jhn OR Smtih~1 OR cordles~1 OR dril~1"
    assert "customer" not in query.lower()
    assert "named" not in query.lower()


def test_fuzzy_query_for_customer_name_requires_all_name_tokens() -> None:
    assert service()._fuzzy_query("name Demo Customer") == "Demo~1"


def test_clarification_prompt_exposes_only_the_selected_field_values() -> None:
    candidates = [
        OrderCandidate(
            customerReference=f"CUST-{index}",
            customerName=name,
            orderReference=f"ORD-{index}",
            orderStatus="DELIVERED",
            confidenceMillionths=500_000,
            evidenceSource="TEST",
            lines=[
                OrderLineCandidate(
                    orderLineId=f"LINE-{index}",
                    productId=f"PRODUCT-{index}",
                    productDescription=product,
                )
            ],
        )
        for index, (name, product) in enumerate(
            (
                ("Maya Foster", "Safety Sensor"),
                ("Maya Foster", "Pump Controller"),
                ("Nadia Diaz", "Safety Sensor"),
            ),
            start=1,
        )
    ]

    prompt = service()._clarification_prompt(
        candidates,
        ["customer_name"],
        "Which customer do you mean?",
    )

    assert prompt is not None
    assert prompt.slot == "customer_name"
    assert [(option.value, option.candidateCount) for option in prompt.options] == [
        ("Maya Foster", 2),
        ("Nadia Diaz", 1),
    ]


def test_candidate_expiry_accepts_naive_mongodb_datetimes() -> None:
    assert _is_expired(datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1))


def test_mongodb_datetimes_are_serialized_as_utc() -> None:
    normalized = _normalize_utc_datetime(datetime(2026, 7, 28, 10, 53, 16))

    assert normalized is not None
    assert normalized.tzinfo is UTC
    assert normalized.isoformat() == "2026-07-28T10:53:16+00:00"
