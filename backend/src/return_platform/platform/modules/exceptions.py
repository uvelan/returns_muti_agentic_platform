"""Module registry error types."""

from __future__ import annotations


class ModuleNotRegistered(RuntimeError):
    """No implementation is registered for the requested module_id."""


class DuplicateImplementation(RuntimeError):
    """The same module_id or implementation_id was registered twice."""


class CapabilityUnsatisfied(RuntimeError):
    """A module declares a required_platform_capabilities entry nothing has published."""


class ModuleInitFailed(RuntimeError):
    """A module's initialize() raised. Startup fails closed in production."""


class MissingInitializationDependency(RuntimeError):
    """A module declares initialization_dependencies naming an unregistered module_id."""


class InitializationCycle(RuntimeError):
    """The initialization dependency graph has a cycle, including a self-dependency."""
