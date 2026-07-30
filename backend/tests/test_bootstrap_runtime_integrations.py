from types import SimpleNamespace

import pytest

from return_platform.configuration.bootstrap_runtime_integrations import (
    _resolve_provider_credentials,
)
from return_platform.secrets.vault import LocalProxySecretResolver, parse_secret_reference


@pytest.mark.asyncio
async def test_discovers_bootstrap_managed_vault_credentials_without_raw_keys() -> None:
    resolver = LocalProxySecretResolver()
    for index in (1, 2):
        await resolver.put_secret(
            parse_secret_reference(
                "vault://secret/production/"
                f"ai/google/credentials/key-{index}#api_key"
            ),
            f"test-key-{index}",
        )

    settings = SimpleNamespace(google_api_key_references=())
    resolved = await _resolve_provider_credentials(
        settings=settings,
        resolver=resolver,
        provider="GOOGLE",
    )

    assert [index for index, _secret in resolved] == [1, 2]
    assert [secret.secret_version for _index, secret in resolved] == [1, 1]


@pytest.mark.asyncio
async def test_explicit_vault_references_remain_supported() -> None:
    resolver = LocalProxySecretResolver()
    reference = parse_secret_reference(
        "vault://secret/production/ai/google/custom/key#api_key"
    )
    await resolver.put_secret(reference, "test-key")
    settings = SimpleNamespace(
        google_api_key_references=(reference.to_uri(),),
    )

    resolved = await _resolve_provider_credentials(
        settings=settings,
        resolver=resolver,
        provider="GOOGLE",
    )

    assert len(resolved) == 1
    assert resolved[0][0] == 1
    assert resolved[0][1].reference.vault_path.endswith("/custom/key")
