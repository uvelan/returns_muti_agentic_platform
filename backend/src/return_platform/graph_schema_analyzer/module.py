"""The Graph Schema Analyzer's activatable module.

This is the **first `module.py` in the codebase**, which has a side effect worth
knowing about: `tests/platform/test_no_module_cross_imports.py` treats a package
as "migrated" exactly when it has one, so creating this file switches that
architecture test on for `graph_schema_analyzer` automatically. It also brings
this package under `test_no_adapters_package_outside_bootstrap`. Both are
intended -- the enforcement arriving with the module is the point.

The module owns no cross-module wiring. Its persistence is built from the
*platform* system store on the runtime context (a platform service, not another
module's capability); discovery, AI reasoning, and graph target access arrive as
ports resolved from the capability registry as C3.2/C3.3 introduce them.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

from fastapi import APIRouter

from return_platform.graph_schema_analyzer.api import router as analyzer_router
from return_platform.graph_schema_analyzer.domain.errors import AnalyzerError
from return_platform.graph_schema_analyzer.persistence import build_system_store_persistence
from return_platform.graph_schema_analyzer.ports.system_store_port import PersistencePort
from return_platform.platform.capabilities.contracts import CapabilityRegistry
from return_platform.platform.contracts.epoch import RuntimeEpoch
from return_platform.platform.modules.contracts import (
    HealthStatus,
    ModuleHealth,
    ModuleRuntime,
    ModuleRuntimeContext,
    ReconfigureOutcome,
)
from return_platform.platform.modules.descriptor import ModuleDescriptor, ModuleKind

__all__ = [
    "GRAPH_SCHEMA_ANALYZER_DESCRIPTOR",
    "GRAPH_SCHEMA_ANALYZER_IMPLEMENTATION_ID",
    "GRAPH_SCHEMA_ANALYZER_MODULE_ID",
    "AnalyzerNotConfigured",
    "GraphSchemaAnalyzerFactory",
    "GraphSchemaAnalyzerModule",
]


class AnalyzerNotConfigured(AnalyzerError):
    """The composition root activated the module without what it needs."""


GRAPH_SCHEMA_ANALYZER_MODULE_ID = "graph_schema_analyzer"
GRAPH_SCHEMA_ANALYZER_IMPLEMENTATION_ID = "graph_schema_analyzer.default"

CAPABILITY_SCHEMA_ANALYSIS = "graph_schema.analysis"

GRAPH_SCHEMA_ANALYZER_DESCRIPTOR = ModuleDescriptor(
    module_id=GRAPH_SCHEMA_ANALYZER_MODULE_ID,
    module_kind=ModuleKind.ANALYZER,
    implementation_id=GRAPH_SCHEMA_ANALYZER_IMPLEMENTATION_ID,
    version="1.0.0",
    capabilities=frozenset({CAPABILITY_SCHEMA_ANALYSIS}),
    configuration_schema="modules/graph_schema_analyzer.yaml",
    required_platform_capabilities=frozenset({"platform.system_store"}),
)


class GraphSchemaAnalyzerModule:
    """Structurally satisfies `ModuleRuntime`."""

    def __init__(self, context: ModuleRuntimeContext, config: Mapping[str, object]) -> None:
        self._context = context
        self._config = config
        self._persistence: PersistencePort | None = None

    async def initialize(self) -> None:
        """Bind persistence to the platform system store.

        Built here rather than resolved from the capability registry because the
        system store is a *platform* service on the runtime context, not another
        module's capability -- R2a governs cross-module reach, and this is not
        that. The analyzer still owns no adapter to any other module: sources, AI,
        and the graph target all arrive as resolved ports in C3.2/C3.3.
        """
        store = self._context.system_store
        if store is None:
            raise AnalyzerNotConfigured(
                "graph_schema_analyzer requires a system store on the runtime context; "
                "the composition root did not provide one."
            )
        self._persistence = build_system_store_persistence(store)

    async def publish_capabilities(self, registry: CapabilityRegistry) -> None:
        """The analyzer publishes no capability yet: nothing else in the platform
        consumes schema analysis programmatically -- the UI drives it over HTTP.
        Declared in the descriptor so the intent is visible, published once a real
        in-process consumer exists rather than speculatively."""

    async def resolve_capabilities(self) -> None:
        """Nothing to resolve yet. `SourceDiscoveryPort`, `SchemaReasoningPort`, and
        `GraphTargetPort` are resolved here once C3.2/C3.3 introduce the
        bootstrap adapters that publish them."""

    @property
    def persistence(self) -> PersistencePort:
        """The module's persistence, for the composition root to hand to the API
        layer. Raises rather than returning None: a router wired to a module that
        failed to initialize must fail loudly, not serve 500s from a None."""
        if self._persistence is None:
            raise AnalyzerNotConfigured("graph_schema_analyzer has not been initialized.")
        return self._persistence

    async def prepare_reconfigure(self, epoch: RuntimeEpoch) -> ReconfigureOutcome:
        """The analyzer keeps no epoch-scoped resource: a running analysis session
        lives in the system store, not in memory, so a configuration change needs
        no candidate to build and nothing to swap."""
        del epoch
        return ReconfigureOutcome.NO_CHANGE

    async def commit_reconfigure(self, epoch: RuntimeEpoch) -> None:
        del epoch

    async def abort_reconfigure(self, epoch: RuntimeEpoch) -> None:
        del epoch

    async def release_epoch(self, epoch: RuntimeEpoch) -> None:
        del epoch

    async def health(self) -> ModuleHealth:
        resolved = self._persistence is not None
        return ModuleHealth(
            module_id=GRAPH_SCHEMA_ANALYZER_MODULE_ID,
            status=HealthStatus.HEALTHY if resolved else HealthStatus.UNAVAILABLE,
            detail=None if resolved else "PersistencePort has not been resolved.",
            checked_at=datetime.now(UTC),
        )

    async def shutdown(self) -> None:
        """Must tolerate being called after a failed initialize(); since nothing is
        acquired, dropping the port reference is the whole of it."""
        self._persistence = None

    @property
    def router(self) -> APIRouter | None:
        return analyzer_router


class GraphSchemaAnalyzerFactory:
    """Structurally satisfies `ModuleFactory`."""

    @property
    def descriptor(self) -> ModuleDescriptor:
        return GRAPH_SCHEMA_ANALYZER_DESCRIPTOR

    def create(self, context: ModuleRuntimeContext, config: Mapping[str, object]) -> ModuleRuntime:
        return GraphSchemaAnalyzerModule(context, config)
