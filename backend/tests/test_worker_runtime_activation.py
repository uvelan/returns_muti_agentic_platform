"""A worker adopts an activated configuration release without being restarted.

The failure these cover: an administrator switches the discovery model in the AI
Control Centre, the API confirms the new route is active, and every subsequent
agent turn still reaches the old provider -- because the Order Agent's reasoning
runs in the order-discovery worker, and that process resolved its configuration
once at startup and never again.

Entered through the production reconciler (`RuntimeConfigurationActivator`), the
production worker state (`ProcessRuntimeState`) and the production participant
from `scripts/run_order_discovery_worker.py`. Nothing here asserts that a class
exists or that configuration round-trips.
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import sys
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest
import pytest_asyncio
from pydantic import SecretStr

from return_platform.ai.routing.selection import AIRoutePool
from return_platform.ai.routing.tasks import (
    LoadedAIGatewayConfiguration,
    load_ai_gateway_configuration,
)
from return_platform.configuration.graph_repository import (
    ConfigurationGraphRepository,
    InMemoryConfigurationGraphRepository,
)
from return_platform.configuration.return_configuration import (
    LoadedReturnConfiguration,
    ReturnPlatformConfiguration,
    load_return_configuration,
)
from return_platform.configuration.runtime_activation import (
    ActivationContext,
    ProcessRuntimeState,
    RuntimeConfigurationActivator,
    run_runtime_activation_loop,
)
from return_platform.configuration.runtime_activation import (
    resolve_runtime_settings_from_vault as _production_vault_resolution,
)
from return_platform.configuration.settings import (
    DEFAULT_AI_GATEWAY_CONFIGURATION_PATH,
    DEFAULT_DEPENDENCY_SIMULATION_CONFIGURATION_PATH,
    DEFAULT_RETURN_CONFIGURATION_PATH,
    Settings,
)
from return_platform.configuration.snapshot import (
    AI_GATEWAY_DOMAIN_KEY,
    DEPENDENCY_SIMULATION_DOMAIN_KEY,
    RETURN_PLATFORM_DOMAIN_KEY,
    ConfigurationSnapshotBuilder,
    PinnedConfigurationSnapshot,
)
from return_platform.dependency_simulation.configuration import (
    load_dependency_simulation_configuration,
)
from return_platform.workflows.order_discovery_activities import (
    OrderDiscoveryActivities,
    OrderDiscoveryRuntime,
)
from return_platform.workflows.order_discovery_workflow import (
    RunOrderDiscoveryTurnActivityInput,
)

_BACKEND = Path(__file__).resolve().parents[1]
_SCRIPTS = _BACKEND / "scripts"
_RETURN_CONFIGURATION_PATH = DEFAULT_RETURN_CONFIGURATION_PATH
_AI_GATEWAY_PATH = DEFAULT_AI_GATEWAY_CONFIGURATION_PATH
_DEPENDENCY_SIMULATION_PATH = DEFAULT_DEPENDENCY_SIMULATION_CONFIGURATION_PATH

# Every worker compose actually deploys. The reconciler is a property of the
# process class, not of the one worker whose failure was noticed first.
_DEPLOYED_WORKERS = (
    "run_order_discovery_worker.py",
    "run_return_workflow_worker.py",
    "run_return_orchestrator.py",
    "run_outbox_publisher.py",
)


# --------------------------------------------------------------------------- #
# Release fixtures
# --------------------------------------------------------------------------- #


def _provider_release(
    configuration: ReturnPlatformConfiguration,
    *,
    provider_key: str,
    model_id: str,
) -> ReturnPlatformConfiguration:
    """One release that enables exactly one model provider.

    This is the administrator's edit in the AI Control Centre, expressed the way
    the platform stores it: `runtime_integrations.ai_providers` is what
    `apply_graph_runtime_configuration` turns into the provider order, the model
    lists and the Vault references a route is built from.
    """

    slug = provider_key.lower()
    provider = {
        "provider_key": provider_key,
        "enabled": True,
        "base_url": f"https://{slug}.invalid/v1",
        "credentials": [
            {
                "profile_key": f"{slug}-runtime",
                "vault_reference": f"vault://secret/production/ai-providers/{slug}#api_key",
                "validation_receipt_id": f"receipt-{slug}",
                "validation_configuration_checksum": "a" * 64,
            }
        ],
        "models": [
            {
                "model_id": model_id,
                "model_class": "STANDARD",
                "task_keys": ["ORDER_AGENT_REASONING_V1"],
                "priority": 1,
            }
        ],
        "validated_routes": [
            {
                "credential_profile_key": f"{slug}-runtime",
                "model_id": model_id,
                "task_key": "ORDER_AGENT_REASONING_V1",
                "validation_receipt_id": f"receipt-{slug}",
                "validation_configuration_checksum": "a" * 64,
            }
        ],
    }
    payload = configuration.model_dump(mode="json")
    payload["runtime_integrations"]["ai_providers"] = [provider]
    # Revalidated rather than `model_copy`d: `model_copy(update=...)` writes the
    # raw dicts straight onto the model, and the release would then be published
    # as something `ReturnPlatformConfiguration` never accepted.
    return ReturnPlatformConfiguration.model_validate(payload)


def _moved_valkey_release(
    configuration: ReturnPlatformConfiguration,
) -> ReturnPlatformConfiguration:
    """A release that moves an infrastructure endpoint a live process is on.

    `valkey_port` is one of the settings the activator refuses to swap under a
    running process. Taking the source out of `bootstrap_managed` is what makes
    the release's own value reach `Settings`, which is the only way an operator
    could actually cause this.
    """

    payload = configuration.model_dump(mode="json")
    for source in payload["runtime_integrations"]["data_sources"]:
        if source["source_key"] != "valkey":
            continue
        source["bootstrap_managed"] = False
        source["port"] = 6380
        source["validation_receipt_id"] = "receipt-valkey"
        source["validation_configuration_checksum"] = "b" * 64
        source["credential"] = {
            "profile_key": "valkey-runtime",
            "vault_reference": "vault://secret/production/data-sources/valkey#password",
            "validation_receipt_id": "receipt-valkey",
            "validation_configuration_checksum": "b" * 64,
        }
    return ReturnPlatformConfiguration.model_validate(payload)


async def _publish(
    repo: ConfigurationGraphRepository,
    release_id: str,
    *,
    configuration: ReturnPlatformConfiguration,
    ai_gateway: LoadedAIGatewayConfiguration,
    dependency_simulation_payload: dict[str, Any],
    expected_head_revision: int,
) -> None:
    for domain_key, payload in (
        (RETURN_PLATFORM_DOMAIN_KEY, configuration.model_dump(mode="json")),
        (AI_GATEWAY_DOMAIN_KEY, ai_gateway.configuration.model_dump(mode="json")),
        (DEPENDENCY_SIMULATION_DOMAIN_KEY, dependency_simulation_payload),
    ):
        await repo.save_draft_domain(
            release_id, domain_key, payload, actor_id="configuration-admin"
        )
    await repo.promote_release(release_id, "VALIDATED", actor_id="configuration-admin")
    await repo.promote_release(
        release_id,
        "RELEASED",
        actor_id="configuration-admin",
        expected_head_revision=expected_head_revision,
    )


@dataclass(slots=True)
class _Harness:
    state: ProcessRuntimeState
    activator: RuntimeConfigurationActivator
    repository: InMemoryConfigurationGraphRepository
    pool: AIRoutePool
    baseline: ReturnPlatformConfiguration
    ai_gateway: LoadedAIGatewayConfiguration
    dependency_simulation_payload: dict[str, Any]
    vault_calls: list[Settings]
    head_revision_reads: list[int]

    async def publish(self, release_id: str, configuration: ReturnPlatformConfiguration) -> None:
        head = await self.repository.get_head_revision()
        await _publish(
            self.repository,
            release_id,
            configuration=configuration,
            ai_gateway=self.ai_gateway,
            dependency_simulation_payload=self.dependency_simulation_payload,
            expected_head_revision=head,
        )


class _CountingRepository(InMemoryConfigurationGraphRepository):
    """The in-memory repository, counting the one read the poll guard protects."""

    def __init__(self, reads: list[int]) -> None:
        super().__init__()
        self._reads = reads

    async def get_head_revision(self) -> int:
        revision = await super().get_head_revision()
        self._reads.append(revision)
        return revision


def _vault_stub(calls: list[Settings]) -> Any:
    """Stand in for Vault, and supply the credentials a route needs.

    Deliberately the *only* source of provider keys in these tests:
    `apply_graph_runtime_configuration` clears every `*_api_keys` field and
    leaves only the Vault references behind, so a route pool with any routes at
    all is proof that re-resolution happened on this activation rather than at
    process start.
    """

    async def resolve(
        settings: Settings, *, resolve_ai_credentials: bool = True
    ) -> tuple[Settings, None]:
        calls.append(settings)
        if not resolve_ai_credentials:
            return settings, None
        updates: dict[str, Any] = {}
        for provider_key in ("google", "nvidia", "openai", "anthropic"):
            references = getattr(settings, f"{provider_key}_api_key_references")
            if references:
                updates[f"{provider_key}_api_keys"] = tuple(
                    SecretStr(f"resolved-{provider_key}-{index}")
                    for index, _ in enumerate(references)
                )
        if not updates:
            return settings, None
        return Settings.model_validate(settings.model_dump(mode="python") | updates), None

    return resolve


@pytest.fixture
def baseline_configuration() -> ReturnPlatformConfiguration:
    return load_return_configuration(_RETURN_CONFIGURATION_PATH).configuration


@pytest.fixture
def baseline_ai_gateway() -> LoadedAIGatewayConfiguration:
    return load_ai_gateway_configuration(_AI_GATEWAY_PATH)


@pytest.fixture
def dependency_simulation_payload() -> dict[str, Any]:
    loaded = load_dependency_simulation_configuration(_DEPENDENCY_SIMULATION_PATH)
    return cast(dict[str, Any], loaded.configuration.model_dump(mode="json"))


@pytest_asyncio.fixture
async def harness(
    test_settings: Settings,
    baseline_configuration: ReturnPlatformConfiguration,
    baseline_ai_gateway: LoadedAIGatewayConfiguration,
    dependency_simulation_payload: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> _Harness:
    """One order-discovery-worker process, started on a GOOGLE release."""

    head_revision_reads: list[int] = []
    repository = _CountingRepository(head_revision_reads)
    vault_calls: list[Settings] = []
    monkeypatch.setattr(
        "return_platform.configuration.runtime_activation.resolve_runtime_settings_from_vault",
        _vault_stub(vault_calls),
    )

    google = _provider_release(
        baseline_configuration, provider_key="GOOGLE", model_id="models/gemini-3.6-flash"
    )
    await _publish(
        repository,
        "release-google",
        configuration=google,
        ai_gateway=baseline_ai_gateway,
        dependency_simulation_payload=dependency_simulation_payload,
        expected_head_revision=0,
    )
    startup = await ConfigurationSnapshotBuilder(repository).build_snapshot(
        google,
        allow_baseline_fallback=False,
        default_ai_gateway_configuration=baseline_ai_gateway.configuration,
    )

    # The settings the process actually booted under: graph-applied, then
    # Vault-resolved, exactly as `resolve_process_configuration` produces them.
    from return_platform.configuration.runtime_integrations import (
        apply_graph_runtime_configuration,
    )

    started_settings, _ = await _vault_stub(vault_calls)(
        apply_graph_runtime_configuration(test_settings, startup.configuration)
    )
    vault_calls.clear()

    from return_platform.ai.routing.routes import build_routes

    pool = AIRoutePool(build_routes(started_settings), baseline_ai_gateway.configuration)
    state = ProcessRuntimeState(
        process_class="order-discovery-worker",
        instance_id="test-instance",
        settings=started_settings,
        return_configuration=LoadedReturnConfiguration(
            configuration=startup.configuration,
            path=_RETURN_CONFIGURATION_PATH,
            sha256=startup.checksum_sha256,
        ),
        return_configuration_snapshot=startup,
        ai_gateway_configuration=baseline_ai_gateway,
        ai_gateway_route_pool=pool,
    )
    activator = RuntimeConfigurationActivator(
        app_state=state,
        repository=repository,
        baseline_path=_RETURN_CONFIGURATION_PATH,
        ai_gateway_baseline_path=_AI_GATEWAY_PATH,
        resources=state,
        refresh_interval_seconds=0,
    )
    return _Harness(
        state=state,
        activator=activator,
        repository=repository,
        pool=pool,
        baseline=baseline_configuration,
        ai_gateway=baseline_ai_gateway,
        dependency_simulation_payload=dependency_simulation_payload,
        vault_calls=vault_calls,
        head_revision_reads=head_revision_reads,
    )


def _providers(pool: AIRoutePool) -> set[str]:
    return {route.provider_name for route in pool.routes}


def _models(pool: AIRoutePool) -> set[str]:
    return {route.model for route in pool.routes}


# --------------------------------------------------------------------------- #
# The headline: a released model change reaches a running worker
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_released_model_change_reaches_the_running_worker(harness: _Harness) -> None:
    """The audit's scenario end to end: Gemini out, Anthropic in, no restart."""

    assert _providers(harness.pool) == {"GOOGLE"}
    assert _models(harness.pool) == {"models/gemini-3.6-flash"}
    started_snapshot = harness.state.return_configuration_snapshot

    await harness.publish(
        "release-anthropic",
        _provider_release(
            harness.baseline,
            provider_key="ANTHROPIC",
            model_id="claude-sonnet-4-5",
        ),
    )
    activated = await harness.activator.refresh()

    assert activated.release_id == "release-anthropic"
    assert harness.state.return_configuration_snapshot is activated
    assert harness.state.return_configuration_snapshot is not started_snapshot
    # The *same* pool object, so the coordinator's invoker -- which holds this
    # instance and nothing else -- dispatches to the new provider on its next
    # attempt without being rebuilt.
    assert harness.state.ai_gateway_route_pool is harness.pool
    assert _providers(harness.pool) == {"ANTHROPIC"}
    assert _models(harness.pool) == {"claude-sonnet-4-5"}
    assert harness.state.settings.ai_provider_order == "ANTHROPIC"


@pytest.mark.asyncio
async def test_activation_re_resolves_vault_and_a_route_cannot_exist_without_it(
    harness: _Harness,
) -> None:
    """Secrets are re-resolved per activation, not carried from process start.

    A released provider change brings new Vault references with it, and the
    references are all the graph holds -- so a route pool that still has routes
    after the swap is proof the activator went back to Vault.
    """

    await harness.publish(
        "release-anthropic",
        _provider_release(harness.baseline, provider_key="ANTHROPIC", model_id="claude-sonnet-4-5"),
    )
    await harness.activator.refresh()

    assert len(harness.vault_calls) == 1
    resolved_input = harness.vault_calls[0]
    assert resolved_input.anthropic_api_key_references == (
        "vault://secret/production/ai-providers/anthropic#api_key",
    )
    assert harness.state.settings.anthropic_api_keys
    assert harness.pool.routes


@pytest.mark.asyncio
async def test_the_poll_guard_still_holds(
    test_settings: Settings,
    baseline_configuration: ReturnPlatformConfiguration,
    baseline_ai_gateway: LoadedAIGatewayConfiguration,
    dependency_simulation_payload: dict[str, Any],
    harness: _Harness,
) -> None:
    """A worker polling on a timer must not read the graph on every tick."""

    harness.activator = RuntimeConfigurationActivator(
        app_state=harness.state,
        repository=harness.repository,
        baseline_path=_RETURN_CONFIGURATION_PATH,
        ai_gateway_baseline_path=_AI_GATEWAY_PATH,
        resources=harness.state,
        refresh_interval_seconds=300.0,
    )
    harness.head_revision_reads.clear()

    await harness.activator.refresh()
    assert len(harness.head_revision_reads) == 1

    await harness.publish(
        "release-anthropic",
        _provider_release(harness.baseline, provider_key="ANTHROPIC", model_id="claude-sonnet-4-5"),
    )
    # Publishing reads the head itself; only the refreshes after this point are
    # the subject.
    reads_before_polling = len(harness.head_revision_reads)
    for _ in range(5):
        await harness.activator.refresh()

    assert len(harness.head_revision_reads) == reads_before_polling
    assert _providers(harness.pool) == {"GOOGLE"}

    assert (await harness.activator.refresh(force=True)).release_id == "release-anthropic"
    assert _providers(harness.pool) == {"ANTHROPIC"}


@pytest.mark.asyncio
async def test_an_infrastructure_change_is_restart_required_and_leaves_the_last_good_release(
    harness: _Harness,
) -> None:
    """Fail closed: the process keeps running the release it already had."""

    await harness.publish("release-moved-valkey", _moved_valkey_release(harness.baseline))

    with pytest.raises(RuntimeError, match="require a restart"):
        await harness.activator.refresh()

    assert harness.state.return_configuration_snapshot is not None
    assert harness.state.return_configuration_snapshot.release_id == "release-google"
    assert _providers(harness.pool) == {"GOOGLE"}


@pytest.mark.asyncio
async def test_the_activation_loop_drives_the_same_reconciler(harness: _Harness) -> None:
    """The worker's substitute for request middleware actually activates."""

    await harness.publish(
        "release-anthropic",
        _provider_release(harness.baseline, provider_key="ANTHROPIC", model_id="claude-sonnet-4-5"),
    )
    task = asyncio.create_task(run_runtime_activation_loop(harness.activator, interval_seconds=0))
    try:
        for _ in range(200):
            await asyncio.sleep(0)
            snapshot = harness.state.return_configuration_snapshot
            if snapshot is not None and snapshot.release_id == "release-anthropic":
                break
        else:  # pragma: no cover - the loop never adopted
            pytest.fail("the activation loop did not adopt the released configuration")
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert _providers(harness.pool) == {"ANTHROPIC"}


@pytest.mark.asyncio
async def test_a_failing_poll_does_not_take_the_worker_down(harness: _Harness) -> None:
    """A restart-required release must not kill the process that refused it."""

    await harness.publish("release-moved-valkey", _moved_valkey_release(harness.baseline))
    task = asyncio.create_task(run_runtime_activation_loop(harness.activator, interval_seconds=0))
    for _ in range(50):
        await asyncio.sleep(0)

    assert not task.done()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert harness.state.return_configuration_snapshot is not None
    assert harness.state.return_configuration_snapshot.release_id == "release-google"


# --------------------------------------------------------------------------- #
# Schema adoption and in-flight pinning
# --------------------------------------------------------------------------- #


def _load_worker_module(name: str) -> ModuleType:
    path = _SCRIPTS / name
    if not path.exists():  # pragma: no cover - real-infra runs copy only src/tests/config
        pytest.skip(f"{name} is not in this run's copy of the tree")
    spec = importlib.util.spec_from_file_location(f"_worker_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _StubSchema:
    """Only the three fields the participant and the activity read."""

    def __init__(self, release_id: str, checksum: str = "checksum-1") -> None:
        self.configuration_release_id = release_id
        self.configuration_checksum = checksum
        self.agent_policies: dict[str, object] = {}


class _RecordingCoordinator:
    def __init__(self, label: str, gate: asyncio.Event | None = None) -> None:
        self.label = label
        self.calls: list[str] = []
        self._gate = gate

    async def process_turn(self, request: Any, guard_context: Any, **kwargs: Any) -> Any:
        del guard_context, kwargs
        self.calls.append(request.conversation_id)
        if self._gate is not None:
            await self._gate.wait()
        raise AssertionError("unreachable in these tests")


def _participant(
    module: ModuleType,
    activities: OrderDiscoveryActivities,
    *,
    adopted_graph_release: tuple[str, int],
) -> Any:
    return module._OrderDiscoveryStackParticipant(
        activities=activities,
        platform_mongo=cast(Any, object()),
        source_mongo=cast(Any, object()),
        neo4j_driver=cast(Any, object()),
        route_pool=cast(Any, object()),
        system_store=cast(Any, object()),
        envelope_encryptor=cast(Any, object()),
        releases=cast(Any, object()),
        targeted_sync_runs=cast(Any, object()),
        temporal_client=cast(Any, object()),
        adopted_graph_release=adopted_graph_release,
    )


def _with_conversation_id(
    request: RunOrderDiscoveryTurnActivityInput, conversation_id: str
) -> RunOrderDiscoveryTurnActivityInput:
    return replace(request, conversation_id=conversation_id)


def _context(snapshot: PinnedConfigurationSnapshot, settings: Settings) -> ActivationContext:
    return ActivationContext(
        snapshot=snapshot,
        settings=settings,
        return_configuration=LoadedReturnConfiguration(
            configuration=snapshot.configuration,
            path=_RETURN_CONFIGURATION_PATH,
            sha256=snapshot.checksum_sha256,
        ),
        ai_gateway_configuration=load_ai_gateway_configuration(_AI_GATEWAY_PATH),
    )


@pytest.mark.asyncio
async def test_a_published_schema_release_rebuilds_the_worker_stack_once(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A schema activated in the analyzer console reaches the worker.

    And is resolved exactly once per rebuild: the coordinator and the activity
    surface must hold the same `ActiveSchema` object, or the guards evaluate
    against a different release than the queries were compiled from.
    """

    module = _load_worker_module("run_order_discovery_worker.py")
    old_schema = _StubSchema("schema-release-1")
    new_schema = _StubSchema("schema-release-2")
    activities = OrderDiscoveryActivities(
        coordinator=cast(Any, _RecordingCoordinator("old")), schema=cast(Any, old_schema)
    )

    resolutions: list[object] = []
    factory_schemas: list[object] = []
    factory_kwargs: list[dict[str, Any]] = []

    async def resolve_active_schema(path: Path, releases: Any = None) -> Any:
        del path, releases
        resolutions.append(new_schema)
        return new_schema

    async def build_runtime(**kwargs: Any) -> Any:
        factory_schemas.append(kwargs["schema"])
        factory_kwargs.append(kwargs)
        return _RecordingCoordinator("new")

    monkeypatch.setattr(module, "resolve_active_schema", resolve_active_schema)
    monkeypatch.setattr(module, "build_dynamic_order_agent_runtime", build_runtime)

    snapshot = harness.state.return_configuration_snapshot
    assert snapshot is not None
    participant = _participant(
        module,
        activities,
        adopted_graph_release=(snapshot.release_id, snapshot.head_revision),
    )
    prepared = await participant.prepare(_context(snapshot, harness.state.settings))
    assert prepared is not None
    participant.publish(prepared)

    assert len(resolutions) == 1
    assert factory_schemas == [new_schema]
    assert activities.runtime.schema is new_schema
    assert activities.runtime.schema is factory_schemas[0]
    assert cast(_RecordingCoordinator, activities.runtime.coordinator).label == "new"
    # WF-01 x T-16. The rebuilt coordinator stamps case timings onto every case
    # it confirms, so it must carry the release being *adopted* -- a case
    # confirmed after this swap is new work, and gets the new active release.
    assert factory_kwargs[0]["return_case_timings"] == snapshot.configuration.return_case
    assert factory_kwargs[0]["temporal_client"] is not None


@pytest.mark.asyncio
async def test_an_unchanged_schema_and_release_rebuild_nothing(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The participant is polled constantly; it must not rebuild constantly."""

    module = _load_worker_module("run_order_discovery_worker.py")
    schema = _StubSchema("schema-release-1")
    activities = OrderDiscoveryActivities(
        coordinator=cast(Any, _RecordingCoordinator("original")), schema=cast(Any, schema)
    )
    rebuilds: list[object] = []

    async def resolve_active_schema(path: Path, releases: Any = None) -> Any:
        del path, releases
        return _StubSchema("schema-release-1")

    async def build_runtime(**kwargs: Any) -> Any:
        rebuilds.append(kwargs)
        raise AssertionError("the stack was rebuilt for an unchanged release")

    monkeypatch.setattr(module, "resolve_active_schema", resolve_active_schema)
    monkeypatch.setattr(module, "build_dynamic_order_agent_runtime", build_runtime)

    snapshot = harness.state.return_configuration_snapshot
    assert snapshot is not None
    participant = _participant(
        module,
        activities,
        adopted_graph_release=(snapshot.release_id, snapshot.head_revision),
    )
    for _ in range(3):
        assert await participant.prepare(_context(snapshot, harness.state.settings)) is None

    assert rebuilds == []
    assert cast(_RecordingCoordinator, activities.runtime.coordinator).label == "original"


@pytest.mark.asyncio
async def test_the_reconciler_reaches_the_participant_through_refresh(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The connection itself: `refresh()` is what adopts the schema.

    Both branches -- the poll where the graph release moved and the poll where
    it did not -- because a schema release has its own activation pointer and
    would otherwise only be noticed when something unrelated changed.
    """

    module = _load_worker_module("run_order_discovery_worker.py")
    activities = OrderDiscoveryActivities(
        coordinator=cast(Any, _RecordingCoordinator("original")),
        schema=cast(Any, _StubSchema("schema-release-1")),
    )
    published = [_StubSchema("schema-release-1")]

    async def resolve_active_schema(path: Path, releases: Any = None) -> Any:
        del path, releases
        return published[-1]

    async def build_runtime(**kwargs: Any) -> Any:
        return _RecordingCoordinator(kwargs["schema"].configuration_release_id)

    monkeypatch.setattr(module, "resolve_active_schema", resolve_active_schema)
    monkeypatch.setattr(module, "build_dynamic_order_agent_runtime", build_runtime)

    snapshot = harness.state.return_configuration_snapshot
    assert snapshot is not None
    activator = RuntimeConfigurationActivator(
        app_state=harness.state,
        repository=harness.repository,
        baseline_path=_RETURN_CONFIGURATION_PATH,
        ai_gateway_baseline_path=_AI_GATEWAY_PATH,
        resources=harness.state,
        refresh_interval_seconds=0,
        participants=(
            _participant(
                module,
                activities,
                adopted_graph_release=(snapshot.release_id, snapshot.head_revision),
            ),
        ),
    )

    # No graph release moved: the schema pointer alone has to be enough.
    published.append(_StubSchema("schema-release-2"))
    await activator.refresh()
    assert activities.runtime.schema.configuration_release_id == "schema-release-2"

    # And a graph release change rebuilds too, because a rebuild reads settings
    # that come from there rather than from the schema.
    await harness.publish(
        "release-anthropic",
        _provider_release(harness.baseline, provider_key="ANTHROPIC", model_id="claude-sonnet-4-5"),
    )
    await activator.refresh()
    assert cast(_RecordingCoordinator, activities.runtime.coordinator).label == "schema-release-2"
    assert _providers(harness.pool) == {"ANTHROPIC"}


@pytest.mark.asyncio
async def test_a_turn_in_flight_finishes_on_the_release_it_started_on() -> None:
    """In-flight pinning, at the granularity the platform already uses.

    A turn pins `configuration_release_id` into its graph state on entry and a
    resumed clarification reads it back off the checkpoint. An adoption landing
    mid-turn must therefore not change the schema underneath it -- while the
    next turn must get the new one.
    """

    gate = asyncio.Event()
    old_coordinator = _RecordingCoordinator("old", gate)
    old_schema = _StubSchema("schema-release-1")
    activities = OrderDiscoveryActivities(
        coordinator=cast(Any, old_coordinator), schema=cast(Any, old_schema)
    )
    old_schema.agent_policies["order_discovery"] = object()

    request = RunOrderDiscoveryTurnActivityInput(
        conversation_id="conversation-in-flight",
        expected_conversation_version=1,
        client_turn_id="turn-1",
        idempotency_key="idem-1",
        message_id="message-1",
        message="where is my order",
        agent_id="order_discovery",
        principal_id="associate-1",
        tenant_id="tenant-1",
        roles=frozenset({"associate"}),
        branch_ids=frozenset({"branch-1"}),
        workflow_id="workflow-1",
        resume_thread_id=None,
        correlation_id="correlation-1",
        session_timezone="UTC",
    )
    in_flight = asyncio.create_task(activities.run_order_discovery_turn(request))
    for _ in range(20):
        await asyncio.sleep(0)
        if old_coordinator.calls:
            break
    assert old_coordinator.calls == ["conversation-in-flight"]

    new_coordinator = _RecordingCoordinator("new")
    new_schema = _StubSchema("schema-release-2")
    new_schema.agent_policies["order_discovery"] = object()
    activities.adopt(
        OrderDiscoveryRuntime(coordinator=cast(Any, new_coordinator), schema=cast(Any, new_schema))
    )

    # The turn that was already running is untouched by the swap.
    assert new_coordinator.calls == []
    gate.set()
    with pytest.raises(AssertionError):
        await in_flight
    assert old_coordinator.calls == ["conversation-in-flight"]

    # The next turn is the one that adopts.
    with pytest.raises(AssertionError):
        await activities.run_order_discovery_turn(
            _with_conversation_id(request, "conversation-next")
        )
    assert new_coordinator.calls == ["conversation-next"]
    assert old_coordinator.calls == ["conversation-in-flight"]


# --------------------------------------------------------------------------- #
# Production wiring
# --------------------------------------------------------------------------- #


def _called_function_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            names.add(func.id)
        elif isinstance(func, ast.Attribute):
            names.add(func.attr)
    return names


def _worker_trees() -> Iterable[tuple[str, ast.AST]]:
    for name in _DEPLOYED_WORKERS:
        path = _SCRIPTS / name
        if not path.exists():  # pragma: no cover - real-infra runs copy only src/tests/config
            pytest.skip(f"{name} is not in this run's copy of the tree")
        yield name, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_every_deployed_worker_runs_the_reconciler() -> None:
    """T-16: *every* long-running API and worker process runs one.

    A worker that resolves configuration once at startup is the defect this
    task exists to remove, and it is reintroduced by writing a new entry point,
    not by editing an old one.
    """

    for name, tree in _worker_trees():
        called = _called_function_names(tree)
        assert "build_worker_runtime_activation" in called, (
            f"{name} never builds a runtime activator; its configuration freezes at startup"
        )
        # `WorkerRuntimeActivation.start()` launches the reconcile loop and the
        # adoption reporter together (CFG-02); a worker that builds an activator
        # and never starts it has one that nothing calls `refresh()` on.
        assert "start" in called, (
            f"{name} builds an activator and never drives it; nothing calls refresh()"
        )


def test_no_worker_grew_a_second_reconciler() -> None:
    """One reconciler per process, or two answers to what is live."""

    for name, tree in _worker_trees():
        constructed = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"RuntimeConfigurationActivator", "build_worker_runtime_activation"}
        ]
        assert len(constructed) == 1, f"{name} builds {len(constructed)} reconcilers"


def test_the_worker_activation_helper_is_not_the_api_path() -> None:
    """Reconciliation rule 2: the API's adoption was already correct.

    `main.py` keeps its own construction and its middleware trigger; the worker
    helper must not have been slipped underneath it.
    """

    main_tree = ast.parse(
        (_BACKEND / "src" / "return_platform" / "main.py").read_text(encoding="utf-8")
    )
    called = _called_function_names(main_tree)
    assert "RuntimeConfigurationActivator" in called
    assert "build_worker_runtime_activation" not in called
    assert "run_runtime_activation_loop" not in called


def test_vault_resolution_is_the_production_symbol() -> None:
    """The stub above patches the name the activator actually calls."""

    assert _production_vault_resolution.__module__ == "return_platform.secrets.runtime"
