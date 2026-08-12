"""Put a just-committed return record into the graph, record-scoped (W2.5).

The authoritative return record is written to MongoDB by
`ReturnCaseActivities.record_support_outcome`. Until this existed, the next
thing that happened was an agent turn reading the graph -- where the record was
not, because the only thing that projects `return_records` is the scheduled
sync. The associate asked "did the RMA come through" and Order Discovery, having
looked in the graph and found nothing, said no.

**Record-scoped, not collection-scoped.** Three agents firing a collection-wide
sync per case would rewrite every case, RMA and item in the platform on every
return. The anchor is the `return_record_id` the workflow just minted, so the
targeted read matches one document.

**Returns only when the write has committed.** `OnDemandSyncCoordinator` writes
the graph and only then completes its receipt, so a `SUCCEEDED` receipt is a
post-commit fact. Every other outcome raises: the caller is `blocking`, and a
sync reported as done while the write was still in flight is precisely the
window a generation-fenced write leaves open -- a read landing inside it sees
half a case.
"""

from __future__ import annotations

import logging

from return_platform.dynamic_knowledge.fingerprint import on_demand_request_digest
from return_platform.dynamic_knowledge.integration.targeted_sync import TargetedGraphAccess
from return_platform.dynamic_knowledge.lifecycle.handle import GenerationHandleProvider
from return_platform.dynamic_knowledge.on_demand_sync.contracts import SyncStatus
from return_platform.dynamic_knowledge.on_demand_sync.coordinator import OnDemandSyncCoordinator
from return_platform.dynamic_knowledge.on_demand_sync.planner import build_targeted_read_plan
from return_platform.dynamic_knowledge.schema import ActiveSchema, EntitySourceAccess, RuntimeMode
from return_platform.workflows.return_case_activities import ReturnRecordSyncOutcome

__all__ = [
    "RETURN_RECORD_ENTITY_ID",
    "RETURN_RECORD_KEY_FIELD_ID",
    "GraphReturnRecordSync",
    "ReturnRecordSyncFailed",
]

logger = logging.getLogger("return_platform.dynamic_knowledge.return_record_sync")

RETURN_RECORD_ENTITY_ID = "return_record"
RETURN_RECORD_KEY_FIELD_ID = "return_record_id"


class ReturnRecordSyncFailed(RuntimeError):
    """The return exists in the platform store and no agent can see it.

    Raised rather than returned so the Temporal activity fails, the retry policy
    applies, and an exhausted retry parks the case. Continuing on a stale graph
    would leave Order Discovery telling an associate their RMA does not exist.
    """


class GraphReturnRecordSync:
    """Satisfies `workflows.return_case_activities.ReturnRecordGraphSyncPort`."""

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
    ) -> GraphReturnRecordSync:
        """What a composition root calls, having built the stack once."""
        return cls(
            schema=access.schema,
            on_demand_sync=access.on_demand_sync,
            generation_handles=access.generation_handles,
            tenant_scope=tenant_scope,
        )

    async def synchronize_records(
        self, *, case_id: str, return_record_ids: tuple[str, ...]
    ) -> ReturnRecordSyncOutcome:
        schema = self._schema
        self._require_syncable(schema)
        if not return_record_ids:
            raise ReturnRecordSyncFailed(
                f"case {case_id!r} asked for a record-scoped sync with no record ids"
            )
        # One read lease for the whole set, so every record in this case lands in
        # the same generation. Re-resolving per record would let a cutover split
        # a case's RMAs across two generations, and a reader pinned to either one
        # would see part of the answer.
        async with self._generation_handles.acquire_read(schema) as handle:
            generation = handle.graph_generation_id
            nodes_written = 0
            for return_record_id in return_record_ids:
                nodes_written += await self._synchronize_one(
                    schema, generation, case_id, return_record_id
                )
        return ReturnRecordSyncOutcome(
            graph_generation_id=generation,
            synchronized_record_ids=return_record_ids,
            nodes_written=nodes_written,
        )

    @staticmethod
    def _require_syncable(schema: ActiveSchema) -> None:
        """Refuse loudly rather than reporting a sync that cannot have happened.

        Both of these are configuration statements, and neither is something a
        `blocking` step may step over: an agent that cannot see the return is
        the same outcome whether the cause was an outage or a descriptor that
        never permitted the read.
        """
        if schema.runtime_mode is not RuntimeMode.CONNECTED_SYNC:
            raise ReturnRecordSyncFailed(
                f"runtime mode is {schema.runtime_mode.value}; targeted sync is unavailable"
            )
        entity = schema.entities.get(RETURN_RECORD_ENTITY_ID)
        if entity is None:
            raise ReturnRecordSyncFailed(
                f"the active schema has no {RETURN_RECORD_ENTITY_ID!r} entity to sync into"
            )
        if entity.source_access is not EntitySourceAccess.CONNECTED_SYNC:
            raise ReturnRecordSyncFailed(
                f"entity {RETURN_RECORD_ENTITY_ID!r} declares source_access "
                f"{entity.source_access.value} and cannot be synchronized"
            )

    async def _synchronize_one(
        self, schema: ActiveSchema, generation: str, case_id: str, return_record_id: str
    ) -> int:
        plan = build_targeted_read_plan(
            schema=schema,
            entity_id=RETURN_RECORD_ENTITY_ID,
            normalized_anchors={RETURN_RECORD_KEY_FIELD_ID: ("EXACT", return_record_id)},
        )
        receipt = await self._on_demand_sync.synchronize(
            schema=schema,
            graph_generation_id=generation,
            request_digest=on_demand_request_digest(
                tenant_scope=self._tenant_scope,
                source_asset_id=plan.source_asset_id,
                entity_id=RETURN_RECORD_ENTITY_ID,
                # No `StrongAnchorDefinition` is involved: this anchor is a
                # primary key the platform minted moments ago, not something a
                # model proposed, so `StrongAnchorGuard` -- which exists to stop
                # a model anchoring on a weak field -- has nothing to decide.
                # The digest still names the shape so two mechanisms anchoring
                # the same entity differently cannot collide.
                strong_anchor_id="platform_return_record_id",
                normalized_anchors={RETURN_RECORD_KEY_FIELD_ID: return_record_id},
                schema_version=schema.schema_version,
                graph_generation_id=generation,
                mapping_version=schema.compiler_version,
            ),
            plan=plan,
        )
        if receipt.status is not SyncStatus.SUCCEEDED:
            raise ReturnRecordSyncFailed(
                f"targeted sync of return record {return_record_id!r} for case {case_id!r} "
                f"ended {receipt.status.value} ({receipt.error_code or 'no error code'})"
            )
        if receipt.nodes_written == 0:
            # A SUCCEEDED sync that wrote nothing is the failure this whole path
            # was rebuilt around: the source answered and the projection threw
            # the answer away. The record was written to Mongo by the activity
            # immediately before this one, so zero nodes means the read did not
            # reach it -- most likely a connector bound to the wrong database.
            raise ReturnRecordSyncFailed(
                f"targeted sync of return record {return_record_id!r} for case {case_id!r} "
                "succeeded without writing a node; the record is in the store and not in the graph"
            )
        logger.info(
            "return_record_synchronized",
            extra={
                "case_id": case_id,
                "return_record_id": return_record_id,
                "graph_generation_id": generation,
                "sync_request_id": receipt.sync_request_id,
                "nodes_written": receipt.nodes_written,
            },
        )
        return receipt.nodes_written
