"""Anthropic Messages API adapter."""

from __future__ import annotations

import json

from return_platform.ai.providers.contracts import (
    ProviderError,
    ProviderRequest,
    ProviderResponse,
)
from return_platform.ai.providers.http import HTTPProvider, secret_value
from return_platform.configuration.settings import Settings


class AnthropicProvider(HTTPProvider):
    name = "ANTHROPIC"

    def __init__(self, settings: Settings) -> None:
        super().__init__(timeout_seconds=settings.ai_timeout_seconds)
        self._api_key = secret_value(settings.anthropic_api_key)
        self._base_url = settings.anthropic_base_url
        self._version = settings.anthropic_version
        self.model = settings.anthropic_model or ""

    @property
    def configured(self) -> bool:
        return self._api_key is not None and bool(self.model)

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        if not self.configured or self._api_key is None:
            raise ProviderError("AUTH_FAILED")
        data = await self._post(
            f"{self._base_url}/messages",
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": self._version,
                "Content-Type": "application/json",
            },
            payload={
                "model": self.model,
                "max_tokens": 512,
                "temperature": 0,
                "system": request.system_prompt,
                "messages": [{"role": "user", "content": json.dumps(request.user_payload)}],
            },
        )
        try:
            text = data["content"][0]["text"]
        except (KeyError, IndexError, TypeError) as error:
            raise ProviderError("RESPONSE_INVALID") from error
        if not isinstance(text, str) or not text.strip():
            raise ProviderError("RESPONSE_INVALID")
        usage = data.get("usage", {})
        input_tokens = usage.get("input_tokens") if isinstance(usage, dict) else None
        output_tokens = usage.get("output_tokens") if isinstance(usage, dict) else None
        return ProviderResponse(
            self.name,
            self.model,
            text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=(
                int(input_tokens or 0) + int(output_tokens or 0)
                if input_tokens is not None or output_tokens is not None
                else None
            ),
        )
