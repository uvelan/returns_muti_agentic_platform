"""Replay answers from a recording, and calls the provider when there is none.

The behaviour that makes an evaluation harness possible: a suite that ran once
against live providers runs again for free and gives the same answers, so a
change in the result is a change in the code rather than in the weather.

The negative cases carry the weight. A replay layer that answered the wrong
recording would be worse than none at all -- the run would be cheap, fast,
reproducible, and about a different question than the one asked.
"""

from __future__ import annotations

from typing import Any

import pytest

from return_platform.ai.providers.contracts import (
    ProviderError,
    ProviderRequest,
    ProviderResponse,
)
from return_platform.ai.providers.replay import ReplayProvider, request_digest

pytestmark = pytest.mark.asyncio


class _Real:
    name = "GOOGLE"
    model = "gemini-x"

    def __init__(self) -> None:
        self.calls = 0

    @property
    def configured(self) -> bool:
        return True

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.calls += 1
        return ProviderResponse(
            provider="GOOGLE", model="gemini-x", text=f'{{"n":{self.calls}}}', total_tokens=42
        )


class _Store:
    def __init__(self) -> None:
        self.data: dict[str, dict[str, Any]] = {}

    async def read(self, digest: str) -> dict[str, Any] | None:
        return self.data.get(digest)

    async def write(self, digest: str, record: dict[str, Any]) -> None:
        self.data[digest] = record


class _BrokenStore:
    async def read(self, digest: str) -> dict[str, Any] | None:
        raise RuntimeError("mongo is unreachable")

    async def write(self, digest: str, record: dict[str, Any]) -> None:
        raise RuntimeError("mongo is unreachable")


def _request(prompt: str = "find the order", **payload: Any) -> ProviderRequest:
    return ProviderRequest(system_prompt=prompt, user_payload=payload or {"a": 1})


async def test_a_miss_calls_the_provider_and_records_the_answer() -> None:
    """The corpus builds itself from ordinary runs rather than a capture step."""
    real, store = _Real(), _Store()
    provider = ReplayProvider(real, store)

    first = await provider.generate(_request())

    assert real.calls == 1
    assert first.text == '{"n":1}'
    assert len(store.data) == 1


async def test_a_hit_answers_without_touching_the_provider() -> None:
    real, store = _Real(), _Store()
    provider = ReplayProvider(real, store)
    await provider.generate(_request())

    second = await provider.generate(_request())

    # Same answer, and the counter proves no second call was made -- a replay
    # that quietly re-called would be a cache that costs exactly as much.
    assert second.text == '{"n":1}'
    assert real.calls == 1


async def test_a_replayed_answer_keeps_the_provider_that_produced_it() -> None:
    """Otherwise recorded runs are incomparable with live ones.

    A trace attributed to "REPLAY" cannot be measured against a trace
    attributed to the model, which is the one thing replay exists to enable.
    """
    provider = ReplayProvider(_Real(), _Store())
    await provider.generate(_request())

    replayed = await provider.generate(_request())

    assert replayed.provider == "GOOGLE"
    assert replayed.model == "gemini-x"
    assert provider.name == "GOOGLE"


def test_payload_key_order_does_not_change_the_key() -> None:
    """Dictionary order belongs to whoever built the request.

    Letting it into the digest would produce a store that misses nearly
    everything while looking like it was working.
    """
    assert request_digest(_request(a=1, b=2)) == request_digest(_request(b=2, a=1))


def test_a_changed_prompt_is_a_different_question() -> None:
    """The failure worth preventing: a fast, cheap answer to the wrong thing."""
    assert request_digest(_request("find the order")) != request_digest(_request("find the order."))


def test_decoding_parameters_are_part_of_the_key() -> None:
    """The same prompt at a different temperature is not the same question."""
    base = ProviderRequest(system_prompt="p", user_payload={}, temperature=0.0)
    hotter = ProviderRequest(system_prompt="p", user_payload={}, temperature=0.7)

    assert request_digest(base) != request_digest(hotter)


async def test_strict_refuses_a_miss_rather_than_calling_out() -> None:
    """For proving a run reached no provider at all.

    Without it "token-free" means "mostly token-free", which is not a claim
    anyone can rely on.
    """
    real = _Real()
    provider = ReplayProvider(real, _Store(), strict=True)

    with pytest.raises(ProviderError) as caught:
        await provider.generate(_request())

    assert caught.value.code == "REPLAY_MISS"
    assert real.calls == 0


async def test_strict_reports_configured_without_credentials() -> None:
    """Replaying a suite on a machine with no keys is the point.

    A route declared unusable because a credential is missing would fail the
    run before the recording could answer it.
    """

    class _Unconfigured(_Real):
        @property
        def configured(self) -> bool:
            return False

    assert ReplayProvider(_Unconfigured(), _Store(), strict=True).configured is True


async def test_an_unreadable_store_degrades_to_a_live_call() -> None:
    """A cache problem must not become an outage.

    The caller gets a real answer and pays for it, which is the safe direction.
    """
    real = _Real()
    provider = ReplayProvider(real, _BrokenStore())

    response = await provider.generate(_request())

    assert response.text == '{"n":1}'
    assert real.calls == 1


async def test_an_unwritable_store_does_not_fail_a_good_answer() -> None:
    real = _Real()
    provider = ReplayProvider(real, _BrokenStore())

    assert (await provider.generate(_request())).text == '{"n":1}'
    assert real.calls == 1
