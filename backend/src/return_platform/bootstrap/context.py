"""ModuleRuntimeContext assembly (design doc section 2.1 step 7).

This module does not know how to construct a RuntimeConfigurationHandle, a
SystemStore, or an AuditSink -- those are built by the phases that introduce them
(configuration, platform/system_store, platform/audit) and handed in here.

Phase 1B scope: only the fields backed by a contract that exists today --
configuration, capabilities, clock, correlation. system_store, secrets, redactor, and
audit are added to ModuleRuntimeContext (and to this assembly) once Phase 3 introduces
those platform packages -- extending this dataclass, not redesigning it.

`system_store` landed in Wave C3.1 (the Graph Schema Analyzer is the first module
with durable per-entity persistence). It defaults to None so every existing
construction site stays valid and a composition root without a store says so
explicitly instead of fabricating one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

from return_platform.platform.capabilities.contracts import CapabilityRegistry
from return_platform.platform.contracts.clock import Clock
from return_platform.platform.contracts.correlation import CorrelationContext
from return_platform.platform.contracts.runtime_configuration import RuntimeConfigurationHandle
from return_platform.platform.system_store.repository import SystemStore


class SystemClock:
    """The default Clock: wall-clock time in UTC."""

    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class StaticCorrelationContext:
    """A fixed correlation identity, generated once if not supplied.

    Real per-request correlation propagation belongs to whichever later phase wires
    actual request handling; nothing in Phase 1B needs it to vary yet.
    """

    correlation_id: str = field(default_factory=lambda: str(uuid4()))


@dataclass(frozen=True, slots=True)
class RuntimeContext:
    """Structurally satisfies ModuleRuntimeContext."""

    configuration: RuntimeConfigurationHandle
    capabilities: CapabilityRegistry
    clock: Clock
    correlation: CorrelationContext
    system_store: SystemStore | None = None
