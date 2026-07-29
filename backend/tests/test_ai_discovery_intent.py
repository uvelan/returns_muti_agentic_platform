from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

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


class _FakeAI:
    def __init__(
        self,
        *,
        anchor_type: AnchorType | None = None,
        anchor_value: str | None = None,
        confidence: int = 950_000,
        fail: bool = False,
    ) -> None:
        self.anchor_type = anchor_type
        self.anchor_value = anchor_value
        self.confidence = confidence
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    async def evaluate(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("simulated provider outage")
        assert self.anchor_type is not None
        assert self.anchor_value is not None
        return SimpleNamespace(
            pending_interception=False,
            trace=SimpleNamespace(
                fallbackUsed=False,
                confidenceMillionths=self.confidence,
                explanation=json.dumps(
                    {
                        "anchorType": self.anchor_type.value,
                        "anchorValue": self.anchor_value,
                    },
                    separators=(",", ":"),
                ),
            ),
        )


def _service(fake_ai: _FakeAI) -> AssociateConversationService:
    service = object.__new__(AssociateConversationService)
    service._return_configuration = load_return_configuration(
        DEFAULT_RETURN_CONFIGURATION_PATH
    ).configuration
    service._ai = fake_ai
    return service


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "anchor_type", "anchor_value"),
    (
        (
            "i want the oders list from Ama",
            AnchorType.CUSTOMER_NAME,
            "Ama",
        ),
        (
            "show odr SO-2026-001",
            AnchorType.ORDER_NUMBER,
            "SO-2026-001",
        ),
        (
            "get orders for cust id CUST-10",
            AnchorType.CUSTOMER_ID,
            "CUST-10",
        ),
        (
            "find shipment track 1Z99",
            AnchorType.TRACKING_NUMBER,
            "1Z99",
        ),
        (
            "show purchases for sku VAL-10",
            AnchorType.SKU,
            "VAL-10",
        ),
        (
            "orders containing pressur valv",
            AnchorType.PRODUCT_DESCRIPTION,
            "pressur valv",
        ),
    ),
)
async def test_ai_resolves_all_discovery_anchor_types(
    message: str,
    anchor_type: AnchorType,
    anchor_value: str,
) -> None:
    fake_ai = _FakeAI(
        anchor_type=anchor_type,
        anchor_value=anchor_value,
    )
    service = _service(fake_ai)

    resolved = await service._extract_anchor_with_ai(
        message,
        session_id="test-session",
    )

    assert resolved.anchorType is anchor_type
    assert resolved.anchorValue == anchor_value
    assert fake_ai.calls[0]["task_id"] == "RETURN_DISCOVERY_INTENT_V1"
    assert fake_ai.calls[0]["redacted_input"]["utterance"] == message


@pytest.mark.asyncio
async def test_exact_strong_identifier_overrides_conflicting_ai() -> None:
    fake_ai = _FakeAI(
        anchor_type=AnchorType.PRODUCT_DESCRIPTION,
        anchor_value="wrong answer",
    )
    service = _service(fake_ai)

    resolved = await service._extract_anchor_with_ai(
        "find SO-2026-000001",
        session_id="test-session",
    )

    assert resolved.anchorType is AnchorType.ORDER_NUMBER
    assert resolved.anchorValue == "SO-2026-000001"
    assert len(fake_ai.calls) == 1


@pytest.mark.asyncio
async def test_ai_failure_uses_existing_safe_fallback() -> None:
    fake_ai = _FakeAI(fail=True)
    service = _service(fake_ai)

    resolved = await service._extract_anchor_with_ai(
        "pressure valve",
        session_id="test-session",
    )

    assert resolved.anchorType is AnchorType.PRODUCT_DESCRIPTION
    assert resolved.anchorValue == "pressure valve"


def test_identifier_source_matcher_supports_prefix() -> None:
    service = object.__new__(AssociateConversationService)

    prefix = service._case_insensitive_query("CUST-10", prefix=True)
    exact = service._case_insensitive_query("CUST-10")

    assert prefix == {
        "$regex": "^CUST\\-10",
        "$options": "i",
    }
    assert exact == {
        "$regex": "^CUST\\-10$",
        "$options": "i",
    }
