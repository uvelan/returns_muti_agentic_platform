"""Cross-store deterministic seed orchestration and validation."""

from __future__ import annotations

from typing import Any

from neo4j import AsyncDriver

from return_platform.configuration.settings import Settings
from return_platform.data_platform.graph.sync_service import (
    GraphSyncRequest,
    GraphSyncScope,
    GraphSyncService,
)
from return_platform.data_platform.schema_registry import SchemaRegistry
from return_platform.operations.models import SeedStatusView, utc_now
from return_platform.operations.repository import OperationalRepository
from return_platform.operations.seed_manifest import SEED_SCENARIOS
from return_platform.operations.sql_business_state import SQLBusinessStateRepository


class SeedCoordinator:
    """Apply deterministic sandbox fixtures, then rebuild the canonical graph projection."""

    def __init__(
        self,
        repository: OperationalRepository,
        sql: SQLBusinessStateRepository,
        neo4j: AsyncDriver,
        settings: Settings,
        registry: SchemaRegistry,
    ) -> None:
        self._repository = repository
        self._sql = sql
        self._neo4j = neo4j
        self._settings = settings
        self._graph_sync = GraphSyncService(
            platform_client=repository.platform_client,
            source_client=repository.source_client,
            driver=neo4j,
            settings=settings,
            registry=registry,
        )

    async def _graph_status(self) -> dict[str, Any]:
        order_references = [str(item["orderReference"]) for item in SEED_SCENARIOS]
        records, _, _ = await self._neo4j.execute_query(
            """
            MATCH (order:SalesOrder)
            WHERE order.sales_order_number IN $orderReferences
            OPTIONAL MATCH (order)-[:HAS_ORDER_LINE]->(line:OrderLine)
            OPTIONAL MATCH (customer:Customer)-[:PLACED_ORDER]->(order)
            RETURN count(DISTINCT order) AS orders,
                   count(DISTINCT line) AS lines,
                   count(DISTINCT CASE WHEN customer IS NOT NULL THEN order.sales_order_number END)
                     AS customerLinks
            """,
            orderReferences=order_references,
            database_=self._settings.neo4j_database,
        )
        row = records[0] if records else {}
        orders = int(row.get("orders", 0))
        lines = int(row.get("lines", 0))
        customer_links = int(row.get("customerLinks", 0))
        expected = len(SEED_SCENARIOS)
        return {
            "orders": orders,
            "lines": lines,
            "customerLinks": customer_links,
            "ready": orders == expected and lines >= expected and customer_links == expected,
        }

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
            counts["graphSeedOrders"] = int(graph_status["orders"])
            counts["graphSeedOrderLines"] = int(graph_status["lines"])
            counts["graphSeedCustomerLinks"] = int(graph_status["customerLinks"])
            if not graph_status["ready"]:
                errors.append("Neo4j canonical seed projection is incomplete.")
        except Exception:
            counts["graphSeedOrders"] = 0
            counts["graphSeedOrderLines"] = 0
            counts["graphSeedCustomerLinks"] = 0
            errors.append("Neo4j canonical seed projection could not be validated.")
        return base.model_copy(
            update={"ready": not errors, "counts": counts, "validationErrors": errors}
        )

    async def apply(self, actor_id: str) -> SeedStatusView:
        applied_at = utc_now()
        await self._repository.apply_seed(actor_id=actor_id)
        await self._sql.apply_seed_manifest(self._settings.seed_version, applied_at)
        await self._graph_sync.ensure_indexes()
        graph_run = await self._graph_sync.sync(
            GraphSyncRequest(
                mode=GraphSyncScope.SOURCE_MONGODB,
                maxRecordsPerAsset=max(100, len(SEED_SCENARIOS)),
                applySchema=True,
            ),
            actor_id=actor_id,
        )
        if graph_run.status != "COMPLETED":
            raise RuntimeError("Canonical graph synchronization did not complete.")
        return await self.status()

    async def reset_and_apply(self, actor_id: str) -> SeedStatusView:
        if self._settings.environment not in {"development", "test"}:
            raise PermissionError("Seed reset is restricted to development and test.")
        seed_version = self._settings.seed_version
        await self._repository.reset_demo_data()
        await self._sql.reset_seed_manifest(seed_version)
        # Neo4j is a derived projection. A sandbox reset may safely rebuild it from sources.
        await self._neo4j.execute_query(
            "MATCH (node) DETACH DELETE node",
            database_=self._settings.neo4j_database,
        )
        return await self.apply(actor_id)
