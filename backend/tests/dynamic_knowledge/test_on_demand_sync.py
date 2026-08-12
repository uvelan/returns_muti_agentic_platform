from __future__ import annotations

from datetime import UTC, datetime

import pytest

from return_platform.dynamic_knowledge.on_demand_sync.contracts import (
    DynamicRecordMutation,
    GraphMutationBatch,
    GraphNodeMutation,
    RawSourceDocument,
    RawSourcePage,
    SyncReceipt,
    SyncReservation,
    SyncStatus,
)
from return_platform.dynamic_knowledge.on_demand_sync.coordinator import OnDemandSyncCoordinator
from return_platform.dynamic_knowledge.on_demand_sync.extraction import (
    GenericSourceRecordExtractor,
)
from return_platform.dynamic_knowledge.on_demand_sync.planner import build_targeted_read_plan
from return_platform.dynamic_knowledge.schema import ActiveSchema
from return_platform.source_connectors.compilation import compile_source_read


class Connector:
    calls = 0

    async def targeted_read(self, *, schema: ActiveSchema, plan: object) -> RawSourcePage:
        self.calls += 1
        return RawSourcePage(
            documents=(
                RawSourceDocument(
                    operation="UPSERT",
                    document={
                        "configured_id": "A-1",
                        "configured_name": "Configured",
                        "configured_changed_at": "2026-08-04T00:00:00Z",
                    },
                    source_identity="A-1",
                ),
            ),
            observed_at=datetime(2026, 8, 4, tzinfo=UTC),
        )


class Registry:
    def __init__(self, connector: Connector) -> None:
        self.connector = connector

    def resolve(self, source_asset_id: str) -> Connector:
        assert source_asset_id == "source_a"
        return self.connector


class Projector:
    async def project(
        self, *, schema: ActiveSchema, mutations: tuple[DynamicRecordMutation, ...]
    ) -> GraphMutationBatch:
        return GraphMutationBatch(
            node_mutations=(
                GraphNodeMutation(
                    operation="UPSERT",
                    projection_id="node_a",
                    entity_id="entity_a",
                    key_values={"id": "A-1"},
                ),
            ),
            relationship_mutations=(),
        )


class Writer:
    async def write(
        self,
        *,
        schema: ActiveSchema,
        graph_generation_id: str,
        batch: GraphMutationBatch,
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
        connectors=Registry(connector),
        extractor=GenericSourceRecordExtractor(),
        projector=Projector(),
        writer=Writer(),
        store=store,
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


def test_a_targeted_mongo_read_is_anchored_and_bounded(active_schema: ActiveSchema) -> None:
    """The filter is the anchor and the read is limited -- never a source-wide scan.

    This used to be named for projecting "only required fields", meaning the
    anchoring entity's own node fields. That narrowing was the defect: the
    document a targeted read returns is extracted by every entity bound to the
    source, so the projection is now computed from the source asset (see
    `source_connectors.compilation.source_projection_paths`). What has not
    changed, and is what this test is actually about, is that the read is
    anchored and bounded.
    """
    plan = build_targeted_read_plan(
        schema=active_schema,
        entity_id="entity_a",
        normalized_anchors={"id": ("EXACT", "A-1")},
    )
    compiled = compile_source_read(active_schema, plan)
    assert compiled.statement["filter"] == {"configured_id": {"$eq": "A-1"}}
    assert compiled.statement["projection"]["configured_id"] == 1
    assert compiled.statement["limit"] == 100
