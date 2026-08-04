from __future__ import annotations

import pytest

from return_platform.dynamic_knowledge.on_demand_sync.contracts import (
    DynamicSourceRecord,
    GraphWriteBatch,
    SyncReceipt,
    SyncReservation,
    SyncStatus,
)
from return_platform.dynamic_knowledge.on_demand_sync.coordinator import OnDemandSyncCoordinator
from return_platform.dynamic_knowledge.on_demand_sync.planner import build_targeted_read_plan
from return_platform.dynamic_knowledge.on_demand_sync.source_compilers import compile_source_read
from return_platform.dynamic_knowledge.schema import ActiveSchema


class Connector:
    calls = 0

    async def targeted_read(
        self, *, schema: ActiveSchema, plan: object
    ) -> tuple[DynamicSourceRecord, ...]:
        self.calls += 1
        return (
            DynamicSourceRecord(
                source_asset_id="source_a",
                entity_id="entity_a",
                natural_key={"id": "A-1"},
                values={"id": "A-1", "name": "Configured", "changed_at": "2026-08-04T00:00:00Z"},
            ),
        )


class Registry:
    def __init__(self, connector: Connector) -> None:
        self.connector = connector

    def resolve(self, source_asset_id: str) -> Connector:
        assert source_asset_id == "source_a"
        return self.connector


class Projector:
    async def project(
        self, *, schema: ActiveSchema, records: tuple[DynamicSourceRecord, ...]
    ) -> GraphWriteBatch:
        return GraphWriteBatch(
            node_rows={"node_a": ({"keys": {"id": "A-1"}, "properties": {}},)}, relationship_rows={}
        )


class Writer:
    async def write(
        self,
        *,
        schema: ActiveSchema,
        graph_generation_id: str,
        batch: GraphWriteBatch,
    ) -> tuple[int, int]:
        assert graph_generation_id == "g1"
        return 1, 0


class Store:
    def __init__(self) -> None:
        self.receipt: SyncReceipt | None = None

    async def reserve(
        self,
        *,
        request_digest: str,
        proposed_request_id: str,
        schema_version: str,
        graph_generation_id: str,
    ) -> SyncReservation:
        if self.receipt and self.receipt.status is SyncStatus.SUCCEEDED:
            return SyncReservation(
                acquired=False,
                sync_request_id=self.receipt.sync_request_id,
                existing_receipt=self.receipt,
            )
        return SyncReservation(acquired=True, sync_request_id=proposed_request_id)

    async def complete(self, receipt: SyncReceipt) -> None:
        self.receipt = receipt


@pytest.mark.asyncio
async def test_identical_targeted_sync_reuses_successful_receipt(
    active_schema: ActiveSchema,
) -> None:
    connector, store = Connector(), Store()
    coordinator = OnDemandSyncCoordinator(
        connectors=Registry(connector), projector=Projector(), writer=Writer(), store=store
    )
    plan = build_targeted_read_plan(
        schema=active_schema,
        entity_id="entity_a",
        normalized_anchors={"id": ("EXACT", "A-1")},
    )
    first = await coordinator.synchronize(
        schema=active_schema,
        graph_generation_id="g1",
        request_digest="d1",
        plan=plan,
    )
    second = await coordinator.synchronize(
        schema=active_schema,
        graph_generation_id="g1",
        request_digest="d1",
        plan=plan,
    )
    assert first == second
    assert connector.calls == 1


def test_targeted_mongo_read_projects_only_required_fields(active_schema: ActiveSchema) -> None:
    plan = build_targeted_read_plan(
        schema=active_schema,
        entity_id="entity_a",
        normalized_anchors={"id": ("EXACT", "A-1")},
    )
    compiled = compile_source_read(active_schema, plan)
    assert compiled.statement["filter"] == {"configured_id": {"$eq": "A-1"}}
    assert compiled.statement["projection"]["configured_id"] == 1
    assert compiled.statement["limit"] == 100
