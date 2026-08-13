"""`SourceInspectionPort` over a Neo4j database treated as a *source*.

Worth separating from `analyzer_graph_target_adapter.py`, which is the other
direction: that module compiles a proposed schema into DDL for the graph the
platform writes. This one reads a Neo4j instance the platform did not build, so
its shape can be described the same way a table or a collection is. The two never
share a code path -- this file emits no DDL and no write, and there is no method
on the port to put one on.

Node labels are the objects. That is the mapping that makes the four connectors
answer the same eight questions: a label has properties (`describe_object`),
constraints and indexes (`list_indexes`), a cheap count from the label count store
(`approximate_row_count`), and -- uniquely among the four -- relationships the
store itself declares rather than infers (`list_relationships`).

Every Cypher literal here is fixed text with bound parameters, except for the
label in a `MATCH (n:Label)` pattern, which Cypher will not accept as a parameter.
That one is re-validated against the identifier pattern immediately before
interpolation, in this file, rather than trusted from the caller.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from neo4j import AsyncDriver
from neo4j.exceptions import Neo4jError, ServiceUnavailable

from return_platform.bootstrap.adapters.source_inspection_profiling import build_profile
from return_platform.graph_schema_analyzer.ports.source_port import (
    FieldDescription,
    IndexDescription,
    ObjectDescription,
    ObjectKind,
    ObjectProfile,
    RelationshipKind,
    RelationshipObservation,
    SourceInspectionPort,
    SourceObjectRef,
    SourceValidation,
)

__all__ = ["Neo4jSourceInspectionAdapter", "build_neo4j_source_inspection_adapter"]

# The same shape `analyzer_graph_target_adapter` enforces, and duplicated for the
# same reason: this is the last gate before interpolation, and a boundary that
# imports its validation from the layer it defends against is not a second gate.
_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")

_SAMPLE_CEILING = 100


class UnsafeLabelError(ValueError):
    """A node label that cannot be safely interpolated into a Cypher pattern."""


class Neo4jSourceInspectionAdapter:
    """Structurally satisfies `SourceInspectionPort` for exactly one source."""

    def __init__(self, driver: AsyncDriver, *, source_id: str, database: str | None = None) -> None:
        self._driver = driver
        self._source_id = source_id
        self._database = database

    async def validate(self, *, source_id: str) -> SourceValidation:
        self._require_source(source_id)
        try:
            rows = await self._run(
                "CALL dbms.components() YIELD name, versions "
                "RETURN name AS name, versions[0] AS version",
                {},
            )
        except (Neo4jError, ServiceUnavailable) as exc:
            return SourceValidation(source_id=source_id, reachable=False, detail=str(exc))
        version = None
        if rows:
            version = f"{rows[0].get('name')} {rows[0].get('version')}".strip()
        return SourceValidation(source_id=source_id, reachable=True, server_version=version)

    async def list_sources(self) -> Sequence[str]:
        return (self._source_id,)

    async def list_objects(self, *, source_id: str) -> Sequence[SourceObjectRef]:
        self._require_source(source_id)
        rows = await self._run("CALL db.labels() YIELD label RETURN label AS label", {})
        return tuple(
            SourceObjectRef(
                source_id=source_id,
                object_name=str(row["label"]),
                object_kind=ObjectKind.NODE_LABEL,
            )
            for row in sorted(rows, key=lambda row: str(row["label"]))
        )

    async def describe_object(self, *, source_id: str, object_name: str) -> ObjectDescription:
        """Properties come from `db.schema.nodeTypeProperties`, not from sampled
        nodes.

        The procedure walks the store's own type information, so a property that
        appears on one node in ten million is still reported -- which a bounded
        sample would miss, and a schema proposal that omits a real property is
        one the analyzer cannot later map.
        """
        label = self._label(source_id, object_name)
        rows = await self._run(
            """
            CALL db.schema.nodeTypeProperties()
            YIELD nodeLabels, propertyName, propertyTypes, mandatory
            WITH nodeLabels, propertyName, propertyTypes, mandatory
            WHERE $label IN nodeLabels
            RETURN propertyName AS property_name,
                   propertyTypes AS property_types,
                   mandatory AS mandatory
            """,
            {"label": label},
        )
        return ObjectDescription(
            source_id=source_id,
            object_name=object_name,
            object_kind=ObjectKind.NODE_LABEL,
            fields=tuple(
                FieldDescription(
                    field_name=str(row["property_name"]),
                    # A property observed with more than one type is reported as
                    # `mixed`; picking one would launder a real disagreement in
                    # the store into a confident mapping that fails at sync time.
                    declared_type=_single_type(row["property_types"]),
                    nullable=not bool(row["mandatory"]),
                )
                for row in sorted(rows, key=lambda row: str(row["property_name"]))
            ),
            approximate_row_count=await self._node_count(label),
        )

    async def sample(
        self,
        *,
        source_id: str,
        object_name: str,
        limit: int,
        fields: Sequence[str] | None = None,
    ) -> Sequence[Mapping[str, Any]]:
        label = self._label(source_id, object_name)
        return tuple(await self._read(label, limit, fields))

    async def profile(self, *, source_id: str, object_name: str, sample_size: int) -> ObjectProfile:
        label = self._label(source_id, object_name)
        description = await self.describe_object(source_id=source_id, object_name=object_name)
        return build_profile(
            source_id=source_id,
            object_name=object_name,
            rows=await self._read(label, sample_size, None),
            approximate_row_count=description.approximate_row_count,
            declared_types={field.field_name: field.declared_type for field in description.fields},
        )

    async def list_indexes(self, *, source_id: str, object_name: str) -> Sequence[IndexDescription]:
        """`SHOW INDEXES` plus `SHOW CONSTRAINTS`, because Neo4j splits what every
        other backend reports together.

        A uniqueness constraint is backed by an index whose `SHOW INDEXES` row
        does not say it is unique; reporting only the index would tell the
        analyzer a key it can rely on is merely a lookup, and it would then not
        propose it as an identity.
        """
        label = self._label(source_id, object_name)
        index_rows = await self._run(
            "SHOW INDEXES YIELD name, labelsOrTypes, properties, owningConstraint "
            "RETURN name AS name, labelsOrTypes AS labels, properties AS properties, "
            "owningConstraint AS owning_constraint",
            {},
        )
        constraint_rows = await self._run(
            "SHOW CONSTRAINTS YIELD name, labelsOrTypes, properties, type "
            "RETURN name AS name, labelsOrTypes AS labels, properties AS properties, "
            "type AS type",
            {},
        )
        unique_keys = {
            (str(row["name"]))
            for row in constraint_rows
            if _matches_label(row["labels"], label) and "UNIQUE" in str(row["type"]).upper()
        }
        return tuple(
            IndexDescription(
                index_name=str(row["name"]),
                fields=tuple(str(prop) for prop in (row["properties"] or ())),
                unique=str(row["owning_constraint"] or "") in unique_keys,
                # Neo4j has no primary key. Reporting one would invent a concept
                # the store does not have; identity is expressed as a uniqueness
                # constraint, which `unique` already carries.
                primary=False,
            )
            for row in index_rows
            if _matches_label(row["labels"], label)
        )

    async def list_relationships(
        self, *, source_id: str, object_name: str | None = None
    ) -> Sequence[RelationshipObservation]:
        """From `db.schema.visualization()`, which reads the store's schema rather
        than scanning relationships.

        A bounded `MATCH ()-[r]->() ... LIMIT n` would be cheaper to write and
        would silently report only the relationship types that happen to appear
        in the first n rows -- an incomplete answer indistinguishable from a
        complete one.
        """
        self._require_source(source_id)
        if object_name is not None:
            self._label(source_id, object_name)
        rows = await self._run(
            "CALL db.schema.visualization() YIELD nodes, relationships "
            "RETURN nodes AS nodes, relationships AS relationships",
            {},
        )
        observations: list[RelationshipObservation] = []
        for row in rows:
            for relationship in row.get("relationships") or ():
                start, end = _endpoint_labels(relationship)
                if start is None or end is None:
                    continue
                if object_name is not None and start != object_name:
                    continue
                observations.append(
                    RelationshipObservation(
                        source_id=source_id,
                        relationship_kind=RelationshipKind.GRAPH_RELATIONSHIP,
                        from_object=start,
                        # A graph relationship is not keyed by properties the way
                        # a foreign key is -- the edge itself is the reference --
                        # so the field tuples are empty rather than invented.
                        from_fields=(),
                        to_object=end,
                        to_fields=(),
                        constraint_name=_relationship_type(relationship),
                    )
                )
        return tuple(observations)

    async def _node_count(self, label: str) -> int | None:
        """`count(n)` over a label alone is answered from Neo4j's count store, so
        it does not scan -- which is why this is affordable on every describe."""
        rows = await self._run(f"MATCH (n:{label}) RETURN count(n) AS node_count", {})
        return int(rows[0]["node_count"]) if rows else None

    async def _read(
        self, label: str, limit: int, fields: Sequence[str] | None
    ) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), _SAMPLE_CEILING))
        rows = await self._run(
            f"MATCH (n:{label}) RETURN properties(n) AS properties LIMIT $limit",
            {"limit": bounded},
        )
        documents = [dict(row["properties"] or {}) for row in rows]
        if fields is None:
            return documents
        allowed = set(fields)
        return [
            {key: value for key, value in document.items() if key in allowed}
            for document in documents
        ]

    async def _run(self, cypher: str, parameters: Mapping[str, Any]) -> list[dict[str, Any]]:
        async with self._driver.session(database=self._database) as session:
            result = await session.run(cypher, dict(parameters))
            return [dict(record) async for record in result]

    def _label(self, source_id: str, object_name: str) -> str:
        self._require_source(source_id)
        if not _IDENTIFIER.fullmatch(object_name):
            raise UnsafeLabelError(f"unsafe Neo4j node label: {object_name!r}")
        return object_name

    def _require_source(self, source_id: str) -> None:
        if source_id != self._source_id:
            raise ValueError(
                f"this adapter serves {self._source_id!r}, not {source_id!r}; "
                "resolve the adapter published for that source."
            )


def _single_type(property_types: Any) -> str:
    types = [str(item) for item in (property_types or ())]
    if not types:
        return "unknown"
    if len(types) == 1:
        return types[0]
    return "mixed"


def _matches_label(labels: Any, label: str) -> bool:
    return label in {str(item) for item in (labels or ())}


def _endpoint_labels(relationship: Any) -> tuple[str | None, str | None]:
    """`db.schema.visualization` returns driver Relationship objects whose
    endpoints are virtual nodes carrying the label. Read defensively: a store
    with an unlabelled endpoint yields a relationship this adapter cannot name,
    and skipping it beats reporting an edge to `None`."""
    start = getattr(relationship, "start_node", None)
    end = getattr(relationship, "end_node", None)
    return (_first_label(start), _first_label(end))


def _first_label(node: Any) -> str | None:
    """One label per endpoint, chosen alphabetically when a node carries several.

    Objects in this interface are single names, so a multi-labelled endpoint has
    to collapse to one. Sorting makes the choice deterministic -- two runs against
    an unchanged graph agree, which is what an operator diffing a re-analysis
    depends on -- but it is still a choice, and a graph that labels nodes
    `:Bay:Location` will see the relationship reported against `Bay`.
    """
    labels = getattr(node, "labels", None)
    if not labels:
        return None
    ordered = sorted(str(item) for item in labels)
    return ordered[0] if ordered else None


def _relationship_type(relationship: Any) -> str | None:
    value = getattr(relationship, "type", None)
    return None if value is None else str(value)


def build_neo4j_source_inspection_adapter(
    driver: AsyncDriver, *, source_id: str, database: str | None = None
) -> SourceInspectionPort:
    """Typed factory -- the return annotation is what makes mypy prove port
    conformance, rather than a runtime isinstance that only checks method names."""
    return Neo4jSourceInspectionAdapter(driver, source_id=source_id, database=database)
