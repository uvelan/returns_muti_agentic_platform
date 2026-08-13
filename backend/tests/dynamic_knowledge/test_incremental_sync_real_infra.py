"""W2.8's Validation clause against real MongoDB: a second run must read less.

An incremental sync that silently full-scans is indistinguishable from a correct
one at every surface an operator has. The run list shows the same rows, the same
statuses and the same green ticks; only the cost differs, and only against a real
source at real volume. So the assertions here are about *which documents each run
read*, never about whether a run succeeded.

`incremental_sync` and `MongoDBSourceScanConnector.scan` were both already
written to the cursor contract. What did not exist was a `CheckpointStore` that
persists anything -- the only implementation in the tree raised
`NotImplementedError`, and the coordinator's unit tests used a fake whose `read`
returns `None` on every call. `after=None` is precisely the input under which a
resume and a full scan produce identical results, so the existing suite could not
have caught a broken resume. These tests exist to close that.

Everything runs against `source_shipments` on the descriptor as shipped: one key
field, no ownership policy, and a cursor W2.6 established resolves to
`shipmentInfoEventMeta.lastUpdateTs` rather than a root `updatedAt` that exists
on none of the 100 verified documents. Timestamps are whole seconds because BSON
stores datetimes at millisecond precision, and a fixture carrying microseconds
would compare a truncated stored value against an untruncated Python one.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
import pytest_asyncio
from pymongo import AsyncMongoClient

from return_platform.dynamic_knowledge.config_loader import load_active_schema
from return_platform.dynamic_knowledge.graph.generation import GraphGenerationStatus
from return_platform.dynamic_knowledge.on_demand_sync.contracts import DynamicSourceRecord
from return_platform.dynamic_knowledge.on_demand_sync.extraction import (
    GenericSourceRecordExtractor,
)
from return_platform.dynamic_knowledge.schema import ActiveSchema
from return_platform.dynamic_knowledge.sync.adapters import scan_connector_registry
from return_platform.dynamic_knowledge.sync.checkpoint_store import (
    CheckpointFenced,
    MongoSyncCheckpointStore,
)
from return_platform.dynamic_knowledge.sync.coordinator import GenericSyncCoordinator
from return_platform.source_connectors.contracts import SourceCursor
from return_platform.source_connectors.mongodb import (
    MongoConnectorError,
    MongoDBSourceScanConnector,
)

SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "config"
    / "dynamic_knowledge"
    / "active-schema.return-order.yaml"
)
SOURCE_ID = "source_shipments"
GENERATION_ID = "incremental-real-infra"
FENCING_TOKEN = 1

#: Whole seconds, ascending, one per seeded shipment. Fixed rather than
#: `datetime.now(UTC)` so an assertion can name the exact cursor value a run is
#: expected to stop at.
BASE_TIME = datetime(2026, 8, 10, 12, 0, 0, tzinfo=UTC)


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise RuntimeError(f"Required test environment variable is not set: {name}")
    return value


def _mongo_dsn(database: str) -> str:
    """`directConnection=true` -- see `test_return_record_sync_real_infra._mongo_dsn`."""
    username = quote(_required_env("MONGO_ROOT_USERNAME"), safe="")
    password = quote(_required_env("MONGO_ROOT_PASSWORD"), safe="")
    host = os.getenv("PLATFORM_TEST_MONGO_HOST", "localhost")
    return (
        f"mongodb://{username}:{password}@{host}:27017/"
        f"{database}?authSource=admin&directConnection=true"
    )


class RecordingWriter:
    """Records the tracking numbers each `project_and_write` call was handed.

    A real `Neo4jDynamicGraphWriter` would prove the graph ends up correct and
    say nothing about what was *read*, which is the entire question here. The
    per-call grouping also makes "processed twice" directly observable rather
    than inferred from a final node count -- MERGE-based writes are idempotent,
    so a duplicated read leaves no trace in the graph at all.
    """

    def __init__(self, *, fail_on_call: int | None = None) -> None:
        self.calls: list[tuple[str, ...]] = []
        self._fail_on_call = fail_on_call

    @property
    def tracking_numbers(self) -> list[str]:
        return [tracking for call in self.calls for tracking in call]

    async def project_and_write(
        self,
        *,
        schema: ActiveSchema,
        graph_generation_id: str,
        records: tuple[DynamicSourceRecord, ...],
        fencing_token: int,
        expected_generation_status: GraphGenerationStatus,
    ) -> tuple[int, int]:
        del schema, graph_generation_id, fencing_token, expected_generation_status
        if self._fail_on_call is not None and len(self.calls) + 1 == self._fail_on_call:
            # Raised *before* recording, so the failed page counts as neither
            # read nor written -- the restart must cover it.
            raise RuntimeError("PROJECTION_WRITE_FAILED")
        self.calls.append(tuple(str(record.natural_key["tracking_number"]) for record in records))
        return len(records), 0


class Harness:
    def __init__(self) -> None:
        self.suffix = uuid.uuid4().hex[:12]
        self.platform_database = f"incremental_platform_{self.suffix}"
        self.source_database = f"incremental_source_{self.suffix}"
        self.schema = load_active_schema(SCHEMA_PATH)
        self.mongo: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
            _mongo_dsn(self.platform_database)
        )
        self.checkpoints = MongoSyncCheckpointStore(self.mongo, self.platform_database)

    def tracking(self, index: int) -> str:
        return f"1Z{self.suffix.upper()}{index:02d}"

    async def seed(self, count: int) -> None:
        """`count` shipments, one second apart, in the shape the verified
        contract declares."""
        for index in range(count):
            await self.write_shipment(index, BASE_TIME + timedelta(seconds=index))

    async def write_shipment(self, index: int, updated_at: datetime) -> None:
        tracking = self.tracking(index)
        await self.mongo[self.source_database]["shipmentInfo"].replace_one(
            {"_id": f"DIST*CW273354*{tracking}"},
            {
                "shipmentInfoEventData": {
                    "trkNum": tracking,
                    "trilOrdNum": "CW273354",
                    "shipmentId": f"SHP-{self.suffix}-{index:02d}",
                    "acctId": "DIST",
                    "currentStatus": "intransit",
                    "srcSystem": "DispatchTrack",
                },
                "shipmentInfoEventMeta": {
                    "docType": "disptrck",
                    "insertTs": BASE_TIME,
                    "lastUpdateTs": updated_at,
                    "updatedBy": "shipment-writer-v1",
                },
            },
            upsert=True,
        )

    async def delete_shipment(self, index: int) -> None:
        tracking = self.tracking(index)
        await self.mongo[self.source_database]["shipmentInfo"].delete_one(
            {"_id": f"DIST*CW273354*{tracking}"}
        )

    def coordinator(self, writer: RecordingWriter, *, page_size: int = 1) -> GenericSyncCoordinator:
        """Assembled as `GraphSyncService._sync_participating_sources` assembles it,
        minus the reconcilers -- Stage B is orthogonal to which records were read,
        and the coordinator's own unit tests already cover the interaction."""
        connector = MongoDBSourceScanConnector(
            self.mongo[self.source_database], schema=self.schema, page_size=page_size
        )
        return GenericSyncCoordinator(
            connectors=scan_connector_registry(
                schema=self.schema, mongo_connector=connector, sqlserver_connector=None
            ),
            extractor=GenericSourceRecordExtractor(),
            writer=writer,
            checkpoints=self.checkpoints,
        )

    async def run(self, writer: RecordingWriter, *, page_size: int = 1) -> tuple[int, int]:
        return await self.coordinator(writer, page_size=page_size).incremental_sync(
            schema=self.schema,
            graph_generation_id=GENERATION_ID,
            fencing_token=FENCING_TOKEN,
            source_asset_ids=frozenset({SOURCE_ID}),
        )

    async def stored_checkpoint(self) -> SourceCursor | None:
        return await self.checkpoints.read(
            source_asset_id=SOURCE_ID, graph_generation_id=GENERATION_ID
        )

    async def close(self) -> None:
        await self.mongo.drop_database(self.source_database)
        await self.mongo.drop_database(self.platform_database)
        await self.mongo.close()


@pytest_asyncio.fixture
async def harness() -> AsyncIterator[Harness]:
    instance = Harness()
    try:
        yield instance
    finally:
        await instance.close()


@pytest.mark.asyncio
async def test_the_first_run_has_no_checkpoint_and_reads_every_document(
    harness: Harness,
) -> None:
    """A first run legitimately reads everything, and leaves a resumable position.

    The second half is the load-bearing part: if `write` silently dropped the
    checkpoint, every subsequent run would also read everything and still pass a
    test that only checked the first run's output.
    """
    await harness.seed(3)
    writer = RecordingWriter()

    nodes, _relationships = await harness.run(writer)

    assert nodes == 3
    assert writer.tracking_numbers == [harness.tracking(i) for i in range(3)]
    checkpoint = await harness.stored_checkpoint()
    assert checkpoint is not None
    assert checkpoint.cursor_type == "FIELD_DATETIME"
    assert datetime.fromisoformat(checkpoint.encoded_value) == BASE_TIME + timedelta(seconds=2)


@pytest.mark.asyncio
async def test_the_second_run_reads_only_the_record_that_changed(harness: Harness) -> None:
    """The whole point of the step, and the one thing S6 cannot show.

    Would catch a scan that ignores `after`, a checkpoint that is never written,
    and a checkpoint that is written but never read -- all three produce a second
    run that reads all four documents and reports success.
    """
    await harness.seed(4)
    await harness.run(RecordingWriter())

    # One record changes, after every document the first run saw.
    await harness.write_shipment(1, BASE_TIME + timedelta(seconds=30))

    second = RecordingWriter()
    nodes, _relationships = await harness.run(second)

    assert nodes == 1
    assert second.tracking_numbers == [harness.tracking(1)]
    checkpoint = await harness.stored_checkpoint()
    assert checkpoint is not None
    assert datetime.fromisoformat(checkpoint.encoded_value) == BASE_TIME + timedelta(seconds=30)


@pytest.mark.asyncio
async def test_a_run_with_nothing_to_do_reads_nothing_and_holds_its_position(
    harness: Harness,
) -> None:
    """An unchanged source costs one watermark query and no scan.

    Distinct from the test above: there, a full-scanning implementation is caught
    by reading the wrong *documents*; here it is caught by reading any at all.
    """
    await harness.seed(3)
    await harness.run(RecordingWriter())
    before = await harness.stored_checkpoint()

    second = RecordingWriter()
    nodes, _relationships = await harness.run(second)

    assert nodes == 0
    assert second.calls == []
    assert await harness.stored_checkpoint() == before


@pytest.mark.asyncio
async def test_a_run_that_fails_partway_resumes_from_the_last_committed_page(
    harness: Harness,
) -> None:
    """The step's Validation clause: sync N, fail, restart, resume, no duplicates.

    The failure is raised by the projection write, before the coordinator reaches
    its checkpoint write, so the checkpoint must sit at page 2 -- the last page
    whose write actually committed. A checkpoint advanced *before* the write is
    the worst failure this contract has: records 3 and 4 would be skipped
    permanently, the restart would report success, and nothing in the run list
    would differ.

    `page_size=1` makes each document its own page and its own checkpoint, which
    is what lets "resumed from exactly here" be asserted rather than approximated.
    """
    await harness.seed(4)

    failing = RecordingWriter(fail_on_call=3)
    with pytest.raises(RuntimeError, match="PROJECTION_WRITE_FAILED"):
        await harness.run(failing)

    assert failing.tracking_numbers == [harness.tracking(0), harness.tracking(1)]
    checkpoint = await harness.stored_checkpoint()
    assert checkpoint is not None
    assert datetime.fromisoformat(checkpoint.encoded_value) == BASE_TIME + timedelta(seconds=1)

    restarted = RecordingWriter()
    await harness.run(restarted)

    # Resumes at 2, and does not re-read 0 or 1.
    assert restarted.tracking_numbers == [harness.tracking(2), harness.tracking(3)]
    everything = failing.tracking_numbers + restarted.tracking_numbers
    assert sorted(everything) == [harness.tracking(i) for i in range(4)]
    assert len(everything) == len(set(everything))


@pytest.mark.asyncio
async def test_a_checkpoint_whose_record_was_deleted_still_resumes_from_that_position(
    harness: Harness,
) -> None:
    """A cursor is a position, not a reference to a row.

    Deleting the document the stored value was taken from must not strand the
    scan (nothing after it ever read again) or restart it (everything read
    again). Both failures are silent.
    """
    await harness.seed(3)
    await harness.run(RecordingWriter())

    await harness.delete_shipment(2)  # the document the checkpoint's value came from
    await harness.write_shipment(3, BASE_TIME + timedelta(seconds=10))

    second = RecordingWriter()
    await harness.run(second)

    assert second.tracking_numbers == [harness.tracking(3)]


@pytest.mark.asyncio
async def test_a_checkpoint_from_a_different_cursor_strategy_is_refused(
    harness: Harness,
) -> None:
    """A stored cursor that predates a change to `incremental_cursor_field`.

    Fail closed. Resuming would compare an ObjectId against a datetime; falling
    back to `after=None` would be a full rescan reported as an incremental run,
    which is the failure this whole module is about.
    """
    await harness.seed(2)
    await harness.checkpoints.write(
        source_asset_id=SOURCE_ID,
        graph_generation_id=GENERATION_ID,
        checkpoint=SourceCursor(cursor_type="OBJECT_ID", encoded_value="68f3a1b2c3d4e5f607182930"),
        fencing_token=FENCING_TOKEN,
    )

    writer = RecordingWriter()
    with pytest.raises(MongoConnectorError, match="cannot be resumed from"):
        await harness.run(writer)
    assert writer.calls == []


@pytest.mark.asyncio
async def test_a_fenced_off_run_cannot_rewind_a_live_cursor(harness: Harness) -> None:
    """Two writers on one generation, the older one still running.

    A silently-dropped write would be worse than this exception: the losing run
    would carry on scanning and reporting success against a checkpoint that never
    moved, re-reading the same window forever.
    """
    live = SourceCursor(cursor_type="FIELD_DATETIME", encoded_value=BASE_TIME.isoformat())
    await harness.checkpoints.write(
        source_asset_id=SOURCE_ID,
        graph_generation_id=GENERATION_ID,
        checkpoint=live,
        fencing_token=7,
    )

    with pytest.raises(CheckpointFenced, match="fenced off"):
        await harness.checkpoints.write(
            source_asset_id=SOURCE_ID,
            graph_generation_id=GENERATION_ID,
            checkpoint=SourceCursor(
                cursor_type="FIELD_DATETIME",
                encoded_value=(BASE_TIME - timedelta(days=1)).isoformat(),
            ),
            fencing_token=6,
        )

    assert await harness.stored_checkpoint() == live


@pytest.mark.asyncio
async def test_checkpoints_are_scoped_to_one_graph_generation(harness: Harness) -> None:
    """A rebuild's target generation starts from its own position.

    Sharing a cursor across generations would make a fresh generation resume at
    the live one's watermark and build a graph missing everything older -- an
    empty-looking rebuild that completes green.
    """
    await harness.seed(2)
    await harness.run(RecordingWriter())
    assert await harness.stored_checkpoint() is not None

    assert (
        await harness.checkpoints.read(
            source_asset_id=SOURCE_ID, graph_generation_id="some-other-generation"
        )
        is None
    )
