"""Cross-module access without cross-module imports. See README.md."""

from return_platform.platform.capabilities.contracts import (
    CapabilityName,
    CapabilityPublication,
    CapabilityRegistry,
)
from return_platform.platform.capabilities.errors import (
    CapabilityNotPublished,
    CapabilityTypeMismatch,
    DuplicateCapability,
)
from return_platform.platform.capabilities.registry import InMemoryCapabilityRegistry

__all__ = [
    "CapabilityName",
    "CapabilityNotPublished",
    "CapabilityPublication",
    "CapabilityRegistry",
    "CapabilityTypeMismatch",
    "DuplicateCapability",
    "InMemoryCapabilityRegistry",
]
