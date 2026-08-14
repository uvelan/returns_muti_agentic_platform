"""Put a just-committed shipment update into the graph, RMA-scoped (SHIP-01).

The authoritative return shipment state is written to `dbo.return_tracking` by
`SQLBusinessStateRepository.record_shipment_update`. Until this existed, that was
where it stopped: fulfilment reads shipment truth from the graph
(`workflows/fulfillment_tracking.py`), the graph learned about shipments only on
the scheduled sync, and so a return that had demonstrably moved went on reading
`AWAITING_HANDOFF` to the associate until the next scheduled run. Contract C4
calls that gap out by name -- shipment state must be "persisted, graph-synchronized
and fulfilment-readable", and only the first of those three was true.

**RMA-scoped, and targeted within it.** The scope key is `return_reference`
because C4 makes shipment state RMA-scoped; the anchors inside it are tracking
numbers, because one RMA legitimately carries several (a split return goes back
in two parcels, each with its own state). A sync scoped to the case would rewrite
every RMA on it, and a sync scoped to the collection would rewrite every shipment
in the platform on every carrier event.

**Deliberately the same shape as `return_record_sync`.** Record-scoped, one lease
for the whole set, raise rather than return, and answer with the generation id so
"the sync finished" and "which graph answers the associate's next turn" are one
statement rather than two hopeful ones. Two conventions for "project what I just
committed" is how one of them ends up with the leasing and the other without it.

**One difference, and it is not an oversight.** A `SUCCEEDED` sync that wrote no
node is a hard failure for a return record and is not one here. The return record
is read back from the platform's own store, written moments earlier by the
activity immediately before -- zero nodes there means the projection did not reach
a document that is certainly present. A shipment is read from the carrier's
source, which may not have published the parcel yet; zero nodes is then a genuine
state, and the fulfilment read already reports it honestly as `ABSENT` rather than
as movement. Raising would turn "the carrier has not filed it yet" into a failed
return update.
"""

from __future__ import annotations

import logging

from return_platform.dynamic_knowledge.fingerprint import on_demand_request_digest
from return_platform.dynamic_knowledge.integration.shipment_observations import (
    SHIPMENT_ENTITY_ID,
    SHIPMENT_TRACKING_FIELD_ID,
)
from return_platform.dynamic_knowledge.integration.targeted_sync import TargetedGraphAccess
from return_platform.dynamic_knowledge.lifecycle.handle import GenerationHandleProvider
from return_platform.dynamic_knowledge.on_demand_sync.contracts import SyncStatus
from return_platform.dynamic_knowledge.on_demand_sync.coordinator import OnDemandSyncCoordinator
from return_platform.dynamic_knowledge.on_demand_sync.planner import build_targeted_read_plan
from return_platform.dynamic_knowledge.schema import ActiveSchema, EntitySourceAccess, RuntimeMode
from return_platform.operations.sql_business_state import ShipmentGraphSyncOutcome

__all__ = [
    "SHIPMENT_STRONG_ANCHOR_ID",
    "GraphShipmentStateSync",
    "ShipmentStateSyncFailed",
]

logger = logging.getLogger("return_platform.dynamic_knowledge.shipment_state_sync")

#: The same anchor id the fulfilment read declares in `shipment_observations`.
#: Shared rather than restated: the digest names the anchor shape so that two
#: mechanisms anchoring the same entity cannot collide, and a write side that
#: named its anchor differently from the read side would defeat exactly that --
#: the sync a fulfilment read is about to perform would not deduplicate against
#: the one the write path just did.
SHIPMENT_STRONG_ANCHOR_ID = "exact_tracking"


class ShipmentStateSyncFailed(RuntimeError):
    """The shipment moved in the platform's store and no agent can see it.

    Raised rather than returned so the caller's retry policy applies. The
    authoritative row is already committed, so a caller that retried the whole
    update would be told DUPLICATE and would not sync again -- which sounds like
    a hole and is not one: the fulfilment read performs its own targeted sync for
    the tracking number before reading, and the scheduled sync covers what that
    misses. Failing loudly costs a retry; continuing quietly would leave
    fulfilment reporting `AWAITING_HANDOFF` for a delivered return.
    """


class GraphShipmentStateSync:
    """Satisfies `operations.sql_business_state.ShipmentGraphSyncPort`."""

    def __init__(
        self,
        *,
        schema: ActiveSchema,
        on_demand_sync: OnDemandSyncCoordinator,
        generation_handles: GenerationHandleProvider,
        tenant_scope: str = "platform",
    ) -> None:
        self._schema = schema
        self._on_demand_sync = on_demand_sync
        self._generation_handles = generation_handles
        self._tenant_scope = tenant_scope

    @classmethod
    def from_access(
        cls, access: TargetedGraphAccess, *, tenant_scope: str = "platform"
    ) -> GraphShipmentStateSync:
        """What a composition root calls, having built the stack once."""
        return cls(
            schema=access.schema,
            on_demand_sync=access.on_demand_sync,
            generation_handles=access.generation_handles,
            tenant_scope=tenant_scope,
        )

    async def synchronize_shipments(
        self, *, return_reference: str, tracking_references: tuple[str, ...]
    ) -> ShipmentGraphSyncOutcome:
        schema = self._schema
        self._require_syncable(schema)
        if not tracking_references:
            raise ShipmentStateSyncFailed(
                f"RMA {return_reference!r} asked for a shipment sync with no tracking references"
            )
        # One read lease for the whole RMA, so a split return's parcels land in
        # the same generation. Re-resolving per parcel would let a cutover split
        # one RMA's shipments across two generations, and a reader pinned to
        # either would see half the return moving and half of it not.
        async with self._generation_handles.acquire_read(schema) as handle:
            generation = handle.graph_generation_id
            nodes_written = 0
            for tracking_reference in tracking_references:
                nodes_written += await self._synchronize_one(
                    schema, generation, return_reference, tracking_reference
                )
        return ShipmentGraphSyncOutcome(
            graph_generation_id=generation,
            synchronized_tracking_references=tracking_references,
            nodes_written=nodes_written,
        )

    @staticmethod
    def _require_syncable(schema: ActiveSchema) -> None:
        """Refuse loudly rather than reporting a sync that cannot have happened.

        Every check is a read of configuration rather than a branch on a source's
        name, so demoting `shipment` stops the sync without a code change. That
        is the same stance `shipment_observations` takes, with one deliberate
        difference in consequence: the read is advisory and records the refusal
        on the observation, while this is the write side and a refusal here means
        the shipment will not be in the graph at all.
        """
        if schema.runtime_mode is not RuntimeMode.CONNECTED_SYNC:
            raise ShipmentStateSyncFailed(
                f"runtime mode is {schema.runtime_mode.value}; targeted sync is unavailable"
            )
        entity = schema.entities.get(SHIPMENT_ENTITY_ID)
        if entity is None:
            raise ShipmentStateSyncFailed(
                f"the active schema has no {SHIPMENT_ENTITY_ID!r} entity to sync into"
            )
        if entity.source_access is not EntitySourceAccess.CONNECTED_SYNC:
            raise ShipmentStateSyncFailed(
                f"entity {SHIPMENT_ENTITY_ID!r} declares source_access "
                f"{entity.source_access.value} and cannot be synchronized"
            )

    async def _synchronize_one(
        self,
        schema: ActiveSchema,
        generation: str,
        return_reference: str,
        tracking_reference: str,
    ) -> int:
        plan = build_targeted_read_plan(
            schema=schema,
            entity_id=SHIPMENT_ENTITY_ID,
            normalized_anchors={SHIPMENT_TRACKING_FIELD_ID: ("EXACT", tracking_reference)},
        )
        receipt = await self._on_demand_sync.synchronize(
            schema=schema,
            graph_generation_id=generation,
            request_digest=on_demand_request_digest(
                tenant_scope=self._tenant_scope,
                source_asset_id=plan.source_asset_id,
                entity_id=SHIPMENT_ENTITY_ID,
                strong_anchor_id=SHIPMENT_STRONG_ANCHOR_ID,
                normalized_anchors={SHIPMENT_TRACKING_FIELD_ID: tracking_reference},
                schema_version=schema.schema_version,
                graph_generation_id=generation,
                mapping_version=schema.compiler_version,
            ),
            plan=plan,
        )
        if receipt.status is not SyncStatus.SUCCEEDED:
            raise ShipmentStateSyncFailed(
                f"targeted sync of shipment {tracking_reference!r} for RMA "
                f"{return_reference!r} ended {receipt.status.value} "
                f"({receipt.error_code or 'no error code'})"
            )
        logger.info(
            "return_shipment_synchronized",
            extra={
                "return_reference": return_reference,
                "tracking_reference": tracking_reference,
                "graph_generation_id": generation,
                "sync_request_id": receipt.sync_request_id,
                "nodes_written": receipt.nodes_written,
            },
        )
        return receipt.nodes_written
