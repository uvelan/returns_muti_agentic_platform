"""`SourceInspectionPort` over one MongoDB database.

Lives here, not in `graph_schema_analyzer/`, because it is the only place
permitted to see both the analyzer's port and a concrete driver (design doc
section 2.7 -- the analyzer owns no `adapters/` package).

**Mongo has no declared schema**, and this adapter is explicit about the
consequences rather than papering over them. `describe_object` reports the union
of keys across a bounded sample: a field absent from every sampled document is
invisible, and a field whose sampled values disagree on type is reported as
`mixed` rather than guessed at. Both are better than a confident wrong answer,
because the analyzer's validation checks treat a declared type as fact.

`list_relationships` returns nothing, and that is the honest answer rather than a
gap. MongoDB declares no cross-collection constraints; an edge inferred from a
field being named `customerId` would arrive in the same shape as SQL Server's
`sys.foreign_keys` entries, and nothing downstream could tell the guess from the
fact.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pymongo import AsyncMongoClient
from pymongo.errors import PyMongoError

from return_platform.bootstrap.adapters.source_inspection_profiling import (
    build_profile,
    python_type_name,
)
from return_platform.graph_schema_analyzer.ports.source_port import (
    FieldDescription,
    IndexDescription,
    ObjectDescription,
    ObjectKind,
    ObjectProfile,
    RelationshipObservation,
    SourceInspectionPort,
    SourceObjectRef,
    SourceValidation,
)

__all__ = ["MongoSourceInspectionAdapter", "build_mongo_source_inspection_adapter"]

# Documents read purely to infer field shape, independent of what a caller may
# retain. Enough to see optional fields without approaching a bulk read; the
# scope layer clamps separately and this is the floor under a misconfiguration.
_STRUCTURE_SAMPLE_CEILING = 50

# Collections that are platform bookkeeping rather than business data. Analysing
# them would propose a graph schema for our own internals.
_EXCLUDED_PREFIXES = ("system.", "platform_")


class MongoSourceInspectionAdapter:
    """Structurally satisfies `SourceInspectionPort` for exactly one source.

    One adapter instance serves one configured source, matching
    `MongoSourceDiscoveryAdapter`: multiple sources are multiple published
    adapters behind the routing adapter rather than one adapter that can reach
    anywhere, which keeps "what can this analysis read" a composition-time
    decision instead of a runtime argument.
    """

    def __init__(
        self, client: AsyncMongoClient[dict[str, Any]], *, database_name: str, source_id: str
    ) -> None:
        self._client = client
        self._database_name = database_name
        self._source_id = source_id

    async def validate(self, *, source_id: str) -> SourceValidation:
        self._require_source(source_id)
        try:
            info = await self._client.admin.command("buildInfo")
        except PyMongoError as exc:
            return SourceValidation(source_id=source_id, reachable=False, detail=str(exc))
        return SourceValidation(
            source_id=source_id, reachable=True, server_version=str(info.get("version"))
        )

    async def list_sources(self) -> Sequence[str]:
        return (self._source_id,)

    async def list_objects(self, *, source_id: str) -> Sequence[SourceObjectRef]:
        database = self._database(source_id)
        # `list_collections` rather than `list_collection_names` because a view
        # cannot be profiled or indexed the way a collection can, and reporting
        # one as a collection would send the analyzer looking for statistics that
        # do not exist.
        objects: list[SourceObjectRef] = []
        async for info in await database.list_collections():
            name = str(info["name"])
            if name.startswith(_EXCLUDED_PREFIXES):
                continue
            objects.append(
                SourceObjectRef(
                    source_id=source_id,
                    object_name=name,
                    object_kind=(
                        ObjectKind.VIEW if info.get("type") == "view" else ObjectKind.COLLECTION
                    ),
                )
            )
        # Mongo returns collections in no defined order; sorting makes two
        # listings of an unchanged database comparable, which is what an operator
        # diffing a re-analysis is relying on.
        return tuple(sorted(objects, key=lambda ref: ref.object_name))

    async def describe_object(self, *, source_id: str, object_name: str) -> ObjectDescription:
        database = self._database(source_id)
        documents = await self._read(database, object_name, _STRUCTURE_SAMPLE_CEILING, None)
        return ObjectDescription(
            source_id=source_id,
            object_name=object_name,
            object_kind=ObjectKind.COLLECTION,
            fields=_infer_fields(documents),
            approximate_row_count=await database[object_name].estimated_document_count(),
        )

    async def sample(
        self,
        *,
        source_id: str,
        object_name: str,
        limit: int,
        fields: Sequence[str] | None = None,
    ) -> Sequence[Mapping[str, Any]]:
        database = self._database(source_id)
        return tuple(await self._read(database, object_name, limit, fields))

    async def profile(self, *, source_id: str, object_name: str, sample_size: int) -> ObjectProfile:
        database = self._database(source_id)
        documents = await self._read(database, object_name, sample_size, None)
        return build_profile(
            source_id=source_id,
            object_name=object_name,
            rows=documents,
            approximate_row_count=await database[object_name].estimated_document_count(),
        )

    async def list_indexes(self, *, source_id: str, object_name: str) -> Sequence[IndexDescription]:
        database = self._database(source_id)
        described: list[IndexDescription] = []
        async for index in await database[object_name].list_indexes():
            keys = tuple(str(key) for key, _ in index.get("key", {}).items())
            described.append(
                IndexDescription(
                    index_name=str(index.get("name", "")),
                    fields=keys,
                    unique=bool(index.get("unique", False)),
                    # Mongo's `_id_` is the only index it creates unasked and the
                    # only one guaranteed unique on every document, which is what
                    # "primary" means to every other backend here.
                    primary=str(index.get("name")) == "_id_",
                )
            )
        return tuple(described)

    async def list_relationships(
        self, *, source_id: str, object_name: str | None = None
    ) -> Sequence[RelationshipObservation]:
        self._require_source(source_id)
        del object_name
        return ()

    def _database(self, source_id: str) -> Any:
        self._require_source(source_id)
        return self._client[self._database_name]

    def _require_source(self, source_id: str) -> None:
        if source_id != self._source_id:
            raise ValueError(
                f"this adapter serves {self._source_id!r}, not {source_id!r}; "
                "resolve the adapter published for that source."
            )

    async def _read(
        self, database: Any, collection: str, limit: int, fields: Sequence[str] | None
    ) -> list[dict[str, Any]]:
        """One bounded read, projected at the server when fields are named.

        `_id` is always dropped: it is storage bookkeeping rather than business
        shape, and it is not JSON-serialisable, which would break the prompt
        framing downstream.
        """
        projection: dict[str, Any] = {"_id": 0}
        if fields is not None:
            projection.update({field: 1 for field in fields})
        bounded = max(1, min(limit, _STRUCTURE_SAMPLE_CEILING))
        return [
            document async for document in database[collection].find({}, projection, limit=bounded)
        ]


def _infer_fields(documents: Sequence[Mapping[str, Any]]) -> tuple[FieldDescription, ...]:
    """Union of observed keys, with a declared type per key.

    `nullable` means "absent from, or null in, at least one observed document" --
    an honest statement about the sample, not a claim about the collection. With
    an empty sample nothing is reported at all rather than an empty-but-confident
    schema.
    """
    if not documents:
        return ()
    observed_types: dict[str, set[str]] = {}
    present_counts: dict[str, int] = {}
    null_counts: dict[str, int] = {}

    for document in documents:
        for key, value in document.items():
            present_counts[key] = present_counts.get(key, 0) + 1
            if value is None:
                null_counts[key] = null_counts.get(key, 0) + 1
                continue
            observed_types.setdefault(key, set()).add(python_type_name(value))

    total = len(documents)
    fields: list[FieldDescription] = []
    for key in sorted(present_counts):
        types = observed_types.get(key, set())
        if len(types) == 1:
            declared = next(iter(types))
        else:
            declared = "unknown" if not types else "mixed"
        fields.append(
            FieldDescription(
                field_name=key,
                declared_type=declared,
                nullable=present_counts[key] < total or key in null_counts,
            )
        )
    return tuple(fields)


def build_mongo_source_inspection_adapter(
    client: AsyncMongoClient[dict[str, Any]], *, database_name: str, source_id: str
) -> SourceInspectionPort:
    """Typed factory -- the return annotation is what makes mypy prove port
    conformance, rather than a runtime isinstance that only checks method names."""
    return MongoSourceInspectionAdapter(client, database_name=database_name, source_id=source_id)
