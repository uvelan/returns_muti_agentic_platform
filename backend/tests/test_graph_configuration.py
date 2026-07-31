"""Unit tests for graph-backed configuration releases and runtime snapshots."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from return_platform.configuration.graph_repository import (
    ConfigurationRevisionConflict,
    InMemoryConfigurationGraphRepository,
)
from return_platform.configuration.return_configuration import (
    LoadedReturnConfiguration,
    ReturnPlatformConfiguration,
    load_return_configuration,
)
from return_platform.configuration.runtime_activation import RuntimeConfigurationActivator
from return_platform.configuration.runtime_integrations import (
    apply_graph_runtime_configuration,
)
from return_platform.configuration.settings import Settings
from return_platform.configuration.snapshot import (
    RETURN_PLATFORM_DOMAIN_KEY,
    ConfigurationSnapshotBuilder,
)


@pytest.fixture
def sample_config() -> ReturnPlatformConfiguration:
    return load_return_configuration(Path("config/returns/production.yaml")).configuration


@pytest.mark.asyncio
async def test_release_publication_is_immutable_and_revision_guarded(
    sample_config: ReturnPlatformConfiguration,
) -> None:
    repo = InMemoryConfigurationGraphRepository()
    payload = sample_config.model_dump(mode="json")

    await repo.save_draft_domain(
        "release-v1",
        RETURN_PLATFORM_DOMAIN_KEY,
        payload,
        actor_id="admin-1",
    )
    await repo.promote_release("release-v1", "VALIDATED", actor_id="admin-1")

    with pytest.raises(ValueError, match="Cannot edit domain"):
        await repo.save_draft_domain(
            "release-v1",
            RETURN_PLATFORM_DOMAIN_KEY,
            payload,
            actor_id="admin-1",
        )

    published = await repo.promote_release(
        "release-v1",
        "RELEASED",
        actor_id="admin-1",
        expected_head_revision=0,
    )
    assert published.status == "RELEASED"
    assert await repo.get_head_revision() == 1
    assert (await repo.get_active_release()).release_id == "release-v1"  # type: ignore[union-attr]

    await repo.save_draft_domain(
        "release-v2",
        RETURN_PLATFORM_DOMAIN_KEY,
        payload,
        actor_id="admin-2",
    )
    await repo.promote_release("release-v2", "VALIDATED", actor_id="admin-2")

    with pytest.raises(ConfigurationRevisionConflict):
        await repo.promote_release(
            "release-v2",
            "RELEASED",
            actor_id="admin-2",
            expected_head_revision=0,
        )
    assert (await repo.get_release("release-v2")).status == "VALIDATED"  # type: ignore[union-attr]

    await repo.promote_release(
        "release-v2",
        "RELEASED",
        actor_id="admin-2",
        expected_head_revision=1,
    )
    assert await repo.get_head_revision() == 2
    assert (await repo.get_release("release-v1")).status == "SUPERSEDED"  # type: ignore[union-attr]
    assert (await repo.get_active_release()).release_id == "release-v2"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_snapshot_builder_uses_explicit_recovery_snapshot(
    sample_config: ReturnPlatformConfiguration,
) -> None:
    repo = InMemoryConfigurationGraphRepository()
    snapshot = await ConfigurationSnapshotBuilder(repo).build_snapshot(
        sample_config,
        allow_baseline_fallback=True,
    )

    assert snapshot.source == "VERSION_CONTROLLED_BASELINE"
    assert snapshot.release_id == "version-controlled-baseline"
    assert snapshot.head_revision == 0
    assert snapshot.configuration == sample_config
    assert snapshot.domain_payloads[RETURN_PLATFORM_DOMAIN_KEY] == sample_config.model_dump(
        mode="json"
    )


@pytest.mark.asyncio
async def test_snapshot_builder_loads_published_graph_release(
    sample_config: ReturnPlatformConfiguration,
) -> None:
    repo = InMemoryConfigurationGraphRepository()
    payload = sample_config.model_dump(mode="json")
    await repo.save_draft_domain(
        "release-graph-1",
        RETURN_PLATFORM_DOMAIN_KEY,
        payload,
        actor_id="admin-1",
    )
    await repo.promote_release("release-graph-1", "VALIDATED", actor_id="admin-1")
    await repo.promote_release(
        "release-graph-1",
        "RELEASED",
        actor_id="admin-1",
        expected_head_revision=0,
    )

    snapshot = await ConfigurationSnapshotBuilder(repo).build_snapshot(
        sample_config,
        allow_baseline_fallback=False,
    )

    assert snapshot.source == "NEO4J_CONFIGURATION_GRAPH"
    assert snapshot.release_id == "release-graph-1"
    assert snapshot.head_revision == 1
    assert snapshot.domain_payloads[RETURN_PLATFORM_DOMAIN_KEY] == payload


@pytest.mark.asyncio
async def test_production_snapshot_requires_every_behavior_domain(
    sample_config: ReturnPlatformConfiguration,
) -> None:
    repo = InMemoryConfigurationGraphRepository()
    await repo.save_draft_domain(
        "incomplete-release",
        RETURN_PLATFORM_DOMAIN_KEY,
        sample_config.model_dump(mode="json"),
        actor_id="admin-1",
    )
    await repo.promote_release("incomplete-release", "VALIDATED", actor_id="admin-1")
    await repo.promote_release(
        "incomplete-release",
        "RELEASED",
        actor_id="admin-1",
        expected_head_revision=0,
    )

    with pytest.raises(RuntimeError, match="Graph-first runtime configuration"):
        await ConfigurationSnapshotBuilder(repo).build_snapshot(
            sample_config,
            allow_baseline_fallback=False,
            require_all_behavior_domains=True,
        )


@pytest.mark.asyncio
async def test_runtime_activator_reconciles_a_new_graph_head(
    sample_config: ReturnPlatformConfiguration,
) -> None:
    repo = InMemoryConfigurationGraphRepository()
    baseline_path = Path("config/returns/production.yaml")
    baseline_snapshot = await ConfigurationSnapshotBuilder(repo).build_snapshot(
        sample_config,
        allow_baseline_fallback=True,
    )
    state = SimpleNamespace(
        return_configuration=LoadedReturnConfiguration(
            configuration=sample_config,
            path=baseline_path,
            sha256=baseline_snapshot.checksum_sha256,
        ),
        return_configuration_snapshot=baseline_snapshot,
    )
    activator = RuntimeConfigurationActivator(
        app_state=state,
        repository=repo,
        baseline_path=baseline_path,
        resources=None,
        refresh_interval_seconds=0,
    )

    changed = sample_config.model_copy(
        update={
            "agents": {
                **sample_config.agents,
                "order_discovery": sample_config.agents["order_discovery"].model_copy(
                    update={"version": "runtime-3.0"}
                ),
            }
        }
    )
    await repo.save_draft_domain(
        "runtime-agent-v3",
        RETURN_PLATFORM_DOMAIN_KEY,
        changed.model_dump(mode="json"),
        actor_id="configuration-admin",
    )
    await repo.promote_release(
        "runtime-agent-v3",
        "VALIDATED",
        actor_id="configuration-admin",
    )
    await repo.promote_release(
        "runtime-agent-v3",
        "RELEASED",
        actor_id="configuration-admin",
        expected_head_revision=0,
    )

    activated = await activator.refresh()

    assert activated.release_id == "runtime-agent-v3"
    assert activated.head_revision == 1
    assert state.return_configuration.configuration.agents["order_discovery"].version == (
        "runtime-3.0"
    )


def test_bootstrap_managed_sources_do_not_override_deployment_settings(
    sample_config: ReturnPlatformConfiguration,
    test_settings: Settings,
) -> None:
    configured = apply_graph_runtime_configuration(test_settings, sample_config)

    assert configured.mongo_dsn.get_secret_value() == test_settings.mongo_dsn.get_secret_value()
    assert configured.source_mongo_dsn == test_settings.source_mongo_dsn
    assert configured.neo4j_uri == test_settings.neo4j_uri
    assert configured.sqlserver_host == test_settings.sqlserver_host
    assert configured.valkey_host == test_settings.valkey_host
    assert configured.temporal_target == test_settings.temporal_target
    assert (
        configured.mongo_dsn_secret_reference
        == test_settings.mongo_dsn_secret_reference
    )
    assert (
        configured.source_mongo_dsn_secret_reference
        == test_settings.source_mongo_dsn_secret_reference
    )
    assert (
        configured.neo4j_password_secret_reference
        == test_settings.neo4j_password_secret_reference
    )
    assert (
        configured.sqlserver_password_secret_reference
        == test_settings.sqlserver_password_secret_reference
    )
    assert (
        configured.valkey_password_secret_reference
        == test_settings.valkey_password_secret_reference
    )
