"""Unit tests for strict AI payload and response policy boundaries."""

import pytest

from return_platform.ai.gateway.service import AIGatewayService, _PayloadPolicyError
from return_platform.ai.providers import ProviderError
from return_platform.operations.models import AIDecision, AIRequestStatus


def test_ai_payload_rejects_sensitive_nested_key() -> None:
    with pytest.raises(_PayloadPolicyError) as captured:
        AIGatewayService._validate_scalar({"customerEmail": "blocked@example.test"})

    assert captured.value.code is AIRequestStatus.REDACTION_FAILED


def test_ai_response_requires_exact_schema() -> None:
    with pytest.raises(ProviderError) as captured:
        AIGatewayService._parse_response(
            '{"decision":"APPROVE","explanation":"ok","confidenceMillionths":900000,"extra":true}'
        )
    assert captured.value.code == "RESPONSE_INVALID"


def test_ai_response_accepts_bounded_exact_json() -> None:
    decision, explanation, confidence = AIGatewayService._parse_response(
        '{"decision":"REVIEW_REQUIRED","explanation":"Evidence conflicts.",'
        '"confidenceMillionths":500000}'
    )

    assert decision is AIDecision.REVIEW_REQUIRED
    assert explanation == "Evidence conflicts."
    assert confidence == 500_000
