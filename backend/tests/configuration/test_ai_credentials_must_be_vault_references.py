"""Outside development and test, a provider key value cannot be configured.

Live Google and NVIDIA keys sat in a plaintext `.env`, duplicated into
`backend/.env`, mounted read-only into the test-runner container. Keys of the
same providers were committed to a test fixture and removed again in `fbfcf05`,
which puts them in git history permanently.

The settings object accepted both a key *value* (`PLATFORM_*_API_KEYS`) and a
Vault *reference* (`PLATFORM_*_API_KEY_REFERENCES`), so nothing ever forced the
safe one. The six infrastructure secrets have used references exclusively all
along; this makes AI credentials match.

Failing at construction matters: a deployment that starts and then serves
traffic on an inline key has already leaked it into process memory, logs and
crash dumps. Refusing to start is the only outcome that prevents that.
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from return_platform.configuration.settings import Settings

PROVIDERS = ("google", "nvidia", "openai", "anthropic")


# `Settings()` reads the repository `.env`, which is a *development* profile:
# its provider order includes MANUAL and its dependency modes are SIMULATED,
# both of which existing production validators reject before this one runs.
# Overridden here so each test fails for the reason it names.
#
# The inline key arrays are cleared for the same reason: that `.env` carries
# real Google and NVIDIA key values, so without this every production case here
# would fail on *those* rather than on what it sets. That it does is itself the
# guard working against the file this rule exists for.
_PRODUCTION_BASELINE: dict[str, object] = {
    "ai_provider_order": "GOOGLE",
    "omc_dependency_mode": "REAL",
    "parcel_dependency_mode": "REAL",
    "freight_dependency_mode": "REAL",
    "lsi_dependency_mode": "REAL",
    "google_api_keys": (),
    "nvidia_api_keys": (),
    "openai_api_keys": (),
    "anthropic_api_keys": (),
    # Independently required in production by an existing validator.
    "reasoning_encryption_key_secret_reference": (
        "vault://secret/production/platform/reasoning#encryption_key"
    ),
}


def _settings(environment: str, **overrides: object) -> Settings:
    baseline = _PRODUCTION_BASELINE if environment == "production" else {}
    return Settings(environment=environment, **{**baseline, **overrides})  # type: ignore[arg-type]


@pytest.mark.parametrize("provider", PROVIDERS)
def test_an_inline_key_is_refused_in_production(provider: str) -> None:
    with pytest.raises(ValidationError) as raised:
        _settings("production", **{f"{provider}_api_keys": (SecretStr("inline-key-value"),)})

    message = str(raised.value)
    assert f"PLATFORM_{provider.upper()}_API_KEYS" in message
    assert "Vault references" in message
    # The diagnostic must name the setting and never echo the value.
    assert "inline-key-value" not in message


@pytest.mark.parametrize("provider", PROVIDERS)
def test_a_vault_reference_is_accepted_in_production(provider: str) -> None:
    settings = _settings(
        "production",
        **{f"{provider}_api_key_references": (f"vault://secret/production/ai/{provider}#api_key",)},
    )

    assert getattr(settings, f"{provider}_api_key_references")
    assert not getattr(settings, f"{provider}_api_keys")


def test_every_inline_provider_is_named_at_once() -> None:
    """One restart per provider would be a miserable way to find this out."""
    with pytest.raises(ValidationError) as raised:
        _settings(
            "production",
            google_api_keys=(SecretStr("a"),),
            nvidia_api_keys=(SecretStr("b"),),
        )

    message = str(raised.value)
    assert "PLATFORM_GOOGLE_API_KEYS" in message
    assert "PLATFORM_NVIDIA_API_KEYS" in message


@pytest.mark.parametrize("environment", ["development", "test"])
def test_inline_keys_remain_usable_in_development_and_test(environment: str) -> None:
    """A contributor with no Vault must still be able to run the stack.

    This exemption is also what stops the rule being routed around by setting
    `PLATFORM_ENVIRONMENT=production` on a laptop to make something else work.
    """
    settings = _settings(environment, google_api_keys=(SecretStr("local-dev-key"),))

    assert len(settings.google_api_keys) == 1


def test_production_with_neither_keys_nor_references_still_constructs() -> None:
    """Absence is not misconfiguration.

    A deployment may legitimately run with no AI provider at all -- the route
    pool is then empty and callers degrade to their deterministic fallback.
    Refusing here would conflate "no AI" with "unsafe AI".
    """
    settings = _settings("production")

    assert not settings.google_api_keys
    assert not settings.google_api_key_references
