"""One construction of the targeted-sync stack, for every process that needs it.

`build_dynamic_order_agent_runtime` assembled this inline, so it existed only
inside the Order Discovery worker. W2.6 needs it in the orchestrator and W2.5
needs it in the return-case worker, and copying the assembly into two more
composition roots is how a connector registry, an extractor secret or a
generation-handle wiring ends up subtly different per process -- three
behaviours from one design, decided by which script started.

Two things here are not obvious and are load-bearing:

* **Platform-store sources get their own connector.** A `MongoDBSourceScanConnector`
  is bound to one database for its lifetime and ignores what the source
  declares, so `source_return_records` -- which names the platform's own
  database -- resolved to the upstream connector and read a collection that is
  not there. That is an empty page, not an error, so a targeted sync of a return
  record reported SUCCEEDED having written nothing. `platform_store_source_ids`
  already decides this for the scheduled sync; the same rule is applied here so
  both halves agree.
* **The extractor is built with the same resolved secrets the scheduled sync
  uses.** A derived field that needs one would otherwise raise mid-turn for a
  document the scheduled pipeline projects without complaint.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from neo4j import AsyncDriver
from pymongo import AsyncMongoClient

from return_platform.configuration.settings import Settings
from return_platform.dynamic_knowledge.config_loader import resolve_active_schema
from return_platform.dynamic_knowledge.graph.generation_writer import Neo4jGenerationWriter
from return_platform.dynamic_knowledge.graph.neo4j_writer import Neo4jDynamicGraphWriter
from return_platform.dynamic_knowledge.graph.projector import GenericGraphProjector
from return_platform.dynamic_knowledge.integration.mongo_store import (
    MongoGraphStateProvider,
    MongoOnDemandSyncStore,
)
from return_platform.dynamic_knowledge.integration.neo4j_gateway import Neo4jKnowledgeGateway
from return_platform.dynamic_knowledge.integration.on_demand_sync_adapters import (
    OnDemandNeo4jGraphWriter,
    targeted_connector_registry,
)
from return_platform.dynamic_knowledge.knowledge.cypher_compiler import CypherCompiler
from return_platform.dynamic_knowledge.lifecycle.handle import GenerationHandleProvider
from return_platform.dynamic_knowledge.lifecycle.lease_store import (
    GENERATION_LEASES_COLLECTION,
    MongoGenerationLeaseStore,
)
from return_platform.dynamic_knowledge.lifecycle.mongo_store import (
    ACTIVE_RUNTIME_SNAPSHOTS_COLLECTION,
    MongoActiveRuntimeSnapshotStore,
)
from return_platform.dynamic_knowledge.on_demand_sync.coordinator import (
    OnDemandSyncCoordinator,
    TargetedSyncRunLedger,
)
from return_platform.dynamic_knowledge.on_demand_sync.extraction import (
    GenericSourceRecordExtractor,
    contact_digest_secrets,
)
from return_platform.dynamic_knowledge.release_store import SchemaReleaseStore
from return_platform.dynamic_knowledge.schema import ActiveSchema, ConnectorType
from return_platform.source_connectors.mongodb import MongoDBSourceScanConnector
from return_platform.source_connectors.sqlserver import (
    SqlServerConnectionSettings,
    SqlServerSourceScanConnector,
)

__all__ = [
    "TargetedGraphAccess",
    "build_targeted_graph_access",
    "platform_store_source_ids",
]


def platform_store_source_ids(schema: ActiveSchema, platform_database: str) -> frozenset[str]:
    """Which Mongo sources name the platform's own database rather than upstream.

    Deliberately the same rule `GraphSyncService.platform_store_source_ids`
    applies to a scheduled run: a source reachable on a schedule but not on
    demand (or the reverse) is a discrepancy nothing announces, and it surfaces
    as a sync that succeeds having written nothing.

    Anything that is not an exact match keeps the upstream connector. The four
    Ferguson sources declare a database name that only equals
    `source_mongo_database` by default, and an operator who renamed it should
    not have targeted reads start failing.
    """
    return frozenset(
        source_id
        for source_id, source in schema.sources.items()
        if source.connector_type is ConnectorType.MONGODB
        and source.object_ref.get("database") == platform_database
    )


@dataclass(frozen=True, slots=True)
class TargetedGraphAccess:
    """Everything a process needs to sync one record and then read it back.

    Carries the lease store, snapshot store and owner id as well as the
    coordinator because `DynamicOrderAgentCoordinator` needs those same
    instances: a read lease taken by a turn and the write reservation taken by
    the sync inside it must be counted against one generation document and one
    drain, which only holds if both come from here.
    """

    schema: ActiveSchema
    on_demand_sync: OnDemandSyncCoordinator
    generation_handles: GenerationHandleProvider
    knowledge_gateway: Neo4jKnowledgeGateway
    compiler: CypherCompiler
    graph_state: MongoGraphStateProvider
    generation_lease_store: MongoGenerationLeaseStore
    active_snapshot_store: MongoActiveRuntimeSnapshotStore
    owner_instance_id: str


async def build_targeted_graph_access(
    *,
    settings: Settings,
    platform_mongo: AsyncMongoClient[dict[str, object]],
    source_mongo: AsyncMongoClient[dict[str, object]],
    neo4j_driver: AsyncDriver,
    owner_role: str,
    targeted_sync_runs: TargetedSyncRunLedger | None = None,
    schema: ActiveSchema | None = None,
) -> TargetedGraphAccess:
    """Assemble the stack once, from the published release where one exists.

    `owner_role` names the holder of a lease. There is no instance_id setting;
    `bootstrap/system_store.py` establishes a per-process uuid as the
    convention, and that is the right granularity -- a lease outlives a request
    but never the process that took it.

    `schema` is for a caller that has already resolved the release and must
    build against that exact one -- the Order Discovery worker's configuration
    reconciler, which decides whether to rebuild by comparing the resolved
    release against the running one. Resolving again here would let a release
    published between the two reads leave the rebuilt coordinator and the
    activity surface holding different schemas, with nothing to notice it
    because both would then look current. Everyone else omits it and resolves.
    """
    releases = SchemaReleaseStore(platform_mongo, settings.mongo_database)
    await releases.ensure_indexes()
    if schema is None:
        schema = await resolve_active_schema(settings.dynamic_knowledge_schema_path, releases)

    graph_state = MongoGraphStateProvider(platform_mongo, settings.mongo_database)
    await graph_state.ensure_indexes()

    generation_lease_store = MongoGenerationLeaseStore(
        platform_mongo[settings.mongo_database][GENERATION_LEASES_COLLECTION]
    )
    active_snapshot_store = MongoActiveRuntimeSnapshotStore(
        platform_mongo[settings.mongo_database][ACTIVE_RUNTIME_SNAPSHOTS_COLLECTION]
    )
    owner_instance_id = f"{owner_role}-{uuid4()}"

    sync_store = MongoOnDemandSyncStore(
        platform_mongo,
        settings.mongo_database,
        receipt_ttl_seconds=settings.on_demand_sync_receipt_ttl_seconds,
    )
    await sync_store.ensure_indexes()

    upstream_mongo = MongoDBSourceScanConnector(
        source_mongo[settings.source_mongo_database], schema=schema
    )
    platform_mongo_connector = MongoDBSourceScanConnector(
        platform_mongo[settings.mongo_database], schema=schema
    )
    connectors = targeted_connector_registry(
        schema=schema,
        mongo=upstream_mongo,
        sqlserver=SqlServerSourceScanConnector(
            SqlServerConnectionSettings(
                server=settings.sqlserver_host,
                port=settings.sqlserver_port,
                user=settings.sqlserver_user,
                password=settings.sqlserver_password.get_secret_value(),
                database=settings.sqlserver_database,
                timeout_seconds=int(settings.operation_timeout_seconds),
            ),
            schema=schema,
        ),
        overrides={
            source_id: platform_mongo_connector
            for source_id in platform_store_source_ids(schema, settings.mongo_database)
        },
    )

    generation_handles = GenerationHandleProvider(
        graph_state,
        lease_store=generation_lease_store,
        owner_instance_id=owner_instance_id,
        snapshot_store=active_snapshot_store,
    )
    on_demand_sync = OnDemandSyncCoordinator(
        connectors=connectors,
        extractor=GenericSourceRecordExtractor(
            resolved_secrets=contact_digest_secrets(
                schema, hmac_key=settings.contact_lookup_hmac_key.get_secret_value()
            )
        ),
        projector=GenericGraphProjector(),
        writer=OnDemandNeo4jGraphWriter(
            Neo4jDynamicGraphWriter(neo4j_driver, database=settings.neo4j_database),
            Neo4jGenerationWriter(neo4j_driver, database=settings.neo4j_database),
        ),
        store=sync_store,
        # Supplied by the composition root rather than built here: the ledger
        # writes a `data_platform` collection, and `dynamic_knowledge` does not
        # depend on that package.
        run_ledger=targeted_sync_runs,
        generation_handles=generation_handles,
    )
    return TargetedGraphAccess(
        schema=schema,
        on_demand_sync=on_demand_sync,
        generation_handles=generation_handles,
        knowledge_gateway=Neo4jKnowledgeGateway(neo4j_driver, database=settings.neo4j_database),
        compiler=CypherCompiler(),
        graph_state=graph_state,
        generation_lease_store=generation_lease_store,
        active_snapshot_store=active_snapshot_store,
        owner_instance_id=owner_instance_id,
    )
