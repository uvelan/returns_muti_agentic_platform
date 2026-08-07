"""Startup composition. Knows every module's factory and nothing else about them."""

from return_platform.bootstrap.activation import activate, construct_all
from return_platform.bootstrap.context import RuntimeContext, StaticCorrelationContext, SystemClock
from return_platform.bootstrap.epoch import (
    EpochAdmission,
    EpochAllocator,
    EpochLease,
    EpochLifecycleState,
    EpochStateError,
    FatalReconfigurationError,
    ReconfigurationCoordinator,
    ReplicaStatus,
    ReplicaUnavailable,
    SimpleRuntimeEpoch,
    StaleReconfiguration,
)
from return_platform.bootstrap.errors import (
    StartupFailure,
    StartupFailureSeverity,
    should_stop_startup,
)
from return_platform.bootstrap.health import collect_module_health, overall_status
from return_platform.bootstrap.lifespan import module_lifespan

__all__ = [
    "EpochAdmission",
    "EpochAllocator",
    "EpochLease",
    "EpochLifecycleState",
    "EpochStateError",
    "FatalReconfigurationError",
    "ReconfigurationCoordinator",
    "ReplicaStatus",
    "ReplicaUnavailable",
    "RuntimeContext",
    "SimpleRuntimeEpoch",
    "StaleReconfiguration",
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
