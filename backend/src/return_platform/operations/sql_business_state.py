"""SQL Server authoritative return/RMA/tracking persistence."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any, TypeVar

import pymssql

from return_platform.configuration.settings import Settings
from return_platform.operations.models import ReturnSessionView
from return_platform.operations.return_support.providers.contracts import ReturnSupportResult
from return_platform.operations.seed_manifest import SEED_ORDERS, SEED_SCENARIOS, manifest_digest

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
        digest = manifest_digest(
            seed_version,
            self._settings.validation_fingerprint_key.get_secret_value(),
        )
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
        digest = manifest_digest(
            seed_version,
            self._settings.validation_fingerprint_key.get_secret_value(),
        )

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

    async def reset_demo_business_state(self, seed_version: str) -> None:
        """Delete business facts created from deterministic E2E seed orders."""

        def operation() -> None:
            with self._connect() as connection:
                try:
                    with connection.cursor() as cursor:
                        rows: list[tuple[Any, ...]] = []
                        for offset in range(0, len(SEED_ORDERS), 1_000):
                            order_batch = tuple(
                                str(SEED_ORDERS[index]["orderReference"])
                                for index in range(
                                    offset,
                                    min(offset + 1_000, len(SEED_ORDERS)),
                                )
                            )
                            placeholders = ",".join("%s" for _ in order_batch)
                            cursor.execute(
                                f"""
                                SELECT session_id, return_reference
                                FROM dbo.return_requests
                                WHERE order_reference IN ({placeholders})
                                """,
                                order_batch,
                            )
                            rows.extend(cursor.fetchall())
                        session_ids = tuple(str(row[0]) for row in rows)
                        return_references = tuple(str(row[1]) for row in rows if row[1] is not None)

                        def delete_many(
                            table: str,
                            column: str,
                            values: tuple[str, ...],
                        ) -> None:
                            for offset in range(0, len(values), 1_000):
                                value_batch = values[offset : offset + 1_000]
                                value_placeholders = ",".join("%s" for _ in value_batch)
                                cursor.execute(
                                    f"DELETE FROM {table} WHERE {column} IN ({value_placeholders})",
                                    value_batch,
                                )

                        delete_many(
                            "platform.bay_assignment",
                            "return_reference",
                            return_references,
                        )
                        delete_many(
                            "dbo.return_tracking",
                            "return_reference",
                            return_references,
                        )
                        delete_many(
                            "integration.return_support_ticket",
                            "session_id",
                            session_ids,
                        )
                        delete_many("dbo.return_items", "session_id", session_ids)
                        delete_many("dbo.return_fulfillment", "session_id", session_ids)
                        delete_many("dbo.return_requests", "session_id", session_ids)
                        cursor.execute(
                            "DELETE FROM dbo.e2e_seed_scenarios WHERE seed_version=%s",
                            (seed_version,),
                        )
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise

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

    async def list_bay_candidates(
        self,
        *,
        warehouse_id: str | None,
        return_method: str,
        product_type: str,
    ) -> list[dict[str, Any]]:
        """Return platform-owned bay capacity candidates for agent ranking."""

        def operation() -> list[dict[str, Any]]:
            with self._connect() as connection:
                with connection.cursor(as_dict=True) as cursor:
                    cursor.execute(
                        """
                        SELECT configuration.bay_id, configuration.bay_type,
                               configuration.active, configuration.priority,
                               configuration.warehouse_id,
                               configuration.supported_shipping_paths,
                               configuration.supported_product_types,
                               configuration.hazardous_allowed,
                               configuration.oversized_allowed,
                               COALESCE(configuration.max_handling_unit_count,
                                        configuration.max_package_count) AS max_capacity,
                               COALESCE(reserved.reserved_capacity, 0) AS reserved_capacity
                        FROM platform.bay_configuration AS configuration
                        OUTER APPLY (
                            SELECT SUM(reservation.reserved_capacity) AS reserved_capacity
                            FROM platform.bay_reservation AS reservation
                            WHERE reservation.bay_id = configuration.bay_id
                              AND reservation.status IN ('RESERVED','ASSIGNED')
                              AND reservation.expires_at > SYSUTCDATETIME()
                        ) AS reserved
                        WHERE (%s IS NULL OR configuration.warehouse_id = %s)
                        ORDER BY configuration.priority ASC, configuration.bay_id ASC;
                        """,
                        (warehouse_id, warehouse_id),
                    )
                    rows = cursor.fetchall() or []
            candidates: list[dict[str, Any]] = []
            for row in rows:
                paths_raw = row.get("supported_shipping_paths") or "[]"
                product_types_raw = row.get("supported_product_types") or "[]"
                try:
                    paths = tuple(str(item) for item in json.loads(str(paths_raw)))
                except (TypeError, ValueError, json.JSONDecodeError):
                    paths = ()
                try:
                    product_types = tuple(str(item) for item in json.loads(str(product_types_raw)))
                except (TypeError, ValueError, json.JSONDecodeError):
                    product_types = ()
                if (
                    paths
                    and return_method not in paths
                    and not (return_method == "BRANCH_UPS" and "PPL" in paths)
                    and not (return_method in {"BRANCH_LTL", "OFFSITE_LTL"} and "BOL" in paths)
                ):
                    continue
                if product_types and product_type not in product_types:
                    continue
                max_capacity = int(row.get("max_capacity") or 0)
                reserved_capacity = int(row.get("reserved_capacity") or 0)
                candidates.append(
                    {
                        "bayId": str(row["bay_id"]),
                        "bayType": str(row["bay_type"]),
                        "warehouseId": str(row["warehouse_id"]),
                        "active": bool(row["active"]),
                        "priority": int(row["priority"]),
                        "capacityAvailable": max(0, max_capacity - reserved_capacity),
                        "supportsHazardous": bool(row.get("hazardous_allowed", False)),
                        "supportsOversized": bool(row.get("oversized_allowed", False)),
                        "supportedReturnMethods": [return_method],
                    }
                )
            return candidates

        return await self._run(operation)

    async def reserve_and_assign_handling_unit(
        self,
        session: ReturnSessionView,
        *,
        handling_unit_id: str,
        return_reference: str,
        bay_id: str,
        warehouse_id: str,
        required_capacity: int,
        actor_id: str,
        reservation_minutes: int = 60,
    ) -> tuple[str, str]:
        """Atomically reserve platform bay capacity and create one assignment."""
        if required_capacity < 1:
            raise ValueError("required_capacity must be at least one")
        reservation_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"bay-reservation:{return_reference}:{handling_unit_id}")
        )
        assignment_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"bay-assignment:{return_reference}:{handling_unit_id}")
        )

        def operation() -> tuple[str, str]:
            with self._connect() as connection:
                with connection.cursor(as_dict=True) as cursor:
                    cursor.execute(
                        """
                        SELECT configuration.bay_id, configuration.warehouse_id,
                               configuration.active,
                               COALESCE(configuration.max_handling_unit_count,
                                        configuration.max_package_count) AS max_capacity,
                               COALESCE(reserved.reserved_capacity, 0) AS reserved_capacity
                        FROM platform.bay_configuration AS configuration WITH (UPDLOCK, HOLDLOCK)
                        OUTER APPLY (
                            SELECT SUM(reservation.reserved_capacity) AS reserved_capacity
                            FROM platform.bay_reservation AS reservation WITH (HOLDLOCK)
                            WHERE reservation.bay_id = configuration.bay_id
                              AND reservation.status IN ('RESERVED','ASSIGNED')
                              AND reservation.expires_at > SYSUTCDATETIME()
                              AND reservation.reservation_id <> %s
                        ) AS reserved
                        WHERE configuration.bay_id = %s
                          AND configuration.warehouse_id = %s;
                        """,
                        (reservation_id, bay_id, warehouse_id),
                    )
                    row = cursor.fetchone()
                    if row is None or not bool(row["active"]):
                        raise RuntimeError("Selected bay is missing or inactive.")
                    available = int(row["max_capacity"] or 0) - int(row["reserved_capacity"] or 0)
                    if available < required_capacity:
                        raise RuntimeError("Selected bay has insufficient available capacity.")
                    cursor.execute(
                        """
                        IF NOT EXISTS (
                            SELECT 1 FROM platform.bay_reservation
                            WHERE reservation_id = %s
                        )
                        INSERT INTO platform.bay_reservation (
                            reservation_id, return_reference, session_id,
                            handling_unit_id, warehouse_id, bay_id,
                            reserved_capacity, status, expires_at
                        ) VALUES (
                            %s,%s,%s,%s,%s,%s,%s,'ASSIGNED',
                            DATEADD(MINUTE,%s,SYSUTCDATETIME())
                        );
                        """,
                        (
                            reservation_id,
                            reservation_id,
                            return_reference,
                            session.id,
                            handling_unit_id,
                            warehouse_id,
                            bay_id,
                            required_capacity,
                            reservation_minutes,
                        ),
                    )
                    first_item = session.itemReferences[0]
                    cursor.execute(
                        """
                        IF NOT EXISTS (
                            SELECT 1 FROM platform.bay_assignment
                            WHERE return_reference = %s AND handling_unit_id = %s
                        )
                        INSERT INTO platform.bay_assignment (
                            assignment_id, return_reference, sales_order_number,
                            order_line_id, item_number, package_count,
                            warehouse_id, bay_id, status,
                            confirmed_by_associate_id, confirmed_at,
                            reservation_id, handling_unit_id,
                            physical_receipt_confirmed, assignment_reason,
                            staged_at
                        ) VALUES (
                            %s,%s,%s,%s,%s,%s,%s,%s,'STAGED',
                            %s,SYSUTCDATETIME(),%s,%s,1,
                            'AGENT_RECOMMENDED_AND_HUMAN_CONFIRMED',SYSUTCDATETIME()
                        );
                        """,
                        (
                            return_reference,
                            handling_unit_id,
                            assignment_id,
                            return_reference,
                            session.orderReference,
                            first_item,
                            first_item,
                            required_capacity,
                            warehouse_id,
                            bay_id,
                            actor_id,
                            reservation_id,
                            handling_unit_id,
                        ),
                    )
                connection.commit()
            return reservation_id, assignment_id

        return await self._run(operation)
