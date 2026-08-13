"""Read-only Neo4j adapter for typed dynamic knowledge plans."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from neo4j import READ_ACCESS, AsyncDriver
from neo4j.spatial import Point
from neo4j.time import Date, DateTime, Duration, Time

from return_platform.dynamic_knowledge.schema import ActiveSchema, FieldDefinition

_PROHIBITED = re.compile(
    r"\b(CREATE|MERGE|SET|DELETE|DETACH|DROP|REMOVE|LOAD\s+CSV|CALL|GRANT|DENY|REVOKE)\b",
    re.IGNORECASE,
)


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

    async def compact_schema(self, schema: ActiveSchema, agent_id: str) -> dict[str, Any]:
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
                            "searchable": field.capabilities.searchable,
                            "filterable": field.capabilities.filterable,
                            "distinct": field.capabilities.distinct,
                            "aggregatable": field.capabilities.aggregatable,
                            "operators": sorted(field.capabilities.operators),
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
        del graph_generation_id, plan
        normalized = compiled_cypher.strip()
        if not normalized.startswith("MATCH") or _PROHIBITED.search(normalized):
            raise ValueError("Only validated read-only Cypher is permitted")
        database = self._database or schema.graph.database
        async with self._driver.session(
            database=database, default_access_mode=READ_ACCESS
        ) as session:
            result = await session.run(normalized, parameters)
            rows = [_json_safe(dict(record)) async for record in result]
        return {"rows": rows, "count": len(rows)}
