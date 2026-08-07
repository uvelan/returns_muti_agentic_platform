"""The module lifecycle half of FastAPI's lifespan: activation (steps 8-11) followed
by ordered initialize (step 12) and reverse-ordered shutdown, wrapping the serving
period (design doc section 2.1).

Steps 1-7 (settings, secrets, system store, audit, capability registry construction,
configuration load, context assembly) and steps 13-15 (router mounting, configuration
reconciler, health gate) are supplied by the caller or added in later phases -- this
function's job is the module lifecycle itself, not everything that surrounds it.
main.py does not call this yet ("introduce the kernel alongside the existing boot
process, migrate nothing yet" -- plan Phase 1B).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager

from return_platform.bootstrap.activation import activate
from return_platform.platform.capabilities.contracts import CapabilityRegistry
from return_platform.platform.modules.contracts import ModuleRuntime, ModuleRuntimeContext
from return_platform.platform.modules.lifecycle import initialize_all, shutdown_all
from return_platform.platform.modules.registry import ModuleRegistry


@asynccontextmanager
async def module_lifespan(
    registry: ModuleRegistry,
    module_ids: Sequence[str],
    context: ModuleRuntimeContext,
    configs: Mapping[str, Mapping[str, object]],
    capabilities: CapabilityRegistry,
    adapter_publishers: Sequence[Callable[[CapabilityRegistry], None]],
) -> AsyncIterator[Sequence[ModuleRuntime]]:
    """Activate every module, initialize it, yield the running modules to serve
    traffic, then shut down in reverse order regardless of how serving ends."""
    modules = await activate(
        registry, module_ids, context, configs, capabilities, adapter_publishers
    )
    await initialize_all(modules)
    try:
        yield modules
    finally:
        await shutdown_all(modules)
