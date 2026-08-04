"""Connector-specific compilation of logical targeted reads."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from return_platform.dynamic_knowledge.on_demand_sync.contracts import LogicalTargetedReadPlan
from return_platform.dynamic_knowledge.schema import (
    ActiveSchema,
    ConnectorType,
    validate_graph_identifier,
)


@dataclass(frozen=True, slots=True)
class CompiledSourceRead:
    connector_type: ConnectorType
    statement: Any
    parameters: dict[str, Any]
    projected_physical_paths: tuple[tuple[str, ...], ...]


def compile_source_read(schema: ActiveSchema, plan: LogicalTargetedReadPlan) -> CompiledSourceRead:
    source = schema.sources[plan.source_asset_id]
    entity = schema.entities[plan.entity_id]
    projected_paths = tuple(
        entity.fields[field_id].physical_path for field_id in plan.required_field_ids
    )
    if source.connector_type is ConnectorType.MONGODB:
        predicates: list[dict[str, Any]] = []
        parameters: dict[str, Any] = {}
        for index, condition in enumerate(plan.conditions):
            path = ".".join(entity.fields[condition.field_id].physical_path)
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
            condition_path = entity.fields[condition.field_id].physical_path
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
            projected_physical_paths=projected_paths,
        )
    raise AssertionError("unreachable connector type")
