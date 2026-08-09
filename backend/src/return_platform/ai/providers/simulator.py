"""Deterministic non-production AI provider for tests and demonstrations."""

from __future__ import annotations

import json

from return_platform.ai.providers.contracts import (
    ProviderError,
    ProviderRequest,
    ProviderResponse,
)
from return_platform.configuration.settings import Settings


class SimulatorProvider:
    name = "SIMULATOR"
    model = "deterministic-eligibility-simulator-v1"

    def __init__(self, settings: Settings) -> None:
        self._enabled = settings.environment in {"development", "test"}

    @property
    def configured(self) -> bool:
        return self._enabled

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        if not self._enabled:
            raise ProviderError("POLICY_BLOCKED")
        requested = request.user_payload.get("requestedDecision")
        if requested in {"APPROVE", "REJECT", "REVIEW_REQUIRED"}:
            decision = requested
        else:
            reason = str(request.user_payload.get("reasonCode", "")).upper()
            order_status = str(request.user_payload.get("orderStatus", "")).upper()
            days = request.user_payload.get("daysSinceDelivery")
            if order_status != "DELIVERED":
                decision = "REJECT"
            elif isinstance(days, int) and days > 45:
                decision = "REJECT"
            elif reason in {"HAZARDOUS", "FRAUD_SUSPECTED", "SERIAL_MISMATCH"}:
                decision = "REVIEW_REQUIRED"
            else:
                decision = "APPROVE"
        text = json.dumps(
            {
                "decision": decision,
                "explanation": "Deterministic simulator policy evaluation.",
                "confidenceMillionths": 900_000 if decision != "REVIEW_REQUIRED" else 500_000,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        return ProviderResponse(self.name, self.model, text)
