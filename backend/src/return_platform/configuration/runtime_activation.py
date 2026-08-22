"""Atomic activation of published graph configuration in long-running processes.

One reconciler, two ways of driving it. The FastAPI process drives
`RuntimeConfigurationActivator.refresh` from request middleware; a worker has no
request to drive it, so `run_runtime_activation_loop` polls the same object on
the same 5-second guard. There is deliberately no second reconciler: an API and
a worker that disagreed about which release is live would be an administrator
switching the discovery model in the AI Control Centre, seeing the new route go
active, and every agent turn still reaching the old provider because the Order
Agent reasons inside the worker.

`ActivationParticipant` is the seam for configuration families that live outside
the graph release document -- today the published `ActiveSchema`, which has its
own activation pointer in Platform Mongo. Participants are adopted by this
reconciler, under this lock, behind this poll guard, so a process only ever has
one answer to "which configuration am I running".
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from neo4j import AsyncDriver, AsyncGraphDatabase
from pymongo import AsyncMongoClient

from return_platform.ai.interception.store import InterceptionStore
from return_platform.ai.providers.replay import ReplayStore
from return_platform.ai.routing.routes import build_routes
from return_platform.ai.routing.selection import AIRoutePool
from return_platform.ai.routing.tasks import (
    LoadedAIGatewayConfiguration,
    build_loaded_ai_gateway_configuration,
)
from return_platform.configuration.graph_repository import (
    ConfigurationGraphRepository,
    InMemoryConfigurationGraphRepository,
    Neo4jConfigurationGraphRepository,
)
from return_platform.configuration.process_adoption import (
    PROCESS_ADOPTIONS_COLLECTION,
    MongoProcessAdoptionStore,
    ProcessAdoptionStore,
    adoption_record_from_snapshot,
)
from return_platform.configuration.return_configuration import LoadedReturnConfiguration
from return_platform.configuration.runtime_integrations import (
    apply_graph_runtime_configuration,
)
from return_platform.configuration.runtime_loader import ResolvedProcessConfiguration
from return_platform.configuration.settings import Settings
from return_platform.configuration.snapshot import (
    ConfigurationSnapshotBuilder,
    PinnedConfigurationSnapshot,
)
from return_platform.dependency_simulation.configuration import (
    LoadedDependencySimulationConfiguration,
    build_loaded_dependency_simulation_configuration,
)
from return_platform.operations.repository import OperationalRepository
from return_platform.secrets.runtime import resolve_runtime_settings_from_vault
from return_platform.secrets.vault import VaultHTTPSecretResolver

logger = logging.getLogger("return_platform.configuration.runtime_activation")

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


@dataclass(frozen=True, slots=True)
class ActivationContext:
    """The release a participant is being asked to adopt.

    Carries the already-validated artifacts rather than letting a participant
    re-read them, so every participant in one activation adopts the same release
    and the snapshot's `release_id`/`head_revision` are the epoch a process
    reports having adopted (CFG-02).
    """

    snapshot: PinnedConfigurationSnapshot
    settings: Settings
    return_configuration: LoadedReturnConfiguration
    ai_gateway_configuration: LoadedAIGatewayConfiguration | None


class ActivationParticipant(Protocol):
    """Process-local state that has to be rebuilt when configuration changes.

    Two phases, because the activation boundary is worth nothing if half of a
    process can fail after the other half has already swapped. `prepare` does
    every fallible thing -- reads, validation, construction -- and returns the
    replacement without making it visible; `publish` is the assignment, runs
    inside the activator's own assignment block, and must not fail.

    Returning `None` from `prepare` means "nothing changed for me". A
    participant owning a configuration family with its own activation pointer
    (the published `ActiveSchema`) is called on every poll, including the polls
    where the graph release did not move, and answers `None` for all of them.
    """

    async def prepare(self, context: ActivationContext) -> object | None: ...

    def publish(self, prepared: object) -> None: ...


class ActivationResources(Protocol):
    """The two fields the activator needs from a process's resources.

    Structural rather than `RuntimeResources` so a worker does not have to build
    an asset catalogue and a probe manager to run the reconciler.
    `RuntimeResources` satisfies it as written.
    """

    settings: Settings
    mongo: AsyncMongoClient[dict[str, object]] | None
    source_mongo: AsyncMongoClient[dict[str, object]] | None


@dataclass(slots=True)
class ProcessRuntimeState:
    """A worker's equivalent of `app.state`, for the same activator.

    The attribute names are the ones `RuntimeConfigurationActivator` already
    reads and writes on the FastAPI app state -- this is the same contract, not
    a parallel one. It is passed as both `app_state` and `resources`, so the
    activator's two settings assignments land on one field and a worker cannot
    end up with a resource-level and a state-level answer that differ.

    `process_class` and `instance_id` are the identity the heartbeat already
    publishes. They live here so adoption reporting (CFG-02) has one place to
    read process class, instance and adopted release/epoch from -- the last two
    being `return_configuration_snapshot.release_id` and `.head_revision`.
    """

    process_class: str
    instance_id: str
    settings: Settings
    return_configuration: LoadedReturnConfiguration
    return_configuration_snapshot: PinnedConfigurationSnapshot | None = None
    ai_gateway_configuration: LoadedAIGatewayConfiguration | None = None
    dependency_simulation_configuration: LoadedDependencySimulationConfiguration | None = None
    secret_resolver: VaultHTTPSecretResolver | None = None
    ai_gateway_route_pool: AIRoutePool | None = None
    # Held so a live route rebuild keeps the durable stores this process started
    # with. Without them `build_routes` rebuilds MANUAL as the filesystem
    # provider and an operator waiting on the console waits for a prompt that
    # was written to a file in the container instead.
    ai_interception_store: InterceptionStore | None = None
    ai_replay_store: ReplayStore | None = None
    mongo: AsyncMongoClient[dict[str, object]] | None = None
    source_mongo: AsyncMongoClient[dict[str, object]] | None = None


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
        resources: ActivationResources | None,
        refresh_interval_seconds: float = 5.0,
        participants: Sequence[ActivationParticipant] = (),
    ) -> None:
        self._app_state = app_state
        self._repository = repository
        self._baseline_path = baseline_path
        self._ai_gateway_baseline_path = ai_gateway_baseline_path
        self._dependency_simulation_baseline_path = dependency_simulation_baseline_path
        self._resources = resources
        self._refresh_interval_seconds = refresh_interval_seconds
        self._participants = tuple(participants)
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
            if current is not None and (
                (
                    current.source == "NEO4J_CONFIGURATION_GRAPH"
                    and current.head_revision == head_revision
                )
                or (current.source == "VERSION_CONTROLLED_BASELINE" and head_revision == 0)
            ):
                # The graph release has not moved. Families with their own
                # activation pointer -- the published `ActiveSchema` -- still get
                # their turn here, because the alternative is a second poll loop
                # with its own guard and its own idea of what is live.
                self._publish_participants(await self._prepare_participants(current))
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
            # Before the route pool is touched, because `replace_routes` mutates
            # the live pool in place: a participant that fails to build its
            # replacement must leave this process on the release it was already
            # running, not on a half-adopted one.
            prepared_participants = await self._prepare_participants(
                snapshot,
                settings=activated_settings,
                return_configuration=loaded,
                ai_gateway_configuration=loaded_ai_gateway,
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
                    replay_store=getattr(self._app_state, "ai_replay_store", None),
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
            self._publish_participants(prepared_participants)
            self._app_state.return_configuration_snapshot = snapshot
            return snapshot

    def _current_snapshot(self) -> PinnedConfigurationSnapshot | None:
        value = getattr(self._app_state, "return_configuration_snapshot", None)
        return value if isinstance(value, PinnedConfigurationSnapshot) else None

    async def _prepare_participants(
        self,
        snapshot: PinnedConfigurationSnapshot,
        *,
        settings: Settings | None = None,
        return_configuration: LoadedReturnConfiguration | None = None,
        ai_gateway_configuration: LoadedAIGatewayConfiguration | None = None,
    ) -> tuple[tuple[ActivationParticipant, object], ...]:
        """Build every replacement, publish none of them.

        Called with explicit artifacts when a release is being adopted, and off
        the process's current state when it is not; either way a participant
        sees one coherent release. An exception here propagates: the caller has
        not swapped anything yet, so the process stays on its last good release.
        """

        if not self._participants:
            return ()
        state_settings = settings if settings is not None else self._state_settings()
        if state_settings is None:
            return ()
        context = ActivationContext(
            snapshot=snapshot,
            settings=state_settings,
            return_configuration=(
                return_configuration
                if return_configuration is not None
                else self._app_state.return_configuration
            ),
            ai_gateway_configuration=(
                ai_gateway_configuration
                if ai_gateway_configuration is not None
                else getattr(self._app_state, "ai_gateway_configuration", None)
            ),
        )
        prepared: list[tuple[ActivationParticipant, object]] = []
        for participant in self._participants:
            replacement = await participant.prepare(context)
            if replacement is not None:
                prepared.append((participant, replacement))
        return tuple(prepared)

    def _publish_participants(
        self, prepared: tuple[tuple[ActivationParticipant, object], ...]
    ) -> None:
        """Make every prepared replacement visible. Assignments only."""

        for participant, replacement in prepared:
            participant.publish(replacement)

    def _state_settings(self) -> Settings | None:
        value = getattr(self._app_state, "settings", None)
        if isinstance(value, Settings):
            return value
        return self._resources.settings if self._resources is not None else None


async def run_runtime_activation_loop(
    activator: RuntimeConfigurationActivator,
    *,
    interval_seconds: float = 5.0,
) -> None:
    """Drive the one reconciler in a process that has no request to drive it.

    This is not a second reconciler and holds no configuration state of its own:
    the API's middleware calls `refresh()` on every request and a worker calls
    it on a timer, and both are gated by the activator's own poll guard.

    A failed poll is logged and retried rather than terminating the worker --
    including the deliberate failure the activator raises when a release changes
    an infrastructure endpoint, which is restart-required and must leave the
    process on its last good release rather than take it down.
    """

    while True:
        try:
            await activator.refresh()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception(
                "runtime_configuration_refresh_failed_using_last_good_snapshot",
                extra={"error_type": type(exc).__name__},
            )
        await asyncio.sleep(interval_seconds)


class AdoptionReportingState(Protocol):
    """The three things a report is derived from.

    Read-only, and structural, so the FastAPI process can report through its own
    `app.state` without being given a second place to hold the snapshot it is
    already serving from.
    """

    @property
    def process_class(self) -> str: ...

    @property
    def instance_id(self) -> str: ...

    @property
    def return_configuration_snapshot(self) -> PinnedConfigurationSnapshot | None: ...


@dataclass(frozen=True, slots=True)
class ApplicationAdoptionState:
    """The API process's identity, reading its release off the live app state.

    A snapshot copied in here would be the release the process had when the
    reporter started, which is the one thing the report must never be.
    """

    process_class: str
    instance_id: str
    app_state: Any

    @property
    def return_configuration_snapshot(self) -> PinnedConfigurationSnapshot | None:
        value = getattr(self.app_state, "return_configuration_snapshot", None)
        return value if isinstance(value, PinnedConfigurationSnapshot) else None


async def run_process_adoption_reporter(
    state: AdoptionReportingState,
    store: ProcessAdoptionStore,
    *,
    interval_seconds: float = 5.0,
) -> None:
    """Say, repeatedly, which release this process is actually serving (C5).

    Repeatedly rather than once per activation, because the report has to expire.
    A process that adopted a release and then died must stop counting towards
    that release being live, and the only way silence reads as absence is if
    presence has to be restated.

    The release is read off the snapshot the process is serving, so there is no
    way for the reported release and the served release to diverge. A failure
    here is logged and retried: a process that cannot reach Mongo for a moment
    is behind on saying what it runs, which is not a reason to stop running it.
    """

    while True:
        try:
            snapshot = state.return_configuration_snapshot
            if snapshot is not None:
                await store.report(
                    adoption_record_from_snapshot(
                        process_class=state.process_class,
                        instance_id=state.instance_id,
                        snapshot=snapshot,
                    ),
                    ttl_seconds=max(1, int(interval_seconds)),
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception(
                "runtime_configuration_adoption_report_failed",
                extra={
                    "error_type": type(exc).__name__,
                    "process_class": state.process_class,
                },
            )
        await asyncio.sleep(interval_seconds)


class HeartbeatWriter(Protocol):
    """The one operation liveness reporting needs.

    Structural so this module does not import the operational repository at
    module scope -- `operations` already imports `configuration`, and a
    module-level import back would close the cycle.
    """

    async def heartbeat(self, worker_name: str, instance_id: str, *, ttl_seconds: int) -> None: ...


async def run_worker_heartbeat(
    writer: HeartbeatWriter,
    *,
    process_class: str,
    instance_id: str,
    ttl_seconds: int,
) -> None:
    """Say this process is alive, on its own task.

    A task rather than a call inside the work loop, which is how
    `outbox-publisher` writes it: a slow flush there stalls the heartbeat and a
    healthy worker reports stale. Liveness must not be hostage to the thing
    whose liveness it reports.

    It also logs, because "silent" and "dead" were indistinguishable. Four of
    five workers produced no output at all across a ninety-minute run -- their
    loops emit nothing, and nothing configures a handler -- so a log line here
    is the cheapest signal that a process is still turning.
    """
    interval = max(1.0, ttl_seconds / 3)
    while True:
        try:
            await writer.heartbeat(process_class, instance_id, ttl_seconds=ttl_seconds)
            logger.info(
                "worker_heartbeat",
                extra={"process_class": process_class, "instance_id": instance_id},
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a missed beat must never kill the worker
            logger.warning(
                "worker_heartbeat_failed",
                extra={"process_class": process_class},
                exc_info=True,
            )
        await asyncio.sleep(interval)


@dataclass(frozen=True, slots=True)
class WorkerRuntimeActivation:
    """A worker's activator, the state it activates into, and its own driver."""

    state: ProcessRuntimeState
    activator: RuntimeConfigurationActivator
    owned_driver: AsyncDriver | None
    adoption_store: ProcessAdoptionStore | None = None
    refresh_interval_seconds: float = 5.0
    #: Writes this process's liveness row. Started here rather than by each
    #: worker for the reason the adoption reporter is -- see `start`.
    heartbeat_writer: HeartbeatWriter | None = None
    heartbeat_ttl_seconds: int = 30

    def start(self) -> tuple[asyncio.Task[None], ...]:
        """Start reconciling, reporting and beating. All three, or half-wired.

        A process that adopts without reporting cannot be told apart from one
        that never adopted, and a release would sit at ACTIVATING forever with
        nothing wrong. Started together so a worker cannot wire one and forget
        the other.

        **The heartbeat joined them because exactly that happened.** Adoption
        was reported through this shared helper and the heartbeat was a loop
        each worker hand-rolled in its own entrypoint, so
        `integration-outbox-worker` got one and not the other: five workers
        reported `1/1 live` while `worker_heartbeats` held four documents, and
        the audit recorded the disagreement without a cause. A liveness signal
        every process must emit does not belong in a per-process loop that a new
        worker has to remember to copy.
        """

        tasks: list[asyncio.Task[None]] = [
            asyncio.create_task(
                run_runtime_activation_loop(
                    self.activator, interval_seconds=self.refresh_interval_seconds
                )
            )
        ]
        if self.adoption_store is not None:
            tasks.append(
                asyncio.create_task(
                    run_process_adoption_reporter(
                        self.state,
                        self.adoption_store,
                        interval_seconds=self.refresh_interval_seconds,
                    )
                )
            )
        if self.heartbeat_writer is not None:
            tasks.append(
                asyncio.create_task(
                    run_worker_heartbeat(
                        self.heartbeat_writer,
                        process_class=self.state.process_class,
                        instance_id=self.state.instance_id,
                        ttl_seconds=self.heartbeat_ttl_seconds,
                    )
                )
            )
        return tuple(tasks)

    async def aclose(self) -> None:
        """Close only a driver this helper opened; a supplied one is the caller's."""

        if self.owned_driver is not None:
            await self.owned_driver.close()


async def build_worker_runtime_activation(
    *,
    runtime: ResolvedProcessConfiguration,
    process_class: str,
    instance_id: str,
    mongo: AsyncMongoClient[dict[str, object]] | None = None,
    source_mongo: AsyncMongoClient[dict[str, object]] | None = None,
    neo4j_driver: AsyncDriver | None = None,
    route_pool: AIRoutePool | None = None,
    interception_store: InterceptionStore | None = None,
    replay_store: ReplayStore | None = None,
    participants: Sequence[ActivationParticipant] = (),
    refresh_interval_seconds: float = 5.0,
) -> WorkerRuntimeActivation:
    """Give one worker process the activator the API process already has.

    The configuration repository has to be the Neo4j one for adoption to mean
    anything -- the in-memory repository reports head revision 0 forever, so a
    process holding it never sees a release move. Falling back to it outside
    production mirrors `resolve_process_configuration`, which is what the
    process booted under; in production an unreachable graph fails closed here
    exactly as it does at startup.
    """

    settings = runtime.settings
    owned_driver: AsyncDriver | None = None
    driver = neo4j_driver
    if driver is None:
        owned_driver = AsyncGraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value()),
        )
        driver = owned_driver
    repository: ConfigurationGraphRepository
    try:
        await driver.verify_connectivity()
        repository = Neo4jConfigurationGraphRepository(driver)
    except Exception as exc:
        logger.exception("configuration_graph_unavailable_for_runtime_activation")
        if settings.environment == "production":
            if owned_driver is not None:
                await owned_driver.close()
            raise RuntimeError("Required Neo4j dependency is unavailable") from exc
        repository = InMemoryConfigurationGraphRepository()

    state = ProcessRuntimeState(
        process_class=process_class,
        instance_id=instance_id,
        settings=settings,
        return_configuration=runtime.return_configuration,
        return_configuration_snapshot=runtime.snapshot,
        ai_gateway_configuration=runtime.ai_gateway_configuration,
        dependency_simulation_configuration=runtime.dependency_simulation_configuration,
        secret_resolver=runtime.secret_resolver,
        ai_gateway_route_pool=route_pool,
        ai_interception_store=interception_store,
        ai_replay_store=replay_store,
        mongo=mongo,
        source_mongo=source_mongo,
    )
    adoption_store: ProcessAdoptionStore | None = None
    if mongo is not None:
        store = MongoProcessAdoptionStore(
            mongo[settings.mongo_database][PROCESS_ADOPTIONS_COLLECTION]
        )
        await store.ensure_indexes()
        adoption_store = store
    activator = RuntimeConfigurationActivator(
        app_state=state,
        repository=repository,
        baseline_path=runtime.return_configuration.path,
        ai_gateway_baseline_path=runtime.ai_gateway_configuration.path,
        dependency_simulation_baseline_path=runtime.dependency_simulation_configuration.path,
        # The same object as `app_state`, so the activator's two settings
        # assignments are one field and a worker cannot hold a resource-level
        # and a state-level answer that disagree.
        resources=state,
        refresh_interval_seconds=refresh_interval_seconds,
        participants=participants,
    )
    heartbeat_writer: HeartbeatWriter | None = None
    if mongo is not None:
        # Imported here rather than at module scope: `operations` imports this
        # package, so a top-level import would be a cycle.
        from return_platform.operations.repository import (  # noqa: PLC0415
            OperationalRepository,
        )

        heartbeat_writer = OperationalRepository(mongo, settings)
    return WorkerRuntimeActivation(
        state=state,
        activator=activator,
        owned_driver=owned_driver,
        heartbeat_writer=heartbeat_writer,
        heartbeat_ttl_seconds=settings.worker_readiness_ttl_seconds,
        adoption_store=adoption_store,
        refresh_interval_seconds=refresh_interval_seconds,
    )
