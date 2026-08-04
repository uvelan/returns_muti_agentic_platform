"""Safe parameterized Cypher compilation from validated logical plans."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from return_platform.dynamic_knowledge.fingerprint import sha256_digest
from return_platform.dynamic_knowledge.knowledge.query_plan import LogicalQueryPlan, QueryOperation
from return_platform.dynamic_knowledge.schema import (
    ActiveSchema,
    EntityDefinition,
    validate_graph_identifier,
)


class QueryCompilationError(ValueError):
    """Raised when a valid logical plan cannot be compiled safely."""


@dataclass(frozen=True, slots=True)
class CompiledQuery:
    cypher: str
    parameters: dict[str, Any]
    checksum: str
    read_only: bool = True


def _quoted(identifier: str) -> str:
    return f"`{validate_graph_identifier(identifier)}`"


def _field_property(schema: ActiveSchema, entity_id: str, field_id: str) -> str:
    entity = schema.entities.get(entity_id)
    if entity is None:
        raise QueryCompilationError(f"unknown entity: {entity_id}")
    field = entity.fields.get(field_id)
    if field is None:
        raise QueryCompilationError(f"unknown field {field_id!r} on entity {entity_id!r}")
    return field.graph_property


def _condition_expression(
    *,
    alias: str,
    property_name: str,
    operator: str,
    parameter_name: str,
) -> str:
    prop = f"{alias}.{_quoted(property_name)}"
    operators = {
        "EXACT": f"{prop} = ${parameter_name}",
        "EQUALS": f"{prop} = ${parameter_name}",
        "NOT_EQUALS": f"{prop} <> ${parameter_name}",
        "PREFIX": f"{prop} STARTS WITH ${parameter_name}",
        "CONTAINS": f"{prop} CONTAINS ${parameter_name}",
        "IN": f"{prop} IN ${parameter_name}",
        "GT": f"{prop} > ${parameter_name}",
        "GTE": f"{prop} >= ${parameter_name}",
        "LT": f"{prop} < ${parameter_name}",
        "LTE": f"{prop} <= ${parameter_name}",
        "EXISTS": f"{prop} IS NOT NULL",
        "MISSING": f"{prop} IS NULL",
    }
    if operator == "BETWEEN":
        return f"{prop} >= ${parameter_name}_from AND {prop} <= ${parameter_name}_to"
    try:
        return operators[operator]
    except KeyError as exc:
        raise QueryCompilationError(f"unsupported query operator: {operator}") from exc


class CypherCompiler:
    """Compile graph reads and schema-driven projection writes."""

    def compile_read(self, schema: ActiveSchema, plan: LogicalQueryPlan) -> CompiledQuery:
        aliases: dict[str, str] = {plan.start_entity_id: "n0"}
        start_node = schema.entity_node(plan.start_entity_id)
        match_parts = [f"MATCH (n0:{_quoted(start_node.label)})"]
        current_entity = plan.start_entity_id
        current_alias = "n0"
        for index, step in enumerate(plan.traversal, start=1):
            relationship = schema.graph.relationships.get(step.relationship_id)
            if relationship is None:
                raise QueryCompilationError(f"unknown relationship: {step.relationship_id}")
            if step.direction == "OUTBOUND":
                expected_source = current_entity
                expected_target = step.target_entity_id
                arrow = f"-[r{index}:{_quoted(relationship.relationship_type)}]->"
            else:
                expected_source = step.target_entity_id
                expected_target = current_entity
                arrow = f"<-[r{index}:{_quoted(relationship.relationship_type)}]-"
            if (
                relationship.source_entity_id != expected_source
                or relationship.target_entity_id != expected_target
            ):
                raise QueryCompilationError(
                    f"relationship {step.relationship_id!r} does not connect the requested entities"
                )
            target_node = schema.entity_node(step.target_entity_id)
            target_alias = f"n{index}"
            match_parts.append(
                f"MATCH ({current_alias}){arrow}({target_alias}:{_quoted(target_node.label)})"
            )
            aliases[step.target_entity_id] = target_alias
            current_entity = step.target_entity_id
            current_alias = target_alias

        parameters: dict[str, Any] = {}
        where_parts: list[str] = []
        for index, condition in enumerate(plan.filters):
            alias = aliases.get(condition.entity_id)
            if alias is None:
                raise QueryCompilationError(
                    f"filter entity {condition.entity_id!r} is not present in the traversal"
                )
            property_name = _field_property(schema, condition.entity_id, condition.field_id)
            parameter_name = f"p{index}"
            where_parts.append(
                _condition_expression(
                    alias=alias,
                    property_name=property_name,
                    operator=condition.operator,
                    parameter_name=parameter_name,
                )
            )
            if condition.operator == "BETWEEN":
                if not isinstance(condition.value, dict) or set(condition.value) != {"from", "to"}:
                    raise QueryCompilationError("BETWEEN requires {'from': ..., 'to': ...}")
                parameters[f"{parameter_name}_from"] = condition.value["from"]
                parameters[f"{parameter_name}_to"] = condition.value["to"]
            elif condition.operator not in {"EXISTS", "MISSING"}:
                parameters[parameter_name] = condition.value

        clauses = match_parts
        if where_parts:
            clauses.append("WHERE " + " AND ".join(where_parts))

        target_entity_id = current_entity
        target_alias = aliases[target_entity_id]
        target_entity = schema.entities[target_entity_id]
        return_clause = self._return_clause(schema, plan, target_entity, target_alias)
        clauses.append(return_clause)

        if plan.sort:
            sort_parts: list[str] = []
            for item in plan.sort:
                alias = aliases.get(item.entity_id)
                if alias is None:
                    raise QueryCompilationError(f"sort entity {item.entity_id!r} is not present")
                prop = _field_property(schema, item.entity_id, item.field_id)
                sort_parts.append(f"{alias}.{_quoted(prop)} {item.direction.value}")
            clauses.append("ORDER BY " + ", ".join(sort_parts))
        clauses.append("LIMIT $limit")
        parameters["limit"] = plan.limit
        cypher = "\n".join(clauses)
        return CompiledQuery(
            cypher=cypher,
            parameters=parameters,
            checksum=sha256_digest({"cypher": cypher, "parameter_names": sorted(parameters)}),
        )

    def _return_clause(
        self,
        schema: ActiveSchema,
        plan: LogicalQueryPlan,
        entity: EntityDefinition,
        alias: str,
    ) -> str:
        operation = plan.operation
        if operation is QueryOperation.COUNT:
            return f"RETURN count({alias}) AS value"
        if operation is QueryOperation.MISSING_VALUE_COUNT:
            field_id = plan.aggregation_field_id
            if field_id is None:
                raise QueryCompilationError("MISSING_VALUE_COUNT requires aggregation_field_id")
            prop = _field_property(schema, entity.entity_id, field_id)
            return f"RETURN count(CASE WHEN {alias}.{_quoted(prop)} IS NULL THEN 1 END) AS value"
        if operation in {
            QueryOperation.COUNT_DISTINCT,
            QueryOperation.MIN,
            QueryOperation.MAX,
            QueryOperation.SUM,
            QueryOperation.AVERAGE,
        }:
            field_id = plan.aggregation_field_id
            if field_id is None:
                raise QueryCompilationError(f"{operation.value} requires aggregation_field_id")
            prop = _field_property(schema, entity.entity_id, field_id)
            fn = {
                QueryOperation.COUNT_DISTINCT: "count(DISTINCT {value})",
                QueryOperation.MIN: "min({value})",
                QueryOperation.MAX: "max({value})",
                QueryOperation.SUM: "sum({value})",
                QueryOperation.AVERAGE: "avg({value})",
            }[operation]
            return "RETURN " + fn.format(value=f"{alias}.{_quoted(prop)}") + " AS value"
        if operation in {QueryOperation.DISTINCT_VALUES, QueryOperation.TOP_VALUES}:
            field_id = plan.aggregation_field_id
            if field_id is None:
                raise QueryCompilationError(f"{operation.value} requires aggregation_field_id")
            prop = _field_property(schema, entity.entity_id, field_id)
            if operation is QueryOperation.DISTINCT_VALUES:
                return f"RETURN DISTINCT {alias}.{_quoted(prop)} AS value"
            return f"RETURN {alias}.{_quoted(prop)} AS value, count(*) AS count ORDER BY count DESC"
        selected = plan.fields or tuple(
            field_id for field_id, field in entity.fields.items() if field.capabilities.displayable
        )
        if not selected:
            raise QueryCompilationError("query has no configured display fields")
        return_items = [
            f"{alias}.{_quoted(_field_property(schema, entity.entity_id, field_id))} AS {_quoted(field_id)}"
            for field_id in selected
        ]
        return "RETURN " + ", ".join(return_items)

    def compile_node_upsert(self, schema: ActiveSchema, entity_id: str) -> CompiledQuery:
        entity = schema.entities[entity_id]
        node = schema.entity_node(entity_id)
        key_entries = ", ".join(
            f"{_quoted(entity.fields[field_id].graph_property)}: row.keys.{_quoted(field_id)}"
            for field_id in node.key_fields
        )
        cypher = (
            "UNWIND $rows AS row\n"
            f"MERGE (node:{_quoted(node.label)} {{{key_entries}}})\n"
            "SET node += row.properties"
        )
        return CompiledQuery(
            cypher=cypher,
            parameters={},
            checksum=sha256_digest({"cypher": cypher}),
            read_only=False,
        )

    def compile_relationship_upsert(
        self, schema: ActiveSchema, relationship_id: str
    ) -> CompiledQuery:
        relationship = schema.graph.relationships[relationship_id]
        source_node = schema.entity_node(relationship.source_entity_id)
        target_node = schema.entity_node(relationship.target_entity_id)
        source_entity = schema.entities[relationship.source_entity_id]
        target_entity = schema.entities[relationship.target_entity_id]
        source_keys = ", ".join(
            f"{_quoted(source_entity.fields[field_id].graph_property)}: row.sourceKeys.{_quoted(field_id)}"
            for field_id in relationship.source_match_fields
        )
        target_keys = ", ".join(
            f"{_quoted(target_entity.fields[field_id].graph_property)}: row.targetKeys.{_quoted(field_id)}"
            for field_id in relationship.target_match_fields
        )
        cypher = (
            "UNWIND $rows AS row\n"
            f"MATCH (source:{_quoted(source_node.label)} {{{source_keys}}})\n"
            f"MATCH (target:{_quoted(target_node.label)} {{{target_keys}}})\n"
            f"MERGE (source)-[relationship:{_quoted(relationship.relationship_type)}]->(target)\n"
            "SET relationship += row.properties"
        )
        return CompiledQuery(
            cypher=cypher,
            parameters={},
            checksum=sha256_digest({"cypher": cypher}),
            read_only=False,
        )
