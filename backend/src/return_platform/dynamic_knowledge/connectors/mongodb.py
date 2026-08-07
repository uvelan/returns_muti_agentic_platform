"""Generic MongoDB SourceScanConnector -- no business collection/field names here.

Two cursor strategies, selected per source purely from its configured
SourceAssetDefinition.incremental_cursor_field:

- No incremental_cursor_field configured: cursor is the document's ``_id``,
  assumed to be a standard MongoDB ObjectId (monotonically increasing per
  mongod instance, encoding an embedded timestamp). This needs no extra
  index and works for any collection using Mongo's default _id generation.
- incremental_cursor_field configured: cursor is that field's value,
  serialized as an ISO 8601 string. A source using this must guarantee the
  field is populated and orderable; ties at the same value are not given a
  stable secondary order here, so such a source should not declare
  supports_stable_ordering=True in its SourceConnectorCapabilities.

Delete-event detection (MongoDB change streams) is not implemented --
supports_delete_events/supports_delta_replay are always False for this
connector. A source needing those must use PERIODIC_AUTHORITATIVE_RECONCILIATION
or another declared alternative (see the source-to-graph alignment plan's
SourceConnectorCapabilities section) until change-stream support is added.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase

from return_platform.dynamic_knowledge.on_demand_sync.contracts import (
    CursorComparison,
    RawSourceDocument,
    RawSourcePage,
    SourceConnectorCapabilities,
    SourceCursor,
)
from return_platform.dynamic_knowledge.schema import ActiveSchema

_OBJECT_ID = "OBJECT_ID"
_FIELD_DATETIME = "FIELD_DATETIME"

CAPABILITIES = SourceConnectorCapabilities(
    supports_high_watermark=True,
    supports_bounded_scan=True,
    supports_delta_replay=False,
    supports_delete_events=False,
    supports_stable_ordering=False,
    supports_snapshot_read=True,
    supports_partitioned_cursors=False,
    supports_cursor_comparison=True,
)


class MongoConnectorError(RuntimeError):
    """A MongoDB source could not be scanned as configured."""


@dataclass(frozen=True, slots=True)
class SeedPin:
    """Restrict a scan to one immutable, digest-verified seed generation.

    Mirrors the fail-closed seed-digest-mismatch behavior this platform has
    always required: any document tagged with this seedVersion but a
    different seedDigest aborts the whole scan rather than silently mixing
    seed generations.
    """

    seed_version: str
    seed_digest: str


class MongoDBSourceScanConnector:
    """One connector instance serves every Mongo-backed source in a database."""

    def __init__(
        self,
        database: AsyncDatabase[dict[str, Any]],
        *,
        page_size: int = 500,
        seed_pins: Mapping[str, SeedPin] | None = None,
    ) -> None:
        self._database = database
        self._page_size = page_size
        self._seed_pins = dict(seed_pins or {})

    def capabilities(self) -> SourceConnectorCapabilities:
        return CAPABILITIES

    async def capture_high_watermark(self, *, source_asset_id: str) -> SourceCursor:
        del source_asset_id
        # A freshly-minted ObjectId's timestamp+counter sorts after every
        # ObjectId already assigned by any process -- no query needed to
        # establish "as of right now".
        return SourceCursor(cursor_type=_OBJECT_ID, encoded_value=str(ObjectId()))

    def compare_cursors(
        self, *, source_asset_id: str, left: SourceCursor, right: SourceCursor
    ) -> CursorComparison:
        del source_asset_id
        if left.cursor_type != right.cursor_type:
            raise MongoConnectorError(
                f"cannot compare cursors of different types: {left.cursor_type!r} "
                f"vs {right.cursor_type!r}"
            )
        if left.cursor_type == _OBJECT_ID:
            left_value, right_value = ObjectId(left.encoded_value), ObjectId(right.encoded_value)
        elif left.cursor_type == _FIELD_DATETIME:
            left_value, right_value = (
                datetime.fromisoformat(left.encoded_value),
                datetime.fromisoformat(right.encoded_value),
            )
        else:
            raise MongoConnectorError(f"unsupported cursor type: {left.cursor_type!r}")
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
        source = schema.sources[source_asset_id]
        collection_name = source.object_ref.get("name")
        if not collection_name:
            raise MongoConnectorError(
                f"source {source_asset_id!r} object_ref is missing a collection 'name'"
            )
        collection = self._database[collection_name]
        cursor_field = source.incremental_cursor_field

        query: dict[str, Any] = {}
        seed_pin = self._seed_pins.get(source_asset_id)
        if seed_pin is not None:
            mismatch_count = await collection.count_documents(
                {
                    "seedVersion": seed_pin.seed_version,
                    "seedDigest": {"$ne": seed_pin.seed_digest},
                },
                limit=1,
            )
            if mismatch_count:
                raise MongoConnectorError(
                    f"seed digest mismatch in {collection_name!r} for seed "
                    f"version {seed_pin.seed_version!r}"
                )
            query["seedVersion"] = seed_pin.seed_version
            query["seedDigest"] = seed_pin.seed_digest

        if cursor_field is None:
            sort_field = "_id"
            bound_query = _object_id_bounds(after, through)
        else:
            sort_field = cursor_field
            bound_query = _field_datetime_bounds(after, through)
        query[sort_field] = bound_query

        mongo_cursor = collection.find(query).sort(sort_field, 1)
        batch: list[RawSourceDocument] = []
        last_sort_value: Any = None
        async for document in mongo_cursor:
            last_sort_value = document.get(sort_field)
            batch.append(
                RawSourceDocument(
                    operation="UPSERT",
                    document=document,
                    source_identity=str(document.get("_id")),
                )
            )
            if len(batch) >= self._page_size:
                yield _page(batch, cursor_field, last_sort_value, through)
                batch = []
        if batch:
            yield _page(batch, cursor_field, last_sort_value, through)


def _object_id_bounds(after: SourceCursor | None, through: SourceCursor) -> dict[str, Any]:
    bounds: dict[str, Any] = {"$lte": ObjectId(through.encoded_value)}
    if after is not None:
        bounds["$gt"] = ObjectId(after.encoded_value)
    return bounds


def _field_datetime_bounds(after: SourceCursor | None, through: SourceCursor) -> dict[str, Any]:
    bounds: dict[str, Any] = {"$lte": datetime.fromisoformat(through.encoded_value)}
    if after is not None:
        bounds["$gt"] = datetime.fromisoformat(after.encoded_value)
    return bounds


def _page(
    documents: list[RawSourceDocument],
    cursor_field: str | None,
    last_sort_value: Any,
    through: SourceCursor,
) -> RawSourcePage:
    next_cursor: SourceCursor | None = None
    if last_sort_value is not None:
        if cursor_field is None:
            next_cursor = SourceCursor(cursor_type=_OBJECT_ID, encoded_value=str(last_sort_value))
        else:
            value = last_sort_value
            if isinstance(value, datetime):
                if value.tzinfo is None:
                    value = value.replace(tzinfo=UTC)
                encoded_value = value.isoformat()
            else:
                encoded_value = str(value)
            next_cursor = SourceCursor(cursor_type=_FIELD_DATETIME, encoded_value=encoded_value)
    return RawSourcePage(
        documents=tuple(documents),
        next_cursor=next_cursor,
        high_watermark=through,
        observed_at=datetime.now(UTC),
    )
