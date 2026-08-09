"""Resolve one published graph configuration release for non-API processes."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from neo4j import AsyncGraphDatabase

from return_platform.ai_gateway.configuration import (
    LoadedAIGatewayConfiguration,
    build_loaded_ai_gateway_configuration,
    load_ai_gateway_configuration,
)
from return_platform.configuration.graph_repository import (
    ConfigurationGraphRepository,
    InMemoryConfigurationGraphRepository,
    Neo4jConfigurationGraphRepository,
)
from return_platform.configuration.return_configuration import (
    LoadedReturnConfiguration,
    load_return_configuration,
)
from return_platform.configuration.runtime_integrations import apply_graph_runtime_configuration
from return_platform.configuration.settings import Settings
from return_platform.configuration.snapshot import (
    ConfigurationSnapshotBuilder,
    PinnedConfigurationSnapshot,
)
from return_platform.dependency_simulation.configuration import (
    LoadedDependencySimulationConfiguration,
    build_loaded_dependency_simulation_configuration,
    load_dependency_simulation_configuration,
)
from return_platform.secrets.runtime import resolve_runtime_settings_from_vault
from return_platform.secrets.vault import VaultHTTPSecretResolver

_DEVELOPMENT_ENVIRONMENTS = frozenset({"development", "test"})
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ResolvedProcessConfiguration:
    settings: Settings
    return_configuration: LoadedReturnConfiguration
    ai_gateway_configuration: LoadedAIGatewayConfiguration
    dependency_simulation_configuration: LoadedDependencySimulationConfiguration
    snapshot: PinnedConfigurationSnapshot
    secret_resolver: VaultHTTPSecretResolver | None


async def resolve_process_configuration(
    configured_settings: Settings | None = None,
) -> ResolvedProcessConfiguration:
    """Resolve Vault bootstrap credentials, graph metadata, then runtime secrets."""

    configured = configured_settings or Settings()
    try:
        bootstrap, resolver = await resolve_runtime_settings_from_vault(
            configured,
            resolve_ai_credentials=False,
        )
    except Exception as exc:
        logger.exception("Vault initialization failed")
        if configured.environment == "production":
            raise RuntimeError("Required Vault dependency is unavailable") from exc
        bootstrap = configured
        resolver = None
    baseline = load_return_configuration(bootstrap.return_configuration_path)
    baseline_ai_gateway = load_ai_gateway_configuration(bootstrap.ai_gateway_configuration_path)
    baseline_dependency_simulation = load_dependency_simulation_configuration(
        bootstrap.dependency_simulation_configuration_path
    )
    driver = AsyncGraphDatabase.driver(
        bootstrap.neo4j_uri,
        auth=(bootstrap.neo4j_user, bootstrap.neo4j_password.get_secret_value()),
    )
    repository: ConfigurationGraphRepository
    try:
        await driver.verify_connectivity()
        repository = Neo4jConfigurationGraphRepository(driver)
    except Exception as exc:
        logger.exception("Neo4j initialization failed")
        if bootstrap.environment == "production":
            raise RuntimeError("Required Neo4j dependency is unavailable") from exc
        repository = InMemoryConfigurationGraphRepository()

    try:
        snapshot = await ConfigurationSnapshotBuilder(repository).build_snapshot(
            baseline.configuration,
            allow_baseline_fallback=(bootstrap.environment in _DEVELOPMENT_ENVIRONMENTS),
            default_ai_gateway_configuration=baseline_ai_gateway.configuration,
            default_dependency_simulation_configuration=(
                baseline_dependency_simulation.configuration
            ),
            require_all_behavior_domains=(bootstrap.environment not in _DEVELOPMENT_ENVIRONMENTS),
        )
    finally:
        await driver.close()

    graph_settings = apply_graph_runtime_configuration(bootstrap, snapshot.configuration)
    try:
        resolved, resolved_resolver = await resolve_runtime_settings_from_vault(graph_settings)
    except Exception as exc:
        logger.exception("Vault initialization failed")
        if graph_settings.environment == "production":
            raise RuntimeError("Required Vault dependency is unavailable") from exc
        resolved = graph_settings
        resolved_resolver = None
    if snapshot.ai_gateway_configuration is None:
        raise RuntimeError("Runtime snapshot has no AI gateway configuration")
    if snapshot.dependency_simulation_configuration is None:
        raise RuntimeError("Runtime snapshot has no dependency simulation configuration")
    return ResolvedProcessConfiguration(
        settings=resolved,
        return_configuration=LoadedReturnConfiguration(
            configuration=snapshot.configuration,
            path=baseline.path,
            sha256=snapshot.checksum_sha256,
        ),
        ai_gateway_configuration=build_loaded_ai_gateway_configuration(
            snapshot.ai_gateway_configuration,
            path=baseline_ai_gateway.path,
        ),
        dependency_simulation_configuration=(
            build_loaded_dependency_simulation_configuration(
                snapshot.dependency_simulation_configuration,
                path=baseline_dependency_simulation.path,
            )
        ),
        snapshot=snapshot,
        secret_resolver=resolved_resolver or resolver,
    )
