"""Where published `ActiveSchema` releases live, and which one is active.

Until now the runtime's schema was a YAML file on disk, read at startup in two
places. That is fine for a schema nobody changes and impossible for one an
analyst edits: publishing meant a code change and a deploy, so the Graph Schema
Analyzer's whole approval lifecycle ended in a document nothing consumed.

**Releases are immutable (D8).** `publish` inserts and never updates; a second
publish of the same `configuration_release_id` is rejected by a unique index
rather than overwriting. What a release said when it was approved is what it
says forever, which is the only way the checksum recorded at approval remains
worth anything.

**Activation is a pointer, not an edit.** Making a release active writes a new
pointer document; the release itself is untouched, and the previous release
stays readable for the conversations still pinned to it while a generation
drains.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from pymongo import ASCENDING, DESCENDING, AsyncMongoClient
from pymongo.errors import DuplicateKeyError

from return_platform.dynamic_knowledge.schema import ActiveSchema

__all__ = [
    "ACTIVE_POINTER_COLLECTION",
    "RELEASES_COLLECTION",
    "ReleaseAlreadyPublished",
    "SchemaReleaseStore",
]

logger = logging.getLogger("return_platform.dynamic_knowledge.release_store")

RELEASES_COLLECTION = "graph_schema_releases"
ACTIVE_POINTER_COLLECTION = "graph_schema_active_release"

# One document, always. The pointer is a single row rather than a flag on each
# release, so "which release is active" cannot have two answers even briefly.
_POINTER_ID = "active"


class ReleaseAlreadyPublished(RuntimeError):
    """A release id that has already been written. Releases are never rewritten."""


class SchemaReleaseStore:
    def __init__(self, client: AsyncMongoClient[dict[str, Any]], database: str) -> None:
        self._releases = client[database][RELEASES_COLLECTION]
        self._pointer = client[database][ACTIVE_POINTER_COLLECTION]

    async def ensure_indexes(self) -> None:
        await self._releases.create_index(
            [("configurationReleaseId", ASCENDING)],
            unique=True,
            name="uq_configuration_release_id",
        )
        await self._releases.create_index([("publishedAt", DESCENDING)], name="ix_published_at")

    async def publish(self, schema: ActiveSchema, *, published_by: str) -> None:
        """Write a release. Once.

        Raises rather than upserting: an analyst re-publishing an id that
        already exists has either lost track of which release they are looking
        at or is trying to change one that is already running, and both deserve
        an error rather than a silent overwrite of an approved artifact.
        """
        try:
            await self._releases.insert_one(
                {
                    "configurationReleaseId": schema.configuration_release_id,
                    "configurationChecksum": schema.configuration_checksum,
                    "publishedBy": published_by,
                    "publishedAt": datetime.now(UTC),
                    "release": schema.model_dump(mode="json"),
                }
            )
        except DuplicateKeyError as exc:
            raise ReleaseAlreadyPublished(
                f"release {schema.configuration_release_id!r} is already published"
            ) from exc

    async def activate(self, configuration_release_id: str) -> None:
        """Point the runtime at a published release.

        Refuses an id that was never published, because the alternative is a
        pointer at nothing and a runtime that silently falls back to the file
        while the console reports the release as live.
        """
        existing = await self._releases.find_one(
            {"configurationReleaseId": configuration_release_id}
        )
        if existing is None:
            raise LookupError(f"release {configuration_release_id!r} has not been published")
        await self._pointer.update_one(
            {"_id": _POINTER_ID},
            {
                "$set": {
                    "configurationReleaseId": configuration_release_id,
                    "activatedAt": datetime.now(UTC),
                }
            },
            upsert=True,
        )

    async def read(self, configuration_release_id: str) -> ActiveSchema | None:
        document = await self._releases.find_one(
            {"configurationReleaseId": configuration_release_id}
        )
        return None if document is None else _release_of(document)

    async def active(self) -> ActiveSchema | None:
        """The release the runtime should load, or nothing if none was published.

        Nothing is an ordinary answer, not a failure: a platform that has never
        run the analyzer has a file and no releases, and that is the state every
        installation starts in.
        """
        pointer = await self._pointer.find_one({"_id": _POINTER_ID})
        if pointer is None:
            return None
        release_id = pointer.get("configurationReleaseId")
        if not isinstance(release_id, str):
            return None
        document = await self._releases.find_one({"configurationReleaseId": release_id})
        if document is None:
            # The pointer names a release that is not there. Refusing to guess:
            # loading "the newest instead" would run a schema nobody activated.
            logger.error(
                "active_release_missing",
                extra={"configuration_release_id": release_id},
            )
            return None
        return _release_of(document)

    async def list_published(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """Newest first, without the release bodies.

        The bodies are large and the console's list needs identity and dates;
        sending every schema to render a list of them is the sort of read that
        only shows up in production.
        """
        cursor = (
            self._releases.find({}, {"release": 0, "_id": 0})
            .sort("publishedAt", DESCENDING)
            .limit(limit)
        )
        return [document async for document in cursor]


def _release_of(document: dict[str, Any]) -> ActiveSchema:
    return ActiveSchema.model_validate(document["release"])
