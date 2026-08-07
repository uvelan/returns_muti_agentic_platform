"""Generic source-to-graph synchronization without business-specific branches."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from return_platform.dynamic_knowledge.graph.generation import GraphGenerationStatus
from return_platform.dynamic_knowledge.on_demand_sync.contracts import (
    CursorComparison,
    DynamicSourceRecord,
    ProjectionReadScope,
    RawSourcePage,
    SourceCursor,
)
from return_platform.dynamic_knowledge.on_demand_sync.extraction import SourceRecordExtractor
from return_platform.dynamic_knowledge.schema import ActiveSchema, RuntimeMode


class SourceScanConnector(Protocol):
    async def capture_high_watermark(self, *, source_asset_id: str) -> SourceCursor: ...

    def compare_cursors(
        self, *, source_asset_id: str, left: SourceCursor, right: SourceCursor
    ) -> CursorComparison: ...

    def scan(
        self,
        *,
        schema: ActiveSchema,
        source_asset_id: str,
        after: SourceCursor | None,
        through: SourceCursor,
    ) -> AsyncIterator[RawSourcePage]: ...


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


class RelationshipReconciler(Protocol):
    """Stage B: graph-side cross-source relationship joins, run once after every
    participating source's Stage A node materialization has completed. Matches
    Neo4jDynamicGraphWriter.reconcile_relationships's signature."""

    async def reconcile_relationships(
        self,
        *,
        schema: ActiveSchema,
        graph_generation_id: str,
        fencing_token: int,
        expected_generation_status: GraphGenerationStatus,
        relationship_ids: tuple[str, ...] | None = None,
    ) -> dict[str, int]: ...


class CheckpointStore(Protocol):
    async def read(
        self, *, source_asset_id: str, graph_generation_id: str
    ) -> SourceCursor | None: ...

    async def write(
        self,
        *,
        source_asset_id: str,
        graph_generation_id: str,
        checkpoint: SourceCursor,
        fencing_token: int,
    ) -> None: ...


class GenericSyncCoordinator:
    """Stage A node materialization for every source, then (when a reconciler is
    configured) Stage B cross-source relationship joins for a full sync.

    Two-stage *incremental* checkpointing (affected-relationship-scoped Stage B,
    delayed checkpoint advancement) and blue/green catch-up/watermark sequencing
    remain out of scope here -- those belong to the incremental-sync rebuild
    machinery (a later stage of the source-to-graph alignment plan) and must not
    be half-implemented in this coordinator's incremental_sync.
    """

    def __init__(
        self,
        *,
        connectors: SourceScanRegistry,
        extractor: SourceRecordExtractor,
        writer: ProjectionWriter,
        checkpoints: CheckpointStore,
        reconciler: RelationshipReconciler | None = None,
    ) -> None:
        self._connectors = connectors
        self._extractor = extractor
        self._writer = writer
        self._checkpoints = checkpoints
        self._reconciler = reconciler

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
            watermark = await connector.capture_high_watermark(source_asset_id=source_asset_id)
            async for page in connector.scan(
                schema=schema,
                source_asset_id=source_asset_id,
                after=None,
                through=watermark,
            ):
                nodes, relationships = await self._project_page(
                    schema=schema,
                    source_asset_id=source_asset_id,
                    graph_generation_id=graph_generation_id,
                    fencing_token=fencing_token,
                    page=page,
                )
                total_nodes += nodes
                total_relationships += relationships
        if self._reconciler is not None:
            # Stage B, graph-side, after every participating source's Stage A has
            # completed -- a full-sync's target generation is always BUILDING
            # (never ACTIVE) until cutover; see the generation lifecycle plan.
            counts = await self._reconciler.reconcile_relationships(
                schema=schema,
                graph_generation_id=graph_generation_id,
                fencing_token=fencing_token,
                expected_generation_status=GraphGenerationStatus.BUILDING,
            )
            total_relationships += sum(counts.values())
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
            connector = self._connectors.resolve(source_asset_id)
            after = await self._checkpoints.read(
                source_asset_id=source_asset_id,
                graph_generation_id=graph_generation_id,
            )
            through = await connector.capture_high_watermark(source_asset_id=source_asset_id)
            async for page in connector.scan(
                schema=schema,
                source_asset_id=source_asset_id,
                after=after,
                through=through,
            ):
                nodes, relationships = await self._project_page(
                    schema=schema,
                    source_asset_id=source_asset_id,
                    graph_generation_id=graph_generation_id,
                    fencing_token=fencing_token,
                    page=page,
                )
                total_nodes += nodes
                total_relationships += relationships
                new_checkpoint = _highest_cursor(connector, source_asset_id, after, page)
                if new_checkpoint is not None:
                    await self._checkpoints.write(
                        source_asset_id=source_asset_id,
                        graph_generation_id=graph_generation_id,
                        checkpoint=new_checkpoint,
                        fencing_token=fencing_token,
                    )
        return total_nodes, total_relationships

    async def _project_page(
        self,
        *,
        schema: ActiveSchema,
        source_asset_id: str,
        graph_generation_id: str,
        fencing_token: int,
        page: RawSourcePage,
    ) -> tuple[int, int]:
        mutations = self._extractor.extract(
            schema=schema,
            source_asset_id=source_asset_id,
            page=page,
            read_scope=ProjectionReadScope.COMPLETE_SOURCE_DOCUMENT,
        )
        records = tuple(
            mutation.record
            for mutation in mutations
            if mutation.operation == "UPSERT" and mutation.record is not None
        )
        if not records:
            return 0, 0
        return await self._writer.project_and_write(
            schema=schema,
            graph_generation_id=graph_generation_id,
            records=records,
            fencing_token=fencing_token,
        )


def _highest_cursor(
    connector: SourceScanConnector,
    source_asset_id: str,
    current: SourceCursor | None,
    page: RawSourcePage,
) -> SourceCursor | None:
    """Delegate all cursor ordering to the connector -- never decode/order encoded_value here.

    This replaces a prior implementation that cast checkpoint values to str
    and took max(), which silently mis-orders anything but zero-padded
    lexically-sortable cursors (e.g. "10" sorts before "2").
    """

    candidate = page.next_cursor or page.high_watermark
    if candidate is None:
        return current
    if current is None:
        return candidate
    comparison = connector.compare_cursors(
        source_asset_id=source_asset_id, left=candidate, right=current
    )
    return candidate if comparison is CursorComparison.AFTER else current
