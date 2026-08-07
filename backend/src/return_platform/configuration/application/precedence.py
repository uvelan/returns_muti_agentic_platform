"""Configuration precedence resolution.

Authority chain (lowest → highest):
    BOOTSTRAP_ENV → BASELINE → ACTIVE_RELEASE → (output) RuntimeSnapshot

RuntimeSnapshot is the FROZEN OUTPUT of resolution, never another input layer.

BOOTSTRAP_ENV may only supply an explicit allowlist of deployment/bootstrap
fields.  Business configuration must not come from environment variables.
Secret values must never enter the snapshot; only Vault URI references are allowed.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Any, Dict, Mapping, Set


# ---------------------------------------------------------------------------
# Allowlisted BOOTSTRAP_ENV keys (deployment-only configuration)
# ---------------------------------------------------------------------------

BOOTSTRAP_ENV_ALLOWLIST: Set[str] = {
    # Runtime environment identity
    "environment",
    "region",
    "instance_id",
    "deployment_id",
    # Service bind configuration
    "host",
    "port",
    # Logging
    "log_level",
    # Bootstrap connection references (Vault URI strings, not resolved values)
    "vault_address",
    "mongodb_bootstrap_connection_reference",
    "temporal_bootstrap_endpoint",
    # Internal service discovery
    "service_name",
    "service_namespace",
}

# Fields that ACTIVE_RELEASE must not override (bootstrap/deployment owned)
BOOTSTRAP_ONLY_KEYS: Set[str] = BOOTSTRAP_ENV_ALLOWLIST


class PrecedenceLayer(StrEnum):
    BOOTSTRAP_ENV = "BOOTSTRAP_ENV"
    BASELINE = "BASELINE"
    ACTIVE_RELEASE = "ACTIVE_RELEASE"


class PrecedenceViolationError(ValueError):
    """Raised when a layer attempts to set a value it is not authoritative for."""


class ConfigurationPrecedenceEvaluator:
    """Resolves the effective configuration from ordered precedence layers.

    Usage::

        evaluator = ConfigurationPrecedenceEvaluator()
        result = evaluator.resolve(
            bootstrap_env={"environment": "production", "log_level": "INFO"},
            baseline={"platform": {"environment": "staging", "region": "us-east-1"}},
            active_release={"platform": {"region": "eu-west-1"}},
        )

    BOOTSTRAP_ENV is validated against the allowlist — any key not in the
    allowlist raises PrecedenceViolationError.

    ACTIVE_RELEASE must not contain any BOOTSTRAP_ONLY_KEYS at the top level.
    """

    def resolve(
        self,
        bootstrap_env: Mapping[str, Any],
        baseline: Mapping[str, Any],
        active_release: Mapping[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """Resolve effective configuration from all three input layers.

        Returns a plain dict that can be used to construct a RuntimeSnapshot.
        The result is meant to be frozen by the SnapshotBuilder caller.
        """
        # 1. Validate bootstrap_env allowlist
        for key in bootstrap_env:
            if key not in BOOTSTRAP_ENV_ALLOWLIST:
                raise PrecedenceViolationError(
                    f"BOOTSTRAP_ENV key '{key}' is not in the deployment allowlist. "
                    f"Business configuration must not come from environment variables."
                )

        # 2. Start from baseline
        result: Dict[str, Any] = self._deep_copy(baseline)

        # 3. Merge ACTIVE_RELEASE overrides (release-controlled values only)
        if active_release:
            for key, value in active_release.items():
                if key in BOOTSTRAP_ONLY_KEYS:
                    raise PrecedenceViolationError(
                        f"ACTIVE_RELEASE attempts to override bootstrap-only key '{key}'. "
                        f"Deployment settings are immutable by release."
                    )
                if isinstance(value, dict) and isinstance(result.get(key), dict):
                    result[key] = self._merge(result[key], value)
                else:
                    result[key] = value

        # 4. Apply BOOTSTRAP_ENV last (highest authority for its allowlisted keys)
        for key, value in bootstrap_env.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._merge(result[key], value)
            else:
                result[key] = value

        return result

    # ------------------------------------------------------------------
    # Legacy compat helper (kept for backward compatibility with existing callers)
    # ------------------------------------------------------------------

    def apply_overrides(
        self,
        base_config: Dict[str, Any],
        overrides: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Simple recursive merge — use resolve() for authority-aware resolution."""
        return self._merge(base_config, overrides)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _deep_copy(d: Mapping[str, Any]) -> Dict[str, Any]:
        import copy
        return copy.deepcopy(dict(d))

    @classmethod
    def _merge(
        cls,
        base: Dict[str, Any],
        overrides: Dict[str, Any],
    ) -> Dict[str, Any]:
        result = dict(base)
        for key, value in overrides.items():
            if isinstance(value, dict) and isinstance(result.get(key), dict):
                result[key] = cls._merge(result[key], value)
            else:
                result[key] = value
        return result
