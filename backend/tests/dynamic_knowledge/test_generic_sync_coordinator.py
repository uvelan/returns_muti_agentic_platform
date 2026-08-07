from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest

from return_platform.dynamic_knowledge.graph.generation import GraphGenerationStatus
from return_platform.dynamic_knowledge.on_demand_sync.contracts import (
    CursorComparison,
    DynamicSourceRecord,
    RawSourceDocument,
    RawSourcePage,
    SourceCursor,
)
from return_platform.dynamic_knowledge.on_demand_sync.extraction import (
    GenericSourceRecordExtractor,
)
from return_platform.dynamic_knowledge.schema import ActiveSchema
from return_platform.dynamic_knowledge.sync.coordinator import GenericSyncCoordinator
from return_platform.dynamic_knowledge.sync.run_manifest import SyncRunManifest


def _numeric_cursor(value: int) -> SourceCursor:
    return SourceCursor(cursor_type="NUMERIC", encoded_value=str(value))


class NumericOrderedConnector:
    """A connector whose cursor values sort correctly only when compared numerically,
    never lexically -- this is what proves the coordinator delegates comparison
    rather than casting to str and calling max()."""

    def __init__(self, pages: list[RawSourcePage], watermark: SourceCursor) -> None:
        self._pages = pages
        self._watermark = watermark
        self.scanned_after: list[SourceCursor | None] = []

    async def capture_high_watermark(self, *, source_asset_id: str) -> SourceCursor:
        return self._watermark

    def compare_cursors(
        self, *, source_asset_id: str, left: SourceCursor, right: SourceCursor
    ) -> CursorComparison:
        left_value, right_value = int(left.encoded_value), int(right.encoded_value)
        if left_value < right_value:
            return CursorComparison.BEFORE
        if left_value > right_value:
            return CursorComparison.AFTER
        return CursorComparison.EQUAL

    async def scan(
        self,
        *,
        schema: ActiveSchema,
        source_asset_id: str,
        after: SourceCursor | None,
        through: SourceCursor,
    ) -> AsyncIterator[RawSourcePage]:
        self.scanned_after.append(after)
        for page in self._pages:
            yield page


class Registry:
    def __init__(self, connector: object) -> None:
        self._connector = connector

    def resolve(self, source_asset_id: str) -> object:
        return self._connector


class RecordingWriter:
    def __init__(self) -> None:
        self.written: list[tuple[str, ...]] = []

    async def project_and_write(
        self,
        *,
        schema: ActiveSchema,
        graph_generation_id: str,
        records: tuple[DynamicSourceRecord, ...],
        fencing_token: int,
    ) -> tuple[int, int]:
        self.written.append(tuple(record.natural_key["id"] for record in records))
        return len(records), 0


class RecordingCheckpoints:
    def __init__(self) -> None:
        self.written: list[SourceCursor] = []

    async def read(
        self, *, source_asset_id: str, graph_generation_id: str
    ) -> SourceCursor | None:
        return None

    async def write(
        self,
        *,
        source_asset_id: str,
        graph_generation_id: str,
        checkpoint: SourceCursor,
        fencing_token: int,
    ) -> None:
        self.written.append(checkpoint)


def _page(order_id: str, cursor_value: int) -> RawSourcePage:
    return RawSourcePage(
        documents=(
            RawSourceDocument(
                operation="UPSERT",
                document={"configured_id": order_id, "configured_name": "n"},
                source_identity=order_id,
            ),
        ),
        next_cursor=_numeric_cursor(cursor_value),
        observed_at=datetime(2026, 8, 6, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_incremental_checkpoint_uses_connector_ordering_not_lexical_string_max(
    active_schema: ActiveSchema,
) -> None:
    """Regression test: cursor "10" must be treated as AFTER "2", never BEFORE it."""
    pages = [_page("A-2", 2), _page("A-10", 10)]
    connector = NumericOrderedConnector(pages, watermark=_numeric_cursor(10))
    checkpoints = RecordingCheckpoints()
    coordinator = GenericSyncCoordinator(
        connectors=Registry(connector),
        extractor=GenericSourceRecordExtractor(),
        writer=RecordingWriter(),
        checkpoints=checkpoints,
    )
    await coordinator.incremental_sync(
        schema=active_schema, graph_generation_id="g1", fencing_token=1
    )
    assert [cursor.encoded_value for cursor in checkpoints.written] == ["2", "10"]


@pytest.mark.asyncio
async def test_full_sync_writes_records_from_every_page(active_schema: ActiveSchema) -> None:
    pages = [_page("A-1", 1), _page("A-2", 2)]
    connector = NumericOrderedConnector(pages, watermark=_numeric_cursor(2))
    writer = RecordingWriter()
    coordinator = GenericSyncCoordinator(
        connectors=Registry(connector),
        extractor=GenericSourceRecordExtractor(),
        writer=writer,
        checkpoints=RecordingCheckpoints(),
    )
    nodes, relationships = await coordinator.full_sync(
        schema=active_schema, graph_generation_id="g1", fencing_token=1
    )
    assert nodes == 2
    assert writer.written == [("A-1",), ("A-2",)]


class RecordingReconciler:
    def __init__(self, counts: dict[str, int]) -> None:
        self._counts = counts
        self.calls: list[dict[str, object]] = []

    async def reconcile_relationships(
        self,
        *,
        schema: ActiveSchema,
        graph_generation_id: str,
        fencing_token: int,
        expected_generation_status: GraphGenerationStatus,
        relationship_ids: tuple[str, ...] | None = None,
    ) -> dict[str, int]:
        self.calls.append(
            {
                "graph_generation_id": graph_generation_id,
                "fencing_token": fencing_token,
                "expected_generation_status": expected_generation_status,
                "relationship_ids": relationship_ids,
            }
        )
        return self._counts


@pytest.mark.asyncio
async def test_full_sync_runs_stage_b_reconciliation_after_every_source_completes(
    active_schema: ActiveSchema,
) -> None:
    pages = [_page("A-1", 1)]
    connector = NumericOrderedConnector(pages, watermark=_numeric_cursor(1))
    reconciler = RecordingReconciler({"a_to_b": 2})
    coordinator = GenericSyncCoordinator(
        connectors=Registry(connector),
        extractor=GenericSourceRecordExtractor(),
        writer=RecordingWriter(),
        checkpoints=RecordingCheckpoints(),
        reconciler=reconciler,
    )
    nodes, relationships = await coordinator.full_sync(
        schema=active_schema, graph_generation_id="g1", fencing_token=1
    )
    assert relationships == 2
    assert len(reconciler.calls) == 1
    assert reconciler.calls[0]["graph_generation_id"] == "g1"
    assert reconciler.calls[0]["expected_generation_status"] is GraphGenerationStatus.BUILDING


@pytest.mark.asyncio
async def test_full_sync_honors_expected_generation_status_override(
    active_schema: ActiveSchema,
) -> None:
    pages = [_page("A-1", 1)]
    connector = NumericOrderedConnector(pages, watermark=_numeric_cursor(1))
    reconciler = RecordingReconciler({})
    coordinator = GenericSyncCoordinator(
        connectors=Registry(connector),
        extractor=GenericSourceRecordExtractor(),
        writer=RecordingWriter(),
        checkpoints=RecordingCheckpoints(),
        reconciler=reconciler,
    )
    await coordinator.full_sync(
        schema=active_schema,
        graph_generation_id="g1",
        fencing_token=1,
        expected_generation_status=GraphGenerationStatus.ACTIVE,
    )
    assert reconciler.calls[0]["expected_generation_status"] is GraphGenerationStatus.ACTIVE


@pytest.mark.asyncio
async def test_full_sync_narrows_to_the_given_source_asset_ids(active_schema: ActiveSchema) -> None:
    pages = [_page("A-1", 1)]
    connector_a = NumericOrderedConnector(pages, watermark=_numeric_cursor(1))
    connector_b = NumericOrderedConnector(pages, watermark=_numeric_cursor(1))

    class TwoSourceRegistry:
        def resolve(self, source_asset_id: str) -> object:
            return connector_a if source_asset_id == "source_a" else connector_b

    coordinator = GenericSyncCoordinator(
        connectors=TwoSourceRegistry(),
        extractor=GenericSourceRecordExtractor(),
        writer=RecordingWriter(),
        checkpoints=RecordingCheckpoints(),
    )
    await coordinator.full_sync(
        schema=active_schema,
        graph_generation_id="g1",
        fencing_token=1,
        source_asset_ids=frozenset({"source_a"}),
    )
    assert connector_a.scanned_after == [None]
    assert connector_b.scanned_after == []


class EventLoggingConnector:
    """Logs capture/scan events to a list shared across every connector
    instance in a run, so ordering across *different* sources is observable
    -- NumericOrderedConnector's own scanned_after list can't show that."""

    def __init__(self, source_asset_id: str, events: list[tuple[str, str]], watermark: SourceCursor) -> None:
        self._source_asset_id = source_asset_id
        self._events = events
        self._watermark = watermark

    async def capture_high_watermark(self, *, source_asset_id: str) -> SourceCursor:
        self._events.append(("watermark", source_asset_id))
        return self._watermark

    def compare_cursors(
        self, *, source_asset_id: str, left: SourceCursor, right: SourceCursor
    ) -> CursorComparison:
        return CursorComparison.EQUAL

    async def scan(
        self,
        *,
        schema: ActiveSchema,
        source_asset_id: str,
        after: SourceCursor | None,
        through: SourceCursor,
    ) -> AsyncIterator[RawSourcePage]:
        self._events.append(("scan", source_asset_id))
        return
        yield  # pragma: no cover - makes this an async generator


@pytest.mark.asyncio
async def test_full_sync_captures_every_watermark_before_any_source_scans(
    active_schema: ActiveSchema,
) -> None:
    events: list[tuple[str, str]] = []
    connector_a = EventLoggingConnector("source_a", events, _numeric_cursor(1))
    connector_b = EventLoggingConnector("source_b", events, _numeric_cursor(1))

    class TwoSourceRegistry:
        def resolve(self, source_asset_id: str) -> object:
            return connector_a if source_asset_id == "source_a" else connector_b

    coordinator = GenericSyncCoordinator(
        connectors=TwoSourceRegistry(),
        extractor=GenericSourceRecordExtractor(),
        writer=RecordingWriter(),
        checkpoints=RecordingCheckpoints(),
    )
    await coordinator.full_sync(schema=active_schema, graph_generation_id="g1", fencing_token=1)
    watermark_events = [event for event in events if event[0] == "watermark"]
    scan_events = [event for event in events if event[0] == "scan"]
    assert len(watermark_events) == 2
    # Both watermark captures happened before either scan started.
    last_watermark_index = max(events.index(event) for event in watermark_events)
    first_scan_index = min(events.index(event) for event in scan_events)
    assert last_watermark_index < first_scan_index


class RecordingRunRecorder:
    def __init__(self) -> None:
        self.manifests: list[SyncRunManifest] = []
        self.scan_completed_calls: list[str] = []
        self.reconciliation_completed_calls: list[str] = []

    async def record_watermarks(self, manifest: SyncRunManifest) -> None:
        self.manifests.append(manifest)

    async def record_scan_completed(self, sync_run_id: str) -> None:
        self.scan_completed_calls.append(sync_run_id)

    async def record_reconciliation_completed(self, sync_run_id: str) -> None:
        self.reconciliation_completed_calls.append(sync_run_id)


@pytest.mark.asyncio
async def test_full_sync_records_a_manifest_before_scanning_and_completion_after(
    active_schema: ActiveSchema,
) -> None:
    pages = [_page("A-1", 1)]
    connector = NumericOrderedConnector(pages, watermark=_numeric_cursor(1))
    recorder = RecordingRunRecorder()
    reconciler = RecordingReconciler({"a_to_b": 1})
    coordinator = GenericSyncCoordinator(
        connectors=Registry(connector),
        extractor=GenericSourceRecordExtractor(),
        writer=RecordingWriter(),
        checkpoints=RecordingCheckpoints(),
        reconciler=reconciler,
        run_recorder=recorder,
    )
    await coordinator.full_sync(
        schema=active_schema,
        graph_generation_id="g1",
        fencing_token=1,
        sync_run_id="run-1",
    )
    assert len(recorder.manifests) == 1
    manifest = recorder.manifests[0]
    assert manifest.sync_run_id == "run-1"
    assert manifest.graph_generation_id == "g1"
    assert manifest.schema_fingerprint == active_schema.configuration_checksum
    assert {record.source_asset_id for record in manifest.source_watermarks} == set(active_schema.sources)
    assert recorder.scan_completed_calls == ["run-1"]
    assert recorder.reconciliation_completed_calls == ["run-1"]


@pytest.mark.asyncio
async def test_full_sync_requires_sync_run_id_when_a_run_recorder_is_configured(
    active_schema: ActiveSchema,
) -> None:
    connector = NumericOrderedConnector([], watermark=_numeric_cursor(1))
    coordinator = GenericSyncCoordinator(
        connectors=Registry(connector),
        extractor=GenericSourceRecordExtractor(),
        writer=RecordingWriter(),
        checkpoints=RecordingCheckpoints(),
        run_recorder=RecordingRunRecorder(),
    )
    with pytest.raises(ValueError, match="sync_run_id is required"):
        await coordinator.full_sync(schema=active_schema, graph_generation_id="g1", fencing_token=1)


@pytest.mark.asyncio
async def test_full_sync_without_a_reconciler_skips_stage_b(active_schema: ActiveSchema) -> None:
    pages = [_page("A-1", 1)]
    connector = NumericOrderedConnector(pages, watermark=_numeric_cursor(1))
    coordinator = GenericSyncCoordinator(
        connectors=Registry(connector),
        extractor=GenericSourceRecordExtractor(),
        writer=RecordingWriter(),
        checkpoints=RecordingCheckpoints(),
    )
    nodes, relationships = await coordinator.full_sync(
        schema=active_schema, graph_generation_id="g1", fencing_token=1
    )
    assert relationships == 0


def _ownership_schema(active_schema: ActiveSchema) -> ActiveSchema:
    raw = active_schema.model_dump(mode="json")
    raw["entities"]["entity_b"] = {
        "entity_id": "entity_b",
        "source_asset_id": "source_b",
        "record_path": ["items"],
        "explode": True,
        "ownership_policy": {"mode": "REPLACE_CHILD_SET", "owner_identity": "SOURCE_DOCUMENT"},
        "fields": {
            "id": {
                "field_id": "id",
                "physical_path": ["itemId"],
                "graph_property": "related_id",
                "data_type": "STRING",
                "nullable": False,
                "capabilities": {"searchable": True, "filterable": True, "operators": ["EXACT"]},
                "permissions": {"searchable_by": ["associate"], "displayable_by": ["associate"]},
            },
        },
        "natural_key": ["id"],
        "strong_anchors": {},
    }
    raw["graph"]["nodes"]["node_b"]["property_fields"] = []
    raw["graph"]["relationships"] = {}
    return ActiveSchema.model_validate(raw)


def _document_page(source_identity: str, document: dict[str, object]) -> RawSourcePage:
    return RawSourcePage(
        documents=(
            RawSourceDocument(operation="UPSERT", document=document, source_identity=source_identity),
        ),
        next_cursor=_numeric_cursor(1),
        observed_at=datetime(2026, 8, 6, tzinfo=UTC),
    )


class RecordingOwnershipReconciler:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

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
        current_children: tuple[tuple[str, dict[str, object]], ...],
        source_version: str | None = None,
    ) -> None:
        self.calls.append(
            {
                "projection_id": projection_id,
                "source_asset_id": source_asset_id,
                "source_identity": source_identity,
                "current_children": current_children,
                "expected_generation_status": expected_generation_status,
            }
        )


@pytest.mark.asyncio
async def test_full_sync_reconciles_ownership_per_parent_document(active_schema: ActiveSchema) -> None:
    schema = _ownership_schema(active_schema)
    page = _document_page("parent-1", {"items": [{"itemId": "C-1"}, {"itemId": "C-2"}]})
    connector = NumericOrderedConnector([page], watermark=_numeric_cursor(1))
    reconciler = RecordingOwnershipReconciler()
    coordinator = GenericSyncCoordinator(
        connectors=Registry(connector),
        extractor=GenericSourceRecordExtractor(),
        writer=RecordingWriter(),
        checkpoints=RecordingCheckpoints(),
        ownership_reconciler=reconciler,
    )
    await coordinator.full_sync(
        schema=schema,
        graph_generation_id="g1",
        fencing_token=1,
        source_asset_ids=frozenset({"source_b"}),
        expected_generation_status=GraphGenerationStatus.ACTIVE,
    )
    assert len(reconciler.calls) == 1
    call = reconciler.calls[0]
    assert call["source_identity"] == "parent-1"
    assert call["projection_id"] == "node_b"
    current_ids = {values["id"] for _, values in call["current_children"]}
    assert current_ids == {"C-1", "C-2"}
    assert call["expected_generation_status"] is GraphGenerationStatus.ACTIVE


@pytest.mark.asyncio
async def test_full_sync_reconciles_ownership_with_zero_children_when_all_removed(
    active_schema: ActiveSchema,
) -> None:
    """The orphan-cleanup case: a parent document whose array is now empty must
    still trigger reconciliation (with an empty current_children set), or a
    previously-owned child would never be detected as removed."""
    schema = _ownership_schema(active_schema)
    page = _document_page("parent-1", {"items": []})
    connector = NumericOrderedConnector([page], watermark=_numeric_cursor(1))
    reconciler = RecordingOwnershipReconciler()
    coordinator = GenericSyncCoordinator(
        connectors=Registry(connector),
        extractor=GenericSourceRecordExtractor(),
        writer=RecordingWriter(),
        checkpoints=RecordingCheckpoints(),
        ownership_reconciler=reconciler,
    )
    await coordinator.full_sync(
        schema=schema,
        graph_generation_id="g1",
        fencing_token=1,
        source_asset_ids=frozenset({"source_b"}),
    )
    assert len(reconciler.calls) == 1
    assert reconciler.calls[0]["current_children"] == ()


@pytest.mark.asyncio
async def test_full_sync_without_an_ownership_reconciler_configured_skips_it(
    active_schema: ActiveSchema,
) -> None:
    schema = _ownership_schema(active_schema)
    page = _document_page("parent-1", {"items": [{"itemId": "C-1"}]})
    connector = NumericOrderedConnector([page], watermark=_numeric_cursor(1))
    coordinator = GenericSyncCoordinator(
        connectors=Registry(connector),
        extractor=GenericSourceRecordExtractor(),
        writer=RecordingWriter(),
        checkpoints=RecordingCheckpoints(),
    )
    # No exception, no-op: proves reconcile_ownership doesn't require a reconciler.
    await coordinator.full_sync(
        schema=schema, graph_generation_id="g1", fencing_token=1, source_asset_ids=frozenset({"source_b"})
    )


@pytest.mark.asyncio
async def test_incremental_sync_never_calls_the_ownership_reconciler(active_schema: ActiveSchema) -> None:
    schema = _ownership_schema(active_schema)
    raw = schema.model_dump(mode="json")
    # A real field on entity_b so this source actually participates in
    # incremental_sync (which skips any source with no incremental_cursor_field
    # at all) -- otherwise this test would trivially pass for the wrong reason.
    raw["sources"]["source_b"]["incremental_cursor_field"] = "id"
    schema = ActiveSchema.model_validate(raw)
    page = _document_page("parent-1", {"items": [{"itemId": "C-1"}]})
    connector = NumericOrderedConnector([page], watermark=_numeric_cursor(1))
    reconciler = RecordingOwnershipReconciler()
    coordinator = GenericSyncCoordinator(
        connectors=Registry(connector),
        extractor=GenericSourceRecordExtractor(),
        writer=RecordingWriter(),
        checkpoints=RecordingCheckpoints(),
        ownership_reconciler=reconciler,
    )
    await coordinator.incremental_sync(schema=schema, graph_generation_id="g1", fencing_token=1)
    assert reconciler.calls == []


@pytest.mark.asyncio
async def test_incremental_sync_scans_from_the_stored_checkpoint(
    active_schema: ActiveSchema,
) -> None:
    connector = NumericOrderedConnector([_page("A-1", 1)], watermark=_numeric_cursor(1))
    coordinator = GenericSyncCoordinator(
        connectors=Registry(connector),
        extractor=GenericSourceRecordExtractor(),
        writer=RecordingWriter(),
        checkpoints=RecordingCheckpoints(),
    )
    await coordinator.incremental_sync(schema=active_schema, graph_generation_id="g1", fencing_token=1)
    # RecordingCheckpoints.read always returns None (no prior checkpoint) in this test.
    assert connector.scanned_after == [None]
