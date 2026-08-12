"""Where recorded provider answers live.

Separate from the interception records on purpose. Those hold text a *human*
typed into an operator console, and `DurableInterceptionProvider` takes care to
keep that labelled as human output so an evaluation set never quietly contains
a person's words as though a model wrote them. A recording is the opposite
thing -- a model's answer, kept so it need not be bought twice -- and merging
the two storages would destroy the distinction both rely on.

Keyed by request digest, so a recording is found by what was asked rather than
by when it was asked. Writes are upserts: re-recording the same question is an
ordinary consequence of a strict run being widened, not a conflict.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pymongo import ASCENDING, AsyncMongoClient

__all__ = ["REPLAY_COLLECTION", "MongoReplayStore"]

REPLAY_COLLECTION = "ai_replay_recordings"


class MongoReplayStore:
    def __init__(
        self,
        client: AsyncMongoClient[dict[str, Any]],
        database: str,
        *,
        collection: str = REPLAY_COLLECTION,
    ) -> None:
        self._collection = client[database][collection]

    async def ensure_indexes(self) -> None:
        await self._collection.create_index([("digest", ASCENDING)], unique=True, name="uq_digest")

    async def read(self, digest: str) -> dict[str, Any] | None:
        document = await self._collection.find_one({"digest": digest}, {"_id": 0})
        if document is None:
            return None
        recorded = document.get("response")
        return recorded if isinstance(recorded, dict) else None

    async def write(self, digest: str, record: dict[str, Any]) -> None:
        now = datetime.now(UTC)
        await self._collection.update_one(
            {"digest": digest},
            {
                "$set": {"response": record, "recordedAt": now},
                # Kept from the first sighting: how long a question has been in
                # the corpus is more useful than when it was last re-recorded.
                "$setOnInsert": {"digest": digest, "firstSeenAt": now},
            },
            upsert=True,
        )
