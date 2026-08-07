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
