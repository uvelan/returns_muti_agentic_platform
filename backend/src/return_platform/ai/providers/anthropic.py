"""Anthropic Messages API adapter."""

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

# Only reached when a caller declares no budget of its own. Every configured task
# in ai_gateway.yaml declares one, so this is a floor for ad-hoc callers, not a cap.
_DEFAULT_MAX_OUTPUT_TOKENS = 4096

_STRUCTURED_TOOL_NAME = "structured_response"


def _response_text(data: dict[str, Any]) -> str:
    """The response body as JSON text, from either content-block shape.

    A forced tool call returns `{"type": "tool_use", "input": {...}}` -- already
    parsed -- while an unconstrained call returns `{"type": "text", "text": "..."}`.
    Both are normalized to text here because `ProviderResponse.text` is what the
    gateway's shared parser and the response digest are built on; re-serializing
    the tool input is cheaper than giving one provider its own result contract.
    """

    blocks = data.get("content")
    if not isinstance(blocks, list) or not blocks:
        raise ProviderError("RESPONSE_INVALID")
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "tool_use":
            tool_input = block.get("input")
            if isinstance(tool_input, dict):
                return json.dumps(tool_input, separators=(",", ":"), sort_keys=True)
        if block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                return text
    raise ProviderError("RESPONSE_INVALID")


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
        payload: dict[str, Any] = {
            "model": self.model,
            # Honour the task's declared budget. This used to be the literal 512,
            # which silently truncated every ORDER_AGENT_REASONING_V1 response
            # (declared budget 4096) mid-JSON -- the parser then failed and the
            # route pool failed over, so an Anthropic route could never serve the
            # order agent. _DEFAULT_MAX_OUTPUT_TOKENS applies only when a caller
            # declares no budget at all; the Messages API requires the field.
            "max_tokens": request.max_output_tokens or _DEFAULT_MAX_OUTPUT_TOKENS,
            "temperature": request.temperature,
            "system": request.system_prompt,
            "messages": [{"role": "user", "content": json.dumps(request.user_payload)}],
        }
        if request.response_schema is not None:
            # The Messages API has no `response_format`, so the schema is carried
            # as a single forced tool whose input_schema *is* the response schema.
            # That is Anthropic's native structured-output mechanism; the result
            # arrives as a `tool_use` block with a parsed `input` object rather
            # than as text. Dropping the schema here instead -- which is what this
            # adapter did -- means the strict contract every other provider
            # enforces is unenforced on exactly one provider, which is worse than
            # not offering the provider at all.
            payload["tools"] = [
                {
                    "name": _STRUCTURED_TOOL_NAME,
                    "description": "Return the response as this exact structure.",
                    "input_schema": request.response_schema,
                }
            ]
            payload["tool_choice"] = {"type": "tool", "name": _STRUCTURED_TOOL_NAME}

        data = await self._post(
            f"{self._base_url}/messages",
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": self._version,
                "Content-Type": "application/json",
            },
            payload=payload,
        )
        text = _response_text(data)
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
