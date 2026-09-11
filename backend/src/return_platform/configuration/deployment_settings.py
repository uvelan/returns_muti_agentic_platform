"""Overlay the release's `deployment` section onto `Settings` (D-CFG-4, CFG-6).

One function does the hot-adopt overlay (`apply_deployment_configuration`),
called from inside `apply_graph_runtime_configuration` just before that
function's final `Settings.model_validate` -- the one place a release becomes
`Settings`, so `deployment`'s fields are re-validated by
`Settings.validate_relationships` on every activation exactly like every other
release-derived field (CFG-6.design.md §0, §4).

A second function (`deployment_payload_from_settings`) does the reverse
direction, for bootstrap seeding: the env values a deployment actually set
become the packaged default the first release carries forward (§3). The two
are deliberately kept apart -- one reads a release and writes `Settings`, the
other reads `Settings` and writes a release-shaped dict -- because conflating
them is exactly how `app.state.settings` (already release-derived, post
adoption) could get fed back into the seeding path as though it were an env
default, which `deployment_payload_from_settings`'s caller must never do (see
its own docstring and `cli/bootstrap_graph_configuration.py`,
`configuration/api/releases.py::_packaged_domain_payloads`).

A third function (`validate_deployment_for_environment`) is the production
gate lifted from `Settings.validate_relationships`: the environment-*dependent*
half of the rules `DeploymentConfiguration`'s own model validators cannot
enforce (a model validator has no `Settings.environment` to consult). Called
from the publish boundary (`POST /api/config/validate/{domain_key}`,
`/publish`, `/adopt-packaged`) and from `runtime_loader.py` at startup.
`Settings.validate_relationships` itself is untouched and stays the backstop.
"""

from __future__ import annotations

from dataclasses import dataclass

from return_platform.configuration.return_configuration import (
    DeploymentConfiguration,
    ReturnPlatformConfiguration,
)
from return_platform.configuration.settings import PRODUCTION_ENVIRONMENT, Settings

#: AI providers `apply_graph_runtime_configuration` can enable through the
#: release's `runtime_integrations` section. A provider named here keeps its
#: *availability* (credentials, and the model pool `runtime_integrations`
#: itself sets) from that section; `deployment.ai.model_pools` fills in a
#: model pool only for a provider NOT in this set for the release at hand
#: (CFG-6.design.md §4 -- the one precedence rule this lease is not free to
#: renegotiate).
_DEPENDENCY_MODE_FIELDS = ("omc", "parcel", "freight", "lsi")


def _governed_ai_providers(configuration: ReturnPlatformConfiguration) -> frozenset[str]:
    return frozenset(
        item.provider_key
        for item in configuration.runtime_integrations.ai_providers
        if item.enabled
    )


def apply_deployment_configuration(
    settings: Settings,
    configuration: ReturnPlatformConfiguration,
) -> Settings:
    """Return settings with `configuration.deployment` overlaid.

    Called from inside `apply_graph_runtime_configuration`, on the `Settings`
    that function has already produced from `runtime_integrations` (or on the
    process's own settings, unchanged, when that section made no changes) --
    either way, `deployment` is applied unconditionally: order and the four
    other switches this lease moves off the environment have exactly one
    source from here on, the release, whether or not `runtime_integrations`
    changed anything this poll.

    Precedence (CFG-6.design.md §4, not negotiable in this lease): order is
    always `deployment.ai.provider_order` -- the only source of `SIMULATOR`/
    `MANUAL`, which `runtime_integrations` cannot express. Availability
    (credentials, and the model pool) stays `runtime_integrations`'s answer for
    every provider it enables; `deployment.ai.model_pools` fills in a pool only
    for a provider it does NOT govern. A provider named in the order but not
    credentialed already contributes zero routes (`routes.py`'s own
    `_provider_credentials`), so naming an ungoverned or uncredentialed
    provider here is not a new failure mode.
    """

    deployment = configuration.deployment
    governed = _governed_ai_providers(configuration)

    updates: dict[str, object] = {
        "ai_provider_order": ",".join(deployment.ai.provider_order),
        "google_thinking_budget": deployment.ai.google.thinking_budget,
        "google_response_schema": deployment.ai.google.response_schema,
        "omc_dependency_mode": deployment.dependencies.omc,
        "parcel_dependency_mode": deployment.dependencies.parcel,
        "freight_dependency_mode": deployment.dependencies.freight,
        "lsi_dependency_mode": deployment.dependencies.lsi,
        "feedback_learning_enabled": deployment.feedback_learning.enabled,
        "support_ticket_mode": deployment.support_ticket.mode,
        "support_ticket_base_url": deployment.support_ticket.base_url,
    }
    for provider_key, pool in deployment.ai.model_pools.items():
        if provider_key in governed:
            continue
        key = provider_key.lower()
        updates[f"{key}_lightweight_models"] = tuple(pool.lightweight)
        updates[f"{key}_standard_models"] = tuple(pool.standard)

    return Settings.model_validate(settings.model_dump(mode="python") | updates)


def deployment_payload_from_settings(settings: Settings) -> dict[str, object]:
    """The `deployment`-shaped dict of values THIS process's env actually set.

    Only fields in `settings.model_fields_set` -- values a deployment set in
    `.env`/compose/the process environment, not pydantic defaults nobody
    configured -- so a bare `Settings()` with nothing overridden contributes an
    empty dict, and the packaged `deployment.yaml` default is what a first
    publish adopts, unchanged (design §3's first row).

    The caller overlays the result onto the packaged `deployment.yaml` payload
    (a dict merge, shallow per top-level unit: `ai`, `dependencies`,
    `feedback_learning`, `support_ticket`) before handing it to
    `adopt_packaged_configuration` -- never the other way around, and never
    computed from `app.state.settings` after that object is release-derived
    (see this module's own docstring and
    `test_adopt_packaged_seeds_from_bootstrap_settings_not_active_settings`).
    """

    # `getattr` rather than a direct attribute access: a caller that is not a
    # real `Settings` instance (a test double standing in for the bootstrap
    # settings the CLI resolves) has nothing that was "set" by an env in the
    # sense this function cares about, and the safe reading of that is
    # "nothing overridden" -- an empty payload, the same as a bare `Settings()`
    # -- not a crash.
    fields_set = getattr(settings, "model_fields_set", frozenset[str]())
    payload: dict[str, object] = {}

    ai: dict[str, object] = {}
    if "ai_provider_order" in fields_set:
        ai["provider_order"] = [
            part.strip() for part in settings.ai_provider_order.split(",") if part.strip()
        ]
    google: dict[str, object] = {}
    if "google_thinking_budget" in fields_set:
        google["thinking_budget"] = settings.google_thinking_budget
    if "google_response_schema" in fields_set:
        google["response_schema"] = settings.google_response_schema
    if google:
        ai["google"] = google

    model_pools: dict[str, object] = {}
    for provider in ("google", "nvidia", "openai", "anthropic", "ollama"):
        pool: dict[str, object] = {}
        lightweight_field = f"{provider}_lightweight_models"
        standard_field = f"{provider}_standard_models"
        if lightweight_field in fields_set:
            pool["lightweight"] = list(getattr(settings, lightweight_field))
        if standard_field in fields_set:
            pool["standard"] = list(getattr(settings, standard_field))
        if pool:
            model_pools[provider.upper()] = pool
    if model_pools:
        ai["model_pools"] = model_pools
    if ai:
        payload["ai"] = ai

    dependencies: dict[str, object] = {}
    for name in _DEPENDENCY_MODE_FIELDS:
        field_name = f"{name}_dependency_mode"
        if field_name in fields_set:
            dependencies[name] = getattr(settings, field_name)
    if dependencies:
        payload["dependencies"] = dependencies

    if "feedback_learning_enabled" in fields_set:
        payload["feedback_learning"] = {"enabled": settings.feedback_learning_enabled}

    support_ticket: dict[str, object] = {}
    if "support_ticket_mode" in fields_set:
        support_ticket["mode"] = settings.support_ticket_mode
    if "support_ticket_base_url" in fields_set:
        support_ticket["base_url"] = settings.support_ticket_base_url
    if support_ticket:
        payload["support_ticket"] = support_ticket

    return payload


def merge_deployment_defaults(
    packaged_deployment: dict[str, object],
    env_defaults: dict[str, object],
) -> dict[str, object]:
    """Overlay `env_defaults` (from `deployment_payload_from_settings`) onto the
    packaged `deployment.yaml` payload, one level deep per unit (`ai`,
    `dependencies`, `feedback_learning`, `support_ticket`) and one level deep
    again inside `ai` (`provider_order`, `google`, `model_pools`) so an env
    override of e.g. `google_thinking_budget` alone does not blank out a
    packaged `provider_order`.
    """

    def _as_dict(value: object) -> dict[str, object] | None:
        return dict(value) if isinstance(value, dict) else None

    merged: dict[str, object] = dict(packaged_deployment)
    for unit, value in env_defaults.items():
        merged_ai = _as_dict(merged.get("ai")) if unit == "ai" else None
        value_as_dict = _as_dict(value)
        if unit == "ai" and merged_ai is not None and value_as_dict is not None:
            for ai_key, ai_value in value_as_dict.items():
                merged_google = _as_dict(merged_ai.get("google")) if ai_key == "google" else None
                ai_value_as_dict = _as_dict(ai_value)
                merged_pools = (
                    _as_dict(merged_ai.get("model_pools")) if ai_key == "model_pools" else None
                )
                if (
                    ai_key == "google"
                    and merged_google is not None
                    and ai_value_as_dict is not None
                ):
                    merged_ai["google"] = {**merged_google, **ai_value_as_dict}
                elif (
                    ai_key == "model_pools"
                    and merged_pools is not None
                    and ai_value_as_dict is not None
                ):
                    for provider, pool in ai_value_as_dict.items():
                        existing_pool = _as_dict(merged_pools.get(provider))
                        pool_as_dict = _as_dict(pool)
                        merged_pools[provider] = (
                            {**existing_pool, **pool_as_dict}
                            if existing_pool is not None and pool_as_dict is not None
                            else pool
                        )
                    merged_ai["model_pools"] = merged_pools
                else:
                    merged_ai[ai_key] = ai_value
            merged["ai"] = merged_ai
        elif (existing := _as_dict(merged.get(unit))) is not None and value_as_dict is not None:
            merged[unit] = {**existing, **value_as_dict}
        else:
            merged[unit] = value
    return merged


@dataclass(frozen=True, slots=True)
class DeploymentEnvironmentViolation:
    """One production-gate refusal, with a pydantic-shaped path for the 422."""

    path: str
    message: str


class DeploymentEnvironmentError(ValueError):
    """Raised by `validate_deployment_for_environment`; carries every violation."""

    def __init__(self, violations: tuple[DeploymentEnvironmentViolation, ...]) -> None:
        self.violations = violations
        super().__init__("; ".join(f"{v.path}: {v.message}" for v in violations))


def validate_deployment_for_environment(
    section: DeploymentConfiguration,
    environment: str,
) -> None:
    """Refuse a `deployment` section production cannot run, with a named path.

    The environment-dependent half of the rules `Settings.validate_relationships`
    enforces for the equivalent env fields (settings.py:936-955): no
    `SIMULATOR`/`MANUAL` in provider order, no `SIMULATED` dependency mode.
    `Settings.validate_relationships` remains the backstop -- this exists so a
    release that would fail it is refused with a named path *before* it is
    published or adopted, not only when a process next polls
    (CFG-6.design.md §2).

    A no-op outside production: a release destined for staging must still be
    inspectable and publishable there even though it would be refused for
    production.
    """

    if environment != PRODUCTION_ENVIRONMENT:
        return
    violations: list[DeploymentEnvironmentViolation] = []
    if "SIMULATOR" in section.ai.provider_order:
        violations.append(
            DeploymentEnvironmentViolation(
                "deployment.ai.provider_order",
                "SIMULATOR cannot be configured in production.",
            )
        )
    if "MANUAL" in section.ai.provider_order:
        violations.append(
            DeploymentEnvironmentViolation(
                "deployment.ai.provider_order",
                "MANUAL cannot be configured in production.",
            )
        )
    for name in _DEPENDENCY_MODE_FIELDS:
        if getattr(section.dependencies, name) == "SIMULATED":
            violations.append(
                DeploymentEnvironmentViolation(
                    f"deployment.dependencies.{name}",
                    "External dependency simulation is forbidden in production.",
                )
            )
    if violations:
        raise DeploymentEnvironmentError(tuple(violations))
