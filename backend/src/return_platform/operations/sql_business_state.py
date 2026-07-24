"""SQL Server authoritative return/RMA/tracking persistence."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any, TypeVar

import pymssql

from return_platform.configuration.settings import Settings
from return_platform.operations.models import ReturnSessionView
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
                    cursor.execute("DELETE FROM dbo.e2e_seed_scenarios WHERE seed_version=%s", (seed_version,))
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
                    cursor.execute("DELETE FROM dbo.e2e_seed_scenarios WHERE seed_version=%s", (seed_version,))
                connection.commit()

        await self._run(operation)
