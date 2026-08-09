"""`graph_schema_drafts`, `schema_revisions`, and `validation_results`.

Three collections, one repository, because they are written as a unit: a
mutation produces a revision *and* advances the draft, and a validation produces
a result *and* marks the draft VALIDATED. Splitting them would invite a caller
to do one without the other.

Revisions are append-only -- insert only, with a unique `(draft_id, sequence)`
index doing the enforcing. Two concurrent writers cannot both create "revision
4": the second gets a duplicate-key error rather than quietly overwriting the
first, which is what makes the history trustworthy as an audit record.
"""

from __future__ import annotations

from collections.abc import Sequence

from return_platform.graph_schema_analyzer.domain.errors import (
    ConcurrentModification,
    UnknownAnalysis,
)
from return_platform.graph_schema_analyzer.domain.schema_draft import GraphSchemaDraft
from return_platform.graph_schema_analyzer.domain.schema_revision import SchemaRevision
from return_platform.graph_schema_analyzer.domain.validation_result import ValidationResult
from return_platform.platform.system_store.repository import SystemStore

__all__ = [
    "GRAPH_SCHEMA_DRAFTS",
    "SCHEMA_REVISIONS",
    "VALIDATION_RESULTS",
    "DraftRepository",
]

GRAPH_SCHEMA_DRAFTS = "graph_schema_drafts"
SCHEMA_REVISIONS = "schema_revisions"
VALIDATION_RESULTS = "validation_results"


class DraftRepository:
    def __init__(self, system_store: SystemStore) -> None:
        self._store = system_store

    # --- drafts ------------------------------------------------------------
    async def create_draft(self, draft: GraphSchemaDraft) -> None:
        await self._store.insert_one(GRAPH_SCHEMA_DRAFTS, dict(draft.to_document()))

    async def save_draft(self, draft: GraphSchemaDraft, *, expected_version: int) -> None:
        result = await self._store.replace_one(
            GRAPH_SCHEMA_DRAFTS,
            {"draft_id": draft.draft_id, "version": expected_version},
            dict(draft.to_document()),
            upsert=False,
        )
        if result.matched_count != 1:
            raise ConcurrentModification(
                f"draft {draft.draft_id} was not at version {expected_version}; "
                "re-read it and re-apply the change."
            )

    async def load_draft(self, draft_id: str) -> GraphSchemaDraft:
        document = await self._store.read_only(GRAPH_SCHEMA_DRAFTS).find_one(
            {"draft_id": draft_id}, {"_id": 0}
        )
        if document is None:
            raise UnknownAnalysis(f"no schema draft with id {draft_id!r}")
        return GraphSchemaDraft.model_validate(document)

    async def load_draft_for_analysis(self, analysis_id: str) -> GraphSchemaDraft | None:
        document = await self._store.read_only(GRAPH_SCHEMA_DRAFTS).find_one(
            {"analysis_id": analysis_id}, {"_id": 0}
        )
        return None if document is None else GraphSchemaDraft.model_validate(document)

    # --- revisions ---------------------------------------------------------
    async def append_revision(self, revision: SchemaRevision) -> None:
        """Insert-only. A duplicate `(draft_id, sequence)` raises from the unique
        index rather than being pre-checked -- a pre-check would be a race."""
        await self._store.insert_one(SCHEMA_REVISIONS, dict(revision.to_document()))

    async def load_revision(self, revision_id: str) -> SchemaRevision:
        document = await self._store.read_only(SCHEMA_REVISIONS).find_one(
            {"revision_id": revision_id}, {"_id": 0}
        )
        if document is None:
            raise UnknownAnalysis(f"no schema revision with id {revision_id!r}")
        return SchemaRevision.model_validate(document)

    async def list_revisions(self, draft_id: str) -> Sequence[SchemaRevision]:
        cursor = (
            self._store.read_only(SCHEMA_REVISIONS)
            .find({"draft_id": draft_id}, {"_id": 0})
            .sort("sequence", 1)
        )
        return [SchemaRevision.model_validate(document) async for document in cursor]

    # --- validation results ------------------------------------------------
    async def save_validation_result(self, result: ValidationResult) -> None:
        await self._store.replace_one(
            VALIDATION_RESULTS,
            {"result_id": result.result_id},
            dict(result.to_document()),
            upsert=True,
        )

    async def load_validation_result(self, result_id: str) -> ValidationResult:
        document = await self._store.read_only(VALIDATION_RESULTS).find_one(
            {"result_id": result_id}, {"_id": 0}
        )
        if document is None:
            raise UnknownAnalysis(f"no validation result with id {result_id!r}")
        return ValidationResult.from_document(document)
