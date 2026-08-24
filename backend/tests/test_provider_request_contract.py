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
from pydantic import SecretStr, ValidationError

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


# --------------------------------------------------------------------------
# The thinking budget, which is a third output-contract field on Gemini alone
# --------------------------------------------------------------------------
#
# Gemini 2.5 and later reason before replying and draw that reasoning from the
# same allowance as the answer. Unbounded it expands to fill whatever room it is
# given -- 7,853 thinking tokens with no ceiling declared, 20,548 once a 65,536
# ceiling was -- so on a demanding prompt it consumes the allowance and the reply
# is cut mid-string. Every ORDER_AGENT_REASONING failure in this deployment has
# been that: `finishReason: MAX_TOKENS`, surfaced as CONTEXT_LIMIT_EXCEEDED.
#
# Lowering `maxOutputTokens` is the wrong lever and makes it worse, because the
# one budget is shared and the answer is starved first. These pin the separate
# control instead.


def _gemini(**overrides: Any) -> GeminiProvider:
    return GeminiProvider(
        _settings(google_api_key=SecretStr("test-key"), google_model="gemini-test", **overrides)
    )


def _gemini_post() -> _CapturedPost:
    return _CapturedPost(
        {
            "candidates": [{"content": {"parts": [{"text": '{"action_type":"RESPOND"}'}]}}],
            "usageMetadata": {},
        }
    )


@pytest.mark.asyncio
async def test_gemini_bounds_thinking_so_the_answer_keeps_its_room() -> None:
    provider = _gemini()
    post = _gemini_post()
    provider._post = post  # type: ignore[method-assign]

    await provider.generate(REQUEST)

    thinking = post.payload["generationConfig"]["thinkingConfig"]
    assert thinking["thinkingBudget"] == 2048, (
        "an unbounded thinking budget is what truncates the order agent's JSON; "
        "the default must reach the wire"
    )


@pytest.mark.asyncio
async def test_gemini_thinking_budget_is_configurable() -> None:
    provider = _gemini(google_thinking_budget=512)
    post = _gemini_post()
    provider._post = post  # type: ignore[method-assign]

    await provider.generate(REQUEST)

    assert post.payload["generationConfig"]["thinkingConfig"]["thinkingBudget"] == 512


@pytest.mark.asyncio
async def test_gemini_omits_the_field_entirely_when_unset() -> None:
    # `None` is the escape hatch back to the old behaviour. It has to send *no*
    # key rather than a null, because Gemini rejects a null `thinkingConfig` and
    # the failure would look like a bad request rather than a setting.
    provider = _gemini(google_thinking_budget=None)
    post = _gemini_post()
    provider._post = post  # type: ignore[method-assign]

    await provider.generate(REQUEST)

    assert "thinkingConfig" not in post.payload["generationConfig"]


def test_an_empty_thinking_budget_does_not_kill_every_process() -> None:
    """`PLATFORM_GOOGLE_THINKING_BUDGET=` with nothing after it must not raise.

    An empty variable is how an operator turns a setting off in a `.env`, and it
    is what the comment beside the field promises restores the unbounded
    behaviour. Reaching an `int | None` field as `""` made pydantic refuse, and
    `Settings()` is constructed at *import*: the backend never binds its port and
    every worker dies before logging why. A shipped default is no protection --
    the failure needs the variable to be present and blank, which is exactly what
    following the documentation produced.
    """
    assert Settings(environment="test", google_thinking_budget="").google_thinking_budget is None
    assert Settings(environment="test", google_thinking_budget="   ").google_thinking_budget is None
    assert Settings(environment="test").google_thinking_budget == 2048
    assert Settings(environment="test", google_thinking_budget=512).google_thinking_budget == 512

    with pytest.raises(ValidationError):
        Settings(environment="test", google_thinking_budget=99_999)


@pytest.mark.asyncio
async def test_gemini_does_not_lower_the_output_budget_to_bound_thinking() -> None:
    # The regression this guards is the tempting wrong fix: capping
    # `maxOutputTokens` to stop the overrun. Thinking and answer share that
    # budget, so it starves the answer first -- measured, the same prompt kept
    # `finishReason: STOP` uncapped and hit MAX_TOKENS at 8192.
    provider = _gemini()
    post = _gemini_post()
    provider._post = post  # type: ignore[method-assign]

    await provider.generate(REQUEST)

    assert post.payload["generationConfig"]["maxOutputTokens"] == 4096, (
        "the task's declared output budget must pass through untouched; "
        "bounding thinking is a separate field"
    )


# --------------------------------------------------------------------------
# PERF-03: the cache accounting the benefit is measured by
# --------------------------------------------------------------------------
#
# The canonical STANDARD route reaches Gemini Flash first and NVIDIA Nemotron on
# failover, and neither adapter sends a caching directive: what these providers
# offer is *automatic* prefix caching, which is earned by putting stable material
# first (see `test_the_cacheable_prefix_is_byte_identical_across_turns`) rather
# than requested. The only thing an adapter has to get right is reading back what
# was served -- and that is where the money is, because `AIPricingEntry` bills
# `cachedInputPerMillionTokensMicros` separately from `inputPerMillionTokensMicros`
# and `AICostEstimate` adds the two lines.
#
# The two vendor conventions are not the same and the difference is a doubled
# bill, not a rounding error:
#
#   subset  Gemini's `cachedContentTokenCount` and OpenAI's `cached_tokens` are
#           *part of* the prompt count, so the uncached prompt is the remainder.
#           Passing the prompt count through unmodified charges the cached half
#           at the full rate as well as at the cached rate.
#   sibling Anthropic's `cache_read_input_tokens` sits *beside* `input_tokens`,
#           which is already the platform's split, so subtracting there would
#           undercount the uncached prompt instead.
#
# Nothing else in the platform can catch this: both shapes parse, both produce a
# plausible number, and the cost report is the only place the error surfaces.


@pytest.mark.asyncio
async def test_gemini_reports_the_cached_prompt_as_a_subset_of_the_prompt_count() -> None:
    provider = GeminiProvider(
        _settings(google_api_key=SecretStr("test-key"), google_model="gemini-test")
    )
    provider._post = _CapturedPost(  # type: ignore[method-assign]
        {
            "candidates": [{"content": {"parts": [{"text": '{"action_type":"RESPOND"}'}]}}],
            "usageMetadata": {
                "promptTokenCount": 16_000,
                "cachedContentTokenCount": 5_200,
                "candidatesTokenCount": 300,
                "totalTokenCount": 16_300,
            },
        }
    )

    response = await provider.generate(REQUEST)

    assert response.cached_input_tokens == 5_200
    assert response.input_tokens == 10_800, (
        "Gemini counts cached content inside promptTokenCount; passing it through "
        "whole bills the cached prefix at the full rate as well"
    )
    assert response.input_tokens + response.cached_input_tokens == 16_000


@pytest.mark.asyncio
async def test_openai_compatible_reports_the_cached_prompt_as_a_subset_too() -> None:
    provider = OpenAICompatibleProvider(
        name="NVIDIA",
        api_key="k",
        base_url="https://example.invalid/v1",
        model="m",
        timeout_seconds=5,
    )
    provider._post = _CapturedPost(  # type: ignore[method-assign]
        {
            "choices": [{"message": {"content": '{"action_type":"RESPOND"}'}}],
            "usage": {
                "prompt_tokens": 16_000,
                "prompt_tokens_details": {"cached_tokens": 5_200},
                "completion_tokens": 300,
                "total_tokens": 16_300,
            },
        }
    )

    response = await provider.generate(REQUEST)

    assert response.cached_input_tokens == 5_200
    assert response.input_tokens == 10_800
    assert response.input_tokens + response.cached_input_tokens == 16_000


@pytest.mark.asyncio
async def test_anthropic_reports_the_cached_prompt_beside_the_prompt_count() -> None:
    """The opposite convention, and the reason this cannot be one shared helper."""
    provider = _anthropic()
    provider._post = _CapturedPost(  # type: ignore[method-assign]
        {
            "content": [{"type": "tool_use", "input": {"action_type": "RESPOND"}}],
            "usage": {
                "input_tokens": 10_800,
                "cache_read_input_tokens": 5_200,
                "output_tokens": 300,
            },
        }
    )

    response = await provider.generate(REQUEST)

    assert response.cached_input_tokens == 5_200
    assert response.input_tokens == 10_800, (
        "Anthropic already reports the uncached prompt; subtracting here would "
        "undercount it by the size of the cache hit"
    )


@pytest.mark.asyncio
async def test_a_provider_silent_about_caching_reports_none_rather_than_zero() -> None:
    """`None` is "the provider said nothing", `0` is "nothing was cached".

    Collapsing them would make a cache-hit-rate report read as a solid 0% for
    every provider that simply does not publish the field -- which is how a
    caching regression on the one provider that *does* publish it stays invisible
    inside an average.
    """
    provider = OpenAICompatibleProvider(
        name="OLLAMA",
        api_key=None,
        base_url="https://example.invalid/v1",
        model="m",
        timeout_seconds=5,
    )
    provider._post = _CapturedPost(  # type: ignore[method-assign]
        {
            "choices": [{"message": {"content": '{"action_type":"RESPOND"}'}}],
            "usage": {"prompt_tokens": 16_000, "completion_tokens": 300},
        }
    )

    response = await provider.generate(REQUEST)

    assert response.cached_input_tokens is None
    assert response.input_tokens == 16_000
