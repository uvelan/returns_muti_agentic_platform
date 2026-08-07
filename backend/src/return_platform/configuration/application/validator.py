from typing import Any
from return_platform.configuration.domain.release import RuntimeSnapshot

class ConfigurationValidationError(ValueError):
    pass

class ConfigurationValidator:
    def validate_snapshot(self, snapshot: RuntimeSnapshot) -> None:
        """Strict validation of a full snapshot."""
        if not snapshot.modules or not snapshot.modules.modules:
            raise ConfigurationValidationError("Snapshot must contain a non-empty modules configuration.")
        if not snapshot.ai:
            raise ConfigurationValidationError("Snapshot must contain valid AI configuration.")
        if not snapshot.platform:
            raise ConfigurationValidationError("Snapshot must contain valid Platform configuration.")
