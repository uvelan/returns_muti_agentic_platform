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

**The pointer no longer moves in the dark.** Because a release is immutable,
going from one to the next is a *generational* step, and the step has
consequences the operator has to be able to see first: whether the graph can
absorb the change incrementally or has to be rebuilt. `preview_activation`
answers that without changing anything; `activate` records the same answer
against the target release before it flips, so what a migration was understood
to be at the moment it happened is still readable afterwards.

Plans live in their own collection rather than on the release document. A
release says one thing forever; a plan is a statement about a *pair*, and which
pair is live changes as the pointer moves -- writing it onto the immutable
document would be the one update this store does not make.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from pymongo import ASCENDING, DESCENDING, AsyncMongoClient
from pymongo.errors import DuplicateKeyError

from return_platform.dynamic_knowledge.release_migration import MigrationPlan, plan_migration
from return_platform.dynamic_knowledge.schema import ActiveSchema

__all__ = [
    "ACTIVE_POINTER_COLLECTION",
    "MIGRATION_PLANS_COLLECTION",
    "RELEASES_COLLECTION",
    "ReleaseAlreadyPublished",
    "SchemaReleaseStore",
]

logger = logging.getLogger("return_platform.dynamic_knowledge.release_store")

RELEASES_COLLECTION = "graph_schema_releases"
ACTIVE_POINTER_COLLECTION = "graph_schema_active_release"
MIGRATION_PLANS_COLLECTION = "graph_schema_migration_plans"

# One document, always. The pointer is a single row rather than a flag on each
# release, so "which release is active" cannot have two answers even briefly.
_POINTER_ID = "active"

# The first activation on an installation has no predecessor. Stored as a
# literal rather than as null so the unique index below has a value to key on --
# a compound unique index over a missing field would collapse every
# first-activation plan onto one document.
_NO_PREDECESSOR = "__none__"


class ReleaseAlreadyPublished(RuntimeError):
    """A release id that has already been written. Releases are never rewritten."""


class SchemaReleaseStore:
    def __init__(self, client: AsyncMongoClient[dict[str, Any]], database: str) -> None:
        self._releases = client[database][RELEASES_COLLECTION]
        self._pointer = client[database][ACTIVE_POINTER_COLLECTION]
        self._plans = client[database][MIGRATION_PLANS_COLLECTION]

    async def ensure_indexes(self) -> None:
        await self._releases.create_index(
            [("configurationReleaseId", ASCENDING)],
            unique=True,
            name="uq_configuration_release_id",
        )
        await self._releases.create_index([("publishedAt", DESCENDING)], name="ix_published_at")
        # Keyed on the pair, because that is what a plan is about. Not sparse:
        # `fromReleaseId` is always written (as `_NO_PREDECESSOR` when there is
        # none), so every plan participates in the uniqueness that stops the
        # same migration being recorded twice with different answers.
        await self._plans.create_index(
            [("fromReleaseId", ASCENDING), ("toReleaseId", ASCENDING)],
            unique=True,
            name="uq_migration_pair",
        )

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

    async def preview_activation(self, configuration_release_id: str) -> MigrationPlan:
        """What activating this release would do to the graph. Changes nothing.

        Separate from `activate` so reviewing a migration needs no write right
        and leaves no trace: an operator deciding whether a change is safe
        should not have to half-perform it to find out.
        """
        target = await self.read(configuration_release_id)
        if target is None:
            raise LookupError(f"release {configuration_release_id!r} has not been published")
        return plan_migration(await self.active(), target)

    async def activate(self, configuration_release_id: str) -> MigrationPlan:
        """Point the runtime at a published release, recording what that means.

        Refuses an id that was never published, because the alternative is a
        pointer at nothing and a runtime that silently falls back to the file
        while the console reports the release as live.

        The plan is written *before* the pointer moves, so a crash between the
        two leaves a recorded plan for a migration that did not happen -- which
        is inert and re-derivable -- rather than a live release nobody can say
        anything about. Returned as well as stored, because the caller flipping
        the pointer is the one who needs to know a rebuild is now owed.
        """
        plan = await self.preview_activation(configuration_release_id)
        await self.record_plan(plan)
        await self._pointer.update_one(
            {"_id": _POINTER_ID},
            {
                "$set": {
                    "configurationReleaseId": configuration_release_id,
                    "activatedAt": datetime.now(UTC),
                    # On the pointer as well as in the plan collection: "what
                    # did activating the thing that is running commit us to" is
                    # answerable from the one document that says what is running.
                    "migratedFromReleaseId": plan.from_release_id,
                    "migrationStrategy": plan.strategy.value,
                }
            },
            upsert=True,
        )
        logger.info(
            "graph_schema_release_activated",
            extra={
                "configuration_release_id": configuration_release_id,
                "from_release_id": plan.from_release_id,
                "migration_strategy": plan.strategy.value,
                "rebuild_reason_count": len(plan.rebuild_reasons),
            },
        )
        return plan

    async def record_plan(self, plan: MigrationPlan) -> None:
        """Store a plan against its pair.

        An upsert rather than an insert: a plan is a pure function of two
        immutable releases, so re-recording one can only ever write the same
        answer, and refusing the second write would make a retried activation
        fail for no reason.
        """
        await self._plans.update_one(
            {
                "fromReleaseId": plan.from_release_id or _NO_PREDECESSOR,
                "toReleaseId": plan.to_release_id,
            },
            {
                "$set": {"plan": plan.model_dump(mode="json"), "plannedAt": datetime.now(UTC)},
            },
            upsert=True,
        )

    async def recorded_plan(
        self, configuration_release_id: str, *, from_release_id: str | None
    ) -> MigrationPlan | None:
        """The plan a past activation was made under, if one was recorded."""
        document = await self._plans.find_one(
            {
                "fromReleaseId": from_release_id or _NO_PREDECESSOR,
                "toReleaseId": configuration_release_id,
            }
        )
        return None if document is None else MigrationPlan.model_validate(document["plan"])

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
