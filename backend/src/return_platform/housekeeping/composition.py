"""Building the five reclaimers from the release and settings that are live now.

Kept out of the reclaimers so none of them takes a `Settings` or a configuration
object: their rules are the part that must be provable without a database, a
Temporal server or a release, and a constructor that reached into configuration
would make every test of those rules a test of configuration loading as well.

Kept out of the worker script for the reason the deleted `run_data_job_worker.py`
demonstrates: a composition root that only ever runs inside a container is a
composition root nothing tests, and that one raised `ImportError` at every start
for weeks. These factories are ordinary functions, so
`tests/housekeeping/test_housekeeping_worker_composition.py` builds the real ones
and asserts the real rules.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from pymongo import AsyncMongoClient
from temporalio.client import Client

from return_platform.ai.interception.store import InterceptionExpirySweep
from return_platform.configuration.return_configuration import (
    HousekeepingConfiguration,
)
from return_platform.configuration.settings import Settings
from return_platform.dynamic_knowledge.lifecycle.lease_store import (
    GENERATION_LEASES_COLLECTION,
    MongoGenerationLeaseStore,
)
from return_platform.dynamic_knowledge.lifecycle.mongo_store import (
    ACTIVE_RUNTIME_SNAPSHOTS_COLLECTION,
)
from return_platform.housekeeping.cycle import HousekeepingCycle, Reclaimer
from return_platform.housekeeping.graph_adapter import Neo4jGenerationReclamationAdapter
from return_platform.housekeeping.graph_generations import (
    RESOURCE_CLASS as GRAPH_GENERATION_CLASS,
)
from return_platform.housekeeping.graph_generations import (
    GraphGenerationReclaimer,
)
from return_platform.housekeeping.interception_expiry import (
    RESOURCE_CLASS as AI_INTERCEPTION_CLASS,
)
from return_platform.housekeeping.interception_expiry import (
    InterceptionExpiryReclaimer,
)
from return_platform.housekeeping.order_line_reservations import (
    RESOURCE_CLASS as ORDER_LINE_RESERVATION_CLASS,
)
from return_platform.housekeeping.order_line_reservations import (
    OrderLineReservationReclaimer,
)
from return_platform.housekeeping.probe_databases import (
    RESOURCE_CLASS as PROBE_DATABASE_CLASS,
)
from return_platform.housekeeping.probe_databases import ProbeDatabaseReclaimer
from return_platform.housekeeping.temporal_executions import (
    RESOURCE_CLASS as TEMPORAL_EXECUTION_CLASS,
)
from return_platform.housekeeping.temporal_executions import (
    TemporalExecutionReclaimer,
    deployed_task_queues,
)
from return_platform.operations.repository import OperationalRepository
from return_platform.platform.system_store.manifest_loader import (
    load_system_store_config,
    structure_definitions,
)
from return_platform.platform.system_store.repository import SystemStore

__all__ = [
    "MongoActiveGenerationReader",
    "build_housekeeping_cycle",
    "build_interception_expiry_sweep",
    "build_probe_database_connector",
]


class MongoActiveGenerationReader:
    """Every generation named by any document in the snapshot collection.

    A projection over the whole collection rather than a read of one named
    snapshot: the set of snapshot names is not this module's to know, and a
    generation referenced by a snapshot nobody told housekeeping about is exactly
    as live as one referenced by `ORDER_DISCOVERY`.
    """

    def __init__(self, collection: Any) -> None:
        self._collection = collection

    async def active_generation_ids(self) -> frozenset[str]:
        cursor = self._collection.find({}, {"graph_generation_id": 1})
        ids: set[str] = set()
        async for document in cursor:
            value = document.get("graph_generation_id")
            if isinstance(value, str) and value:
                ids.add(value)
        return frozenset(ids)


def build_probe_database_connector(settings: Settings) -> Callable[[str], Any]:
    """A `pymssql` connector bound to the configured instance.

    Imported inside the function so this module can be imported -- and the
    factories below exercised -- on a machine without the SQL Server driver
    built. The reclaimer never calls it in an environment where it is gated off,
    so a missing driver must not be an import-time failure for the whole worker.
    """

    def connect(database: str) -> Any:
        import pymssql

        return pymssql.connect(
            server=settings.sqlserver_host,
            port=str(settings.sqlserver_port),
            user=settings.sqlserver_user,
            password=settings.sqlserver_password.get_secret_value(),
            database=database,
            login_timeout=10,
            timeout=30,
            autocommit=True,
        )

    return connect


def build_interception_expiry_sweep(
    settings: Settings, mongo: AsyncMongoClient[dict[str, object]]
) -> InterceptionExpirySweep:
    """A sweep bound to the `ai_interceptions` structure the manifest declares.

    **Resolved through the manifest, never by physical name.** Logical naming is
    mandatory for system-store access, and a reaper that hardcoded
    `platform_ai_interceptions` would be the one caller a collection rename did
    not reach.

    **Deliberately not `bootstrap_system_store`.** That runs migrations, takes a
    lease and builds an `AesGcmEnvelopeEncryptor` from the reasoning encryption
    key. This process creates no structures and opens no envelope -- it moves
    `status` on documents another process wrote -- so requiring it to hold the
    key that unseals held prompts, in order to reap records it must never read,
    would be exactly backwards.

    Built per pass like every other reclaimer here, so a released change to the
    manifest path is adopted without a restart. A manifest that cannot be loaded,
    or one that does not declare `ai_interceptions`, raises -- and the cycle
    records `configuration rejected` and reclaims nothing, which is the
    fail-closed direction this package chose everywhere else.
    """
    structures = structure_definitions(
        load_system_store_config(settings.system_store_manifest_path)
    )
    return InterceptionExpirySweep(
        SystemStore(
            mongo,
            {definition.logical_name: definition for definition in structures},
            database="platform",
        )
    )


def build_housekeeping_cycle(
    *,
    settings_provider: Callable[[], Settings],
    configuration_provider: Callable[[], HousekeepingConfiguration],
    temporal_client: Client | None,
    mongo: AsyncMongoClient[dict[str, object]] | None,
    neo4j_driver: Any | None,
    to_thread: Callable[..., Any] = asyncio.to_thread,
) -> HousekeepingCycle:
    """One cycle whose factories read the release on every pass.

    The providers are callables, not values, because the runtime activator
    replaces this process's `Settings` and configuration in place when a release
    is adopted. Capturing either here would pin housekeeping to the release the
    process booted on -- the exact defect `RuntimeConfigurationActivator` exists
    to remove.
    """

    def temporal_factory() -> Reclaimer | None:
        configuration = configuration_provider()
        if not configuration.enabled or not configuration.temporal_executions.enabled:
            return None
        if temporal_client is None:
            return None
        settings = settings_provider()
        block = configuration.temporal_executions
        return TemporalExecutionReclaimer(
            client=temporal_client,
            environment=settings.environment,
            protected_task_queues=deployed_task_queues(settings),
            reclaimable_task_queue_prefixes=block.reclaimable_task_queue_prefixes,
            minimum_age_seconds=block.minimum_age_seconds,
            batch_limit=block.batch_limit,
        )

    def generation_factory() -> Reclaimer | None:
        configuration = configuration_provider()
        if not configuration.enabled or not configuration.graph_generations.enabled:
            return None
        if mongo is None or neo4j_driver is None:
            return None
        settings = settings_provider()
        database = mongo[settings.mongo_database]
        block = configuration.graph_generations
        return GraphGenerationReclaimer(
            graph=Neo4jGenerationReclamationAdapter(neo4j_driver, database=settings.neo4j_database),
            lease_store=MongoGenerationLeaseStore(database[GENERATION_LEASES_COLLECTION]),
            active_generations=MongoActiveGenerationReader(
                database[ACTIVE_RUNTIME_SNAPSHOTS_COLLECTION]
            ),
            retention_seconds=block.retention_seconds,
            batch_limit=block.batch_limit,
            node_delete_batch_size=block.node_delete_batch_size,
            abandoned_build_seconds=block.abandoned_build_seconds,
            orphaned_active_seconds=block.orphaned_active_seconds,
        )

    def probe_database_factory() -> Reclaimer | None:
        configuration = configuration_provider()
        if not configuration.enabled or not configuration.probe_databases.enabled:
            return None
        settings = settings_provider()
        block = configuration.probe_databases
        return ProbeDatabaseReclaimer(
            connect=build_probe_database_connector(settings),
            to_thread=to_thread,
            environment=settings.environment,
            # Both the platform's own database and the AI Studio one. The second
            # is optional and is `None` on most deployments; the reclaimer drops
            # `None` entries rather than making every caller filter them.
            protected_database_names=(
                settings.sqlserver_database,
                settings.ai_studio_sqlserver_database,
            ),
            name_suffixes=block.name_suffixes,
            minimum_age_seconds=block.minimum_age_seconds,
            batch_limit=block.batch_limit,
        )

    def order_line_reservation_factory() -> Reclaimer | None:
        configuration = configuration_provider()
        if not configuration.enabled or not configuration.order_line_reservations.enabled:
            return None
        if mongo is None:
            return None
        settings = settings_provider()
        # Built per pass like every other reclaimer here, and cheap to build:
        # `OperationalRepository.__init__` binds collection handles and opens
        # nothing. Constructed rather than injected because the sweep's rules --
        # the `ACTIVE and past its deadline` predicate and the conditional update
        # that enforces the state machine -- belong to the repository beside the
        # lifecycle they implement, and a second implementation reachable from
        # here is exactly the drift this package avoids.
        return OrderLineReservationReclaimer(
            sweep=OperationalRepository(mongo, settings),
            batch_limit=configuration.order_line_reservations.batch_limit,
        )

    def interception_expiry_factory() -> Reclaimer | None:
        configuration = configuration_provider()
        if not configuration.enabled or not configuration.ai_interceptions.enabled:
            return None
        if mongo is None:
            return None
        return InterceptionExpiryReclaimer(
            sweep=build_interception_expiry_sweep(settings_provider(), mongo),
            batch_limit=configuration.ai_interceptions.batch_limit,
        )

    return HousekeepingCycle(
        (
            (TEMPORAL_EXECUTION_CLASS, temporal_factory),
            (GRAPH_GENERATION_CLASS, generation_factory),
            (PROBE_DATABASE_CLASS, probe_database_factory),
            (ORDER_LINE_RESERVATION_CLASS, order_line_reservation_factory),
            (AI_INTERCEPTION_CLASS, interception_expiry_factory),
        )
    )
