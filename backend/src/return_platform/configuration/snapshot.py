"""Runtime configuration release pinning and immutable snapshot builder."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from return_platform.configuration.graph_repository import ConfigurationGraphRepository
from return_platform.configuration.return_configuration import ReturnPlatformConfiguration

logger = logging.getLogger("return_platform.configuration.snapshot")

RETURN_PLATFORM_DOMAIN_KEY = "RETURN_PLATFORM"
_ACTIVE_RELEASE_STATUSES = frozenset({"RELEASED"})


class PinnedConfigurationSnapshot(BaseModel):
    """One immutable, validated runtime configuration snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    release_id: str
    head_revision: int
    checksum_sha256: str
    loaded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source: str
    configuration: ReturnPlatformConfiguration
    domain_payloads: dict[str, Any] = Field(default_factory=dict)


class ConfigurationSnapshotBuilder:
    """Build immutable runtime snapshots from the active Neo4j release."""

    def __init__(self, repository: ConfigurationGraphRepository) -> None:
        self._repo = repository

    @staticmethod
    def _release_checksum(domain_payloads: dict[str, Any]) -> str:
        hasher = hashlib.sha256()
        for domain_key in sorted(domain_payloads):
            hasher.update(domain_key.encode("utf-8"))
            hasher.update(json.dumps(domain_payloads[domain_key], sort_keys=True).encode("utf-8"))
        return hasher.hexdigest()

    @staticmethod
    def _baseline_snapshot(
        default_configuration: ReturnPlatformConfiguration,
    ) -> PinnedConfigurationSnapshot:
        payload = default_configuration.model_dump(mode="json")
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return PinnedConfigurationSnapshot(
            release_id="version-controlled-baseline",
            head_revision=0,
            checksum_sha256=hashlib.sha256(encoded).hexdigest(),
            source="VERSION_CONTROLLED_BASELINE",
            configuration=default_configuration,
            domain_payloads={RETURN_PLATFORM_DOMAIN_KEY: payload},
        )

    async def build_snapshot(
        self,
        default_configuration: ReturnPlatformConfiguration,
        *,
        allow_baseline_fallback: bool,
    ) -> PinnedConfigurationSnapshot:
        """Load and validate the active graph release or use an explicitly allowed baseline."""

        try:
            active_release = await self._repo.get_active_release()
            if active_release is None:
                raise RuntimeError("No active graph configuration release exists")
            if active_release.status not in _ACTIVE_RELEASE_STATUSES:
                raise RuntimeError(
                    f"Graph configuration release {active_release.release_id} is not active"
                )
            if not active_release.checksum_sha256:
                raise RuntimeError(
                    f"Graph configuration release {active_release.release_id} has no checksum"
                )

            domain_payloads = await self._repo.get_all_domain_configs(active_release.release_id)
            calculated_checksum = self._release_checksum(domain_payloads)
            if calculated_checksum != active_release.checksum_sha256:
                raise RuntimeError(
                    f"Graph configuration release {active_release.release_id} checksum mismatch"
                )
            payload = domain_payloads.get(RETURN_PLATFORM_DOMAIN_KEY)
            if payload is None:
                raise RuntimeError(
                    f"Graph configuration release {active_release.release_id} does not contain "
                    f"the {RETURN_PLATFORM_DOMAIN_KEY} domain"
                )

            validated = ReturnPlatformConfiguration.model_validate(payload)
            return PinnedConfigurationSnapshot(
                release_id=active_release.release_id,
                head_revision=await self._repo.get_head_revision(),
                checksum_sha256=active_release.checksum_sha256,
                source="NEO4J_CONFIGURATION_GRAPH",
                configuration=validated,
                domain_payloads=domain_payloads,
            )
        except Exception as exc:
            if not allow_baseline_fallback:
                raise RuntimeError("Graph-first runtime configuration could not be loaded") from exc
            logger.warning(
                "graph_configuration_unavailable_using_version_controlled_baseline",
                extra={"error_type": type(exc).__name__},
            )
            return self._baseline_snapshot(default_configuration)
