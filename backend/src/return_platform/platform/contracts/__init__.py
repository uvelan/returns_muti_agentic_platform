"""Neutral protocols that domain modules structurally satisfy without platform naming them."""

from return_platform.platform.contracts.clock import Clock
from return_platform.platform.contracts.consistency import ConsistencyChanged, ConsistencyHandle
from return_platform.platform.contracts.correlation import CorrelationContext
from return_platform.platform.contracts.epoch import RuntimeEpoch
from return_platform.platform.contracts.runtime_configuration import (
    ReleaseNotRetained,
    RuntimeConfigurationHandle,
    RuntimeConfigurationView,
)

__all__ = [
    "Clock",
    "ConsistencyChanged",
    "ConsistencyHandle",
    "CorrelationContext",
    "ReleaseNotRetained",
    "RuntimeConfigurationHandle",
    "RuntimeConfigurationView",
    "RuntimeEpoch",
]
