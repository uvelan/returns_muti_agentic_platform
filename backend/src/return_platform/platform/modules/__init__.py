"""Module identity, declaration, and descriptor bookkeeping. See README.md."""

from return_platform.platform.modules.builtins import (
    BuiltinModuleFactories,
    builtin_module_factories,
)
from return_platform.platform.modules.contracts import (
    HealthStatus,
    ModuleFactory,
    ModuleHealth,
    ModuleRuntime,
    ModuleRuntimeContext,
    ReconfigureOutcome,
)
from return_platform.platform.modules.descriptor import ModuleDescriptor, ModuleKind
from return_platform.platform.modules.exceptions import (
    CapabilityUnsatisfied,
    DuplicateImplementation,
    ModuleInitFailed,
    ModuleNotRegistered,
)
from return_platform.platform.modules.lifecycle import (
    initialize_all,
    rollup_health,
    shutdown_all,
)
from return_platform.platform.modules.registry import ModuleRegistry

__all__ = [
    "BuiltinModuleFactories",
    "CapabilityUnsatisfied",
    "DuplicateImplementation",
    "HealthStatus",
    "ModuleDescriptor",
    "ModuleFactory",
    "ModuleHealth",
    "ModuleInitFailed",
    "ModuleKind",
    "ModuleNotRegistered",
    "ModuleRegistry",
    "ModuleRuntime",
    "ModuleRuntimeContext",
    "ReconfigureOutcome",
    "builtin_module_factories",
    "initialize_all",
    "rollup_health",
    "shutdown_all",
]
