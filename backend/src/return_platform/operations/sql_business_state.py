"""SQL Server authoritative return/RMA/tracking persistence."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any, TypeVar

import pymssql

from return_platform.configuration.settings import Settings
from return_platform.operations.models import ReturnSessionView
from return_platform.operations.return_support.providers.contracts import ReturnSupportResult
from return_platform.operations.seed_manifest import SEED_SCENARIOS, manifest_digest

T = TypeVar("T")


class SQLBusinessStateRepository:
    """Bounded blocking SQL access isolated behind asynchronous methods."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _connect(self) -> Any:
        timeout = max(1, int(self._settings.operation_timeout_seconds))
        return pymssql.connect(
            server=self._settings.sqlserver_host,
            port=str(self._settings.sqlserver_port),
            user=self._settings.sqlserver_user,
            password=self._settings.sqlserver_password.get_secret_value(),
            database=self._settings.sqlserver_database,
            login_timeout=timeout,
            timeout=timeout,
            autocommit=False,
        )

    async def _run(self, operation: Callable[[], T]) -> T:
        async with asyncio.timeout(self._settings.operation_timeout_seconds):
            return await asyncio.to_thread(operation)

    async def record_return_decision(
        self,
        session: ReturnSessionView,
        *,
        decision: str,
        return_reference: str | None,
        status: str,
    ) -> None:
        def operation() -> None:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE dbo.return_requests WITH (UPDLOCK, SERIALIZABLE)
                        SET correlation_id=%s, customer_reference=%s, order_reference=%s,
                            reason_code=%s, eligibility_decision=%s, return_reference=%s,
                            return_status=%s, row_version=row_version+1, updated_at=SYSUTCDATETIME()
                        WHERE session_id=%s;
                        IF @@ROWCOUNT = 0
                        INSERT INTO dbo.return_requests (
                            session_id, correlation_id, customer_reference, order_reference,
                            reason_code, eligibility_decision, return_reference, return_status
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s);
                        """,
                        (
                            session.correlationId,
                            session.customerReference,
                            session.orderReference,
                            session.reasonCode,
                            decision,
                            return_reference,
                            status,
                            session.id,
                            session.id,
                            session.correlationId,
                            session.customerReference,
                            session.orderReference,
                            session.reasonCode,
                            decision,
                            return_reference,
                            status,
                        ),
                    )
                connection.commit()

        await self._run(operation)

    async def record_fulfillment(
        self,
        session_id: str,
        *,
        fulfillment_reference: str | None,
        tracking_reference: str | None,
        warehouse_reference: str | None,
        bay_reference: str | None,
        status: str,
    ) -> None:
        def operation() -> None:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE dbo.return_fulfillment WITH (UPDLOCK, SERIALIZABLE)
                        SET fulfillment_reference=%s, tracking_reference=%s,
                            warehouse_reference=%s, bay_reference=%s,
                            fulfillment_status=%s, row_version=row_version+1,
                            updated_at=SYSUTCDATETIME()
                        WHERE session_id=%s;
                        IF @@ROWCOUNT = 0
                        INSERT INTO dbo.return_fulfillment (
                            session_id, fulfillment_reference, tracking_reference,
                            warehouse_reference, bay_reference, fulfillment_status
                        ) VALUES (%s,%s,%s,%s,%s,%s);
                        """,
                        (
                            fulfillment_reference,
                            tracking_reference,
                            warehouse_reference,
                            bay_reference,
                            status,
                            session_id,
                            session_id,
                            fulfillment_reference,
                            tracking_reference,
                            warehouse_reference,
                            bay_reference,
                            status,
                        ),
                    )
                connection.commit()

        await self._run(operation)

    async def mark_return_status(self, session_id: str, status: str) -> None:
        def operation() -> None:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE dbo.return_requests
                        SET return_status=%s, row_version=row_version+1, updated_at=SYSUTCDATETIME()
                        WHERE session_id=%s
                        """,
                        (status, session_id),
                    )
                    if cursor.rowcount != 1:
                        raise RuntimeError("Authoritative return record is missing.")
                connection.commit()

        await self._run(operation)

    async def apply_seed_manifest(self, seed_version: str, applied_at: datetime) -> int:
        digest = manifest_digest(seed_version)
        rows: Sequence[tuple[Any, ...]] = tuple(
            (
                scenario["id"],
                seed_version,
                digest,
                scenario["orderReference"],
                scenario["customerReference"],
                scenario["reasonCode"],
                scenario["expectedDecision"],
                applied_at,
            )
            for scenario in SEED_SCENARIOS
        )

        def operation() -> int:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM dbo.e2e_seed_scenarios WHERE seed_version=%s", (seed_version,)
                    )
                    cursor.executemany(
                        """
                        INSERT INTO dbo.e2e_seed_scenarios (
                            scenario_id, seed_version, seed_digest, order_reference,
                            customer_reference, reason_code, expected_decision, applied_at
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                        """,
                        rows,
                    )
                connection.commit()
            return len(rows)

        return await self._run(operation)

    async def seed_status(self, seed_version: str) -> dict[str, Any]:
        digest = manifest_digest(seed_version)

        def operation() -> dict[str, Any]:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT COUNT_BIG(*), MIN(seed_digest), MAX(seed_digest)
                        FROM dbo.e2e_seed_scenarios
                        WHERE seed_version=%s
                        """,
                        (seed_version,),
                    )
                    row = cursor.fetchone()
            count = int(row[0]) if row is not None else 0
            observed = str(row[1]) if row is not None and row[1] is not None else ""
            uniform = bool(row is not None and row[1] == row[2])
            return {
                "count": count,
                "digest": observed,
                "ready": count == len(SEED_SCENARIOS) and uniform and observed == digest,
            }

        return await self._run(operation)

    async def reset_seed_manifest(self, seed_version: str) -> None:
        def operation() -> None:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM dbo.e2e_seed_scenarios WHERE seed_version=%s", (seed_version,)
                    )
                connection.commit()

        await self._run(operation)

    async def persist_support_result(
        self,
        session: ReturnSessionView,
        *,
        decision: str,
        request_digest: str,
        result: ReturnSupportResult,
    ) -> None:
        """Atomically persist the support ticket and authoritative return/tracking facts."""

        def operation() -> None:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE dbo.return_requests WITH (UPDLOCK, SERIALIZABLE)
                        SET correlation_id=%s, customer_reference=%s, order_reference=%s,
                            reason_code=%s, eligibility_decision=%s, return_reference=%s,
                            return_status=%s, row_version=row_version+1, updated_at=SYSUTCDATETIME()
                        WHERE session_id=%s;
                        IF @@ROWCOUNT = 0
                        INSERT INTO dbo.return_requests (
                            session_id, correlation_id, customer_reference, order_reference,
                            reason_code, eligibility_decision, return_reference, return_status
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s);
                        """,
                        (
                            session.correlationId,
                            session.customerReference,
                            session.orderReference,
                            session.reasonCode,
                            decision,
                            result.return_reference,
                            "APPROVED" if decision == "APPROVE" else "REJECTED",
                            session.id,
                            session.id,
                            session.correlationId,
                            session.customerReference,
                            session.orderReference,
                            session.reasonCode,
                            decision,
                            result.return_reference,
                            "APPROVED" if decision == "APPROVE" else "REJECTED",
                        ),
                    )
                    cursor.execute(
                        """
                        UPDATE integration.return_support_ticket WITH (UPDLOCK, SERIALIZABLE)
                        SET request_digest=%s, status=%s, external_reference=%s,
                            return_reference=%s, updated_at=SYSUTCDATETIME()
                        WHERE session_id=%s;
                        IF @@ROWCOUNT = 0
                        INSERT INTO integration.return_support_ticket (
                            ticket_id, session_id, request_digest, status,
                            external_reference, return_reference
                        ) VALUES (%s,%s,%s,%s,%s,%s);
                        """,
                        (
                            request_digest,
                            result.ticket_status,
                            result.external_reference,
                            result.return_reference,
                            session.id,
                            result.ticket_id,
                            session.id,
                            request_digest,
                            result.ticket_status,
                            result.external_reference,
                            result.return_reference,
                        ),
                    )
                    if result.return_reference is not None:
                        item_id = f"ITEM-{result.return_reference}"
                        cursor.execute(
                            """
                            IF NOT EXISTS (SELECT 1 FROM dbo.return_items WHERE return_item_id=%s)
                            INSERT INTO dbo.return_items (
                                return_item_id, session_id, return_reference, order_line_id,
                                product_id, quantity, reason_code, item_status
                            ) VALUES (%s,%s,%s,%s,%s,%s,%s,'CREATED');
                            """,
                            (
                                item_id,
                                item_id,
                                session.id,
                                result.return_reference,
                                session.itemReferences[0],
                                (session.productReferences or session.itemReferences)[0],
                                session.returnQuantity,
                                session.reasonCode,
                            ),
                        )
                        cursor.execute(
                            """
                            UPDATE dbo.return_fulfillment WITH (UPDLOCK, SERIALIZABLE)
                            SET fulfillment_reference=%s, tracking_reference=%s,
                                fulfillment_status='TRACKING_ACTIVE', row_version=row_version+1,
                                updated_at=SYSUTCDATETIME()
                            WHERE session_id=%s;
                            IF @@ROWCOUNT = 0
                            INSERT INTO dbo.return_fulfillment (
                                session_id, fulfillment_reference, tracking_reference,
                                fulfillment_status
                            ) VALUES (%s,%s,%s,'TRACKING_ACTIVE');
                            """,
                            (
                                result.fulfillment_reference,
                                result.tracking_reference,
                                session.id,
                                session.id,
                                result.fulfillment_reference,
                                result.tracking_reference,
                            ),
                        )
                        if result.tracking_reference is not None:
                            tracking_id = f"TRACK-{result.return_reference}"
                            cursor.execute(
                                """
                                IF NOT EXISTS (
                                    SELECT 1 FROM dbo.return_tracking WHERE tracking_id=%s
                                )
                                INSERT INTO dbo.return_tracking (
                                    tracking_id, return_reference, tracking_type,
                                    tracking_reference, carrier_code, tracking_status, event_at
                                ) VALUES (%s,%s,%s,%s,'UPS','LABEL_CREATED',SYSUTCDATETIME());
                                """,
                                (
                                    tracking_id,
                                    tracking_id,
                                    result.return_reference,
                                    result.shipping_path,
                                    result.tracking_reference,
                                ),
                            )
                connection.commit()

        await self._run(operation)

    async def assign_bay(
        self,
        session: ReturnSessionView,
        *,
        return_reference: str,
        shipping_path: str,
        package_count: int = 1,
    ) -> tuple[str, str]:
        """Select the highest-priority compatible active bay and persist one assignment."""

        def operation() -> tuple[str, str]:
            with self._connect() as connection:
                with connection.cursor(as_dict=True) as cursor:
                    cursor.execute(
                        """
                        SELECT TOP (1) configuration.bay_id, configuration.warehouse_id
                        FROM platform.bay_configuration AS configuration WITH (UPDLOCK, HOLDLOCK)
                        OUTER APPLY (
                            SELECT COALESCE(SUM(assignment.package_count), 0) AS assigned_packages
                            FROM platform.bay_assignment AS assignment WITH (HOLDLOCK)
                            WHERE assignment.bay_id=configuration.bay_id
                              AND assignment.status IN ('CREATED','CONFIRMED','HOLD')
                        ) AS capacity
                        WHERE configuration.active=1
                          AND configuration.supported_shipping_paths LIKE %s
                          AND configuration.supported_product_types LIKE %s
                          AND (%s IS NULL OR configuration.warehouse_id=%s)
                          AND configuration.max_package_count-capacity.assigned_packages >= %s
                        ORDER BY configuration.priority ASC, configuration.bay_id ASC
                        """,
                        (
                            f'%"{shipping_path}"%',
                            f'%"{session.productType or "STANDARD"}"%',
                            session.processingWarehouseReference,
                            session.processingWarehouseReference,
                            package_count,
                        ),
                    )
                    row = cursor.fetchone()
                    if row is None:
                        raise RuntimeError("No compatible active bay is configured.")
                    bay_id = str(row["bay_id"])
                    warehouse_id = str(row["warehouse_id"])
                    assignment_id = f"ASN-{return_reference}"
                    cursor.execute(
                        """
                        IF NOT EXISTS (
                            SELECT 1 FROM platform.bay_assignment
                            WHERE return_reference=%s AND order_line_id=%s
                        )
                        INSERT INTO platform.bay_assignment (
                            assignment_id, return_reference, sales_order_number, order_line_id,
                            item_number, package_count, warehouse_id, bay_id, status
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'CREATED');
                        UPDATE dbo.return_fulfillment
                        SET warehouse_reference=%s, bay_reference=%s,
                            fulfillment_status='ASSIGNED', row_version=row_version+1,
                            updated_at=SYSUTCDATETIME()
                        WHERE session_id=%s;
                        """,
                        (
                            return_reference,
                            session.itemReferences[0],
                            assignment_id,
                            return_reference,
                            session.orderReference,
                            session.itemReferences[0],
                            session.itemReferences[0],
                            package_count,
                            warehouse_id,
                            bay_id,
                            warehouse_id,
                            bay_id,
                            session.id,
                        ),
                    )
                connection.commit()
                return warehouse_id, bay_id

        return await self._run(operation)

    async def record_feedback_recommendation(
        self,
        *,
        recommendation_id: str,
        session_id: str,
        area: str,
        recommendation: str,
        evidence_digest: str,
    ) -> None:
        def operation() -> None:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        IF NOT EXISTS (
                            SELECT 1 FROM platform.feedback_recommendation
                            WHERE session_id=%s AND area=%s AND evidence_digest=%s
                        )
                        INSERT INTO platform.feedback_recommendation (
                            recommendation_id, session_id, area, recommendation,
                            evidence_digest, review_status
                        ) VALUES (%s,%s,%s,%s,%s,'REVIEW_PENDING');
                        """,
                        (
                            session_id,
                            area,
                            evidence_digest,
                            recommendation_id,
                            session_id,
                            area,
                            recommendation,
                            evidence_digest,
                        ),
                    )
                connection.commit()

        await self._run(operation)
