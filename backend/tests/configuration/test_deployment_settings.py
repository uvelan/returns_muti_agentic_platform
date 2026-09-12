"""Unit tests for CFG-6: the `deployment` section, its overlay, and its gates.

Covers the brief's "Tests to add" 1, 2, 4, 5, 6, 7 and 9. Test 3 (route table
rebuild on activation) is added to `test_worker_runtime_activation.py`
(the existing harness for `RuntimeConfigurationActivator.refresh`); test 8
(retired-key drop) and test 11 (rollback on any refusal) live in
`test_configuration_api.py`/`test_graph_configuration_bootstrap.py` beside the
carry-forward and publish-endpoint tests they extend; test 10 (outbox
dispatcher rebuild) lives beside `workers/integration_outbox.py`'s own tests.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from pydantic import SecretStr, ValidationError

from return_platform.configuration.application.packaged_adoption import (
    PACKAGED_KEY_DIGESTS,
    _drop_retired_keys,
    _key_digests,
    adopt_packaged_configuration,
)
from return_platform.configuration.deployment_settings import (
    DeploymentEnvironmentError,
    apply_deployment_configuration,
    deployment_payload_from_settings,
    merge_deployment_defaults,
    overlay_env_deployment_defaults,
    validate_deployment_for_environment,
)
from return_platform.configuration.graph_repository import InMemoryConfigurationGraphRepository
from return_platform.configuration.return_configuration import (
    AIModelBindingConfiguration,
    AIProviderRuntimeConfiguration,
    CredentialBindingConfiguration,
    DeploymentConfiguration,
    ReturnPlatformConfiguration,
    load_return_configuration,
)
from return_platform.configuration.runtime_integrations import (
    apply_graph_runtime_configuration,
)
from return_platform.configuration.settings import DEFAULT_RETURN_CONFIGURATION_PATH, Settings
from return_platform.configuration.snapshot import (
    RETURN_PLATFORM_DOMAIN_KEY,
    ConfigurationSnapshotBuilder,
)

_PACKAGED = load_return_configuration(DEFAULT_RETURN_CONFIGURATION_PATH).configuration


def _configuration(**deployment_updates: Any) -> ReturnPlatformConfiguration:
    deployment = DeploymentConfiguration.model_validate(deployment_updates)
    return _PACKAGED.model_copy(update={"deployment": deployment})


# --------------------------------------------------------------------------- #
# 1. The overlay itself
# --------------------------------------------------------------------------- #


def test_deployment_section_overlays_settings(test_settings: Settings) -> None:
    configuration = _configuration(
        ai={
            "provider_order": ["NVIDIA", "GOOGLE", "SIMULATOR"],
            "google": {"thinking_budget": 512, "response_schema": True},
        },
        dependencies={"omc": "REAL", "parcel": "MANUAL", "freight": "BLOCKED", "lsi": "REAL"},
        feedback_learning={"enabled": False},
        support_ticket={
            "mode": "INTERNAL_WITH_EXTERNAL_MIRROR",
            "base_url": "https://ticket.invalid",
        },
    )

    updated = apply_deployment_configuration(test_settings, configuration)

    assert updated.ai_provider_order == "NVIDIA,GOOGLE,SIMULATOR"
    assert updated.google_thinking_budget == 512
    assert updated.google_response_schema is True
    assert updated.omc_dependency_mode == "REAL"
    assert updated.parcel_dependency_mode == "MANUAL"
    assert updated.freight_dependency_mode == "BLOCKED"
    assert updated.lsi_dependency_mode == "REAL"
    assert updated.feedback_learning_enabled is False
    assert updated.support_ticket_mode == "INTERNAL_WITH_EXTERNAL_MIRROR"
    assert updated.support_ticket_base_url == "https://ticket.invalid"


def test_deployment_applies_even_when_runtime_integrations_makes_no_change(
    test_settings: Settings,
) -> None:
    """The one behaviour change from before CFG-6: `apply_graph_runtime_configuration`
    used to return `settings` unchanged when `runtime_integrations` had nothing
    to say. It must still apply `deployment` -- order and the four other
    switches have exactly one source now, regardless of `runtime_integrations`.
    """
    configuration = _configuration(ai={"provider_order": ["NVIDIA"]})
    assert not configuration.runtime_integrations.ai_providers

    updated = apply_graph_runtime_configuration(test_settings, configuration)

    assert updated.ai_provider_order == "NVIDIA"


# --------------------------------------------------------------------------- #
# 2. Precedence with `runtime_integrations`
# --------------------------------------------------------------------------- #


def test_deployment_precedence_over_runtime_integrations() -> None:
    """Design §4: availability from `runtime_integrations`, order from
    `deployment`, and `SIMULATOR` present only because `deployment` names it.
    """
    provider = AIProviderRuntimeConfiguration(
        provider_key="GOOGLE",
        enabled=True,
        base_url="https://google.invalid/v1",
        credentials=(
            CredentialBindingConfiguration(
                profile_key="google-runtime",
                bootstrap_managed=True,
            ),
        ),
        models=(
            AIModelBindingConfiguration(
                model_id="models/gemini-3.7-flash",
                model_class="STANDARD",
                task_keys=("ORDER_AGENT_REASONING_V1",),
                priority=1,
            ),
        ),
    )
    configuration = _PACKAGED.model_copy(
        update={
            "runtime_integrations": _PACKAGED.runtime_integrations.model_copy(
                update={"ai_providers": (provider,)}
            ),
            "deployment": DeploymentConfiguration.model_validate(
                {
                    "ai": {
                        "provider_order": ["SIMULATOR", "GOOGLE"],
                        # A conflicting pool for the GOVERNED provider: must be
                        # ignored -- `runtime_integrations` owns GOOGLE's pool.
                        "model_pools": {"GOOGLE": {"standard": ["not-the-real-model"]}},
                    }
                }
            ),
        }
    )
    settings = Settings(
        google_api_keys=(SecretStr("dev-key"),),
        environment="development",
    )

    updated = apply_graph_runtime_configuration(settings, configuration)

    # Order: deployment's, unconditionally -- and the only source of SIMULATOR.
    assert updated.ai_provider_order == "SIMULATOR,GOOGLE"
    # Availability/pool: runtime_integrations' answer for the provider it governs.
    assert updated.google_standard_models == ("models/gemini-3.7-flash",)
    assert "not-the-real-model" not in updated.google_standard_models


def test_deployment_model_pool_fills_in_only_ungoverned_providers() -> None:
    """A provider `runtime_integrations` does not mention gets its pool from
    `deployment.ai.model_pools`; one it governs does not.
    """
    provider = AIProviderRuntimeConfiguration(
        provider_key="GOOGLE",
        enabled=True,
        base_url="https://google.invalid/v1",
        credentials=(
            CredentialBindingConfiguration(profile_key="google-runtime", bootstrap_managed=True),
        ),
        models=(
            AIModelBindingConfiguration(
                model_id="governed-model",
                model_class="STANDARD",
                task_keys=("ORDER_AGENT_REASONING_V1",),
                priority=1,
            ),
        ),
    )
    configuration = _PACKAGED.model_copy(
        update={
            "runtime_integrations": _PACKAGED.runtime_integrations.model_copy(
                update={"ai_providers": (provider,)}
            ),
            "deployment": DeploymentConfiguration.model_validate(
                {
                    "ai": {
                        "provider_order": ["GOOGLE", "NVIDIA"],
                        "model_pools": {
                            "GOOGLE": {"standard": ["ungoverned-should-not-apply"]},
                            "NVIDIA": {"standard": ["nvidia-a"]},
                        },
                    }
                }
            ),
        }
    )
    settings = Settings(environment="development")

    updated = apply_deployment_configuration(settings, configuration)

    assert updated.nvidia_standard_models == ("nvidia-a",)
    # GOOGLE is governed -- `apply_deployment_configuration` leaves its pool to
    # whatever `runtime_integrations`' own pass already set (empty here, since
    # this test calls `apply_deployment_configuration` directly rather than
    # going through `apply_graph_runtime_configuration`).
    assert "ungoverned-should-not-apply" not in updated.google_standard_models


# --------------------------------------------------------------------------- #
# 4. Production gates
# --------------------------------------------------------------------------- #


def test_production_refuses_simulator_in_release_provider_order() -> None:
    configuration = _configuration(ai={"provider_order": ["GOOGLE", "SIMULATOR"]})
    with pytest.raises(DeploymentEnvironmentError, match=r"deployment\.ai\.provider_order"):
        validate_deployment_for_environment(configuration.deployment, "production")


def test_production_refuses_manual_in_release_provider_order() -> None:
    configuration = _configuration(ai={"provider_order": ["MANUAL"]})
    with pytest.raises(DeploymentEnvironmentError, match=r"deployment\.ai\.provider_order"):
        validate_deployment_for_environment(configuration.deployment, "production")


def test_production_refuses_simulated_dependency_mode_from_release() -> None:
    configuration = _configuration(dependencies={"omc": "SIMULATED"})
    with pytest.raises(DeploymentEnvironmentError, match=r"deployment\.dependencies\.omc"):
        validate_deployment_for_environment(configuration.deployment, "production")


def test_non_production_environment_is_not_gated() -> None:
    configuration = _configuration(
        ai={"provider_order": ["SIMULATOR"]}, dependencies={"omc": "SIMULATED"}
    )
    validate_deployment_for_environment(configuration.deployment, "development")
    validate_deployment_for_environment(configuration.deployment, "test")


def test_settings_validate_relationships_remains_the_backstop() -> None:
    """Design §2/§0: even bypassing the named gate, `Settings.model_validate`
    inside `apply_graph_runtime_configuration` still refuses -- the mechanism
    `validate_deployment_for_environment` exists to catch things BEFORE.

    A production `Settings` must itself be valid at construction (else this
    would just be testing the constructor, not the overlay's re-validation),
    so every OTHER production rule is satisfied here and only the release's
    `deployment.ai.provider_order` carries the violation.
    """
    configuration = _configuration(
        ai={"provider_order": ["SIMULATOR"]},
        dependencies={"omc": "REAL", "parcel": "REAL", "freight": "REAL", "lsi": "REAL"},
    )
    settings = Settings(
        environment="production",
        ai_provider_order="GOOGLE",
        omc_dependency_mode="REAL",
        parcel_dependency_mode="REAL",
        freight_dependency_mode="REAL",
        lsi_dependency_mode="REAL",
        validation_fingerprint_key=SecretStr("a-real-production-fingerprint-key"),
        contact_lookup_hmac_key=SecretStr("a-real-production-hmac-key"),
        reasoning_encryption_key=SecretStr("MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="),
    )

    with pytest.raises(ValidationError, match="SIMULATOR cannot be configured in production"):
        apply_graph_runtime_configuration(settings, configuration)


# --------------------------------------------------------------------------- #
# 5. Carry-forward: the three `_carry_forward` rows, for `deployment.ai`
# --------------------------------------------------------------------------- #


def _packaged_with_provider_order(order: list[str]) -> dict[str, Any]:
    payload = _PACKAGED.model_dump(mode="json")
    payload["deployment"]["ai"]["provider_order"] = order
    return payload


def test_deployment_first_publish_seeds_from_env() -> None:
    """No active release: the packaged (env-seeded) value is adopted outright."""
    packaged = _packaged_with_provider_order(["GOOGLE", "NVIDIA", "SIMULATOR"])

    result = adopt_packaged_configuration(
        packaged_return_platform=packaged,
        packaged_domains={"AI_GATEWAY": {}, "DEPENDENCY_SIMULATION": {}},
        active_return_platform=None,
        active_domains={},
        active_metadata={},
    )

    assert result.undecided[RETURN_PLATFORM_DOMAIN_KEY] == ()
    # RV round 1 F3: design §3 row 1 is "env/packaged value adopted, BASELINE
    # RECORDED" -- a first publish (no active release; `recordable_baseline`
    # never leaves its initial `_key_digests(packaged_units)` value, since the
    # `active_return_platform is not None` branch that would overwrite it does
    # not run) records all four `deployment.*` unit digests, not none.
    assert sorted(key for key in result.recordable_baseline if key.startswith("deployment")) == [
        "deployment.ai",
        "deployment.dependencies",
        "deployment.feedback_learning",
        "deployment.support_ticket",
    ]


def test_deployment_later_start_keeps_operator_value() -> None:
    """An operator edited `deployment.ai` away from the file; env also moved.
    With a recorded baseline matching the OLD file value, the release (the
    operator's edit) wins.
    """
    packaged = _packaged_with_provider_order(["GOOGLE", "NVIDIA", "SIMULATOR"])
    active = _packaged_with_provider_order(["ANTHROPIC"])  # the operator's edit
    baseline_packaged = _packaged_with_provider_order(["GOOGLE", "NVIDIA"])  # the file, when cut
    baseline = _key_digests({"deployment.ai": baseline_packaged["deployment"]["ai"]})

    result = adopt_packaged_configuration(
        packaged_return_platform=packaged,
        packaged_domains={"AI_GATEWAY": {}, "DEPENDENCY_SIMULATION": {}},
        active_return_platform=active,
        active_domains={},
        active_metadata={PACKAGED_KEY_DIGESTS: baseline},
    )

    merged = result.merged_domains[RETURN_PLATFORM_DOMAIN_KEY]
    assert merged["deployment"]["ai"]["provider_order"] == ["ANTHROPIC"]


def test_deployment_later_start_adopts_changed_env() -> None:
    """No operator edit yet (release matches the OLD baseline exactly): a new
    env value is adopted, because the release was never touched.
    """
    old_packaged = _packaged_with_provider_order(["GOOGLE", "NVIDIA"])
    new_packaged = _packaged_with_provider_order(["GOOGLE", "NVIDIA", "ANTHROPIC"])
    baseline = _key_digests({"deployment.ai": old_packaged["deployment"]["ai"]})

    result = adopt_packaged_configuration(
        packaged_return_platform=new_packaged,
        packaged_domains={"AI_GATEWAY": {}, "DEPENDENCY_SIMULATION": {}},
        active_return_platform=old_packaged,
        active_domains={},
        active_metadata={PACKAGED_KEY_DIGESTS: baseline},
    )

    merged = result.merged_domains[RETURN_PLATFORM_DOMAIN_KEY]
    assert merged["deployment"]["ai"]["provider_order"] == ["GOOGLE", "NVIDIA", "ANTHROPIC"]
    assert "deployment.ai" not in result.undecided[RETURN_PLATFORM_DOMAIN_KEY]


# --------------------------------------------------------------------------- #
# 6. Bootstrap seeding: circularity guard
# --------------------------------------------------------------------------- #
#
# RV round 1 A3: the real regression guard -- proving `_packaged_domain_payloads`
# (`configuration/api/releases.py`) actually seeds `deployment` from
# `app.state.packaged_deployment_defaults` rather than `app.state.settings` --
# is `test_packaged_domain_payloads_seeds_deployment_from_the_bootstrap_snapshot_not_app_state_settings`
# in `tests/test_configuration_api.py`, which drives the real call site
# through `POST /api/config/adopt-packaged`. A prior version of this test
# lived here and only proved `deployment_payload_from_settings` itself is a
# pure settings-in/dict-out projection with no reference to any release --
# true, but not a guard against the actual regression (RV: "its own docstring
# concedes this"), so it was removed rather than kept beside the real one.


def test_merge_deployment_defaults_only_overlays_env_set_fields() -> None:
    packaged = cast("dict[str, Any]", _PACKAGED.model_dump(mode="json")["deployment"])
    env_defaults = deployment_payload_from_settings(Settings(environment="development"))

    merged = merge_deployment_defaults(packaged, env_defaults)

    # A bare `Settings()` contributes nothing this repo's own `.env` did not
    # already set -- the packaged default survives untouched for any field
    # `model_fields_set` does not carry.
    assert (
        cast("dict[str, Any]", merged["support_ticket"])["mode"]
        == cast("dict[str, Any]", packaged["support_ticket"])["mode"]
    )


# --------------------------------------------------------------------------- #
# 7. RETURN_PLATFORM carry-forward is unchanged for a release with no `deployment`
# --------------------------------------------------------------------------- #


def test_return_platform_carry_forward_unchanged_without_deployment() -> None:
    """Neither side carries `deployment`: `_units`/`_assemble` routing must be
    a no-op, identical to the flat, pre-CFG-6 carry-forward.
    """
    packaged = _PACKAGED.model_dump(mode="json")
    del packaged["deployment"]
    active = dict(packaged)
    active["discovery"] = {**packaged["discovery"], "identification_fields": []}

    result = adopt_packaged_configuration(
        packaged_return_platform=packaged,
        packaged_domains={"AI_GATEWAY": {}, "DEPENDENCY_SIMULATION": {}},
        active_return_platform=active,
        active_domains={},
        active_metadata={},
    )

    merged = result.merged_domains.get(RETURN_PLATFORM_DOMAIN_KEY)
    # `discovery` carries no baseline and release != packaged there -> undecided,
    # the release's value kept -- exactly the pre-CFG-6 answer.
    assert merged is None or merged["discovery"]["identification_fields"] == []
    assert "deployment" not in _drop_retired_keys(dict(active))


# --------------------------------------------------------------------------- #
# 9. The Mongo `providerOrder` duplicate is gone
# --------------------------------------------------------------------------- #


def test_ai_settings_has_no_provider_order() -> None:
    from return_platform.operations.models import AIGatewaySettingsUpdate, AIGatewaySettingsView

    assert "providerOrder" not in AIGatewaySettingsView.model_fields
    assert "providerOrder" not in AIGatewaySettingsUpdate.model_fields

    with pytest.raises(ValidationError):
        AIGatewaySettingsUpdate.model_validate(
            {
                "interceptMode": False,
                "providerOrder": ["GOOGLE"],
                "expectedVersion": 0,
            }
        )


# --------------------------------------------------------------------------- #
# 8. Revert: `_drop_retired_keys` removes `deployment` exactly like any other
#    top-level key the model stops declaring -- design §7's rollback path.
# --------------------------------------------------------------------------- #


def test_deployment_key_is_dropped_after_revert(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulates a `git revert` of this lease: the model no longer declares
    `deployment` (monkeypatched off `model_fields`, the same set
    `_drop_retired_keys` reads), but the active release -- published while it
    did -- still carries it. Republishing must drop it rather than refuse the
    whole release, exactly as it already does for `feature_flags`/`extensions`
    (CFG-1, `test_a_key_the_model_retired_is_dropped_from_the_carried_release`)
    -- no special-casing was added for `deployment`, so none is needed to
    remove it either.
    """
    reverted_fields = {
        key: value
        for key, value in ReturnPlatformConfiguration.model_fields.items()
        if key != "deployment"
    }
    monkeypatch.setattr(ReturnPlatformConfiguration, "model_fields", reverted_fields)

    payload = _PACKAGED.model_dump(mode="json")
    assert "deployment" in payload

    dropped = _drop_retired_keys(dict(payload))

    assert "deployment" not in dropped
    assert set(dropped) == set(reverted_fields)


# --------------------------------------------------------------------------- #
# RV round 1 F4: the allow_baseline_fallback path must overlay env too
# --------------------------------------------------------------------------- #


def test_overlay_env_deployment_defaults_wins_over_the_packaged_file() -> None:
    """A `Settings` whose provider order differs from the packaged file: the
    env value must win in the overlaid configuration, unconditionally.
    """
    assert tuple(_PACKAGED.deployment.ai.provider_order) != ("GOOGLE", "NVIDIA")
    settings = Settings(ai_provider_order="GOOGLE,NVIDIA")

    overlaid = overlay_env_deployment_defaults(_PACKAGED, settings)

    assert overlaid.deployment.ai.provider_order == ("GOOGLE", "NVIDIA")


def test_overlay_env_deployment_defaults_is_a_noop_with_nothing_env_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When `deployment_payload_from_settings` has nothing to contribute (a
    settings object that set none of the eight switches this session), the
    packaged configuration comes back unchanged -- design §7's
    already-passing case must stay a no-op, not merely "close to the same
    values".
    """
    monkeypatch.setattr(
        "return_platform.configuration.deployment_settings.deployment_payload_from_settings",
        lambda _settings: {},
    )

    overlaid = overlay_env_deployment_defaults(_PACKAGED, Settings())

    assert overlaid is _PACKAGED


@pytest.mark.asyncio
async def test_allow_baseline_fallback_snapshot_honors_the_env_provider_order() -> None:
    """End to end, through the real seam: no active release, development, the
    graph's own baseline-fallback path (`ConfigurationSnapshotBuilder.build_snapshot`,
    `allow_baseline_fallback=True`) -- the env's provider order must reach the
    resulting snapshot, not the packaged file's.

    This is the exact scenario RV round 1 F4 proved broken: an env whose
    `PLATFORM_AI_PROVIDER_ORDER` differs from the packaged default silently
    lost to it on this path.
    """
    assert tuple(_PACKAGED.deployment.ai.provider_order) != ("GOOGLE", "NVIDIA")
    settings = Settings(ai_provider_order="GOOGLE,NVIDIA")
    repository = InMemoryConfigurationGraphRepository()
    default_configuration = overlay_env_deployment_defaults(_PACKAGED, settings)

    snapshot = await ConfigurationSnapshotBuilder(repository).build_snapshot(
        default_configuration,
        allow_baseline_fallback=True,
    )

    assert snapshot.source == "VERSION_CONTROLLED_BASELINE"
    assert snapshot.configuration.deployment.ai.provider_order == ("GOOGLE", "NVIDIA")


@pytest.mark.asyncio
async def test_allow_baseline_fallback_without_the_fix_would_use_the_packaged_order() -> None:
    """Negative control: proves the test above actually exercises F4, not
    something that would pass either way -- the UN-overlaid packaged
    configuration reaches the snapshot with the packaged file's own order.
    """
    settings = Settings(ai_provider_order="GOOGLE,NVIDIA")
    repository = InMemoryConfigurationGraphRepository()

    snapshot = await ConfigurationSnapshotBuilder(repository).build_snapshot(
        _PACKAGED,  # the pre-F4 call: no overlay
        allow_baseline_fallback=True,
    )

    assert snapshot.configuration.deployment.ai.provider_order == tuple(
        _PACKAGED.deployment.ai.provider_order
    )
    assert snapshot.configuration.deployment.ai.provider_order != ("GOOGLE", "NVIDIA")
    assert settings.ai_provider_order == "GOOGLE,NVIDIA"
