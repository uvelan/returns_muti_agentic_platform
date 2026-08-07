"""Domain-level configuration errors."""
from __future__ import annotations


class ConfigurationError(Exception):
    """Base error for all configuration-plane failures."""


class InvalidTransitionError(ValueError):
    """Raised when a lifecycle transition is not permitted from the current status."""


class ConfigurationReleaseNotFoundError(LookupError):
    """Raised when a release cannot be located by its ID."""


class ConfigurationValidationError(ValueError):
    """Raised when a canonical snapshot fails semantic validation."""
