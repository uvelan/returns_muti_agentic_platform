"""Re-exports for backward compatibility."""

from return_platform.configuration.application.compatibility import (
    LegacyCompatibilityAdapter,
    build_snapshot_from_legacy_configs,
)
from return_platform.configuration.domain.release import ReleaseStatus
from return_platform.configuration.domain.release_model import ConfigurationRelease, RuntimeSnapshot

__all__ = [
    "ConfigurationRelease",
    "LegacyCompatibilityAdapter",
    "ReleaseStatus",
    "RuntimeSnapshot",
    "build_snapshot_from_legacy_configs",
]
