"""Ordered module lifecycle: initialize, shutdown, and health rollup.

Modules activate and shut down in the order the caller supplies -- ModuleDescriptor
does not yet declare dependency edges, so there is no ordering to sort by beyond
that. Add a real dependency sort here if a descriptor grows a declared dependency
list; do not invent one speculatively.
"""

from __future__ import annotations

from collections.abc import Sequence

from return_platform.platform.modules.contracts import HealthStatus, ModuleHealth, ModuleRuntime


async def initialize_all(modules: Sequence[ModuleRuntime]) -> None:
    """Initialize every module in order.

    Fails closed: a raise stops the sequence and propagates -- the caller does not get
    a partially-initialized module list back.
    """
    for module in modules:
        await module.initialize()


async def shutdown_all(modules: Sequence[ModuleRuntime]) -> None:
    """Shut down every module in reverse order, best-effort.

    Every module gets a shutdown attempt regardless of earlier failures; failures are
    collected and raised together so one module's failure never prevents another's
    cleanup from running.
    """
    errors: list[Exception] = []
    for module in reversed(modules):
        try:
            await module.shutdown()
        except Exception as exc:
            errors.append(exc)
    if errors:
        raise ExceptionGroup("module shutdown failed", errors)


def rollup_health(healths: Sequence[ModuleHealth]) -> HealthStatus:
    """Worst-of aggregation: any UNAVAILABLE wins, then any DEGRADED, else HEALTHY."""
    statuses = {health.status for health in healths}
    if HealthStatus.UNAVAILABLE in statuses:
        return HealthStatus.UNAVAILABLE
    if HealthStatus.DEGRADED in statuses:
        return HealthStatus.DEGRADED
    return HealthStatus.HEALTHY
