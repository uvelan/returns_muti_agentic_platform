"""Module descriptor bookkeeping: uniqueness, resolution, capability-declaration
validation, and construction.

Phase 1A built everything except construct() -- it could not exist before ModuleRuntime
and ModuleFactory existed. Health aggregation over a list of running modules lives in
platform/modules/lifecycle.py instead, since this registry tracks descriptors and
factories, not constructed runtime instances.
"""

from __future__ import annotations

from collections.abc import Mapping

from return_platform.platform.capabilities.contracts import CapabilityName, CapabilityRegistry
from return_platform.platform.modules.contracts import (
    ModuleFactory,
    ModuleRuntime,
    ModuleRuntimeContext,
)
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

    def construct(
        self, module_id: str, context: ModuleRuntimeContext, config: Mapping[str, object]
    ) -> ModuleRuntime:
        """Resolve `module_id`'s factory and construct its runtime.

        Raises TypeError if the registered factory does not satisfy ModuleFactory --
        register() does not validate factory shape at registration time, so this is
        the first point that can.
        """
        _, factory = self.resolve(module_id)
        if not isinstance(factory, ModuleFactory):
            raise TypeError(
                f"module_id {module_id!r} was registered with a non-ModuleFactory object"
            )
        return factory.create(context, config)

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
