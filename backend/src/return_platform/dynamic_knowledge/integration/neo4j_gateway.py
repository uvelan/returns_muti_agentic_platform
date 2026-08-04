"""Read-only Neo4j adapter for typed dynamic knowledge plans."""

from __future__ import annotations

import re
from typing import Any

from neo4j import READ_ACCESS, AsyncDriver

from return_platform.dynamic_knowledge.schema import ActiveSchema

_PROHIBITED = re.compile(
    r"\b(CREATE|MERGE|SET|DELETE|DETACH|DROP|REMOVE|LOAD\s+CSV|CALL|GRANT|DENY|REVOKE)\b",
    re.IGNORECASE,
)


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
            rows = [dict(record) async for record in result]
        return {"rows": rows, "count": len(rows)}
