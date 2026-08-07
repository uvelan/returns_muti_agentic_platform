"""Startup composition. Knows every module's factory and nothing else about them."""

from return_platform.bootstrap.activation import activate, construct_all
from return_platform.bootstrap.context import RuntimeContext, StaticCorrelationContext, SystemClock
from return_platform.bootstrap.epoch import (
    EpochAllocator,
    EpochLeaseTracker,
    EpochPointer,
    ReconfigurationCoordinator,
    SimpleRuntimeEpoch,
)
from return_platform.bootstrap.errors import (
    StartupFailure,
    StartupFailureSeverity,
    should_stop_startup,
)
from return_platform.bootstrap.health import collect_module_health, overall_status
from return_platform.bootstrap.lifespan import module_lifespan

__all__ = [
    "EpochAllocator",
    "EpochLeaseTracker",
    "EpochPointer",
    "ReconfigurationCoordinator",
    "RuntimeContext",
    "SimpleRuntimeEpoch",
    "StartupFailure",
    "StartupFailureSeverity",
    "StaticCorrelationContext",
    "SystemClock",
    "activate",
    "collect_module_health",
    "construct_all",
    "module_lifespan",
    "overall_status",
    "should_stop_startup",
]
