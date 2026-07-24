"""Cross-store deterministic seed orchestration and validation."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from neo4j import AsyncDriver

from return_platform.configuration.settings import Settings
from return_platform.operations.models import SeedStatusView, utc_now
from return_platform.operations.repository import OperationalRepository
from return_platform.operations.seed_manifest import SEED_SCENARIOS, manifest_digest
from return_platform.operations.sql_business_state import SQLBusinessStateRepository


class SeedCoordinator:
    def __init__(
        self,
        repository: OperationalRepository,
        sql: SQLBusinessStateRepository,
        neo4j: AsyncDriver,
        settings: Settings,
    ) -> None:
        self._repository = repository
        self._sql = sql
        self._neo4j = neo4j
        self._settings = settings

    async def _apply_graph(self, applied_at: datetime) -> int:
        seed_version = self._settings.seed_version
        digest = manifest_digest(seed_version)
        scenarios = [dict(item) for item in SEED_SCENARIOS]
        query = """
        UNWIND $scenarios AS scenario
        MERGE (customer:Customer {reference: scenario.customerReference})
        SET customer.seedVersion = $seedVersion, customer.seedDigest = $seedDigest
        MERGE (product:Product {sku: scenario.sku})
        SET product.seedVersion = $seedVersion, product.seedDigest = $seedDigest
        MERGE (order:Order {reference: scenario.orderReference})
        SET order.status = scenario.orderStatus,
            order.scenarioId = scenario.id,
            order.expectedDecision = scenario.expectedDecision,
            order.reasonCode = scenario.reasonCode,
            order.seedVersion = $seedVersion,
            order.seedDigest = $seedDigest,
            order.seedAppliedAt = $appliedAt
        MERGE (customer)-[:PLACED]->(order)
        MERGE (order)-[:CONTAINS]->(product)
        RETURN count(order) AS count
        """
        records, _, _ = await self._neo4j.execute_query(
            query,
            scenarios=scenarios,
            seedVersion=seed_version,
            seedDigest=digest,
            appliedAt=applied_at,
            database_=self._settings.neo4j_database,
        )
        return int(records[0]["count"]) if records else 0

    async def _graph_status(self) -> dict[str, Any]:
        seed_version = self._settings.seed_version
        digest = manifest_digest(seed_version)
        records, _, _ = await self._neo4j.execute_query(
            """
            MATCH (order:Order {seedVersion: $seedVersion})
            RETURN count(order) AS count,
                   count(CASE WHEN order.seedDigest = $seedDigest THEN 1 END) AS matching
            """,
            seedVersion=seed_version,
            seedDigest=digest,
            database_=self._settings.neo4j_database,
        )
        count = int(records[0]["count"]) if records else 0
        matching = int(records[0]["matching"]) if records else 0
        return {"count": count, "ready": count == len(SEED_SCENARIOS) and matching == count}

    async def status(self) -> SeedStatusView:
        base = await self._repository.seed_status()
        errors = list(base.validationErrors)
        counts = dict(base.counts)
        try:
            sql_status = await self._sql.seed_status(self._settings.seed_version)
            counts["sqlSeedScenarios"] = int(sql_status["count"])
            if not sql_status["ready"]:
                errors.append("SQL Server seed manifest is incomplete or has digest drift.")
        except Exception:
            counts["sqlSeedScenarios"] = 0
            errors.append("SQL Server seed manifest could not be validated.")
        try:
            graph_status = await self._graph_status()
            counts["graphSeedOrders"] = int(graph_status["count"])
            if not graph_status["ready"]:
                errors.append("Neo4j seed projection is incomplete or has digest drift.")
        except Exception:
            counts["graphSeedOrders"] = 0
            errors.append("Neo4j seed projection could not be validated.")
        return base.model_copy(
            update={"ready": not errors, "counts": counts, "validationErrors": errors}
        )

    async def apply(self, actor_id: str) -> SeedStatusView:
        applied_at = utc_now()
        await self._repository.apply_seed(actor_id=actor_id)
        await self._sql.apply_seed_manifest(self._settings.seed_version, applied_at)
        graph_count = await self._apply_graph(applied_at)
        if graph_count != len(SEED_SCENARIOS):
            raise RuntimeError("Neo4j did not acknowledge every seed scenario.")
        return await self.status()

    async def reset_and_apply(self, actor_id: str) -> SeedStatusView:
        seed_version = self._settings.seed_version
        await self._repository.reset_demo_data()
        await self._sql.reset_seed_manifest(seed_version)
        await self._neo4j.execute_query(
            "MATCH (node {seedVersion: $seedVersion}) DETACH DELETE node",
            seedVersion=seed_version,
            database_=self._settings.neo4j_database,
        )
        return await self.apply(actor_id)
