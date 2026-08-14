"""Run the Order Discovery workflow worker as a dedicated Docker process.

The Order Agent's reasoning runs *here*, not in the API process, so this is
where an activated configuration release has to land. Without the reconciler
below, an administrator switching the discovery model in the AI Control Centre
would see the API confirm the new route while every subsequent agent turn still
reached the old provider -- the API and this worker disagreeing about what is
live, for as long as nobody restarted the container (T-16).
"""

import asyncio
import socket
import uuid
from dataclasses import dataclass

from neo4j import AsyncDriver, AsyncGraphDatabase
from pymongo import AsyncMongoClient
from temporalio.client import Client

from return_platform.ai.interception.store import SystemStoreInterceptionStore
from return_platform.ai.providers.replay_store import SystemStoreReplayStore
from return_platform.ai_gateway.routing import AIRoutePool, build_routes
from return_platform.bootstrap.system_store import bootstrap_system_store
from return_platform.configuration.runtime_activation import (
    ActivationContext,
    build_worker_runtime_activation,
)
from return_platform.configuration.runtime_loader import resolve_process_configuration
from return_platform.data_platform.graph.sync_service import MongoTargetedSyncRunLedger
from return_platform.dynamic_knowledge.config_loader import resolve_active_schema
from return_platform.dynamic_knowledge.integration.runtime_factory import (
    build_dynamic_order_agent_runtime,
)
from return_platform.dynamic_knowledge.release_store import SchemaReleaseStore
from return_platform.dynamic_knowledge.schema import ActiveSchema
from return_platform.operations.repository import OperationalRepository
from return_platform.platform.secrets.envelope import EnvelopeEncryptor
from return_platform.platform.system_store.repository import SystemStore
from return_platform.workflows.order_discovery_activities import (
    OrderDiscoveryActivities,
    OrderDiscoveryRuntime,
)
from return_platform.workflows.order_discovery_worker import create_order_discovery_worker

_PROCESS_CLASS = "order-discovery-worker"


class _OrderDiscoveryStackParticipant:
    """Rebuild the reasoning stack when the release it was built from changes.

    Routes and models need nothing here: the activator calls `replace_routes` on
    the pool this process handed it, and that pool is the same object the
    coordinator's invoker holds, so a provider change reaches an in-flight turn's
    next attempt with no rebuild at all.

    What does need rebuilding is everything that froze an `ActiveSchema` at
    construction -- the compiler, the knowledge gateway, the connectors, the
    guards' view of agent policies. The schema has its own activation pointer in
    Platform Mongo rather than living in the graph release, so this participant
    is asked on every poll and answers `None` for the ones where nothing moved.

    Rebuilding is not the same as restarting: the replacement is published as one
    frozen pair, so a turn already inside `run_order_discovery_turn` finishes on
    the release it started with (and on the graph generation it pinned), while
    the next activity task adopts the new one.
    """

    def __init__(
        self,
        *,
        activities: OrderDiscoveryActivities,
        platform_mongo: AsyncMongoClient[dict[str, object]],
        source_mongo: AsyncMongoClient[dict[str, object]],
        neo4j_driver: AsyncDriver,
        route_pool: AIRoutePool,
        system_store: SystemStore,
        envelope_encryptor: EnvelopeEncryptor,
        releases: SchemaReleaseStore,
        targeted_sync_runs: MongoTargetedSyncRunLedger,
        temporal_client: Client,
        adopted_graph_release: tuple[str, int],
    ) -> None:
        self._activities = activities
        self._platform_mongo = platform_mongo
        self._source_mongo = source_mongo
        self._neo4j_driver = neo4j_driver
        self._route_pool = route_pool
        self._system_store = system_store
        self._envelope_encryptor = envelope_encryptor
        self._releases = releases
        self._targeted_sync_runs = targeted_sync_runs
        self._temporal_client = temporal_client
        self._adopted_graph_release = adopted_graph_release

    async def prepare(self, context: ActivationContext) -> object | None:
        if context.ai_gateway_configuration is None:
            raise RuntimeError("Order Discovery worker activation has no AI gateway configuration")
        schema = await resolve_active_schema(
            context.settings.dynamic_knowledge_schema_path, self._releases
        )
        graph_release = (context.snapshot.release_id, context.snapshot.head_revision)
        if not self._changed(schema, graph_release):
            return None
        coordinator = await build_dynamic_order_agent_runtime(
            settings=context.settings,
            platform_mongo=self._platform_mongo,
            source_mongo=self._source_mongo,
            neo4j_driver=self._neo4j_driver,
            ai_gateway_configuration=context.ai_gateway_configuration,
            route_pool=self._route_pool,
            system_store=self._system_store,
            reasoning_encryptor=self._envelope_encryptor,
            targeted_sync_runs=self._targeted_sync_runs,
            temporal_client=self._temporal_client,
            # From the release being adopted, not the one the process booted on
            # (WF-01 + T-16): a case already confirmed carries the timings
            # stamped onto it at confirmation, and a case confirmed after this
            # swap is new work and gets the new active release's.
            return_case_timings=context.return_configuration.configuration.return_case,
            # The release this rebuild is *for*. Letting the factory resolve
            # again would let a publish landing between the two reads leave the
            # coordinator and the activity surface on different schemas.
            schema=schema,
        )
        return _PreparedStack(
            runtime=OrderDiscoveryRuntime(coordinator=coordinator, schema=schema),
            graph_release=graph_release,
        )

    def publish(self, prepared: object) -> None:
        if not isinstance(prepared, _PreparedStack):
            raise TypeError("Order Discovery activation published an unexpected replacement")
        # The adopted marker moves with the swap, not with the decision to
        # rebuild: a prepare that raised must leave this participant willing to
        # try the same release again on the next poll.
        self._adopted_graph_release = prepared.graph_release
        self._activities.adopt(prepared.runtime)

    def _changed(self, schema: ActiveSchema, graph_release: tuple[str, int]) -> bool:
        """Whether either of this stack's two inputs moved.

        The checksum as well as the release id, because the id names a release
        and the checksum is what the release actually said. The graph release
        too, because a rebuild reads settings that come from there rather than
        from the schema -- the contact-digest HMAC key, the sync receipt TTL,
        the source database names.
        """

        current = self._activities.runtime.schema
        return (
            current.configuration_release_id != schema.configuration_release_id
            or current.configuration_checksum != schema.configuration_checksum
            or self._adopted_graph_release != graph_release
        )


@dataclass(frozen=True, slots=True)
class _PreparedStack:
    """A rebuilt stack and the graph release it was built under."""

    runtime: OrderDiscoveryRuntime
    graph_release: tuple[str, int]


async def _run() -> None:
    runtime = await resolve_process_configuration()
    settings = runtime.settings
    platform_mongo: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        settings.mongo_dsn.get_secret_value()
    )
    source_dsn = (
        settings.source_mongo_dsn.get_secret_value()
        if settings.source_mongo_dsn is not None
        else settings.mongo_dsn.get_secret_value()
    )
    source_mongo: AsyncMongoClient[dict[str, object]] = (
        platform_mongo
        if source_dsn == settings.mongo_dsn.get_secret_value()
        else AsyncMongoClient[dict[str, object]](source_dsn)
    )
    neo4j_driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value()),
    )
    try:
        await platform_mongo.admin.command("ping")
        if source_mongo is not platform_mongo:
            await source_mongo.admin.command("ping")
        await neo4j_driver.verify_connectivity()

        # Connected before the coordinator rather than after it: this is the
        # process that runs CONFIRM_ORDER, and confirmation now starts the
        # case's `ReturnCaseWorkflow` (WF-01), so the client is a dependency of
        # the coordinator instead of something only the worker needs.
        temporal = await Client.connect(settings.temporal_target)

        system_store, envelope_encryptor = await bootstrap_system_store(settings, platform_mongo)
        # This is the process that actually runs MANUAL reasoning turns, and it
        # already has a SystemStore before routes are built -- so MANUAL resolves
        # to the durable interception store here rather than to
        # ManualFileProvider's `.manual_llm/` directory, which would be relative
        # to whatever CWD the worker container happened to start in.
        interception_store = SystemStoreInterceptionStore(system_store, envelope_encryptor)
        replay_store = SystemStoreReplayStore(system_store, envelope_encryptor)
        route_pool = AIRoutePool(
            build_routes(
                settings,
                interception_store=interception_store,
                replay_store=replay_store,
            ),
            runtime.ai_gateway_configuration.configuration,
        )
        # Composed here rather than inside the factory: the ledger writes a
        # `data_platform` collection and `dynamic_knowledge` does not import
        # that package. Without it a targeted sync still runs and still
        # records its receipt -- it just never appears on the sync screen,
        # which is how an agent-initiated write to the graph stayed
        # invisible to operators.
        targeted_sync_runs = MongoTargetedSyncRunLedger(platform_mongo, settings.mongo_database)
        releases = SchemaReleaseStore(platform_mongo, settings.mongo_database)
        await releases.ensure_indexes()
        # Resolved once and handed to both halves, so the schema the guards use
        # is the schema the coordinator compiled against.
        schema = await resolve_active_schema(settings.dynamic_knowledge_schema_path, releases)
        coordinator = await build_dynamic_order_agent_runtime(
            settings=settings,
            platform_mongo=platform_mongo,
            source_mongo=source_mongo,
            neo4j_driver=neo4j_driver,
            ai_gateway_configuration=runtime.ai_gateway_configuration,
            route_pool=route_pool,
            system_store=system_store,
            reasoning_encryptor=envelope_encryptor,
            targeted_sync_runs=targeted_sync_runs,
            temporal_client=temporal,
            # Pinned onto every case this process confirms. Read from the
            # release this coordinator was built for, so a case carries the
            # timings that were active when it was confirmed -- and a rebuild on
            # activation gives *new* cases the new timings while cases already
            # confirmed keep the ones stamped onto them.
            return_case_timings=runtime.return_configuration.configuration.return_case,
            schema=schema,
        )
        activities = OrderDiscoveryActivities(coordinator=coordinator, schema=schema)

        worker = create_order_discovery_worker(temporal, activities)
        operational_repository = OperationalRepository(platform_mongo, settings)
        instance_id = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"

        # The same reconciler the FastAPI process runs, driven by a timer
        # instead of by request middleware. The route pool and the two AI
        # stores are handed over so a live route rebuild swaps *this* pool and
        # keeps MANUAL on the console rather than rebuilding it as the
        # filesystem provider.
        activation = await build_worker_runtime_activation(
            runtime=runtime,
            process_class=_PROCESS_CLASS,
            instance_id=instance_id,
            mongo=platform_mongo,
            source_mongo=source_mongo,
            neo4j_driver=neo4j_driver,
            route_pool=route_pool,
            interception_store=interception_store,
            replay_store=replay_store,
            participants=(
                _OrderDiscoveryStackParticipant(
                    activities=activities,
                    platform_mongo=platform_mongo,
                    source_mongo=source_mongo,
                    neo4j_driver=neo4j_driver,
                    route_pool=route_pool,
                    system_store=system_store,
                    envelope_encryptor=envelope_encryptor,
                    releases=releases,
                    targeted_sync_runs=targeted_sync_runs,
                    temporal_client=temporal,
                    adopted_graph_release=(
                        runtime.snapshot.release_id,
                        runtime.snapshot.head_revision,
                    ),
                ),
            ),
        )
        state = activation.state

        async def heartbeat() -> None:
            while True:
                await operational_repository.heartbeat(
                    _PROCESS_CLASS,
                    instance_id,
                    # Read through the activated state rather than off the
                    # startup settings, so a released TTL change takes effect
                    # without a restart like everything else here.
                    ttl_seconds=state.settings.worker_readiness_ttl_seconds,
                )
                await asyncio.sleep(max(1.0, state.settings.worker_readiness_ttl_seconds / 3))

        background = (asyncio.create_task(heartbeat()), *activation.start())
        try:
            await worker.run()
        finally:
            for task in background:
                task.cancel()
            await asyncio.gather(*background, return_exceptions=True)
            await activation.aclose()
    finally:
        await neo4j_driver.close()
        if source_mongo is not platform_mongo:
            await source_mongo.close()
        await platform_mongo.close()


if __name__ == "__main__":
    asyncio.run(_run())
