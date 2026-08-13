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


#: The result column a FULLTEXT_SEARCH row carries its server-side relevance in.
#: Consumers narrow by this rather than by row position -- the whole point of
#: the operation is that ranking happens over the complete indexed set, so the
#: score is the only bound that means anything.
FULLTEXT_SCORE_FIELD = "search_score"

#: The single read-only procedure this compiler is allowed to call. Written out
#: rather than assembled, so the read boundary in `Neo4jKnowledgeGateway` can
#: compare against exactly the same literal.
FULLTEXT_PROCEDURE = "db.index.fulltext.queryNodes"


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
        "PREFIX": f"toLower({prop}) STARTS WITH toLower(${parameter_name})",
        "CONTAINS": f"toLower({prop}) CONTAINS toLower(${parameter_name})",
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
        if plan.operation is QueryOperation.FULLTEXT_SEARCH:
            return self._compile_fulltext_read(schema, plan)
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

    def _compile_fulltext_read(self, schema: ActiveSchema, plan: LogicalQueryPlan) -> CompiledQuery:
        """Compile a ranked approximate search over a full-text index.

        The corpus is every node the index covers; `$limit` bounds how many of
        the *ranked* rows come back, which is the distinction the whole
        operation exists for. `ORDER BY score DESC` is emitted unconditionally
        here rather than through `plan.sort` -- a full-text read whose ordering
        were optional would be an unordered window again, and that is the defect
        this replaces.

        The index name is a parameter, never interpolated, and the label
        predicate is re-asserted from the schema's own node projection: if a
        caller ever named an index over some other label, this query returns
        nothing rather than nodes the plan's entity does not describe.
        """
        entity = schema.entities.get(plan.start_entity_id)
        if entity is None:
            raise QueryCompilationError(f"unknown entity: {plan.start_entity_id}")
        node = schema.entity_node(plan.start_entity_id)
        field_id = plan.fulltext_field_id
        if field_id is None or field_id not in entity.fields:
            raise QueryCompilationError(
                f"unknown full-text field {field_id!r} on entity {plan.start_entity_id!r}"
            )
        indexed_property = entity.fields[field_id].graph_property
        selected = plan.fields or tuple(
            selectable
            for selectable, field in entity.fields.items()
            if field.capabilities.displayable
        )
        if not selected:
            raise QueryCompilationError("query has no configured display fields")
        if FULLTEXT_SCORE_FIELD in selected:
            raise QueryCompilationError(
                f"{FULLTEXT_SCORE_FIELD!r} is reserved for the full-text relevance score"
            )
        return_items = [
            f"n0.{_quoted(_field_property(schema, entity.entity_id, selectable))} "
            f"AS {_quoted(selectable)}"
            for selectable in selected
        ]
        return_items.append(f"score AS {_quoted(FULLTEXT_SCORE_FIELD)}")
        cypher = "\n".join(
            (
                f"CALL {FULLTEXT_PROCEDURE}($fulltext_index, $fulltext_query, {{limit: $limit}})",
                "YIELD node AS n0, score",
                "WITH n0, score",
                f"WHERE n0:{_quoted(node.label)} AND n0.{_quoted(indexed_property)} IS NOT NULL",
                "RETURN " + ", ".join(return_items),
                "ORDER BY score DESC",
                "LIMIT $limit",
            )
        )
        parameters: dict[str, Any] = {
            "fulltext_index": plan.fulltext_index,
            "fulltext_query": plan.fulltext_query,
            "limit": plan.limit,
        }
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
        if operation is QueryOperation.EXISTS:
            return f"RETURN count({alias}) > 0 AS value"
        if operation is QueryOperation.GROUP_BY:
            if not plan.group_by_field_ids:
                raise QueryCompilationError("GROUP_BY requires group_by_field_ids")
            group_items = [
                f"{alias}.{_quoted(_field_property(schema, entity.entity_id, field_id))} "
                f"AS {_quoted(field_id)}"
                for field_id in plan.group_by_field_ids
            ]
            if plan.aggregation_field_id:
                agg_prop = _field_property(schema, entity.entity_id, plan.aggregation_field_id)
                group_items.append(f"count(DISTINCT {alias}.{_quoted(agg_prop)}) AS count")
            else:
                group_items.append("count(*) AS count")
            # Cypher implicitly groups by every non-aggregated expression in RETURN,
            # so listing the group-by columns alongside the count is sufficient.
            return "RETURN " + ", ".join(group_items) + " ORDER BY count DESC"
        if operation in {QueryOperation.DATE_RANGE, QueryOperation.SEMANTIC_SEARCH}:
            # Not implemented: DATE_RANGE should be expressed as a BETWEEN filter
            # condition instead of a top-level operation, and SEMANTIC_SEARCH needs
            # dedicated vector-index support this compiler does not yet have. Fail
            # loudly rather than silently falling back to an unrelated field read.
            raise QueryCompilationError(
                f"{operation.value} is not implemented by the Cypher compiler"
            )
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
