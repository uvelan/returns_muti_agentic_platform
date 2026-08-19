"""How a provider key may be configured, now that Vault is optional.

This file used to assert the opposite rule: outside development and test, a key
*value* was refused and only a `vault://` reference was accepted. That rule was
coherent only while Vault was mandatory. It is not -- the platform reads its
credentials from the process environment unless `PLATFORM_VAULT_ENABLED` is
set -- and an inline key is now the ordinary way to configure a provider.

What survives is the part that was never about Vault: a provider must not be
configured by value *and* by reference at once. With a resolver running, the
reference overwrites the value, so supplying both means one of the two is dead
configuration and nobody can tell which from reading it. That is refused at
construction, because a deployment that starts and then serves traffic on the
key nobody intended has already used it.

The diagnostic still names settings and never echoes a value.
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from return_platform.configuration.settings import Settings

PROVIDERS = ("google", "nvidia", "openai", "anthropic")


# `Settings()` reads the repository `.env`, which is a *development* profile:
# its provider order includes MANUAL and its dependency modes are SIMULATED,
# both of which production validators reject before these rules run. Overridden
# here so each test fails for the reason it names.
#
# The inline key arrays are cleared for the same reason: that `.env` may carry
# real provider key values, so without this a production case would fail on
# those rather than on what it sets.
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
    # Production refuses to start on a development key. Stated rather than
    # inherited from a developer's `.env`, so this file asserts the same
    # behaviour on any machine.
    "validation_fingerprint_key": SecretStr("production-validation-fingerprint-key"),
    "contact_lookup_hmac_key": SecretStr("production-contact-lookup-hmac-key"),
    "reasoning_encryption_key": SecretStr("cHJvZHVjdGlvbi1yZWFzb25pbmcta2V5LTMyYnl0ZXM="),
}


def _settings(environment: str, **overrides: object) -> Settings:
    baseline = _PRODUCTION_BASELINE if environment == "production" else {}
    return Settings(environment=environment, **{**baseline, **overrides})  # type: ignore[arg-type]


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("environment", ["development", "test", "staging", "production"])
def test_an_inline_key_is_accepted_in_every_environment(provider: str, environment: str) -> None:
    """The rule this file used to enforce, inverted deliberately.

    With no Vault in the platform, refusing a key value outside development
    would leave production with no way to configure a provider at all.
    """
    settings = _settings(environment, **{f"{provider}_api_keys": (SecretStr("a-key-value"),)})

    assert len(getattr(settings, f"{provider}_api_keys")) == 1


@pytest.mark.parametrize("provider", PROVIDERS)
def test_a_reference_and_a_value_together_are_refused_when_vault_is_enabled(
    provider: str,
) -> None:
    with pytest.raises(ValidationError) as raised:
        _settings(
            "production",
            vault_enabled=True,
            **{
                f"{provider}_api_keys": (SecretStr("inline-key-value"),),
                f"{provider}_api_key_references": (
                    f"vault://secret/production/ai/{provider}#api_key",
                ),
            },
        )

    message = str(raised.value)
    assert provider.upper() in message
    # The diagnostic must name the setting and never echo the value.
    assert "inline-key-value" not in message


@pytest.mark.parametrize("provider", PROVIDERS)
def test_a_reference_alone_is_accepted_when_vault_is_enabled(provider: str) -> None:
    settings = _settings(
        "production",
        vault_enabled=True,
        **{f"{provider}_api_key_references": (f"vault://secret/production/ai/{provider}#api_key",)},
    )

    assert getattr(settings, f"{provider}_api_key_references")
    assert not getattr(settings, f"{provider}_api_keys")


@pytest.mark.parametrize("provider", PROVIDERS)
def test_both_together_are_ignored_while_vault_is_off(provider: str) -> None:
    """Nothing dereferences the reference, so there is no conflict to refuse.

    A reference left behind in a `.env` after turning Vault off is inert. It
    would be worse to refuse startup over a string nothing reads.
    """
    settings = _settings(
        "production",
        **{
            f"{provider}_api_keys": (SecretStr("a-key-value"),),
            f"{provider}_api_key_references": (f"vault://secret/production/ai/{provider}#api_key",),
        },
    )

    assert len(getattr(settings, f"{provider}_api_keys")) == 1


def test_every_conflicting_provider_is_named_at_once() -> None:
    """One restart per provider would be a miserable way to find this out."""
    with pytest.raises(ValidationError) as raised:
        _settings(
            "production",
            vault_enabled=True,
            google_api_keys=(SecretStr("a"),),
            google_api_key_references=("vault://secret/production/ai/google#api_key",),
            nvidia_api_keys=(SecretStr("b"),),
            nvidia_api_key_references=("vault://secret/production/ai/nvidia#api_key",),
        )

    message = str(raised.value)
    assert "GOOGLE" in message
    assert "NVIDIA" in message


def test_production_with_neither_keys_nor_references_still_constructs() -> None:
    """Absence is not misconfiguration.

    A deployment may legitimately run with no AI provider at all -- the route
    pool is then empty and callers degrade to their deterministic fallback.
    Refusing here would conflate "no AI" with "unsafe AI".
    """
    settings = _settings("production")

    assert not settings.google_api_keys
    assert not settings.google_api_key_references


@pytest.mark.parametrize(
    "field",
    [
        "validation_fingerprint_key",
        "contact_lookup_hmac_key",
    ],
)
def test_production_refuses_a_development_key(field: str) -> None:
    """The one secret rule production still enforces.

    It used to hide behind `vault_secrets_resolved`, which made it a property of
    where the key came from rather than of the key. Nothing gates it now.
    """
    with pytest.raises(ValidationError) as raised:
        _settings("production", **{field: SecretStr("development-something-change-me")})

    assert "must be replaced" in str(raised.value)


def test_production_refuses_the_development_reasoning_key() -> None:
    from return_platform.configuration.settings import DEV_DEFAULT_REASONING_ENCRYPTION_KEY_B64

    with pytest.raises(ValidationError) as raised:
        _settings(
            "production",
            reasoning_encryption_key=SecretStr(DEV_DEFAULT_REASONING_ENCRYPTION_KEY_B64),
        )

    assert "must be replaced" in str(raised.value)
