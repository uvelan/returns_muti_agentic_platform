"""Generic source-to-graph synchronization without business-specific branches."""

from __future__ import annotations

from typing import AsyncIterator, Protocol

from return_platform.dynamic_knowledge.on_demand_sync.contracts import DynamicSourceRecord
from return_platform.dynamic_knowledge.schema import ActiveSchema, RuntimeMode


class SourceScanConnector(Protocol):
    async def scan(
        self,
        *,
        schema: ActiveSchema,
        source_asset_id: str,
        checkpoint: str | None,
    ) -> AsyncIterator[tuple[DynamicSourceRecord, ...]]: ...


class SourceScanRegistry(Protocol):
    def resolve(self, source_asset_id: str) -> SourceScanConnector: ...


class ProjectionWriter(Protocol):
    async def project_and_write(
        self,
        *,
        schema: ActiveSchema,
        graph_generation_id: str,
        records: tuple[DynamicSourceRecord, ...],
        fencing_token: int,
    ) -> tuple[int, int]: ...


class CheckpointStore(Protocol):
    async def read(self, *, source_asset_id: str, graph_generation_id: str) -> str | None: ...
    async def write(
        self,
        *,
        source_asset_id: str,
        graph_generation_id: str,
        checkpoint: str,
        fencing_token: int,
    ) -> None: ...


class GenericSyncCoordinator:
    def __init__(
        self,
        *,
        connectors: SourceScanRegistry,
        writer: ProjectionWriter,
        checkpoints: CheckpointStore,
    ) -> None:
        self._connectors = connectors
        self._writer = writer
        self._checkpoints = checkpoints

    async def full_sync(
        self,
        *,
        schema: ActiveSchema,
        graph_generation_id: str,
        fencing_token: int,
    ) -> tuple[int, int]:
        if schema.runtime_mode is RuntimeMode.KNOWLEDGE_ONLY:
            raise RuntimeError("FULL_SYNC_DISABLED_IN_KNOWLEDGE_ONLY_MODE")
        total_nodes = 0
        total_relationships = 0
        for source_asset_id in sorted(schema.sources):
            connector = self._connectors.resolve(source_asset_id)
            async for batch in connector.scan(
                schema=schema,
                source_asset_id=source_asset_id,
                checkpoint=None,
            ):
                nodes, relationships = await self._writer.project_and_write(
                    schema=schema,
                    graph_generation_id=graph_generation_id,
                    records=batch,
                    fencing_token=fencing_token,
                )
                total_nodes += nodes
                total_relationships += relationships
        return total_nodes, total_relationships

    async def incremental_sync(
        self,
        *,
        schema: ActiveSchema,
        graph_generation_id: str,
        fencing_token: int,
    ) -> tuple[int, int]:
        if schema.runtime_mode is not RuntimeMode.CONNECTED_SYNC:
            raise RuntimeError("INCREMENTAL_SYNC_REQUIRES_CONNECTED_SYNC_MODE")
        total_nodes = 0
        total_relationships = 0
        for source_asset_id, source in sorted(schema.sources.items()):
            if source.incremental_cursor_field is None:
                continue
            checkpoint = await self._checkpoints.read(
                source_asset_id=source_asset_id,
                graph_generation_id=graph_generation_id,
            )
            connector = self._connectors.resolve(source_asset_id)
            async for batch in connector.scan(
                schema=schema,
                source_asset_id=source_asset_id,
                checkpoint=checkpoint,
            ):
                nodes, relationships = await self._writer.project_and_write(
                    schema=schema,
                    graph_generation_id=graph_generation_id,
                    records=batch,
                    fencing_token=fencing_token,
                )
                total_nodes += nodes
                total_relationships += relationships
                new_checkpoint = _highest_checkpoint(schema, source_asset_id, batch)
                if new_checkpoint is not None:
                    await self._checkpoints.write(
                        source_asset_id=source_asset_id,
                        graph_generation_id=graph_generation_id,
                        checkpoint=new_checkpoint,
                        fencing_token=fencing_token,
                    )
        return total_nodes, total_relationships


def _highest_checkpoint(
    schema: ActiveSchema,
    source_asset_id: str,
    batch: tuple[DynamicSourceRecord, ...],
) -> str | None:
    cursor = schema.sources[source_asset_id].incremental_cursor_field
    if cursor is None:
        return None
    values = [str(record.values[cursor]) for record in batch if cursor in record.values]
    return max(values) if values else None
