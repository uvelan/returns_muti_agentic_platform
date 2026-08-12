"""Atomic activation of published graph configuration in long-running API processes."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from return_platform.ai_gateway.configuration import (
    LoadedAIGatewayConfiguration,
    build_loaded_ai_gateway_configuration,
)
from return_platform.ai_gateway.routing import AIRoutePool, build_routes
from return_platform.configuration.graph_repository import ConfigurationGraphRepository
from return_platform.configuration.return_configuration import LoadedReturnConfiguration
from return_platform.configuration.runtime_integrations import (
    apply_graph_runtime_configuration,
)
from return_platform.configuration.snapshot import (
    ConfigurationSnapshotBuilder,
    PinnedConfigurationSnapshot,
)
from return_platform.dependency_simulation.configuration import (
    LoadedDependencySimulationConfiguration,
    build_loaded_dependency_simulation_configuration,
)
from return_platform.operations.repository import OperationalRepository
from return_platform.resources import RuntimeResources
from return_platform.secrets.runtime import resolve_runtime_settings_from_vault

_RESTART_REQUIRED_SETTINGS = (
    "mongo_dsn",
    "mongo_database",
    "source_mongo_dsn",
    "source_mongo_database",
    "neo4j_uri",
    "neo4j_user",
    "neo4j_database",
    "sqlserver_host",
    "sqlserver_port",
    "sqlserver_database",
    "valkey_host",
    "valkey_port",
    "temporal_target",
)


class RuntimeConfigurationActivator:
    """Refresh one process from the active graph release without exposing partial state."""

    def __init__(
        self,
        *,
        app_state: Any,
        repository: ConfigurationGraphRepository,
        baseline_path: Path,
        ai_gateway_baseline_path: Path | None = None,
        dependency_simulation_baseline_path: Path | None = None,
        resources: RuntimeResources | None,
        refresh_interval_seconds: float = 5.0,
    ) -> None:
        self._app_state = app_state
        self._repository = repository
        self._baseline_path = baseline_path
        self._ai_gateway_baseline_path = ai_gateway_baseline_path
        self._dependency_simulation_baseline_path = dependency_simulation_baseline_path
        self._resources = resources
        self._refresh_interval_seconds = refresh_interval_seconds
        self._last_checked_monotonic = 0.0
        self._lock = asyncio.Lock()

    async def refresh(self, *, force: bool = False) -> PinnedConfigurationSnapshot:
        """Activate the current graph head when it differs from this process snapshot."""

        now = time.monotonic()
        current = self._current_snapshot()
        if (
            not force
            and current is not None
            and now - self._last_checked_monotonic < self._refresh_interval_seconds
        ):
            return current

        async with self._lock:
            now = time.monotonic()
            current = self._current_snapshot()
            if (
                not force
                and current is not None
                and now - self._last_checked_monotonic < self._refresh_interval_seconds
            ):
                return current

            self._last_checked_monotonic = now
            head_revision = await self._repository.get_head_revision()
            if (
                current is not None
                and current.source == "NEO4J_CONFIGURATION_GRAPH"
                and current.head_revision == head_revision
            ):
                return current
            if (
                current is not None
                and current.source == "VERSION_CONTROLLED_BASELINE"
                and head_revision == 0
            ):
                return current

            baseline = getattr(self._app_state, "return_configuration", None)
            if not isinstance(baseline, LoadedReturnConfiguration):
                raise RuntimeError("Runtime configuration is not loaded")
            baseline_ai_gateway = getattr(
                self._app_state,
                "ai_gateway_configuration",
                None,
            )
            baseline_dependency_simulation = getattr(
                self._app_state,
                "dependency_simulation_configuration",
                None,
            )

            snapshot = await ConfigurationSnapshotBuilder(self._repository).build_snapshot(
                baseline.configuration,
                allow_baseline_fallback=False,
                default_ai_gateway_configuration=(
                    baseline_ai_gateway.configuration
                    if isinstance(
                        baseline_ai_gateway,
                        LoadedAIGatewayConfiguration,
                    )
                    else None
                ),
                default_dependency_simulation_configuration=(
                    baseline_dependency_simulation.configuration
                    if isinstance(
                        baseline_dependency_simulation,
                        LoadedDependencySimulationConfiguration,
                    )
                    else None
                ),
                require_all_behavior_domains=(
                    self._ai_gateway_baseline_path is not None
                    or self._dependency_simulation_baseline_path is not None
                ),
            )
            loaded = LoadedReturnConfiguration(
                configuration=snapshot.configuration,
                path=self._baseline_path,
                sha256=snapshot.checksum_sha256,
            )
            loaded_ai_gateway = None
            if self._ai_gateway_baseline_path is not None:
                if snapshot.ai_gateway_configuration is None:
                    raise RuntimeError("Graph release has no AI gateway configuration")
                loaded_ai_gateway = build_loaded_ai_gateway_configuration(
                    snapshot.ai_gateway_configuration,
                    path=self._ai_gateway_baseline_path,
                )
            loaded_dependency_simulation = None
            if self._dependency_simulation_baseline_path is not None:
                if snapshot.dependency_simulation_configuration is None:
                    raise RuntimeError("Graph release has no dependency simulation configuration")
                loaded_dependency_simulation = build_loaded_dependency_simulation_configuration(
                    snapshot.dependency_simulation_configuration,
                    path=self._dependency_simulation_baseline_path,
                )
            activated_settings = self._resources.settings if self._resources is not None else None
            activated_secret_resolver = None
            if self._resources is not None:
                graph_settings = apply_graph_runtime_configuration(
                    self._resources.settings,
                    snapshot.configuration,
                )
                (
                    activated_settings,
                    activated_secret_resolver,
                ) = await resolve_runtime_settings_from_vault(graph_settings)
                changed_infrastructure = [
                    field_name
                    for field_name in _RESTART_REQUIRED_SETTINGS
                    if getattr(activated_settings, field_name)
                    != getattr(self._resources.settings, field_name)
                ]
                if changed_infrastructure:
                    raise RuntimeError(
                        "Released configuration changes infrastructure settings that require a "
                        "restart: " + ", ".join(changed_infrastructure)
                    )
            route_pool = None
            if activated_settings is not None and loaded_ai_gateway is not None:
                # The store the process was started with, carried into the
                # rebuild. Without it a live configuration change silently
                # rebuilt MANUAL as the filesystem provider, so an operator
                # watching the AI Control Center for a prompt would wait for one
                # that had been written to a file on disk instead -- the same
                # handoff, moved out from under them by an unrelated release.
                next_routes = build_routes(
                    activated_settings,
                    interception_store=getattr(self._app_state, "ai_interception_store", None),
                )
                current_route_pool = getattr(
                    self._app_state,
                    "ai_gateway_route_pool",
                    None,
                )
                if isinstance(current_route_pool, AIRoutePool):
                    await current_route_pool.replace_routes(
                        next_routes,
                        loaded_ai_gateway.configuration,
                    )
                    route_pool = current_route_pool
                else:
                    route_pool = AIRoutePool(
                        next_routes,
                        loaded_ai_gateway.configuration,
                    )

            # Persist the complete validated document before making it visible to request handlers.
            if self._resources is not None and self._resources.mongo is not None:
                operational_repository = OperationalRepository(
                    self._resources.mongo,
                    self._resources.settings,
                    self._resources.source_mongo,
                )
                await operational_repository.persist_return_configuration_snapshot(
                    path=str(loaded.path),
                    sha256=loaded.sha256,
                    schema_version=loaded.configuration.schema_version,
                    assumption_set_version=loaded.configuration.assumption_set_version,
                    configuration=loaded.configuration.model_dump(mode="json"),
                    behavior_domains=snapshot.domain_payloads,
                )

            # These two assignments form the process-level activation boundary. Consumers either
            # observe the previous validated release or the new validated release, never a draft.
            self._app_state.return_configuration = loaded
            if self._resources is not None and activated_settings is not None:
                self._resources.settings = activated_settings
                self._app_state.settings = activated_settings
            if activated_secret_resolver is not None:
                self._app_state.secret_resolver = activated_secret_resolver
            if loaded_ai_gateway is not None:
                self._app_state.ai_gateway_configuration = loaded_ai_gateway
            if loaded_dependency_simulation is not None:
                self._app_state.dependency_simulation_configuration = loaded_dependency_simulation
            if route_pool is not None:
                self._app_state.ai_gateway_route_pool = route_pool
            self._app_state.return_configuration_snapshot = snapshot
            return snapshot

    def _current_snapshot(self) -> PinnedConfigurationSnapshot | None:
        value = getattr(self._app_state, "return_configuration_snapshot", None)
        return value if isinstance(value, PinnedConfigurationSnapshot) else None
