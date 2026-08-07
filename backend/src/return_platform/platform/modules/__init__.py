"""Module identity, declaration, and descriptor bookkeeping. See README.md."""

from return_platform.platform.modules.builtins import (
    BuiltinModuleFactories,
    builtin_module_factories,
)
from return_platform.platform.modules.descriptor import ModuleDescriptor, ModuleKind
from return_platform.platform.modules.exceptions import (
    CapabilityUnsatisfied,
    DuplicateImplementation,
    ModuleInitFailed,
    ModuleNotRegistered,
)
from return_platform.platform.modules.registry import ModuleRegistry

__all__ = [
    "BuiltinModuleFactories",
    "CapabilityUnsatisfied",
    "DuplicateImplementation",
    "ModuleDescriptor",
    "ModuleInitFailed",
    "ModuleKind",
    "ModuleNotRegistered",
    "ModuleRegistry",
    "builtin_module_factories",
]
