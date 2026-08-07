"""Health aggregation across all active modules (design doc section 2.1 step 15).

Configuration-adoption reporting (/health/ready degrading while a replica has a
pending release) is added once bootstrap/reconciler.py exists in Phase 2 -- this
module currently reports module health only.
"""

from __future__ import annotations

from collections.abc import Sequence

from return_platform.platform.modules.contracts import HealthStatus, ModuleHealth, ModuleRuntime
from return_platform.platform.modules.lifecycle import rollup_health


async def collect_module_health(modules: Sequence[ModuleRuntime]) -> tuple[ModuleHealth, ...]:
    return tuple([await module.health() for module in modules])


async def overall_status(modules: Sequence[ModuleRuntime]) -> HealthStatus:
    healths = await collect_module_health(modules)
    return rollup_health(healths)
