"""Module construction (design doc section 2.1 step 8) and the top-level activation
sequence that ties construction, capability publication, and resolution together.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from return_platform.bootstrap.capabilities import (
    publish_adapter_capabilities,
    publish_native_capabilities,
    resolve_all,
)
from return_platform.platform.capabilities.contracts import CapabilityRegistry
from return_platform.platform.modules.contracts import ModuleRuntime, ModuleRuntimeContext
from return_platform.platform.modules.registry import ModuleRegistry


def construct_all(
    registry: ModuleRegistry,
    module_ids: Sequence[str],
    context: ModuleRuntimeContext,
    configs: Mapping[str, Mapping[str, object]],
) -> list[ModuleRuntime]:
    """Step 8: call factory.create() for every enabled module, in order."""
    return [
        registry.construct(module_id, context, configs.get(module_id, {}))
        for module_id in module_ids
    ]


async def activate(
    registry: ModuleRegistry,
    module_ids: Sequence[str],
    context: ModuleRuntimeContext,
    configs: Mapping[str, Mapping[str, object]],
    capabilities: CapabilityRegistry,
    adapter_publishers: Sequence[Callable[[CapabilityRegistry], None]],
) -> list[ModuleRuntime]:
    """Run steps 8-11 in order: construct, publish native, publish adapters, resolve."""
    modules = construct_all(registry, module_ids, context, configs)
    await publish_native_capabilities(modules, capabilities)
    await publish_adapter_capabilities(adapter_publishers, capabilities)
    await resolve_all(modules)
    return modules
