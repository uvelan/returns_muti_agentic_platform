from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from return_platform.dynamic_knowledge.graph.generation import GraphGenerationStatus, GraphWriteReceipt
from return_platform.dynamic_knowledge.on_demand_sync.contracts import (
    DynamicRecordMutation,
    DynamicSourceRecord,
    GraphMutationBatch,
)
from return_platform.dynamic_knowledge.schema import ActiveSchema
from return_platform.dynamic_knowledge.sync.adapters import ProjectorGraphWriter, SourceConnectorRegistry


class FakeProjector:
    def __init__(self) -> None:
        self.calls: list[tuple[DynamicRecordMutation, ...]] = []

    async def project(
        self, *, schema: ActiveSchema, mutations: tuple[DynamicRecordMutation, ...]
    ) -> GraphMutationBatch:
        self.calls.append(mutations)
        return GraphMutationBatch(node_mutations=(), relationship_mutations=())


class FakeWriter:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def write(
        self,
        *,
        schema: ActiveSchema,
        graph_generation_id: str,
        fencing_token: int,
        expected_generation_status: GraphGenerationStatus,
        sync_run_id: str,
        chunk_id: str,
        batch: GraphMutationBatch,
    ) -> GraphWriteReceipt:
        self.calls.append(
            {
                "graph_generation_id": graph_generation_id,
                "fencing_token": fencing_token,
                "expected_generation_status": expected_generation_status,
                "sync_run_id": sync_run_id,
                "chunk_id": chunk_id,
            }
        )
        return GraphWriteReceipt(
            sync_run_id=sync_run_id,
            chunk_id=chunk_id,
            payload_checksum="deadbeef",
            graph_generation_id=graph_generation_id,
            committed_at=datetime(2026, 8, 7, tzinfo=UTC),
            nodes_written=3,
            relationships_written=1,
        )


@pytest.mark.asyncio
async def test_project_and_write_reconstructs_mutations_and_returns_receipt_counts(
    active_schema: ActiveSchema,
) -> None:
    projector = FakeProjector()
    writer = FakeWriter()
    adapter = ProjectorGraphWriter(
        projector=projector,
        writer=writer,
        sync_run_id="run-1",
        expected_generation_status=GraphGenerationStatus.ACTIVE,
    )
    records = (
        DynamicSourceRecord(
            source_asset_id="source_a",
            entity_id="entity_a",
            natural_key={"id": "A-1"},
            values={"id": "A-1", "name": "n"},
        ),
    )
    nodes, relationships = await adapter.project_and_write(
        schema=active_schema, graph_generation_id="gen-1", records=records, fencing_token=1
    )
    assert nodes == 3
    assert relationships == 1
    assert len(projector.calls) == 1
    (mutation,) = projector.calls[0]
    assert mutation.operation == "UPSERT"
    assert mutation.entity_id == "entity_a"
    assert mutation.projection_id == "node_a"
    assert mutation.resolved_key == {"id": "A-1"}
    assert writer.calls[0]["expected_generation_status"] is GraphGenerationStatus.ACTIVE
    assert writer.calls[0]["chunk_id"] == "chunk-1"


@pytest.mark.asyncio
async def test_project_and_write_advances_chunk_id_across_calls(active_schema: ActiveSchema) -> None:
    adapter = ProjectorGraphWriter(
        projector=FakeProjector(),
        writer=(writer := FakeWriter()),
        sync_run_id="run-1",
        expected_generation_status=GraphGenerationStatus.BUILDING,
    )
    records = (
        DynamicSourceRecord(
            source_asset_id="source_a", entity_id="entity_a", natural_key={"id": "A-1"}, values={"id": "A-1"}
        ),
    )
    await adapter.project_and_write(
        schema=active_schema, graph_generation_id="gen-1", records=records, fencing_token=1
    )
    await adapter.project_and_write(
        schema=active_schema, graph_generation_id="gen-1", records=records, fencing_token=1
    )
    assert [call["chunk_id"] for call in writer.calls] == ["chunk-1", "chunk-2"]


def test_registry_dispatches_by_connector_type(active_schema: ActiveSchema) -> None:
    raw = active_schema.model_dump(mode="json")
    raw["sources"]["source_b"]["connector_type"] = "MSSQL"
    schema = ActiveSchema.model_validate(raw)
    mongo = object()
    sqlserver = object()
    registry = SourceConnectorRegistry(
        schema=schema, mongo_connector=mongo, sqlserver_connector=sqlserver
    )
    assert registry.resolve("source_a") is mongo  # source_a is MONGODB in the shared fixture
    assert registry.resolve("source_b") is sqlserver


def test_registry_raises_for_an_unregistered_connector_type(active_schema: ActiveSchema) -> None:
    registry = SourceConnectorRegistry(schema=active_schema, mongo_connector=object())
    with pytest.raises(ValueError, match="no connector registered"):
        registry.resolve("source_b")
