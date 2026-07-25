"""Fixed Neo4j schema management derived from the validated schema registry."""

from __future__ import annotations

import re

from neo4j import AsyncDriver

from return_platform.data_platform.schema_registry import GraphSchema

_SAFE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class GraphSchemaManager:
    def __init__(self, driver: AsyncDriver, database: str, schema: GraphSchema) -> None:
        self._driver = driver
        self._database = database
        self._schema = schema

    @staticmethod
    def _identifier(value: str) -> str:
        if not _SAFE.fullmatch(value):
            raise ValueError(f"Unsafe graph identifier: {value}")
        return value

    async def apply(self) -> list[str]:
        applied: list[str] = []
        async with self._driver.session(database=self._database) as session:
            for node in self._schema.nodes:
                label = self._identifier(node.label)
                key = self._identifier(node.key_property)
                constraint = self._identifier(f"uq_{label.lower()}_{key}")
                query = (
                    f"CREATE CONSTRAINT {constraint} IF NOT EXISTS "
                    f"FOR (n:{label}) REQUIRE n.{key} IS UNIQUE"
                )
                result = await session.run(query)
                await result.consume()
                applied.append(constraint)
        return applied

    async def describe(self) -> dict[str, object]:
        return {
            "nodes": [node.model_dump(mode="json") for node in self._schema.nodes],
            "relationships": [
                relationship.model_dump(mode="json") for relationship in self._schema.relationships
            ],
        }
