"""Module descriptor bookkeeping: uniqueness, resolution, and capability-declaration
validation.

Construction (calling a factory to produce a running ModuleRuntime) and health
aggregation are added in Phase 1B, once ModuleRuntime exists -- this registry is
deliberately usable without ever importing that contract.
"""

from __future__ import annotations

from return_platform.platform.capabilities.contracts import CapabilityName, CapabilityRegistry
from return_platform.platform.modules.descriptor import ModuleDescriptor
from return_platform.platform.modules.exceptions import (
    CapabilityUnsatisfied,
    DuplicateImplementation,
    ModuleNotRegistered,
)


class ModuleRegistry:
    """Registers module descriptors and resolves them by module_id."""

    def __init__(self) -> None:
        self._descriptors: dict[str, ModuleDescriptor] = {}
        self._factories: dict[str, object] = {}
        self._implementation_ids: set[str] = set()

    def register(self, descriptor: ModuleDescriptor, factory: object) -> None:
        """Register `factory` under `descriptor.module_id`.

        Raises DuplicateImplementation if the module_id or the implementation_id is
        already registered.
        """
        if descriptor.module_id in self._descriptors:
            raise DuplicateImplementation(
                f"module_id {descriptor.module_id!r} is already registered"
            )
        if descriptor.implementation_id in self._implementation_ids:
            raise DuplicateImplementation(
                f"implementation_id {descriptor.implementation_id!r} is already registered"
            )
        self._descriptors[descriptor.module_id] = descriptor
        self._factories[descriptor.module_id] = factory
        self._implementation_ids.add(descriptor.implementation_id)

    def resolve(self, module_id: str) -> tuple[ModuleDescriptor, object]:
        """Return the descriptor and factory registered for `module_id`."""
        try:
            return self._descriptors[module_id], self._factories[module_id]
        except KeyError as exc:
            raise ModuleNotRegistered(f"module_id {module_id!r} is not registered") from exc

    def all(self) -> tuple[ModuleDescriptor, ...]:
        return tuple(self._descriptors.values())

    def validate_capabilities(self, module_id: str, capabilities: CapabilityRegistry) -> None:
        """Raise CapabilityUnsatisfied if a required platform capability has no publisher.

        Only checks presence, not shape -- a shape mismatch surfaces at resolve() time,
        against the concrete contract the caller actually needs.
        """
        descriptor, _ = self.resolve(module_id)
        published = {publication.capability for publication in capabilities.list()}
        for raw_name in descriptor.required_platform_capabilities:
            capability = CapabilityName(raw_name)
            if capability not in published:
                raise CapabilityUnsatisfied(
                    f"module {module_id!r} requires capability {capability!r}, "
                    "which nothing has published"
                )
