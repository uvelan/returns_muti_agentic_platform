"""OpenAI-compatible chat-completions adapter used by NVIDIA and Ollama."""

from __future__ import annotations

import json
import logging
from typing import Any

from return_platform.ai.providers.contracts import (
    ProviderError,
    ProviderRequest,
    ProviderResponse,
)
from return_platform.ai.providers.http import HTTPProvider

logger = logging.getLogger("return_platform.ai.providers.openai_compatible")


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
            # Every model gets the strict schema, nemotron included.
            #
            # Nemotron used to be excepted here on the grounds that it supports
            # `json_object` but not strict JSON-schema constraints. Measured
            # against nemotron-3-super-120b on the real narrowing prompt, that
            # is no longer true and the exception was costing more than it
            # saved: the NVIDIA integrate API answers `json_schema` with 200,
            # returns the correct flat AgentAction envelope, and does it in
            # 31.7s against 71.3s for the same call under `json_object`.
            #
            # Unconstrained, the model invents its own envelope -- action_type
            # beside a `payload` wrapper that AgentAction does not have, with
            # the required `business_capability` missing. That is not a near
            # miss the correction pass can repair, it is a different document,
            # and it is what made both NVIDIA STANDARD routes structurally
            # unable to serve a schema-bound task. Constrained, what is left is
            # an omitted conditional block -- the payload a given action_type
            # requires, which no JSON Schema can express -- and that is exactly
            # what `validationError` and `invalidActionJson` feed back for.
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "agent_action",
                    "schema": request.response_schema,
                    "strict": True,
                },
            }

        data = await self._post(
            f"{self._base_url}/chat/completions",
            headers=headers,
            payload=payload,
        )
        logger.debug(
            "openai_compatible_response",
            extra={"provider": self.name, "model": self.model},
        )
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise ProviderError("RESPONSE_INVALID") from error
        if not isinstance(text, str) or not text.strip():
            raise ProviderError("RESPONSE_INVALID")
        usage = data.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens") if isinstance(usage, dict) else None
        # Chat-completions convention: `cached_tokens` is a subset of
        # `prompt_tokens`, so the uncached prompt is the remainder. Absent on
        # every provider that does not cache, which stays `None` rather than 0.
        details = usage.get("prompt_tokens_details") if isinstance(usage, dict) else None
        cached_input_tokens = details.get("cached_tokens") if isinstance(details, dict) else None
        uncached_input_tokens = prompt_tokens
        if isinstance(prompt_tokens, int) and isinstance(cached_input_tokens, int):
            uncached_input_tokens = max(0, prompt_tokens - cached_input_tokens)
        return ProviderResponse(
            self.name,
            self.model,
            text,
            input_tokens=uncached_input_tokens,
            cached_input_tokens=cached_input_tokens,
            output_tokens=usage.get("completion_tokens") if isinstance(usage, dict) else None,
            total_tokens=usage.get("total_tokens") if isinstance(usage, dict) else None,
        )
