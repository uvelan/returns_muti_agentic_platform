"""Generic MongoDB SourceScanConnector -- no business collection/field names here.

Moved from `dynamic_knowledge.connectors.mongodb` (Phase 8 / Wave C1) --
`dynamic_knowledge.connectors.mongodb` re-exports these names unchanged so
existing importers do not need to change.

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

from return_platform.dynamic_knowledge.schema import ActiveSchema
from return_platform.source_connectors.compilation import compile_source_read
from return_platform.source_connectors.contracts import (
    CursorComparison,
    LogicalTargetedReadPlan,
    RawSourceDocument,
    RawSourcePage,
    SourceConnectorCapabilities,
    SourceCursor,
)
from return_platform.source_connectors.path_resolution import resolve_physical_path

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
    supports_point_lookup=True,
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
    """One connector instance serves every Mongo-backed source in one schema.

    SourceScanConnector.capture_high_watermark/compare_cursors take no schema
    parameter (only scan() does), so a connector that needs schema to know
    whether a source uses ObjectId or field-based cursoring -- as any
    connector serving sources with real business-key _id values must -- has
    to have it bound at construction time. The schema passed into scan() is
    ignored in favor of this bound one, so all three methods always agree on
    one configuration for the lifetime of this connector instance; construct
    a fresh connector (via the registry) if the active schema changes.
    """

    def __init__(
        self,
        database: AsyncDatabase[dict[str, Any]],
        *,
        schema: ActiveSchema,
        page_size: int = 500,
        seed_pins: Mapping[str, SeedPin] | None = None,
        max_records_per_source: int | None = None,
    ) -> None:
        self._database = database
        self._schema = schema
        self._page_size = page_size
        self._seed_pins = dict(seed_pins or {})
        # Never applied to a seed-pinned scan -- a pinned seed generation is read
        # exhaustively by design, matching this platform's long-standing
        # fail-closed seed safety behavior (a partial seed read would be worse
        # than no read at all).
        self._max_records_per_source = max_records_per_source

    def capabilities(self) -> SourceConnectorCapabilities:
        return CAPABILITIES

    async def capture_high_watermark(self, *, source_asset_id: str) -> SourceCursor:
        source = self._schema.sources[source_asset_id]
        if source.incremental_cursor_field is None:
            # A freshly-minted ObjectId's timestamp+counter sorts after every
            # ObjectId already assigned by any process -- no query needed to
            # establish "as of right now". Only valid when the source's _id
            # really is a Mongo-generated ObjectId; see the module docstring.
            return SourceCursor(cursor_type=_OBJECT_ID, encoded_value=str(ObjectId()))

        collection_name = source.object_ref.get("name")
        if not collection_name:
            raise MongoConnectorError(
                f"source {source_asset_id!r} object_ref is missing a collection 'name'"
            )
        sort_field = self._resolve_physical_field(source_asset_id, source.incremental_cursor_field)
        collection = self._database[collection_name]
        latest = [
            document
            async for document in collection.find({}, {sort_field: 1}).sort(sort_field, -1).limit(1)
        ]
        if not latest:
            # No documents yet -- "now" is a safe upper bound; an empty
            # collection must not block the rest of the run.
            return SourceCursor(
                cursor_type=_FIELD_DATETIME, encoded_value=datetime.now(UTC).isoformat()
            )
        newest_value = _dotted_get(latest[0], sort_field)
        if newest_value is None:
            # Fail closed, here, where the source and field can still be named.
            # `_encode_field_value(None)` returns the *string* "None", which is a
            # perfectly well-formed SourceCursor -- the run then proceeds and dies
            # much later in `_field_datetime_bounds` with a bare
            # `ValueError: Invalid isoformat string: 'None'` that mentions neither
            # the source nor the cursor field.
            #
            # Not defaulted: substituting `now()` would silently skip every record
            # (the silent-empty-build failure mode), and substituting the epoch
            # would silently rescan the whole collection. A missing cursor value on
            # the newest document is a schema/data problem for an operator to fix.
            raise MongoConnectorError(
                f"source {source_asset_id!r}: newest document has no value at cursor "
                f"field {sort_field!r} (logical field {source.incremental_cursor_field!r}); "
                "cannot establish a high watermark"
            )
        return SourceCursor(
            cursor_type=_FIELD_DATETIME,
            encoded_value=_encode_field_value(newest_value),
        )

    def _resolve_physical_field(self, source_asset_id: str, field_id: str) -> str:
        """incremental_cursor_field is a logical field_id, never a physical
        document path -- resolve it through every entity this source backs
        (dot-joined for nested paths) and require they all agree, rather than
        assuming field_id == physical field name."""

        path = resolve_physical_path(
            self._schema,
            source_asset_id=source_asset_id,
            field_id=field_id,
            error_cls=MongoConnectorError,
            noun="physical paths",
        )
        return ".".join(path)

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
        del (
            schema
        )  # this connector always uses the schema bound at construction; see class docstring
        source = self._schema.sources[source_asset_id]
        collection_name = source.object_ref.get("name")
        if not collection_name:
            raise MongoConnectorError(
                f"source {source_asset_id!r} object_ref is missing a collection 'name'"
            )
        collection = self._database[collection_name]
        cursor_field = (
            None
            if source.incremental_cursor_field is None
            else self._resolve_physical_field(source_asset_id, source.incremental_cursor_field)
        )

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
        if seed_pin is None and self._max_records_per_source is not None:
            mongo_cursor = mongo_cursor.limit(self._max_records_per_source)
        batch: list[RawSourceDocument] = []
        last_sort_value: Any = None
        async for document in mongo_cursor:
            last_sort_value = (
                document.get(sort_field)
                if cursor_field is None
                else _dotted_get(document, sort_field)
            )
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

    async def targeted_read(
        self, *, schema: ActiveSchema, plan: LogicalTargetedReadPlan
    ) -> RawSourcePage:
        """Bounded, anchor-conditioned read for one entity -- never a source-wide
        scan. Reuses `compile_source_read`'s MongoDB branch (AND-only conditions,
        the only shape any caller produces today) rather than re-deriving filter
        translation here; this is the first connector to actually execute it."""

        del (
            schema
        )  # this connector always uses the schema bound at construction; see class docstring
        source = self._schema.sources[plan.source_asset_id]
        collection_name = source.object_ref.get("name")
        if not collection_name:
            raise MongoConnectorError(
                f"source {plan.source_asset_id!r} object_ref is missing a collection 'name'"
            )
        compiled = compile_source_read(self._schema, plan)
        collection = self._database[collection_name]
        documents = [
            document
            async for document in collection.find(
                compiled.statement["filter"], compiled.statement["projection"]
            ).limit(compiled.statement["limit"])
        ]
        return RawSourcePage(
            documents=tuple(
                RawSourceDocument(
                    operation="UPSERT",
                    document=document,
                    source_identity=str(document.get("_id")),
                )
                for document in documents
            ),
            next_cursor=None,
            high_watermark=None,
            observed_at=datetime.now(UTC),
        )


def _dotted_get(document: Mapping[str, Any], dotted_path: str) -> Any:
    """Reads a possibly-nested field from a raw document using the same dot
    notation MongoDB itself uses for query/sort paths -- plain dict.get()
    only handles single-segment paths."""

    current: Any = document
    for segment in dotted_path.split("."):
        if not isinstance(current, Mapping) or segment not in current:
            return None
        current = current[segment]
    return current


def _encode_field_value(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.isoformat()
    return str(value)


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
            next_cursor = SourceCursor(
                cursor_type=_FIELD_DATETIME, encoded_value=_encode_field_value(last_sort_value)
            )
    return RawSourcePage(
        documents=tuple(documents),
        next_cursor=next_cursor,
        high_watermark=through,
        observed_at=datetime.now(UTC),
    )


async def fetch_one(
    database: AsyncDatabase[dict[str, Any]],
    *,
    collection_name: str,
    key: Mapping[str, Any],
    max_time_ms: int | None = None,
    comment: str | None = None,
) -> dict[str, Any] | None:
    """Bounded single-document lookup by exact key -- no scan, no cursor.

    A thin, shared primitive for consumers with an already-resolved physical
    collection and key (e.g. a customer-lookup adapter). Callers needing
    additional hardening (client-side cancellation, depth/node budgets,
    evidence hashing) apply it around this call; this function only owns the
    actual `find_one`. `max_time_ms`/`comment` are passed straight through to
    the driver (server-side time bound, server-side observability tag) --
    callers computing their own rounding/flooring policy for the former do so
    before calling this.
    """

    kwargs: dict[str, Any] = {}
    if max_time_ms is not None:
        kwargs["max_time_ms"] = max_time_ms
    if comment is not None:
        kwargs["comment"] = comment
    return await database[collection_name].find_one(dict(key), **kwargs)


async def find_many(
    database: AsyncDatabase[dict[str, Any]],
    *,
    collection_name: str,
    filter: Mapping[str, Any],
    limit: int,
) -> list[dict[str, Any]]:
    """Bounded multi-document read with a caller-supplied filter.

    For consumers whose query shape (OR-of-conditions, regex, cross-field
    matching -- see v2.runtime_adapters.MongoOrderSourceGateway) does not fit
    the AND-only LogicalTargetedReadPlan model that `targeted_read()` serves.
    Business query-shape knowledge stays with the caller; this function only
    owns the actual bounded `find`, so no caller needs its own raw pymongo
    dependency just to read a MongoDB collection.
    """

    if limit < 1:
        raise MongoConnectorError(f"find_many limit must be >= 1, got {limit!r}")
    cursor = database[collection_name].find(dict(filter)).limit(limit)
    return [document async for document in cursor]


async def sample_documents(
    database: AsyncDatabase[dict[str, Any]],
    *,
    collection_name: str,
    offset: int,
    limit: int,
) -> list[dict[str, Any]]:
    """Bounded, offset-paginated sample read for admin browse/preview use
    cases -- never used for durable sync (see scan() for that)."""

    if limit < 1:
        raise MongoConnectorError(f"sample limit must be >= 1, got {limit!r}")
    cursor = database[collection_name].find({}).skip(max(0, offset)).limit(limit)
    return [document async for document in cursor]
