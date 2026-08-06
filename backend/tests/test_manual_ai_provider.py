from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from return_platform.ai_gateway.providers.contracts import ProviderError, ProviderRequest
from return_platform.ai_gateway.providers.manual import ManualFileProvider
from return_platform.configuration.settings import Settings


def test_configured_only_in_development_or_test(test_settings: Settings) -> None:
    assert ManualFileProvider(test_settings.model_copy(update={"environment": "test"})).configured
    assert ManualFileProvider(
        test_settings.model_copy(update={"environment": "development"})
    ).configured
    assert not ManualFileProvider(
        test_settings.model_copy(update={"environment": "production"})
    ).configured


@pytest.mark.asyncio
async def test_generate_blocked_outside_development_or_test(
    test_settings: Settings, tmp_path: Path
) -> None:
    base_dir = tmp_path / "manual_llm"
    provider = ManualFileProvider(
        test_settings.model_copy(update={"environment": "production"}),
        base_dir=base_dir,
    )
    with pytest.raises(ProviderError) as error:
        await provider.generate(ProviderRequest(system_prompt="s", user_payload={}))
    assert error.value.code == "POLICY_BLOCKED"
    assert not base_dir.exists()


@pytest.mark.asyncio
async def test_generate_writes_request_and_returns_human_response(
    test_settings: Settings, tmp_path: Path
) -> None:
    provider = ManualFileProvider(
        test_settings.model_copy(update={"environment": "test"}),
        base_dir=tmp_path,
        poll_seconds=0.01,
        timeout_seconds=5,
    )

    async def respond_once_request_appears() -> None:
        requests_dir = tmp_path / "requests"
        responses_dir = tmp_path / "responses"
        for _ in range(500):
            files = list(requests_dir.glob("*.json")) if requests_dir.is_dir() else []
            if files:
                request_id = files[0].stem
                responses_dir.mkdir(parents=True, exist_ok=True)
                (responses_dir / f"{request_id}.json").write_text(
                    '{"decision":"human-authored"}', encoding="utf-8"
                )
                return
            await asyncio.sleep(0.01)
        raise AssertionError("request file never appeared")

    response, _ = await asyncio.gather(
        provider.generate(
            ProviderRequest(system_prompt="Return JSON.", user_payload={"message": "hi"})
        ),
        respond_once_request_appears(),
    )

    assert response.text == '{"decision":"human-authored"}'
    assert response.provider == "MANUAL"
    # The request file is cleaned up once answered; the response file is
    # consumed on read so no artifacts linger for the next turn.
    assert not any((tmp_path / "requests").glob("*.json"))
    assert not any((tmp_path / "responses").glob("*.json"))


@pytest.mark.asyncio
async def test_generate_times_out_when_no_response_is_supplied(
    test_settings: Settings, tmp_path: Path
) -> None:
    provider = ManualFileProvider(
        test_settings.model_copy(update={"environment": "test"}),
        base_dir=tmp_path,
        poll_seconds=0.01,
        timeout_seconds=0.05,
    )
    with pytest.raises(ProviderError) as error:
        await provider.generate(ProviderRequest(system_prompt="s", user_payload={}))
    assert error.value.code == "TIMEOUT"
    assert not any((tmp_path / "requests").glob("*.json"))
