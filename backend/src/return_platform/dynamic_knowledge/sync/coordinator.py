"""Generic source-to-graph synchronization without business-specific branches."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Protocol

from return_platform.dynamic_knowledge.graph.generation import GraphGenerationStatus
from return_platform.dynamic_knowledge.graph.write_compiler import canonical_key_hash
from return_platform.dynamic_knowledge.on_demand_sync.contracts import (
    DynamicRecordMutation,
    DynamicSourceRecord,
    ProjectionReadScope,
)
from return_platform.dynamic_knowledge.on_demand_sync.extraction import SourceRecordExtractor
from return_platform.dynamic_knowledge.schema import ActiveSchema, RuntimeMode
from return_platform.dynamic_knowledge.sync.run_manifest import (
    RunManifestRecorder,
    SourceWatermarkRecord,
    SyncRunManifest,
)
from return_platform.source_connectors.contracts import (
    CursorComparison,
    RawSourcePage,
    SourceCursor,
)
from return_platform.source_connectors.protocols import SourceScanConnector, SourceScanRegistry

__all__ = [
    "CheckpointStore",
    "ChildOwnershipReconciler",
    "GenericSyncCoordinator",
    "ProjectionWriter",
    "RelationshipReconciler",
    "SourceScanConnector",
    "SourceScanRegistry",
]


class ProjectionWriter(Protocol):
    async def project_and_write(
        self,
        *,
        schema: ActiveSchema,
        graph_generation_id: str,
        records: tuple[DynamicSourceRecord, ...],
        fencing_token: int,
        expected_generation_status: GraphGenerationStatus,
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


class ChildOwnershipReconciler(Protocol):
    """Replace-child-set reconciliation for one entity exploded from one parent
    document, run per parent document alongside Stage A. Matches
    Neo4jDynamicGraphWriter.reconcile_child_ownership's signature."""

    async def reconcile_child_ownership(
        self,
        *,
        schema: ActiveSchema,
        graph_generation_id: str,
        fencing_token: int,
        expected_generation_status: GraphGenerationStatus,
        projection_id: str,
        source_asset_id: str,
        source_identity: str,
        current_children: tuple[tuple[str, dict[str, Any]], ...],
        source_version: str | None = None,
    ) -> Any: ...


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
        run_recorder: RunManifestRecorder | None = None,
        ownership_reconciler: ChildOwnershipReconciler | None = None,
    ) -> None:
        self._connectors = connectors
        self._extractor = extractor
        self._writer = writer
        self._checkpoints = checkpoints
        self._reconciler = reconciler
        self._run_recorder = run_recorder
        self._ownership_reconciler = ownership_reconciler

    async def full_sync(
        self,
        *,
        schema: ActiveSchema,
        graph_generation_id: str,
        fencing_token: int,
        source_asset_ids: frozenset[str] | None = None,
        expected_generation_status: GraphGenerationStatus = GraphGenerationStatus.BUILDING,
        sync_run_id: str | None = None,
    ) -> tuple[int, int]:
        """source_asset_ids narrows which configured sources participate in this
        run (default: every source in the schema) -- e.g. a caller doing a
        Mongo-only or SQL-only resync. expected_generation_status defaults to
        BUILDING (a real blue/green rebuild's target generation is never ACTIVE
        until cutover); a caller resyncing directly into an already-ACTIVE
        generation (no blue/green rebuild in progress) passes ACTIVE instead --
        both node writes and Stage B reconciliation use the same expectation.
        sync_run_id is required only when a run_recorder is configured.

        Every participating source's high-watermark is captured before any
        source's scan begins -- capturing per-source right before that
        source's own scan would let a later source's bound sit after an
        earlier source's scan already started, silently letting the later
        source see writes the earlier source's scan missed. See the plan's
        "correct high-watermark sequencing".
        """

        if schema.runtime_mode is RuntimeMode.KNOWLEDGE_ONLY:
            raise RuntimeError("FULL_SYNC_DISABLED_IN_KNOWLEDGE_ONLY_MODE")
        if self._run_recorder is not None and sync_run_id is None:
            raise ValueError("sync_run_id is required when a run_recorder is configured")

        participating = source_asset_ids if source_asset_ids is not None else frozenset(schema.sources)
        ordered_source_ids = sorted(participating)
        connectors_by_source = {
            source_asset_id: self._connectors.resolve(source_asset_id)
            for source_asset_id in ordered_source_ids
        }
        watermarks: dict[str, SourceCursor] = {}
        for source_asset_id, connector in connectors_by_source.items():
            watermarks[source_asset_id] = await connector.capture_high_watermark(
                source_asset_id=source_asset_id
            )

        if self._run_recorder is not None:
            assert sync_run_id is not None
            await self._run_recorder.record_watermarks(
                SyncRunManifest(
                    sync_run_id=sync_run_id,
                    graph_generation_id=graph_generation_id,
                    schema_fingerprint=schema.configuration_checksum,
                    source_watermarks=tuple(
                        SourceWatermarkRecord(
                            source_asset_id=source_asset_id,
                            captured_watermark=watermarks[source_asset_id],
                            connector_version=type(connectors_by_source[source_asset_id]).__qualname__,
                        )
                        for source_asset_id in ordered_source_ids
                    ),
                )
            )

        total_nodes = 0
        total_relationships = 0
        for source_asset_id, connector in connectors_by_source.items():
            async for page in connector.scan(
                schema=schema,
                source_asset_id=source_asset_id,
                after=None,
                through=watermarks[source_asset_id],
            ):
                nodes, relationships, _touched_entity_ids = await self._project_page(
                    schema=schema,
                    source_asset_id=source_asset_id,
                    graph_generation_id=graph_generation_id,
                    fencing_token=fencing_token,
                    page=page,
                    expected_generation_status=expected_generation_status,
                    reconcile_ownership=True,
                )
                total_nodes += nodes
                total_relationships += relationships

        if self._run_recorder is not None:
            assert sync_run_id is not None
            await self._run_recorder.record_scan_completed(sync_run_id)

        if self._reconciler is not None:
            # Stage B, graph-side, after every participating source's Stage A has
            # completed.
            counts = await self._reconciler.reconcile_relationships(
                schema=schema,
                graph_generation_id=graph_generation_id,
                fencing_token=fencing_token,
                expected_generation_status=expected_generation_status,
            )
            total_relationships += sum(counts.values())
            if self._run_recorder is not None:
                assert sync_run_id is not None
                await self._run_recorder.record_reconciliation_completed(sync_run_id)
        return total_nodes, total_relationships

    async def incremental_sync(
        self,
        *,
        schema: ActiveSchema,
        graph_generation_id: str,
        fencing_token: int,
        expected_generation_status: GraphGenerationStatus = GraphGenerationStatus.ACTIVE,
    ) -> tuple[int, int]:
        """Incremental Stage A (node upserts/deletes) then, per page, Incremental
        Stage B: reconcile only the relationships touched by that page's changed
        entities -- never the whole schema, unlike full_sync's single end-of-run
        reconciliation. The checkpoint advances only after both stages succeed
        for a page; advancing right after the node write risks permanently
        skipping a relationship that later fails to reconcile.
        """

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
                nodes, relationships, touched_entity_ids = await self._project_page(
                    schema=schema,
                    source_asset_id=source_asset_id,
                    graph_generation_id=graph_generation_id,
                    fencing_token=fencing_token,
                    page=page,
                    expected_generation_status=expected_generation_status,
                )
                total_nodes += nodes
                total_relationships += relationships

                if self._reconciler is not None and touched_entity_ids:
                    affected_relationship_ids = tuple(
                        sorted(
                            relationship_id
                            for relationship_id, relationship in schema.graph.relationships.items()
                            if relationship.source_entity_id in touched_entity_ids
                            or relationship.target_entity_id in touched_entity_ids
                        )
                    )
                    if affected_relationship_ids:
                        counts = await self._reconciler.reconcile_relationships(
                            schema=schema,
                            graph_generation_id=graph_generation_id,
                            fencing_token=fencing_token,
                            expected_generation_status=expected_generation_status,
                            relationship_ids=affected_relationship_ids,
                        )
                        total_relationships += sum(counts.values())

                new_checkpoint = _highest_cursor(connector, source_asset_id, after, page)
                if new_checkpoint is not None:
                    await self._checkpoints.write(
                        source_asset_id=source_asset_id,
                        graph_generation_id=graph_generation_id,
                        checkpoint=new_checkpoint,
                        fencing_token=fencing_token,
                    )
                    after = new_checkpoint
        return total_nodes, total_relationships

    async def _project_page(
        self,
        *,
        schema: ActiveSchema,
        source_asset_id: str,
        graph_generation_id: str,
        fencing_token: int,
        page: RawSourcePage,
        expected_generation_status: GraphGenerationStatus = GraphGenerationStatus.BUILDING,
        reconcile_ownership: bool = False,
    ) -> tuple[int, int, frozenset[str]]:
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
        nodes, relationships = (0, 0)
        if records:
            nodes, relationships = await self._writer.project_and_write(
                schema=schema,
                graph_generation_id=graph_generation_id,
                records=records,
                fencing_token=fencing_token,
                expected_generation_status=expected_generation_status,
            )

        # Ownership reconciliation is explicitly opt-in per call (never implied
        # just because a reconciler is configured) so incremental_sync -- which
        # has no equivalent concept yet -- can never accidentally pick it up.
        if reconcile_ownership and self._ownership_reconciler is not None:
            await self._reconcile_ownership_for_page(
                schema=schema,
                source_asset_id=source_asset_id,
                graph_generation_id=graph_generation_id,
                fencing_token=fencing_token,
                expected_generation_status=expected_generation_status,
                page=page,
                mutations=mutations,
            )
        touched_entity_ids = frozenset(mutation.entity_id for mutation in mutations)
        return nodes, relationships, touched_entity_ids

    async def _reconcile_ownership_for_page(
        self,
        *,
        schema: ActiveSchema,
        source_asset_id: str,
        graph_generation_id: str,
        fencing_token: int,
        expected_generation_status: GraphGenerationStatus,
        page: RawSourcePage,
        mutations: tuple[DynamicRecordMutation, ...],
    ) -> None:
        assert self._ownership_reconciler is not None
        ownership_entities = [
            entity
            for entity in schema.entities.values()
            if entity.source_asset_id == source_asset_id and entity.ownership_policy is not None
        ]
        if not ownership_entities:
            return

        # Grouped from the mutations actually produced, keyed by (entity, the
        # parent document's own source_identity -- every exploded child's
        # mutation carries its parent's source_identity, never its own).
        children_by_parent: dict[tuple[str, str], list[DynamicRecordMutation]] = defaultdict(list)
        for mutation in mutations:
            if (
                mutation.operation == "UPSERT"
                and mutation.read_scope is ProjectionReadScope.COMPLETE_SOURCE_DOCUMENT
            ):
                children_by_parent[(mutation.entity_id, mutation.source_identity)].append(mutation)

        for document in page.documents:
            if document.operation != "UPSERT":
                continue
            for entity in ownership_entities:
                group = children_by_parent.get((entity.entity_id, document.source_identity), [])
                current_children = tuple(
                    (canonical_key_hash(mutation.resolved_key), dict(mutation.resolved_key))
                    for mutation in group
                )
                projection_id = schema.entity_node(entity.entity_id).projection_id
                source_version = group[0].source_version if group else None
                await self._ownership_reconciler.reconcile_child_ownership(
                    schema=schema,
                    graph_generation_id=graph_generation_id,
                    fencing_token=fencing_token,
                    expected_generation_status=expected_generation_status,
                    projection_id=projection_id,
                    source_asset_id=entity.source_asset_id,
                    source_identity=document.source_identity,
                    current_children=current_children,
                    source_version=source_version,
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
