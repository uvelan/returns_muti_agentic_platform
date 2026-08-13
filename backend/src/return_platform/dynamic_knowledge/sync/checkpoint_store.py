"""Where an incremental sync's resume position is durably kept.

`CheckpointStore` has been a protocol since `GenericSyncCoordinator` was written
and had exactly one implementation in the tree: one that raises
(`data_platform.graph.sync_service._UnusedCheckpointStore`). `incremental_sync`
therefore ran only against a test fake whose `read` always returned `None` --
the single input under which a resume is indistinguishable from a full scan. A
sync that silently re-reads everything looks identical from the run list: same
rows, same statuses, same green ticks.

Two properties this store must hold, both about failures the run list cannot show.

**A stale writer must not rewind a live cursor.** Two processes holding the same
generation -- a rebuild whose lease was taken over, a redeployed worker that has
not noticed -- would otherwise trade the cursor back and forth, and every
exchange skips or repeats a window of records. `fencing_token` has been part of
the protocol from the start and was ignored by the only prior implementation
because that implementation never ran. Here the write is conditional on the
stored token and fails loudly; a checkpoint write that is silently dropped is
worse than one that raises, because the run that lost it goes on to report
success.

**The cursor is opaque.** Ordering belongs to the connector that produced the
cursor (`SourceScanConnector.compare_cursors`), so this store never decodes
`encoded_value` and never decides whether an incoming checkpoint is "newer" than
the stored one. It stores what it is given, under the fencing rule above, and
nothing else. `GenericSyncCoordinator._highest_cursor` is where ordering is
decided, using the connector.

What this store deliberately does not guard: a source whose cursor *binding*
changes underneath a stored cursor -- the same `cursor_type` now read from a
different physical path. The protocol gives `read` only the source id and the
generation, so the binding is not knowable here. `MongoDBSourceScanConnector.scan`
rejects the coarse case (a cursor of the wrong `cursor_type` for the strategy the
source now uses); the finer case is recorded as a known gap rather than papered
over.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pymongo import AsyncMongoClient
from pymongo.errors import DuplicateKeyError

from return_platform.source_connectors.contracts import SourceCursor

__all__ = [
    "CHECKPOINTS_COLLECTION",
    "CheckpointFenced",
    "MongoSyncCheckpointStore",
]

CHECKPOINTS_COLLECTION = "graph_sync_checkpoints"


class CheckpointFenced(RuntimeError):
    """A checkpoint write lost to a higher fencing token and was not applied.

    Raised rather than swallowed: the caller is a sync run that believes it owns
    this generation, and the correct response is to stop, not to keep scanning
    and writing graph state it can no longer checkpoint.
    """


def _key(*, source_asset_id: str, graph_generation_id: str) -> dict[str, str]:
    """The composite `_id`, as a subdocument rather than a joined string.

    A `f"{generation}::{source}"` key would be one delimiter collision away from
    two sources sharing a checkpoint, and neither id is constrained to exclude
    the delimiter. Field order is fixed here because MongoDB compares
    subdocument `_id`s by exact key order.
    """
    return {"graphGenerationId": graph_generation_id, "sourceAssetId": source_asset_id}


class MongoSyncCheckpointStore:
    """`CheckpointStore` over one collection in the platform's own database.

    Keyed by (generation, source): a blue/green rebuild's target generation is a
    different scan of the same sources and must start from its own position, not
    inherit the live generation's.
    """

    def __init__(
        self,
        client: AsyncMongoClient[dict[str, Any]],
        database: str,
        *,
        collection: str = CHECKPOINTS_COLLECTION,
    ) -> None:
        self._checkpoints = client[database][collection]

    async def ensure_indexes(self) -> None:
        # The `_id` subdocument already enforces uniqueness and serves the point
        # read. This index serves the other question an operator asks: what has
        # every source in this generation reached?
        await self._checkpoints.create_index("graphGenerationId")

    async def read(self, *, source_asset_id: str, graph_generation_id: str) -> SourceCursor | None:
        """`None` means "no position yet", which the coordinator passes to the
        connector as `after=None` -- a first run legitimately reads everything up
        to the captured watermark."""

        document = await self._checkpoints.find_one(
            {"_id": _key(source_asset_id=source_asset_id, graph_generation_id=graph_generation_id)}
        )
        if document is None:
            return None
        return SourceCursor(
            cursor_type=str(document["cursorType"]),
            encoded_value=str(document["encodedValue"]),
        )

    async def write(
        self,
        *,
        source_asset_id: str,
        graph_generation_id: str,
        checkpoint: SourceCursor,
        fencing_token: int,
    ) -> None:
        key = _key(source_asset_id=source_asset_id, graph_generation_id=graph_generation_id)
        try:
            await self._checkpoints.update_one(
                # `$lte`, not `$lt`: the same run checkpoints once per page and
                # holds one token for its whole lifetime.
                {"_id": key, "fencingToken": {"$lte": fencing_token}},
                {
                    "$set": {
                        "cursorType": checkpoint.cursor_type,
                        "encodedValue": checkpoint.encoded_value,
                        "fencingToken": fencing_token,
                        "updatedAt": datetime.now(UTC),
                    },
                    # Duplicated out of the `_id` subdocument so the collection
                    # can be indexed and read by a human without projecting
                    # through `_id`.
                    "$setOnInsert": {
                        "graphGenerationId": graph_generation_id,
                        "sourceAssetId": source_asset_id,
                    },
                },
                upsert=True,
            )
        except DuplicateKeyError as error:
            # The filter matched nothing *and* the key exists, which for this
            # filter means exactly one thing: the stored token is higher.
            raise CheckpointFenced(
                f"checkpoint for source {source_asset_id!r} in generation "
                f"{graph_generation_id!r} is held at a fencing token higher than "
                f"{fencing_token}; this run has been fenced off"
            ) from error
