"""Re-exports for backward compatibility."""
from return_platform.configuration.application.compatibility import (
    LegacyCompatibilityAdapter,
    build_snapshot_from_legacy_configs,
)
from return_platform.configuration.domain.release_model import RuntimeSnapshot, ConfigurationRelease
from return_platform.configuration.domain.release import ReleaseStatus

__all__ = [
    "LegacyCompatibilityAdapter",
    "build_snapshot_from_legacy_configs",
    "RuntimeSnapshot",
    "ConfigurationRelease",
    "ReleaseStatus",
]
