"""Every provider must honour `max_output_tokens` and `response_schema`.

These two fields are the whole of `ProviderRequest`'s output contract, and a
provider that accepts them and sends neither is worse than one that refuses: the
gateway records a `RESPONSE_INVALID` attempt and fails over, so the defect looks
like a flaky provider rather than a broken adapter. That is exactly what
`AnthropicProvider` (a hardcoded `max_tokens: 512`, no schema) and
`OpenAIResponsesProvider` (neither field) did.

Asserting on the *constructed payload* rather than on a live call is deliberate:
the contract is what we send, no credential is needed to check it, and a test
that needs a real key is a test that does not run in CI.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import SecretStr

from return_platform.ai.providers.anthropic import AnthropicProvider
from return_platform.ai.providers.contracts import ProviderError, ProviderRequest
from return_platform.ai.providers.google import GeminiProvider
from return_platform.ai.providers.openai import OpenAIResponsesProvider
from return_platform.ai.providers.openai_compatible import OpenAICompatibleProvider
from return_platform.configuration.settings import Settings

# A representative strict schema. Shape matters, contents do not.
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"action_type": {"type": "string"}},
    "required": ["action_type"],
    "additionalProperties": False,
}

REQUEST = ProviderRequest(
    system_prompt="system",
    user_payload={"mode": "DECIDE"},
    max_output_tokens=4096,
    temperature=0.0,
    response_schema=SCHEMA,
)


class _CapturedPost:
    """Stands in for `HTTPProvider._post`, recording the payload and replying."""

    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.url: str | None = None
        self.payload: dict[str, Any] = {}

    async def __call__(
        self, url: str, *, headers: dict[str, str], payload: dict[str, Any]
    ) -> dict[str, Any]:
        self.url = url
        self.payload = payload
        return self.response


def _settings(**overrides: Any) -> Settings:
    return Settings(environment="test", **overrides)


# --------------------------------------------------------------------------
# Anthropic
# --------------------------------------------------------------------------


def _anthropic() -> AnthropicProvider:
    return AnthropicProvider(
        _settings(
            anthropic_api_key=SecretStr("test-key"),
            anthropic_model="claude-test",
        )
    )


@pytest.mark.asyncio
async def test_anthropic_sends_the_declared_output_budget_not_a_hardcoded_512() -> None:
    provider = _anthropic()
    post = _CapturedPost(
        {
            "content": [{"type": "tool_use", "input": {"action_type": "RESPOND"}}],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
    )
    provider._post = post  # type: ignore[method-assign]

    await provider.generate(REQUEST)

    assert post.payload["max_tokens"] == 4096, (
        "the task's declared budget must reach the provider; a literal here "
        "truncates every order-agent response mid-JSON"
    )


@pytest.mark.asyncio
async def test_anthropic_carries_the_response_schema_as_a_forced_tool() -> None:
    provider = _anthropic()
    post = _CapturedPost(
        {"content": [{"type": "tool_use", "input": {"action_type": "RESPOND"}}], "usage": {}}
    )
    provider._post = post  # type: ignore[method-assign]

    await provider.generate(REQUEST)

    tools = post.payload["tools"]
    assert len(tools) == 1
    assert tools[0]["input_schema"] == SCHEMA
    # Forced, not merely offered: an optional tool lets the model answer in prose.
    assert post.payload["tool_choice"] == {"type": "tool", "name": tools[0]["name"]}


@pytest.mark.asyncio
async def test_anthropic_returns_tool_input_as_json_text() -> None:
    provider = _anthropic()
    provider._post = _CapturedPost(  # type: ignore[method-assign]
        {
            "content": [{"type": "tool_use", "input": {"action_type": "RESPOND"}}],
            "usage": {"input_tokens": 3, "output_tokens": 4},
        }
    )

    response = await provider.generate(REQUEST)

    # The gateway's shared parser and the response digest are both built on
    # `.text`, so a tool result must be normalized rather than given its own shape.
    assert response.text == '{"action_type":"RESPOND"}'
    assert response.total_tokens == 7


@pytest.mark.asyncio
async def test_anthropic_still_reads_a_plain_text_block_when_no_schema_is_requested() -> None:
    provider = _anthropic()
    post = _CapturedPost({"content": [{"type": "text", "text": "hello"}], "usage": {}})
    provider._post = post  # type: ignore[method-assign]

    response = await provider.generate(
        ProviderRequest(system_prompt="s", user_payload={}, max_output_tokens=64)
    )

    assert response.text == "hello"
    assert "tools" not in post.payload
    assert post.payload["max_tokens"] == 64


@pytest.mark.asyncio
async def test_anthropic_rejects_a_body_with_no_usable_content_block() -> None:
    provider = _anthropic()
    provider._post = _CapturedPost({"content": [], "usage": {}})  # type: ignore[method-assign]

    with pytest.raises(ProviderError):
        await provider.generate(REQUEST)


# --------------------------------------------------------------------------
# OpenAI Responses
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_openai_responses_sends_both_budget_and_schema() -> None:
    provider = OpenAIResponsesProvider(
        _settings(openai_api_key=SecretStr("test-key"), openai_model="gpt-test")
    )
    post = _CapturedPost({"output_text": '{"action_type":"RESPOND"}', "usage": {}})
    provider._post = post  # type: ignore[method-assign]

    await provider.generate(REQUEST)

    assert post.payload["max_output_tokens"] == 4096
    text_format = post.payload["text"]["format"]
    assert text_format["type"] == "json_schema"
    assert text_format["schema"] == SCHEMA
    assert text_format["strict"] is True


@pytest.mark.asyncio
async def test_openai_responses_omits_both_when_the_caller_declares_neither() -> None:
    provider = OpenAIResponsesProvider(
        _settings(openai_api_key=SecretStr("test-key"), openai_model="gpt-test")
    )
    post = _CapturedPost({"output_text": "plain", "usage": {}})
    provider._post = post  # type: ignore[method-assign]

    await provider.generate(ProviderRequest(system_prompt="s", user_payload={}))

    assert "max_output_tokens" not in post.payload
    assert "text" not in post.payload


# --------------------------------------------------------------------------
# The two adapters that were already correct, pinned so they stay that way
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_openai_compatible_sends_both() -> None:
    provider = OpenAICompatibleProvider(
        name="NVIDIA",
        api_key="k",
        base_url="https://example.invalid/v1",
        model="m",
        timeout_seconds=5,
    )
    post = _CapturedPost(
        {"choices": [{"message": {"content": '{"action_type":"RESPOND"}'}}], "usage": {}}
    )
    provider._post = post  # type: ignore[method-assign]

    await provider.generate(REQUEST)

    assert post.payload["max_tokens"] == 4096
    assert post.payload["response_format"]["json_schema"]["schema"] == SCHEMA


@pytest.mark.asyncio
async def test_gemini_sends_both() -> None:
    provider = GeminiProvider(
        _settings(google_api_key=SecretStr("test-key"), google_model="gemini-test")
    )
    post = _CapturedPost(
        {
            "candidates": [{"content": {"parts": [{"text": '{"action_type":"RESPOND"}'}]}}],
            "usageMetadata": {},
        }
    )
    provider._post = post  # type: ignore[method-assign]

    await provider.generate(REQUEST)

    generation_config = post.payload["generationConfig"]
    assert generation_config["maxOutputTokens"] == 4096
    assert "responseSchema" in generation_config
