"""Module runtime lifecycle contracts.

ModuleRuntimeContext, ModuleRuntime, and ModuleFactory -- the pieces platform/modules/
could not define in Phase 1A because they describe what a RUNNING module looks like,
which nothing existed to describe yet.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from return_platform.platform.capabilities.contracts import CapabilityRegistry
from return_platform.platform.contracts.clock import Clock
from return_platform.platform.contracts.correlation import CorrelationContext
from return_platform.platform.contracts.epoch import RuntimeEpoch
from return_platform.platform.contracts.runtime_configuration import RuntimeConfigurationHandle
from return_platform.platform.modules.descriptor import ModuleDescriptor


class HealthStatus(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"


class ModuleHealth(BaseModel):
    """One module's self-reported health at one point in time."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    module_id: str
    status: HealthStatus
    detail: str | None = None
    checked_at: datetime


@runtime_checkable
class ModuleRuntimeContext(Protocol):
    """Platform services plus the capability registry. Nothing module-specific (R2a).

    No `.ai`, no `.knowledge`, no `.graph`, and no domain-owned type anywhere in this
    signature. A module needing another module's service declares a Protocol in its
    own ports/ and resolves it from `capabilities` during resolve_capabilities().

    Phase 1B scope: only the fields backed by an existing contract. system_store,
    secrets, redactor, and audit are added once Phase 3 introduces those platform
    packages -- extending this Protocol, not redesigning it.

    Declared as read-only properties, not plain attributes: a plain `x: T` Protocol
    member means "readable AND writable" to mypy, which the natural frozen
    implementation (see bootstrap/context.py's RuntimeContext) cannot satisfy.
    """

    @property
    def configuration(self) -> RuntimeConfigurationHandle: ...
    @property
    def capabilities(self) -> CapabilityRegistry: ...
    @property
    def clock(self) -> Clock: ...
    @property
    def correlation(self) -> CorrelationContext: ...


class ReconfigureOutcome(StrEnum):
    READY = "READY"
    NO_CHANGE = "NO_CHANGE"
    RESTART_REQUIRED = "RESTART_REQUIRED"


@runtime_checkable
class ModuleRuntime(Protocol):
    """The lifecycle every activated module implements."""

    async def initialize(self) -> None: ...

    async def publish_capabilities(self, registry: CapabilityRegistry) -> None:
        """Publish what this module provides. Called before any module resolves."""

    async def resolve_capabilities(self) -> None:
        """Resolve this module's ports. Called after every publication, including
        bootstrap-constructed adapters. Never resolve in create() or initialize()."""

    async def prepare_reconfigure(self, epoch: RuntimeEpoch) -> ReconfigureOutcome:
        """Do ALL fallible work here: validate, build candidate resources for `epoch`.
        Mutate nothing live. Must be safe to abandon."""

    async def commit_reconfigure(self, epoch: RuntimeEpoch) -> None:
        """Make the prepared candidate ADDRESSABLE under `epoch`. Does not make it
        current -- the replica's single epoch-pointer swap does that. Must not fail."""

    async def abort_reconfigure(self, epoch: RuntimeEpoch) -> None:
        """Destroy candidates for `epoch`. Live state untouched. Must not fail."""

    async def release_epoch(self, epoch: RuntimeEpoch) -> None:
        """Drop resources for a fully drained epoch. Must not fail."""

    async def health(self) -> ModuleHealth: ...

    async def shutdown(self) -> None: ...

    @property
    def router(self) -> APIRouter | None: ...


@runtime_checkable
class ModuleFactory(Protocol):
    """Constructs a ModuleRuntime for one module_id from its configuration."""

    @property
    def descriptor(self) -> ModuleDescriptor: ...

    def create(
        self, context: ModuleRuntimeContext, config: Mapping[str, object]
    ) -> ModuleRuntime: ...
