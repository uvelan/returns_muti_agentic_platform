"""Connector-specific compilation of logical targeted reads.

Moved from `dynamic_knowledge.on_demand_sync.source_compilers` (Phase 8 /
Wave C1) -- it had zero production consumers and exactly one test importer
(updated in the same commit), and is a source-*read* compilation concern
(translating a `LogicalTargetedReadPlan` into one connector-specific
statement), not a graph-mutation concern, so it belongs here rather than
under `dynamic_knowledge.on_demand_sync`.

`MongoDBSourceScanConnector.targeted_read()` is the first real caller of
`compile_source_read`'s MongoDB branch (Phase 8 / Wave C1) -- previously this
module was compiled-but-never-executed logic, since no connector implemented
`TargetedSourceConnector.targeted_read()` anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from return_platform.dynamic_knowledge.schema import (
    ActiveSchema,
    ConnectorType,
    EntityDefinition,
    PathOrigin,
    validate_graph_identifier,
)
from return_platform.source_connectors.contracts import LogicalTargetedReadPlan


@dataclass(frozen=True, slots=True)
class CompiledSourceRead:
    connector_type: ConnectorType
    statement: Any
    parameters: dict[str, Any]
    projected_physical_paths: tuple[tuple[str, ...], ...]


def _physical_path(entity: EntityDefinition, field_id: str) -> tuple[str, ...]:
    """The field's source path, or a refusal naming the field.

    `FieldDefinition.physical_path` is optional because a field may be *derived*
    (`derive: FieldDerivation`) rather than read from the source. Compiling a
    read for such a field is a real mistake, and every use below assumed it
    could not happen: `".".join(None)` raises `TypeError: can only join an
    iterable`, which says nothing about which field or why, and the projection
    path let a bare `None` into `projected_physical_paths` for a caller to trip
    over later.
    """
    field = entity.fields[field_id]
    path = field.physical_path
    if path is None:
        raise ValueError(
            f"field {field_id!r} on entity {entity.entity_id!r} has no physical path; "
            "a derived field cannot be read directly from the source"
        )
    return path


def _projected_candidates(
    record_path: tuple[str, ...], origin: PathOrigin, path: tuple[str, ...]
) -> tuple[tuple[str, ...], ...]:
    """Where in the raw document a field's value could be read from.

    `physical_path` is relative to the field's `path_origin`, not to the
    document, so an exploded child's own paths only exist under the entity's
    `record_path`. A projection built from `physical_path` alone selects
    `lineNumber` at the top level -- a key no salesInv document has -- and the
    child is silently never extracted.

    PARENT_RECORD is the one origin the schema cannot resolve on its own: it is
    the map one list level above the exploded record, which is the root when the
    record_path crossed one list and an intermediate element when it crossed
    more, and how many it crossed depends on the document. Every level it could
    have been is projected. All of them are paths configuration named, so
    over-projecting here cannot surface a field nobody approved.
    """
    if origin is PathOrigin.ROOT_DOCUMENT:
        return (path,)
    if origin is PathOrigin.CURRENT_RECORD:
        return ((*record_path, *path),)
    depths = range(len(record_path)) if record_path else range(1)
    return tuple((*record_path[:depth], *path) for depth in depths)


def source_projection_paths(
    schema: ActiveSchema, source_asset_id: str
) -> tuple[tuple[str, ...], ...]:
    """Every physical path extraction reads out of one document of this source.

    **A targeted read is projected before it is extracted, and extraction runs
    every entity bound to the source over whatever document it is handed.**
    Projecting only the anchoring entity's own mapped fields therefore hands
    extraction a document that cannot satisfy the rules it is about to apply.
    That was not theoretical: `sales_order` restricts itself with
    `where: salesHdrEventData.docType == headerLines`, which is nobody's mapped
    field, so an on-demand read anchored on an order number fetched the right
    salesInv document, stripped the discriminator out of it, failed the `where`,
    and discarded the order -- reporting SUCCEEDED with the order's own node
    never written. The order's lines went the same way, for the same reason.

    So the projection is a property of the *source asset*, not of one entity:
    every mapped field of every entity backed by it, every `where` selector
    those entities test, and nothing else. A derived field contributes nothing;
    it has no source path and is computed from siblings already projected.

    Paths that are prefixes of other selected paths are dropped -- MongoDB
    rejects a projection naming both `a` and `a.b`.
    """
    paths: set[tuple[str, ...]] = set()
    for entity in schema.entities.values():
        if entity.source_asset_id != source_asset_id:
            continue
        record_path = entity.record_path if entity.explode else ()
        for field in entity.fields.values():
            if field.physical_path is None:
                continue
            paths.update(_projected_candidates(record_path, field.path_origin, field.physical_path))
        for selector in entity.where:
            # Current-record relative: `_passes_where` tests the exploded record,
            # not the root.
            paths.update(
                _projected_candidates(
                    record_path, PathOrigin.CURRENT_RECORD, selector.physical_path
                )
            )
    return tuple(
        sorted(
            path
            for path in paths
            if not any(len(other) > len(path) and other[: len(path)] == path for other in paths)
        )
    )


def compile_source_read(schema: ActiveSchema, plan: LogicalTargetedReadPlan) -> CompiledSourceRead:
    source = schema.sources[plan.source_asset_id]
    entity = schema.entities[plan.entity_id]
    # The whole source document as configuration describes it, not just the
    # anchoring entity's slice of it -- see `source_projection_paths`. The plan's
    # `required_field_ids` still names what the *caller* asked for, and the
    # Neo4j branch below returns exactly those, because a Neo4j source read
    # yields rows keyed by field id rather than a document to extract from.
    projected_paths = source_projection_paths(schema, plan.source_asset_id)
    if source.connector_type is ConnectorType.MONGODB:
        predicates: list[dict[str, Any]] = []
        parameters: dict[str, Any] = {}
        for index, condition in enumerate(plan.conditions):
            path = ".".join(_physical_path(entity, condition.field_id))
            parameter = f"p{index}"
            parameters[parameter] = condition.value
            if condition.operator in {"EXACT", "EQUALS"}:
                predicates.append({path: {"$eq": condition.value}})
            elif condition.operator == "NORMALIZED_EQUALS":
                predicates.append(
                    {
                        "$expr": {
                            "$eq": [
                                {"$toLower": {"$trim": {"input": f"${path}"}}},
                                condition.value,
                            ]
                        }
                    }
                )
            elif condition.operator == "BETWEEN":
                if not isinstance(condition.value, dict):
                    raise ValueError("BETWEEN requires an object value")
                predicates.append(
                    {path: {"$gte": condition.value["from"], "$lte": condition.value["to"]}}
                )
            else:
                raise ValueError(f"unsupported MongoDB anchor operator: {condition.operator}")
        query = predicates[0] if len(predicates) == 1 else {"$and": predicates}
        projection = {".".join(path): 1 for path in projected_paths}
        return CompiledSourceRead(
            connector_type=source.connector_type,
            statement={"filter": query, "projection": projection, "limit": plan.maximum_rows},
            parameters=parameters,
            projected_physical_paths=projected_paths,
        )
    if source.connector_type in {ConnectorType.MSSQL, ConnectorType.POSTGRESQL}:
        table = source.object_ref.get("name")
        if table is None:
            raise ValueError("SQL source object_ref requires name")
        validate_graph_identifier(table)
        columns: list[str] = []
        for projected_path in projected_paths:
            if len(projected_path) != 1:
                raise ValueError("SQL physical paths must contain exactly one column segment")
            validate_graph_identifier(projected_path[0])
            columns.append(f'"{projected_path[0]}"')
        sql_where: list[str] = []
        parameters = {}
        for index, condition in enumerate(plan.conditions):
            condition_path = _physical_path(entity, condition.field_id)
            if len(condition_path) != 1:
                raise ValueError("SQL anchor path must contain exactly one column segment")
            column = condition_path[0]
            validate_graph_identifier(column)
            parameter = f"p{index}"
            if condition.operator in {"EXACT", "EQUALS"}:
                sql_where.append(f'"{column}" = :{parameter}')
                parameters[parameter] = condition.value
            elif condition.operator == "NORMALIZED_EQUALS":
                sql_where.append(f'LOWER(TRIM("{column}")) = :{parameter}')
                parameters[parameter] = condition.value
            elif condition.operator == "BETWEEN":
                if not isinstance(condition.value, dict):
                    raise ValueError("BETWEEN requires an object value")
                sql_where.append(f'"{column}" BETWEEN :{parameter}_from AND :{parameter}_to')
                parameters[f"{parameter}_from"] = condition.value["from"]
                parameters[f"{parameter}_to"] = condition.value["to"]
            else:
                raise ValueError(f"unsupported SQL anchor operator: {condition.operator}")
        limit = "TOP (:limit) " if source.connector_type is ConnectorType.MSSQL else ""
        suffix = "" if source.connector_type is ConnectorType.MSSQL else " LIMIT :limit"
        parameters["limit"] = plan.maximum_rows
        statement = f'SELECT {limit}{", ".join(columns)} FROM "{table}" WHERE {" AND ".join(sql_where)}{suffix}'
        return CompiledSourceRead(
            connector_type=source.connector_type,
            statement=statement,
            parameters=parameters,
            projected_physical_paths=projected_paths,
        )
    if source.connector_type is ConnectorType.NEO4J:
        label = source.object_ref.get("label")
        if label is None:
            raise ValueError("Neo4j source object_ref requires label")
        validate_graph_identifier(label)
        parameters = {"limit": plan.maximum_rows}
        neo4j_where: list[str] = []
        for index, condition in enumerate(plan.conditions):
            field = entity.fields[condition.field_id]
            validate_graph_identifier(field.graph_property)
            parameter = f"p{index}"
            if condition.operator in {"EXACT", "EQUALS"}:
                neo4j_where.append(f"n.`{field.graph_property}` = ${parameter}")
                parameters[parameter] = condition.value
            elif condition.operator == "NORMALIZED_EQUALS":
                neo4j_where.append(
                    f"toLower(trim(toString(n.`{field.graph_property}`))) = ${parameter}"
                )
                parameters[parameter] = condition.value
            elif condition.operator == "BETWEEN":
                if not isinstance(condition.value, dict):
                    raise ValueError("BETWEEN requires an object value")
                neo4j_where.append(
                    f"n.`{field.graph_property}` >= ${parameter}_from AND "
                    f"n.`{field.graph_property}` <= ${parameter}_to"
                )
                parameters[f"{parameter}_from"] = condition.value["from"]
                parameters[f"{parameter}_to"] = condition.value["to"]
            else:
                raise ValueError(f"unsupported Neo4j anchor operator: {condition.operator}")
        returns = ", ".join(
            f"n.`{entity.fields[field_id].graph_property}` AS `{field_id}`"
            for field_id in plan.required_field_ids
        )
        statement = (
            f"MATCH (n:`{label}`) WHERE {' AND '.join(neo4j_where)} RETURN {returns} LIMIT $limit"
        )
        return CompiledSourceRead(
            connector_type=source.connector_type,
            statement=statement,
            parameters=parameters,
            # The RETURN clause above, not the source-wide projection: this
            # branch returns rows keyed by the requested field ids, so those are
            # what it read.
            projected_physical_paths=tuple(
                _physical_path(entity, field_id) for field_id in plan.required_field_ids
            ),
        )
    raise AssertionError("unreachable connector type")
