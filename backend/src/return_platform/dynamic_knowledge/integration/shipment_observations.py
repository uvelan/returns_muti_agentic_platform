"""Read one shipment from the graph, syncing it on demand first (W2.6).

Two halves with different failure policies, deliberately:

* **The sync is advisory.** A scheduled sync may already have brought the
  shipment in, and a source outage is not a reason to refuse to look at what is
  already there. A skipped or failed sync is recorded on the observation and the
  read goes ahead.
* **The read decides the answer.** If the graph cannot be read there is no
  evidence either way, and this raises rather than reporting an absent shipment
  -- reporting `ABSENT` on an outage would silently downgrade every return in
  the deployment to `AWAITING_HANDOFF` and look like data rather than breakage.
  The caller (`ReturnOrchestrator`) owns the `best_effort` policy and turns that
  exception into `UNAVAILABLE`.

The read goes through `CypherCompiler` on a `LogicalQueryPlan`, not through
hand-written Cypher, so it is subject to the same field allowlists and identifier
validation as an agent's query and it follows the descriptor when a path moves.
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
from return_platform.workflows.fulfillment_tracking import (
    ShipmentEvidence,
    ShipmentObservation,
)

__all__ = [
    "SHIPMENT_ENTITY_ID",
    "SHIPMENT_TRACKING_FIELD_ID",
    "GraphReadExecutor",
    "GraphShipmentObservations",
]

logger = logging.getLogger("return_platform.dynamic_knowledge.shipment_observations")

#: The `shipmentInfo` -> `shipment` -> `Shipment` binding already present in the
#: descriptor. Named here rather than passed in: an entity id is schema
#: vocabulary, and a caller free to supply a different one would be free to
#: point fulfillment at an entity that is not a shipment.
SHIPMENT_ENTITY_ID = "shipment"
SHIPMENT_TRACKING_FIELD_ID = "tracking_number"

#: Graph properties worth carrying back onto the observation. `current_status`
#: is the only authoritative "was it delivered" signal in the model -- salesInv
#: carries commit and ship dates but no proof of delivery.
#:
#: No carrier: verifying `shipmentInfo` against real documents established that
#: shipmentInfoEventData has no carrier field, and the carrier the source does
#: hold sits at a different grain the entity cannot reach. Asking for a property
#: the descriptor no longer declares would fail the compiler's field allowlist.
_STATUS_FIELD_ID = "current_status"
_SHIPMENT_ID_FIELD_ID = "shipment_id"


class GraphReadExecutor(Protocol):
    """`Neo4jKnowledgeGateway.execute`, and nothing else from it.

    Narrow so this adapter can be exercised against a real `CypherCompiler` and
    the real descriptor without a Neo4j driver -- what matters about the read is
    the plan and the compiled query, and a test that had to stand up a database
    to assert them would not be run.
    """

    async def execute(
        self,
        *,
        schema: ActiveSchema,
        graph_generation_id: str,
        plan: Any,
        compiled_cypher: str,
        parameters: dict[str, Any],
    ) -> Any: ...


class GraphShipmentObservations:
    """Satisfies `workflows.fulfillment_tracking.ShipmentObservationPort`."""

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
        # The sync idempotency digest is tenant-scoped so one tenant's sync
        # cannot answer another's request. Fulfillment runs per return rather
        # than per associate, so the scope is the platform unless a caller knows
        # better.
        self._tenant_scope = tenant_scope

    @classmethod
    def from_access(
        cls, access: TargetedGraphAccess, *, tenant_scope: str = "platform"
    ) -> GraphShipmentObservations:
        """What a composition root calls, having built the stack once."""
        return cls(
            schema=access.schema,
            on_demand_sync=access.on_demand_sync,
            generation_handles=access.generation_handles,
            knowledge_gateway=access.knowledge_gateway,
            compiler=access.compiler,
            tenant_scope=tenant_scope,
        )

    async def observe(self, tracking_reference: str) -> ShipmentObservation:
        schema = self._schema
        async with self._generation_handles.acquire_read(schema) as handle:
            generation = handle.graph_generation_id
            sync_request_id, skipped = await self._synchronize(
                schema, generation, tracking_reference
            )
            row = await self._read(schema, generation, tracking_reference)
        if row is None:
            return ShipmentObservation(
                tracking_reference=tracking_reference,
                evidence=ShipmentEvidence.ABSENT,
                graph_generation_id=generation,
                sync_request_id=sync_request_id,
                sync_skipped_reason=skipped,
            )
        return ShipmentObservation(
            tracking_reference=tracking_reference,
            evidence=ShipmentEvidence.OBSERVED,
            graph_generation_id=generation,
            current_status=_text(row.get(_STATUS_FIELD_ID)),
            shipment_id=_text(row.get(_SHIPMENT_ID_FIELD_ID)),
            sync_request_id=sync_request_id,
            sync_skipped_reason=skipped,
        )

    async def _synchronize(
        self, schema: ActiveSchema, generation: str, tracking_reference: str
    ) -> tuple[str | None, str | None]:
        """Bring this one tracking number in, or say why not.

        The descriptor decides whether a targeted read is legal at all, and the
        checks below are reads of configuration rather than branches over a
        source's name. `shipment` was `SEED_ONLY` until its paths were confirmed
        against genuine `shipmentInfo` documents; promoting it to
        `CONNECTED_SYNC` turned the sync on with no change here, which is what
        those checks being configuration rather than code buys. They stay
        because an entity can be demoted again -- a source contract that stops
        holding must stop the sync, not be routed around.
        """
        if schema.runtime_mode is not RuntimeMode.CONNECTED_SYNC:
            return None, f"RUNTIME_MODE_{schema.runtime_mode.value}"
        entity = schema.entities.get(SHIPMENT_ENTITY_ID)
        if entity is None:
            return None, "SHIPMENT_ENTITY_ABSENT"
        if entity.source_access is not EntitySourceAccess.CONNECTED_SYNC:
            return None, f"SOURCE_ACCESS_{entity.source_access.value}"
        anchors = {SHIPMENT_TRACKING_FIELD_ID: ("EXACT", tracking_reference)}
        try:
            plan = build_targeted_read_plan(
                schema=schema,
                entity_id=SHIPMENT_ENTITY_ID,
                normalized_anchors=anchors,
            )
            receipt = await self._on_demand_sync.synchronize(
                schema=schema,
                graph_generation_id=generation,
                request_digest=on_demand_request_digest(
                    tenant_scope=self._tenant_scope,
                    source_asset_id=plan.source_asset_id,
                    entity_id=SHIPMENT_ENTITY_ID,
                    strong_anchor_id="exact_tracking",
                    normalized_anchors={SHIPMENT_TRACKING_FIELD_ID: tracking_reference},
                    schema_version=schema.schema_version,
                    graph_generation_id=generation,
                    mapping_version=schema.compiler_version,
                ),
                plan=plan,
            )
        except Exception as error:  # noqa: BLE001 - advisory half; the read still runs
            logger.warning(
                "fulfillment_shipment_sync_failed",
                extra={"error_code": type(error).__name__},
                exc_info=True,
            )
            return None, f"SYNC_FAILED_{type(error).__name__.upper()}"
        return receipt.sync_request_id, None

    async def _read(
        self, schema: ActiveSchema, generation: str, tracking_reference: str
    ) -> dict[str, Any] | None:
        plan = LogicalQueryPlan(
            operation=QueryOperation.FILTER,
            start_entity_id=SHIPMENT_ENTITY_ID,
            fields=(
                SHIPMENT_TRACKING_FIELD_ID,
                _STATUS_FIELD_ID,
                _SHIPMENT_ID_FIELD_ID,
            ),
            filters=(
                QueryCondition(
                    entity_id=SHIPMENT_ENTITY_ID,
                    field_id=SHIPMENT_TRACKING_FIELD_ID,
                    operator="EQUALS",
                    value=tracking_reference,
                ),
            ),
            # One tracking number identifies one shipment; a second row would be
            # a key violation upstream, and taking the first quietly is how that
            # would stay unnoticed.
            limit=2,
        )
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
            return None
        if len(rows) > 1:
            logger.warning(
                "fulfillment_shipment_ambiguous",
                extra={"row_count": len(rows), "graph_generation_id": generation},
            )
        first = rows[0]
        return dict(first) if isinstance(first, dict) else None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
