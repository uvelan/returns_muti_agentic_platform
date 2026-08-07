"""Capability-registry passes over the module list (design doc section 2.1 steps 9, 11).

Step 9 (native publication) and step 11 (resolution) both operate purely on
CapabilityRegistry plus the constructed module list -- neither needs a ModuleRegistry
or a factory, so they live together here, separate from construct_all() in
activation.py.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from return_platform.platform.capabilities.contracts import CapabilityRegistry
from return_platform.platform.modules.contracts import ModuleRuntime


async def publish_native_capabilities(
    modules: Sequence[ModuleRuntime], registry: CapabilityRegistry
) -> None:
    """Step 9: ask every constructed module to publish what it natively provides."""
    for module in modules:
        await module.publish_capabilities(registry)


async def publish_adapter_capabilities(
    adapter_publishers: Sequence[Callable[[CapabilityRegistry], None]],
    registry: CapabilityRegistry,
) -> None:
    """Step 10: bootstrap/adapters/ binds provider contracts to consumer-shaped ports.

    Each entry is one adapter module's publish function. Listed here rather than in
    bootstrap/adapters/ itself because the adapters package holds bindings, not the
    sequencing that invokes them.
    """
    for publish in adapter_publishers:
        publish(registry)


async def resolve_all(modules: Sequence[ModuleRuntime]) -> None:
    """Step 11: every module resolves its ports, now that every publication exists."""
    for module in modules:
        await module.resolve_capabilities()
