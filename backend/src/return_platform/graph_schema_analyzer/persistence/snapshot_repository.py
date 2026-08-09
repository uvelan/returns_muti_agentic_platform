"""`source_snapshots` persistence.

Snapshots are immutable and content-addressed, which changes what "save" means:
writing the same snapshot twice is idempotent, and there is deliberately no update
method at all. Re-reading validates the content hash through
`SourceSchemaSnapshot`'s own validator, so metadata tampered with in the database
fails on load rather than being silently reasoned over.

This repository never writes sample rows. `samples_ref` points at the separate
`source_samples` structure, which is declared `encrypted: true` -- the store layer
itself refuses a plaintext write there (section 13.6), so there is no path by
which raw samples reach disk through this class.
"""

from __future__ import annotations

from return_platform.graph_schema_analyzer.domain.errors import UnknownAnalysis
from return_platform.graph_schema_analyzer.domain.source_snapshot import SourceSchemaSnapshot
from return_platform.platform.system_store.repository import SystemStore

__all__ = ["SOURCE_SNAPSHOTS", "SourceSnapshotRepository"]

SOURCE_SNAPSHOTS = "source_snapshots"


class SourceSnapshotRepository:
    def __init__(self, system_store: SystemStore) -> None:
        self._store = system_store

    async def save(self, snapshot: SourceSchemaSnapshot) -> None:
        """Idempotent by content address: an identical re-capture rewrites the same
        document. `upsert=True` is safe precisely *because* the snapshot is
        immutable -- there is no prior state a replace could destroy."""
        await self._store.replace_one(
            SOURCE_SNAPSHOTS,
            {"snapshot_id": snapshot.snapshot_id},
            dict(snapshot.to_document()),
            upsert=True,
        )

    async def load(self, snapshot_id: str) -> SourceSchemaSnapshot:
        document = await self._store.read_only(SOURCE_SNAPSHOTS).find_one(
            {"snapshot_id": snapshot_id}, {"_id": 0}
        )
        if document is None:
            raise UnknownAnalysis(f"no source snapshot with id {snapshot_id!r}")
        # Validation re-derives the content hash; a mismatch raises
        # SnapshotIntegrityError rather than returning a tampered snapshot.
        return SourceSchemaSnapshot.model_validate(document)

    async def latest_for_analysis(self, analysis_id: str) -> SourceSchemaSnapshot | None:
        cursor = (
            self._store.read_only(SOURCE_SNAPSHOTS)
            .find({"analysis_id": analysis_id}, {"_id": 0})
            .sort("captured_at", -1)
            .limit(1)
        )
        async for document in cursor:
            return SourceSchemaSnapshot.model_validate(document)
        return None
