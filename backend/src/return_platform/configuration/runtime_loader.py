"""Resolve one published graph configuration release for non-API processes."""

from __future__ import annotations

from dataclasses import dataclass

from neo4j import AsyncGraphDatabase

from return_platform.configuration.graph_repository import Neo4jConfigurationGraphRepository
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
from return_platform.secrets.runtime import resolve_runtime_settings_from_vault
from return_platform.secrets.vault import VaultHTTPSecretResolver

_DEVELOPMENT_ENVIRONMENTS = frozenset({"development", "test"})


@dataclass(frozen=True, slots=True)
class ResolvedProcessConfiguration:
    settings: Settings
    return_configuration: LoadedReturnConfiguration
    snapshot: PinnedConfigurationSnapshot
    secret_resolver: VaultHTTPSecretResolver | None


async def resolve_process_configuration(
    configured_settings: Settings | None = None,
) -> ResolvedProcessConfiguration:
    """Resolve Vault bootstrap credentials, graph metadata, then runtime secrets."""

    configured = configured_settings or Settings()
    bootstrap, resolver = await resolve_runtime_settings_from_vault(
        configured,
        resolve_ai_credentials=False,
    )
    baseline = load_return_configuration(bootstrap.return_configuration_path)
    driver = AsyncGraphDatabase.driver(
        bootstrap.neo4j_uri,
        auth=(bootstrap.neo4j_user, bootstrap.neo4j_password.get_secret_value()),
    )
    try:
        await driver.verify_connectivity()
        repository = Neo4jConfigurationGraphRepository(driver)
        graph_first = baseline.configuration.feature_flags.graph_first_runtime_configuration
        snapshot = await ConfigurationSnapshotBuilder(repository).build_snapshot(
            baseline.configuration,
            allow_baseline_fallback=(
                not graph_first or bootstrap.environment in _DEVELOPMENT_ENVIRONMENTS
            ),
        )
    finally:
        await driver.close()

    graph_settings = apply_graph_runtime_configuration(bootstrap, snapshot.configuration)
    resolved, resolved_resolver = await resolve_runtime_settings_from_vault(graph_settings)
    return ResolvedProcessConfiguration(
        settings=resolved,
        return_configuration=LoadedReturnConfiguration(
            configuration=snapshot.configuration,
            path=baseline.path,
            sha256=snapshot.checksum_sha256,
        ),
        snapshot=snapshot,
        secret_resolver=resolved_resolver or resolver,
    )
