"""Read one warehouse and its bays from the graph, syncing on demand first (W2.7).

The same two halves as `shipment_observations`, with the same split of failure
policy, because they are answering the same shape of question:

* **The sync is advisory.** A scheduled sync may already have brought the
  warehouse in, and a SQL Server outage is not a reason to refuse to look at what
  the graph already holds. A skipped or failed sync is recorded and the read
  proceeds.
* **The read decides the answer.** If the graph cannot be read there is no
  evidence either way, so this raises rather than reporting an absent warehouse.
  Reporting `ABSENT` on an outage would mark every return's bay omitted across
  the deployment and look like configuration.

**One anchored read brings both entities in.** `warehouse` and `bay` are both
bound to `platform.bay_configuration`, and `GenericSourceRecordExtractor` runs
every entity bound to a source over each document a targeted read returns. So a
read anchored on `warehouse.warehouse_id` compiles to
`... WHERE warehouse_id = ?`, comes back with that warehouse's bay rows, and
projects one `Warehouse` node and its `Bay` nodes from them. There is no second
sync for bays and no anchor on `bay.warehouse_id`, which could not honestly
declare a `maximum_expected_matches` anyway.

**Two reads, not a traversal.** "Is there such a warehouse" and "what bays does
it have" are separate questions, and a traversal answers them as one: an empty
result would mean either. Asking separately is what makes
`WAREHOUSE_NOT_IN_GRAPH` distinguishable from a warehouse whose bays are all
inactive -- and the second is a state a real warehouse is legitimately in.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from return_platform.dynamic_knowledge.fingerprint import on_demand_request_digest
from return_platform.dynamic_knowledge.integration.targeted_sync import TargetedGraphAccess
from return_platform.dynamic_knowledge.knowledge.cypher_compiler import CypherCompiler
from return_platform.dynamic_knowledge.knowledge.query_plan import (
    LogicalQueryPlan,
    QueryCondition,
    QueryOperation,
)
from return_platform.dynamic_knowledge.lifecycle.handle import GenerationHandleProvider
from return_platform.dynamic_knowledge.on_demand_sync.coordinator import OnDemandSyncCoordinator
from return_platform.dynamic_knowledge.on_demand_sync.planner import build_targeted_read_plan
from return_platform.dynamic_knowledge.schema import (
    ActiveSchema,
    EntitySourceAccess,
    RuntimeMode,
)
from return_platform.operations.warehouse.observations import (
    BayEvidence,
    WarehouseObservation,
)

__all__ = [
    "BAY_ENTITY_ID",
    "WAREHOUSE_ANCHOR_ID",
    "WAREHOUSE_ENTITY_ID",
    "WAREHOUSE_FIELD_ID",
    "GraphWarehouseBayObservations",
]

logger = logging.getLogger("return_platform.dynamic_knowledge.bay_observations")

#: The `platform.bay_configuration` -> `warehouse`/`bay` binding W2.4 compiled
#: into the descriptor. Named here rather than passed in for the reason
#: `shipment_observations` names its own: an entity id is schema vocabulary, and
#: a caller free to supply a different one is free to point bay placement at
#: something that is not a bay.
WAREHOUSE_ENTITY_ID = "warehouse"
BAY_ENTITY_ID = "bay"
WAREHOUSE_FIELD_ID = "warehouse_id"
WAREHOUSE_ANCHOR_ID = "exact_warehouse_id"

#: The bay properties placement ranks on. Every one is a column
#: `platform.bay_configuration` declares; nothing derived and nothing live --
#: see `WarehouseObservation` on why reserved capacity is not here.
_BAY_FIELDS = (
    "bay_id",
    "bay_name",
    "bay_type",
    "warehouse_id",
    "branch_id",
    "active",
    "priority",
    "supported_shipping_paths",
    "supported_product_types",
    "hazardous_allowed",
    "oversized_allowed",
    "max_package_count",
    "max_handling_unit_count",
    "capacity_unit",
)


class GraphReadExecutor(Protocol):
    """`Neo4jKnowledgeGateway.execute`, and nothing else from it."""

    async def execute(
        self,
        *,
        schema: ActiveSchema,
        graph_generation_id: str,
        plan: Any,
        compiled_cypher: str,
        parameters: dict[str, Any],
    ) -> Any: ...


class GraphWarehouseBayObservations:
    """Satisfies `operations.warehouse.observations.WarehouseBayObservationPort`."""

    def __init__(
        self,
        *,
        schema: ActiveSchema,
        on_demand_sync: OnDemandSyncCoordinator,
        generation_handles: GenerationHandleProvider,
        knowledge_gateway: GraphReadExecutor,
        compiler: CypherCompiler | None = None,
        tenant_scope: str = "platform",
    ) -> None:
        self._schema = schema
        self._on_demand_sync = on_demand_sync
        self._generation_handles = generation_handles
        self._knowledge_gateway = knowledge_gateway
        self._compiler = compiler or CypherCompiler()
        self._tenant_scope = tenant_scope

    @classmethod
    def from_access(
        cls, access: TargetedGraphAccess, *, tenant_scope: str = "platform"
    ) -> GraphWarehouseBayObservations:
        return cls(
            schema=access.schema,
            on_demand_sync=access.on_demand_sync,
            generation_handles=access.generation_handles,
            knowledge_gateway=access.knowledge_gateway,
            compiler=access.compiler,
            tenant_scope=tenant_scope,
        )

    async def observe(self, warehouse_reference: str | None) -> WarehouseObservation:
        if warehouse_reference is None:
            # Refused before any lease is taken. This is the case the SQL query
            # answered with every bay in the estate; there is nothing to look up
            # and nothing to guess.
            return WarehouseObservation(
                warehouse_reference=None,
                evidence=BayEvidence.ABSENT,
                absent_reason="NO_WAREHOUSE_REFERENCE",
            )
        schema = self._schema
        async with self._generation_handles.acquire_read(schema) as handle:
            generation = handle.graph_generation_id
            sync_request_id, skipped = await self._synchronize(
                schema, generation, warehouse_reference
            )
            warehouse = await self._read_warehouse(schema, generation, warehouse_reference)
            candidates = (
                ()
                if warehouse is None
                else await self._read_bays(schema, generation, warehouse_reference)
            )
        if warehouse is None:
            return WarehouseObservation(
                warehouse_reference=warehouse_reference,
                evidence=BayEvidence.ABSENT,
                graph_generation_id=generation,
                absent_reason="WAREHOUSE_NOT_IN_GRAPH",
                sync_request_id=sync_request_id,
                sync_skipped_reason=skipped,
            )
        return WarehouseObservation(
            warehouse_reference=warehouse_reference,
            evidence=BayEvidence.OBSERVED,
            graph_generation_id=generation,
            candidates=candidates,
            sync_request_id=sync_request_id,
            sync_skipped_reason=skipped,
        )

    async def _synchronize(
        self, schema: ActiveSchema, generation: str, warehouse_reference: str
    ) -> tuple[str | None, str | None]:
        """Bring this one warehouse's bay rows in, or say why not.

        Every check is a read of configuration rather than a branch on a source's
        name, so demoting `warehouse` stops the sync without a code change --
        which is the property that made the same checks in
        `shipment_observations` worth keeping after `shipment` was promoted.
        """
        if schema.runtime_mode is not RuntimeMode.CONNECTED_SYNC:
            return None, f"RUNTIME_MODE_{schema.runtime_mode.value}"
        entity = schema.entities.get(WAREHOUSE_ENTITY_ID)
        if entity is None:
            return None, "WAREHOUSE_ENTITY_ABSENT"
        if entity.source_access is not EntitySourceAccess.CONNECTED_SYNC:
            return None, f"SOURCE_ACCESS_{entity.source_access.value}"
        anchors = {WAREHOUSE_FIELD_ID: ("EXACT", warehouse_reference)}
        try:
            plan = build_targeted_read_plan(
                schema=schema,
                entity_id=WAREHOUSE_ENTITY_ID,
                normalized_anchors=anchors,
            )
            receipt = await self._on_demand_sync.synchronize(
                schema=schema,
                graph_generation_id=generation,
                request_digest=on_demand_request_digest(
                    tenant_scope=self._tenant_scope,
                    source_asset_id=plan.source_asset_id,
                    entity_id=WAREHOUSE_ENTITY_ID,
                    strong_anchor_id=WAREHOUSE_ANCHOR_ID,
                    normalized_anchors={WAREHOUSE_FIELD_ID: warehouse_reference},
                    schema_version=schema.schema_version,
                    graph_generation_id=generation,
                    mapping_version=schema.compiler_version,
                ),
                plan=plan,
            )
        except Exception as error:  # noqa: BLE001 - advisory half; the read still runs
            logger.warning(
                "warehouse_bay_sync_failed",
                extra={"error_code": type(error).__name__},
                exc_info=True,
            )
            return None, f"SYNC_FAILED_{type(error).__name__.upper()}"
        return receipt.sync_request_id, None

    async def _read_warehouse(
        self, schema: ActiveSchema, generation: str, warehouse_reference: str
    ) -> dict[str, Any] | None:
        rows = await self._execute(
            schema,
            generation,
            LogicalQueryPlan(
                operation=QueryOperation.FILTER,
                start_entity_id=WAREHOUSE_ENTITY_ID,
                fields=(WAREHOUSE_FIELD_ID, "branch_id"),
                filters=(
                    QueryCondition(
                        entity_id=WAREHOUSE_ENTITY_ID,
                        field_id=WAREHOUSE_FIELD_ID,
                        operator="EQUALS",
                        value=warehouse_reference,
                    ),
                ),
                # One node per warehouse id -- the node key makes it so. A second
                # row would be a key violation, and taking the first quietly is
                # how that would stay unnoticed.
                limit=2,
            ),
        )
        if not rows:
            return None
        if len(rows) > 1:
            logger.warning(
                "warehouse_observation_ambiguous",
                extra={"row_count": len(rows), "graph_generation_id": generation},
            )
        return rows[0]

    async def _read_bays(
        self, schema: ActiveSchema, generation: str, warehouse_reference: str
    ) -> tuple[dict[str, Any], ...]:
        """Every bay the graph holds for this warehouse, active or not.

        Inactive bays are returned rather than filtered here. Eligibility is the
        bay-assignment agent's decision and it already tests `active`; dropping
        them at the read would make "this warehouse has no bay configured" and
        "every bay is switched off" the same answer -- which is the distinction
        this module exists to keep.
        """
        return tuple(
            await self._execute(
                schema,
                generation,
                LogicalQueryPlan(
                    operation=QueryOperation.FILTER,
                    start_entity_id=BAY_ENTITY_ID,
                    fields=_BAY_FIELDS,
                    filters=(
                        QueryCondition(
                            entity_id=BAY_ENTITY_ID,
                            field_id=WAREHOUSE_FIELD_ID,
                            operator="EQUALS",
                            value=warehouse_reference,
                        ),
                    ),
                    sort=(),
                    # The source's own lookup index is (warehouse_id, active,
                    # priority); a warehouse with more bays than this has a
                    # configuration problem, and truncating silently is worse
                    # than a list the agent ranks in full.
                    limit=200,
                ),
            )
        )

    async def _execute(
        self, schema: ActiveSchema, generation: str, plan: LogicalQueryPlan
    ) -> list[dict[str, Any]]:
        compiled = self._compiler.compile_read(schema, plan)
        result = await self._knowledge_gateway.execute(
            schema=schema,
            graph_generation_id=generation,
            plan=plan,
            compiled_cypher=compiled.cypher,
            parameters=compiled.parameters,
        )
        rows = result.get("rows") if isinstance(result, dict) else None
        if not rows:
            return []
        return [dict(row) for row in rows if isinstance(row, dict)]
