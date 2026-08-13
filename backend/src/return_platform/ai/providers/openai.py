"""OpenAI Responses API adapter."""

from __future__ import annotations

import json
from typing import Any

from return_platform.ai.providers.contracts import (
    ProviderError,
    ProviderRequest,
    ProviderResponse,
)
from return_platform.ai.providers.http import HTTPProvider, secret_value
from return_platform.configuration.settings import Settings


class OpenAIResponsesProvider(HTTPProvider):
    name = "OPENAI"

    def __init__(self, settings: Settings) -> None:
        super().__init__(timeout_seconds=settings.ai_timeout_seconds)
        self._api_key = secret_value(settings.openai_api_key)
        self._base_url = settings.openai_base_url
        self.model = settings.openai_model or ""

    @property
    def configured(self) -> bool:
        return self._api_key is not None and bool(self.model)

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        if not self.configured or self._api_key is None:
            raise ProviderError("AUTH_FAILED")
        payload: dict[str, Any] = {
            "model": self.model,
            "instructions": request.system_prompt,
            "input": json.dumps(request.user_payload),
            "store": False,
            "temperature": request.temperature,
        }
        # Both fields used to be dropped on the floor here, so a task declaring a
        # 4096-token budget and a strict AgentAction schema got neither -- the
        # response came back unconstrained and, past the model's own default cap,
        # truncated. `max_output_tokens` and `text.format` are the Responses API's
        # names for what `max_tokens` and `response_format` are on chat/completions.
        if request.max_output_tokens is not None:
            payload["max_output_tokens"] = request.max_output_tokens
        if request.response_schema is not None:
            payload["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": "agent_action",
                    "schema": request.response_schema,
                    "strict": True,
                }
            }

        data = await self._post(
            f"{self._base_url}/responses",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            payload=payload,
        )
        text = data.get("output_text")
        if not isinstance(text, str) or not text.strip():
            try:
                text = data["output"][0]["content"][0]["text"]
            except (KeyError, IndexError, TypeError) as error:
                raise ProviderError("RESPONSE_INVALID") from error
        if not isinstance(text, str) or not text.strip():
            raise ProviderError("RESPONSE_INVALID")
        usage = data.get("usage", {})
        input_tokens = usage.get("input_tokens") if isinstance(usage, dict) else None
        output_tokens = usage.get("output_tokens") if isinstance(usage, dict) else None
        # OpenAI reports `cached_tokens` as a *subset* of `input_tokens`, so the
        # uncached remainder is the subtraction. Passing the raw pair through
        # would bill the cached prompt twice, once at each rate.
        details = usage.get("input_tokens_details") if isinstance(usage, dict) else None
        cached_input_tokens = details.get("cached_tokens") if isinstance(details, dict) else None
        uncached_input_tokens = input_tokens
        if isinstance(input_tokens, int) and isinstance(cached_input_tokens, int):
            uncached_input_tokens = max(0, input_tokens - cached_input_tokens)
        return ProviderResponse(
            self.name,
            self.model,
            text,
            input_tokens=uncached_input_tokens,
            cached_input_tokens=cached_input_tokens,
            output_tokens=output_tokens,
            # The vendor's own total, which still counts the whole prompt once.
            total_tokens=(
                int(input_tokens or 0) + int(output_tokens or 0)
                if input_tokens is not None or output_tokens is not None
                else None
            ),
        )
