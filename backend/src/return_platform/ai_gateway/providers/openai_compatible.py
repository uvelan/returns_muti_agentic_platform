"""OpenAI-compatible chat-completions adapter used by NVIDIA and Ollama."""

from __future__ import annotations

import json

from return_platform.ai_gateway.providers.contracts import (
    ProviderError,
    ProviderRequest,
    ProviderResponse,
)
from return_platform.ai_gateway.providers.http import HTTPProvider


class OpenAICompatibleProvider(HTTPProvider):
    def __init__(
        self,
        *,
        name: str,
        api_key: str | None,
        base_url: str,
        model: str | None,
        timeout_seconds: float,
    ) -> None:
        super().__init__(timeout_seconds=timeout_seconds)
        self.name = name
        self._api_key = api_key
        self._base_url = base_url
        self.model = model or ""

    @property
    def configured(self) -> bool:
        return bool(self.model) and (self.name == "OLLAMA" or self._api_key is not None)

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        if not self.configured:
            raise ProviderError("AUTH_FAILED")
        headers = {"Content-Type": "application/json"}
        if self._api_key is not None:
            headers["Authorization"] = f"Bearer {self._api_key}"

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": json.dumps(request.user_payload)},
            ],
            "temperature": request.temperature,
            "stream": False,
            **(
                {"max_tokens": request.max_output_tokens}
                if request.max_output_tokens is not None
                else {}
            ),
        }
        if request.response_schema is not None:
            if "nemotron" in self.model.lower():
                # Nemotron models via NVIDIA integrate API support json_object mode, but maybe not structured JSON schema for all.
                # Just requesting JSON format helps.
                payload["response_format"] = {"type": "json_object"}
            else:
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "agent_action",
                        "schema": request.response_schema,
                        "strict": True
                    }
                }

        data = await self._post(
            f"{self._base_url}/chat/completions",
            headers=headers,
            payload=payload,
        )
        print(f"DEBUG OPENAI_COMPATIBLE RESPONSE [{self.name} {self.model}]:", data)
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise ProviderError("RESPONSE_INVALID") from error
        if not isinstance(text, str) or not text.strip():
            raise ProviderError("RESPONSE_INVALID")
        usage = data.get("usage", {})
        return ProviderResponse(
            self.name,
            self.model,
            text,
            input_tokens=usage.get("prompt_tokens") if isinstance(usage, dict) else None,
            output_tokens=usage.get("completion_tokens") if isinstance(usage, dict) else None,
            total_tokens=usage.get("total_tokens") if isinstance(usage, dict) else None,
        )
