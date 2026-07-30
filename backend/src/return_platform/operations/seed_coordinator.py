"""Cross-store deterministic seed orchestration and validation."""

from __future__ import annotations

import asyncio
from contextlib import suppress
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
from return_platform.operations.seed_control import SeedOperationCancelled, SeedOperationControl
from return_platform.operations.seed_manifest import (
    effective_seed_counts,
    manifest_digest,
    seed_order_line_count,
)
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

    async def _graph_status(self, record_limit: int | None) -> dict[str, Any]:
        seed_version = self._settings.seed_version
        seed_digest = manifest_digest(
            seed_version,
            self._settings.validation_fingerprint_key.get_secret_value(),
            record_limit,
        )
        records, _, _ = await self._neo4j.execute_query(
            """
            MATCH (order:SalesOrder)
            WHERE order.seed_version = $seedVersion AND order.seed_digest = $seedDigest
            OPTIONAL MATCH (order)-[:HAS_ORDER_LINE]->(line:OrderLine)
            OPTIONAL MATCH (customer:Customer)-[:PLACED_ORDER]->(order)
            RETURN count(DISTINCT order) AS orders,
                   count(DISTINCT line) AS lines,
                   count(DISTINCT CASE WHEN customer IS NOT NULL THEN order.sales_order_number END)
                     AS customerLinks
            """,
            seedVersion=seed_version,
            seedDigest=seed_digest,
            database_=self._settings.neo4j_database,
        )
        row = records[0] if records else {}
        orders = int(row.get("orders", 0))
        lines = int(row.get("lines", 0))
        customer_links = int(row.get("customerLinks", 0))
        expected = effective_seed_counts(record_limit)["orders"]
        expected_lines = seed_order_line_count(record_limit)
        return {
            "orders": orders,
            "lines": lines,
            "customerLinks": customer_links,
            "ready": (
                orders == expected and lines == expected_lines and customer_links == expected
            ),
        }

    async def status(self) -> SeedStatusView:
        base = await self._repository.seed_status()
        record_limit = base.requestedRecordLimit
        errors = list(base.validationErrors)
        counts = dict(base.counts)
        try:
            sql_status = await self._sql.seed_status(
                self._settings.seed_version,
                record_limit,
            )
            counts["sqlSeedScenarios"] = int(sql_status["count"])
            if not sql_status["ready"]:
                errors.append("SQL Server seed manifest is incomplete or has digest drift.")
        except Exception:
            counts["sqlSeedScenarios"] = 0
            errors.append("SQL Server seed manifest could not be validated.")
        try:
            graph_status = await self._graph_status(record_limit)
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

    async def apply(
        self,
        actor_id: str,
        *,
        record_limit: int,
        control: SeedOperationControl,
        operation_id: str,
    ) -> SeedStatusView:
        applied_at = utc_now()
        await self._repository.apply_seed(
            actor_id=actor_id,
            record_limit=record_limit,
            cancel_check=lambda: control.raise_if_cancelled(operation_id),
            progress=lambda count, phase: control.update(
                operation_id,
                processed_delta=count,
                phase=phase,
            ),
        )
        control.raise_if_cancelled(operation_id)
        await control.update(operation_id, phase="Writing SQL seed manifest")
        await self._sql.apply_seed_manifest(
            self._settings.seed_version,
            applied_at,
            record_limit,
        )
        control.raise_if_cancelled(operation_id)
        await control.update(operation_id, phase="Synchronizing Neo4j projection")
        await self._graph_sync.ensure_indexes()
        graph_task = asyncio.create_task(
            self._graph_sync.sync(
                GraphSyncRequest(
                    mode=GraphSyncScope.SOURCE_MONGODB,
                    maxRecordsPerAsset=min(record_limit, 100_000),
                    applySchema=True,
                ),
                actor_id=actor_id,
                seed_version=self._settings.seed_version,
                seed_digest=manifest_digest(
                    self._settings.seed_version,
                    self._settings.validation_fingerprint_key.get_secret_value(),
                    record_limit,
                ),
            )
        )
        cancel_task = asyncio.create_task(control.wait_for_cancel(operation_id))
        done, _ = await asyncio.wait(
            {graph_task, cancel_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if cancel_task in done:
            graph_task.cancel()
            with suppress(asyncio.CancelledError):
                await graph_task
            raise SeedOperationCancelled("Seed operation stopped during graph synchronization.")
        cancel_task.cancel()
        with suppress(asyncio.CancelledError):
            await cancel_task
        graph_run = await graph_task
        if graph_run.status != "COMPLETED":
            raise RuntimeError("Canonical graph synchronization did not complete.")
        return await self.status()

    async def delete_all(
        self,
        *,
        record_limit: int | None = None,
        control: SeedOperationControl | None = None,
        operation_id: str | None = None,
    ) -> SeedStatusView:
        if self._settings.environment not in {"development", "test"}:
            raise PermissionError("Seed deletion is restricted to development and test.")

        def check_cancelled() -> None:
            if control is not None and operation_id is not None:
                control.raise_if_cancelled(operation_id)

        async def update_phase(phase: str) -> None:
            if control is not None and operation_id is not None:
                await control.update(operation_id, phase=phase)

        seed_version = self._settings.seed_version
        order_count = effective_seed_counts(record_limit)["orders"]
        check_cancelled()
        await update_phase("Deleting MongoDB seed records")
        await self._repository.delete_seed_data()
        check_cancelled()
        await update_phase("Deleting SQL seed records")
        await self._sql.reset_demo_business_state(seed_version, order_count)
        check_cancelled()
        await update_phase("Deleting Neo4j seed projection")
        await self._neo4j.execute_query(
            """
            MATCH (node)
            WHERE node.seed_version = $seedVersion
            DETACH DELETE node
            """,
            seedVersion=seed_version,
            database_=self._settings.neo4j_database,
        )
        check_cancelled()
        return await self.status()

    async def reset_and_apply(
        self,
        actor_id: str,
        *,
        record_limit: int,
        control: SeedOperationControl,
        operation_id: str,
    ) -> SeedStatusView:
        current = await self._repository.seed_status()
        await self.delete_all(
            record_limit=current.requestedRecordLimit,
            control=control,
            operation_id=operation_id,
        )
        return await self.apply(
            actor_id,
            record_limit=record_limit,
            control=control,
            operation_id=operation_id,
        )
