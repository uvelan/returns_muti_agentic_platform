"""Read-only Neo4j adapter for typed dynamic knowledge plans."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from neo4j import READ_ACCESS, AsyncDriver
from neo4j.spatial import Point
from neo4j.time import Date, DateTime, Duration, Time

from return_platform.dynamic_knowledge.knowledge.cypher_compiler import (
    FULLTEXT_PROCEDURE,
    GENERATION_PARAMETER,
)
from return_platform.dynamic_knowledge.knowledge.guards import roles_allowed as _roles_allowed
from return_platform.dynamic_knowledge.schema import ActiveSchema, FieldDefinition

_PROHIBITED = re.compile(
    r"\b(CREATE|MERGE|SET|DELETE|DETACH|DROP|REMOVE|LOAD\s+CSV|CALL|GRANT|DENY|REVOKE)\b",
    re.IGNORECASE,
)

#: The one procedure call this boundary admits, matched as a literal prefix.
#:
#: `CALL` is otherwise prohibited outright, and the ban is worth keeping: an
#: arbitrary procedure can write, read outside the graph, or reach the file
#: system. `db.index.fulltext.queryNodes` is a read procedure over an index the
#: platform itself creates (migration 0013) and is the only server-side
#: approximate match available without APOC -- the alternative was an unordered
#: window of rows scored on the client, which could miss the row it was looking
#: for. Everything after the prefix is still held to the same prohibition, so a
#: second `CALL` (or any write clause) smuggled into the tail is refused.
_FULLTEXT_PREFIX = f"CALL {FULLTEXT_PROCEDURE}("


def _json_safe(value: Any) -> Any:
    """Translate driver-native values into plain JSON at the read boundary.

    The driver returns its own types for temporal, duration, and spatial
    properties -- `neo4j.time.DateTime`, not `datetime`. Every consumer of a
    result row treats it as plain JSON: the evidence checksum canonicalizes it,
    the reasoning context serializes it into the prompt, and the API response
    encodes it. All three raise `TypeError: Object of type DateTime is not JSON
    serializable`, so any plan selecting a date field failed outright rather
    than degrading -- an order search on `sales_order` returns `order_date`, so
    this was most of them.

    Converting here rather than in the canonicalizer is the point. `sha256_digest`
    is a generic function with no business knowing what a graph driver is, and
    teaching it about `neo4j.time` would leave the same types loose in the prompt
    and the HTTP response. Driver types belong to the adapter and stop at its
    edge; ISO-8601 is what the rest of the system already expects a date to be.
    """
    if isinstance(value, (Date, Time, DateTime, Duration)):
        return value.iso_format()
    if isinstance(value, Point):
        # A tuple subclass, so it would otherwise serialize as a bare array and
        # silently lose the coordinate reference system that gives it meaning.
        return {"srid": value.srid, "coordinates": list(value)}
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str):
        return [_json_safe(item) for item in value]
    return value


def _is_permitted_read(normalized: str) -> bool:
    """Whether this compiled query may be executed at all.

    Two shapes only: a plain `MATCH` read, or a full-text query that opens with
    the single allowlisted read procedure. In the second case the tail after the
    procedure name is held to the identical prohibition, so the exception buys
    exactly one `CALL` and nothing else.
    """
    if normalized.startswith(_FULLTEXT_PREFIX):
        return not _PROHIBITED.search(normalized[len(_FULLTEXT_PREFIX) :])
    return normalized.startswith("MATCH") and not _PROHIBITED.search(normalized)


def _queryability_of(field: FieldDefinition, principal_roles: frozenset[str]) -> dict[str, Any]:
    """What this principal may actually put in a filter, not what the field can do.

    `SchemaQueryGuard.validate` admits a filter condition only when the field
    carries `filterable` *or* `searchable` **and** the principal holds a role in
    `permissions.searchable_by`. This method used to publish the first half and
    omit the second, so the schema in front of the model advertised 55 fields as
    `filterable: true` that the guard then refused for every role there is --
    `permissions.searchable_by` is absent on all 55, and an empty permitted-role
    set denies everyone. `order_line.account_id` is the one that cost a model a
    scenario; `sales_order.shipped_at`, `order_line.line_number` and
    `return_item.item_condition` are on the same list.

    The mismatch is a disclosure defect and it is fixed on the disclosure side.
    Granting those fields a `searchable_by` would make the advertisement true by
    widening what the guard permits, which is a security boundary moved to make a
    model's life easier; publishing the *effective* capability instead makes the
    advertisement true by narrowing it, and can only ever publish less than
    before. The predicate is the guard's own, imported rather than restated, so
    the two cannot drift.

    `operators` goes with them. A list of comparison operators for a field no
    filter may name is the same false offer one field further down, and nothing
    else reads it -- `strongAnchors` publishes its own per-anchor operator lists.

    The field itself is still listed, with its description, type, selectivity and
    display capabilities intact. What is withdrawn is only the claim that it can
    be queried on, which was never true for this principal.
    """
    filterable = field.capabilities.filterable or field.capabilities.searchable
    permitted = filterable and _roles_allowed(principal_roles, field.permissions.searchable_by)
    return {
        "searchable": field.capabilities.searchable and permitted,
        "filterable": permitted,
        "operators": sorted(field.capabilities.operators) if permitted else [],
    }


def _selectivity_of(field: FieldDefinition) -> dict[str, Any]:
    """W4.8: what the analyzer measured about how well this field narrows.

    Merged into the field entry rather than nested under its own key so it reads
    as more properties of the field, alongside `searchable` and `operators` --
    which is what it is. A model deciding which clarifying question is worth
    asking needs it in the same glance as the capability that makes the question
    askable at all.

    **Omitted entirely when nothing profiled the field.** Emitting
    `"identifierLikelihood": "UNKNOWN"` on every field of a hand-authored
    descriptor would spend context on eleven entities' worth of "we do not know",
    and a model reading a stated UNKNOWN tends to treat it as a finding rather
    than as silence. Absence is the honest encoding of "not measured".

    **`sampledRows` and `approximateRowCount` are emitted with the numbers they
    qualify.** `approximateDistinct: 40` is a strong narrowing signal over a
    50-row table and a weak one over a million rows sampled 50 deep. A consumer
    that cannot tell those apart is not ranking by selectivity, it is ranking by
    coincidence -- so the basis is not optional here even though it costs tokens.

    Nothing in this method orders anything. It reports evidence and leaves the
    ranking to the reasoning step, because a question order hardcoded here would
    be the same guess this step exists to replace, just written down.
    """
    selectivity = field.selectivity
    if selectivity is None:
        return {}
    reported: dict[str, Any] = {
        "nullRate": round(selectivity.null_rate, 4),
        "identifierLikelihood": selectivity.identifier_likelihood.value,
        "sampledRows": selectivity.sampled_rows,
    }
    if selectivity.approximate_distinct is not None:
        reported["approximateDistinct"] = selectivity.approximate_distinct
    if selectivity.approximate_row_count is not None:
        reported["approximateRowCount"] = selectivity.approximate_row_count
    ratio = selectivity.distinct_ratio
    if ratio is not None:
        # The derived number a ranker actually compares across fields, rounded
        # because four decimal places of a fifty-row sample is precision the
        # sample does not have.
        reported["distinctRatio"] = round(ratio, 4)
    return reported


class Neo4jKnowledgeGateway:
    def __init__(self, driver: AsyncDriver, *, database: str) -> None:
        self._driver = driver
        self._database = database

    async def compact_schema(
        self,
        schema: ActiveSchema,
        agent_id: str,
        *,
        principal_roles: frozenset[str],
    ) -> dict[str, Any]:
        policy = schema.agent_policies[agent_id]
        return {
            "schemaVersion": schema.schema_version,
            "entities": {
                entity_id: {
                    "description": schema.entities[entity_id].description,
                    "fields": {
                        field_id: {
                            "description": field.description,
                            "type": field.data_type.value,
                            **_queryability_of(field, principal_roles),
                            "distinct": field.capabilities.distinct,
                            "aggregatable": field.capabilities.aggregatable,
                            **_selectivity_of(field),
                        }
                        for field_id, field in schema.entities[entity_id].fields.items()
                    },
                }
                for entity_id in sorted(policy.allowed_entity_ids)
            },
            "relationships": {
                relationship_id: {
                    "from": relationship.source_entity_id,
                    "to": relationship.target_entity_id,
                    "direction": "OUTBOUND",
                }
                for relationship_id, relationship in schema.graph.relationships.items()
                if relationship.source_entity_id in policy.allowed_entity_ids
                and relationship.target_entity_id in policy.allowed_entity_ids
            },
            # The anchors a REQUEST_ON_DEMAND_SYNC may be built on.
            #
            # Without these the escalation is unreachable however well the
            # prompt describes it: the action needs a `strong_anchor_id` and the
            # exact fields that anchor requires, and the model had no way to
            # learn either. Only anchors that permit on-demand sync are listed,
            # because an anchor that does not is not an option the model has.
            "strongAnchors": {
                anchor_id: {
                    "entity": entity_id,
                    "fields": [
                        {
                            "field": anchor_field.field_id,
                            "operators": sorted(anchor_field.allowed_operators),
                            "required": anchor_field.required,
                        }
                        for anchor_field in anchor.fields
                    ],
                    "minimumFieldsPresent": anchor.minimum_fields_present,
                    "maximumExpectedMatches": anchor.maximum_expected_matches,
                }
                for entity_id in sorted(policy.allowed_entity_ids)
                for anchor_id, anchor in schema.entities[entity_id].strong_anchors.items()
                if anchor.on_demand_sync_allowed
            },
            "capabilities": sorted(policy.allowed_business_capabilities),
        }

    async def schema_details(
        self,
        schema: ActiveSchema,
        entity_ids: tuple[str, ...],
    ) -> dict[str, Any]:
        return {
            entity_id: schema.entities[entity_id].model_dump(mode="json")
            for entity_id in entity_ids
            if entity_id in schema.entities
        }

    async def execute(
        self,
        *,
        schema: ActiveSchema,
        graph_generation_id: str,
        plan: Any,
        compiled_cypher: str,
        parameters: dict[str, Any],
    ) -> Any:
        """Execute one compiled read, pinned to the generation the caller resolved.

        The generation used to be deleted here. `CypherCompiler` emitted no
        generation predicate and this method discarded the id, so the whole
        blue/green protocol -- the snapshot compare-and-swap, the read lease, the
        drain -- ended at the point where a query was actually run, and every
        read saw every generation the database held. Binding it is the other half
        of the predicate the compiler now emits (see `GENERATION_PARAMETER`).

        The caller's value always wins: a compiled query cannot supply its own
        generation, and a parameter map that carried one would be a second
        opinion about which generation serves this request -- exactly what
        `lifecycle/handle.py` exists to make impossible.
        """
        del plan
        normalized = compiled_cypher.strip()
        if not _is_permitted_read(normalized):
            raise ValueError("Only validated read-only Cypher is permitted")
        bound = {**parameters, GENERATION_PARAMETER: graph_generation_id}
        database = self._database or schema.graph.database
        async with self._driver.session(
            database=database, default_access_mode=READ_ACCESS
        ) as session:
            result = await session.run(normalized, bound)
            rows = [_json_safe(dict(record)) async for record in result]
        return {"rows": rows, "count": len(rows)}
