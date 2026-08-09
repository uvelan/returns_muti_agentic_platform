"""Binds the analyzer's `SourceDiscoveryPort` to real Mongo sources.

Lives here, not in `graph_schema_analyzer/`, because it is the only place
permitted to see both the analyzer's port and the concrete connector world
(design doc section 2.7 -- the analyzer owns no `adapters/` package).

**Mongo has no declared schema**, so "field metadata" is inferred by observing
sampled documents rather than read from a catalogue. That is a real limitation
with real consequences, and the adapter is explicit about it: a field absent from
every sampled document is invisible, and a field whose sampled values disagree on
type is reported as `mixed` rather than guessed at. Both are better than a
confident wrong answer, because the analyzer's validation checks treat declared
types as fact.

Sampling is bounded twice: by the caller's `sample_limit` (which the analyzer
derives from the source's own policy) and again by `_METADATA_SAMPLE_CEILING`
here, so a caller cannot turn discovery into a bulk export.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any

from pymongo import AsyncMongoClient

from return_platform.graph_schema_analyzer.ports.source_port import (
    DiscoveredDataset,
    SourceDiscoveryPort,
)

__all__ = ["MongoSourceDiscoveryAdapter", "build_mongo_source_discovery_adapter"]

logger = logging.getLogger(__name__)

# Documents read purely to infer field shape, independent of how many rows the
# caller may retain. Enough to see optional fields without approaching a bulk read.
_METADATA_SAMPLE_CEILING = 50

# Collections that are platform bookkeeping rather than business data. Analysing
# them would propose a graph schema for our own internals.
_EXCLUDED_PREFIXES = ("system.", "platform_")


class MongoSourceDiscoveryAdapter:
    """Structurally satisfies `SourceDiscoveryPort`."""

    def __init__(
        self, client: AsyncMongoClient[dict[str, Any]], *, database_name: str, source_id: str
    ) -> None:
        self._client = client
        self._database_name = database_name
        self._source_id = source_id

    async def list_source_ids(self) -> Sequence[str]:
        """One adapter instance serves one configured source.

        Multiple sources are multiple published adapters rather than one adapter
        that can reach anywhere -- which keeps "what can this analysis read" a
        composition-time decision instead of a runtime argument.
        """
        return (self._source_id,)

    async def discover(self, *, source_id: str, sample_limit: int) -> Sequence[DiscoveredDataset]:
        if source_id != self._source_id:
            raise ValueError(
                f"this adapter serves {self._source_id!r}, not {source_id!r}; "
                "resolve the adapter published for that source."
            )
        database = self._client[self._database_name]
        names = [
            name
            for name in await database.list_collection_names()
            if not name.startswith(_EXCLUDED_PREFIXES)
        ]

        datasets: list[DiscoveredDataset] = []
        for name in sorted(names):
            # Always read enough to infer shape, even when the caller retains
            # nothing: metadata is what the analysis actually reasons over.
            inspect_limit = max(sample_limit, 1) if sample_limit else _METADATA_SAMPLE_CEILING
            inspect_limit = min(inspect_limit, _METADATA_SAMPLE_CEILING)
            observed = [document async for document in database[name].find({}, limit=inspect_limit)]
            datasets.append(
                DiscoveredDataset(
                    source_id=source_id,
                    dataset_name=name,
                    fields=_infer_fields(observed),
                    approximate_row_count=await database[name].estimated_document_count(),
                    # Only what the caller asked for crosses back as retainable
                    # sample data; the rest was read for inference and dropped.
                    sample_rows=tuple(_strip_ids(observed[:sample_limit])),
                )
            )
        logger.info(
            "analyzer_source_discovered",
            extra={"source_id": source_id, "dataset_count": len(datasets)},
        )
        return datasets


def _strip_ids(documents: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Drop Mongo's `_id`. It is storage bookkeeping, not business shape, and it
    is not JSON-serialisable, which would break the prompt framing downstream."""
    return [{k: v for k, v in document.items() if k != "_id"} for document in documents]


def _infer_fields(documents: Sequence[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
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
            if key == "_id":
                continue
            present_counts[key] = present_counts.get(key, 0) + 1
            if value is None:
                null_counts[key] = null_counts.get(key, 0) + 1
                continue
            observed_types.setdefault(key, set()).add(_type_name(value))

    total = len(documents)
    fields: list[Mapping[str, Any]] = []
    for key in sorted(present_counts):
        types = observed_types.get(key, set())
        declared = types.pop() if len(types) == 1 else ("unknown" if not types else "mixed")
        fields.append(
            {
                "field_name": key,
                "declared_type": declared,
                "nullable": present_counts[key] < total or key in null_counts,
                "null_count": null_counts.get(key, 0),
            }
        )
    return tuple(fields)


def _type_name(value: Any) -> str:
    """Map a Python value onto the type vocabulary the analyzer's
    TYPE_COMPATIBILITY check understands. `bool` is checked before `int`
    because bool is an int subclass in Python."""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, datetime):
        return "datetime"
    if isinstance(value, date):
        return "date"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unknown"


def build_mongo_source_discovery_adapter(
    client: AsyncMongoClient[dict[str, Any]], *, database_name: str, source_id: str
) -> SourceDiscoveryPort:
    """Typed factory -- the return annotation is what makes mypy prove port
    conformance, rather than a runtime isinstance that only checks method names."""
    return MongoSourceDiscoveryAdapter(client, database_name=database_name, source_id=source_id)
