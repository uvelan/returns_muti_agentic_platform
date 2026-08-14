"""Dropping the SQL Server databases the test suites leave behind.

`return_case_probe`, `return_pool_probe`, `return_shipment_probe`,
`return_shipment_graph_probe` and `return_shipment_concurrency_probe` are created
by real-infrastructure suites with `IF DB_ID(...) IS NULL CREATE DATABASE` and
never dropped -- their fixtures clean rows, not databases. They accumulate on
every machine anyone has run the suite on.

**Gated to development and test, hard.** The same gate
`DurableInterceptionProvider` uses, for a stronger reason: production has no
probe databases at all, so a reclaimer that runs there has nothing to gain and a
`DROP DATABASE` to lose. Nothing about this can be enabled by configuration.

**The suffix is a positive test and the application's databases are refused
against it.** `__init__` checks every protected name -- the platform's own
`sqlserver_database`, the AI Studio database, and SQL Server's four system
databases -- against every configured suffix and raises if any matches. A release
cannot name a suffix that would select the application's database, because the
reclaimer will not be constructed.

**A database in use is left alone, and that is the intended behaviour.** This
issues a plain `DROP DATABASE`, with no `SET SINGLE_USER WITH ROLLBACK
IMMEDIATE`. SQL Server refuses to drop a database with open connections, and the
one thing that holds connections to a probe database is a suite running against
it right now. Forcing the drop would make housekeeping able to fail a running
test; refusing to force it means the worst case is a database reclaimed on the
next pass instead of this one.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from return_platform.housekeeping.reclamation import ReclamationOutcome
from return_platform.source_connectors.identifiers import (
    UnsafeIdentifierError,
    validate_identifier,
)

__all__ = ["RESOURCE_CLASS", "SYSTEM_DATABASES", "ProbeDatabaseReclaimer"]

logger = logging.getLogger("return_platform.housekeeping.probe_databases")

RESOURCE_CLASS = "probe-database"

#: SQL Server's own databases. Dropping any of them takes the instance with it.
SYSTEM_DATABASES = frozenset({"master", "tempdb", "model", "msdb"})

_RECLAIMABLE_ENVIRONMENTS = frozenset({"development", "test"})


class ProbeDatabaseReclaimer:
    """Drops aged, unused, suffix-matched databases on the configured instance.

    Synchronous work behind an async method, like every other SQL caller in this
    platform: `pymssql` is a blocking C extension, so the connection is opened
    on a worker thread through the caller's `to_thread` shim rather than on the
    event loop.
    """

    def __init__(
        self,
        *,
        connect: Callable[[str], Any],
        to_thread: Callable[..., Any],
        environment: str,
        protected_database_names: Iterable[str | None],
        name_suffixes: Sequence[str],
        minimum_age_seconds: float,
        batch_limit: int,
    ) -> None:
        if minimum_age_seconds < 0:
            raise ValueError("minimum_age_seconds must not be negative")
        if batch_limit < 1:
            raise ValueError("batch_limit must be at least 1")
        suffixes = tuple(name_suffixes)
        if not suffixes:
            raise ValueError("name_suffixes must not be empty")
        protected = (
            frozenset(name.lower() for name in protected_database_names if name) | SYSTEM_DATABASES
        )
        for suffix in suffixes:
            if not suffix:
                # An empty suffix matches every database name in the instance.
                raise ValueError("a probe-database name suffix must not be empty")
            matched = sorted(name for name in protected if name.endswith(suffix.lower()))
            if matched:
                raise ValueError(
                    f"probe-database suffix {suffix!r} matches protected database(s) "
                    f"{', '.join(matched)}; a database this platform reads or writes can "
                    "never be reclaimable"
                )
        self._connect = connect
        self._to_thread = to_thread
        self._enabled = environment in _RECLAIMABLE_ENVIRONMENTS
        self._environment = environment
        self._protected = protected
        self._suffixes = suffixes
        self._minimum_age_seconds = minimum_age_seconds
        self._batch_limit = batch_limit

    @property
    def enabled(self) -> bool:
        return self._enabled

    def is_reclaimable(self, name: str) -> bool:
        """The whole name rule. Protection first, and independent of the suffix.

        The constructor has proved the two cannot overlap; this stays so that a
        later change to suffix matching cannot reach a protected name without
        also changing this line.
        """

        lowered = name.lower()
        if lowered in self._protected:
            return False
        return any(lowered.endswith(suffix.lower()) for suffix in self._suffixes)

    async def reclaim_once(self) -> ReclamationOutcome:
        if not self._enabled:
            return ReclamationOutcome.skipped(
                RESOURCE_CLASS,
                f"environment {self._environment!r} is not one of "
                f"{sorted(_RECLAIMABLE_ENVIRONMENTS)}",
            )
        cutoff = datetime.now(UTC) - timedelta(seconds=self._minimum_age_seconds)
        try:
            candidates = await self._to_thread(self._list_databases)
        except Exception:  # noqa: BLE001 - an unreachable instance is not a fault here
            logger.warning("housekeeping_probe_database_listing_failed", exc_info=True)
            return ReclamationOutcome.skipped(
                RESOURCE_CLASS, "the SQL Server instance could not be listed"
            )

        examined = 0
        too_young = 0
        unsafe = 0
        reclaimed: list[str] = []
        failed = 0
        for name, created_at in candidates:
            if len(reclaimed) >= self._batch_limit:
                break
            examined += 1
            if not self.is_reclaimable(name):
                continue
            try:
                # The name goes into bracketed DDL, which cannot be
                # parameterized. Validated against the shared allowlist rather
                # than trusted because it came from `sys.databases`: a database
                # whose name needs quoting is one this reclaimer declines to
                # compose a statement for.
                validate_identifier(name, what="database")
            except UnsafeIdentifierError:
                unsafe += 1
                logger.warning(
                    "housekeeping_probe_database_name_not_safe_to_drop",
                    extra={"database": name},
                )
                continue
            if created_at is None:
                continue
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=UTC)
            if created_at > cutoff:
                too_young += 1
                continue
            try:
                await self._to_thread(self._drop_database, name)
            except Exception:  # noqa: BLE001 - in use, or gone; the next pass retries
                failed += 1
                logger.info(
                    "housekeeping_probe_database_not_dropped",
                    extra={"database": name},
                    exc_info=True,
                )
                continue
            reclaimed.append(name)
            logger.info("housekeeping_probe_database_reclaimed", extra={"database": name})

        return ReclamationOutcome(
            resource_class=RESOURCE_CLASS,
            examined=examined,
            reclaimed=len(reclaimed),
            reclaimed_ids=tuple(reclaimed),
            failed=failed,
            details={"within_minimum_age": too_young, "unsafe_name": unsafe},
        )

    def _list_databases(self) -> tuple[tuple[str, datetime | None], ...]:
        connection = self._connect("master")
        try:
            with connection.cursor() as cursor:
                # `database_id > 4` excludes the system databases at the source
                # as well as in `SYSTEM_DATABASES`; `state_desc` excludes
                # databases that are not ONLINE, which a DROP would fail on
                # anyway and which may be mid-restore.
                cursor.execute(
                    "SELECT name, create_date FROM sys.databases "
                    "WHERE database_id > 4 AND state_desc = 'ONLINE' "
                    "ORDER BY create_date ASC"
                )
                return tuple((str(row[0]), _as_datetime(row[1])) for row in cursor.fetchall())
        finally:
            connection.close()

    def _drop_database(self, name: str) -> None:
        # Re-validated at the point of composition. The caller already did it,
        # and this is the line that actually builds SQL from a name -- a guard
        # one call frame away from the interpolation is a guard that a later
        # refactor can leave behind.
        safe = validate_identifier(name, what="database")
        connection = self._connect("master")
        try:
            with connection.cursor() as cursor:
                cursor.execute(f"DROP DATABASE [{safe}]")
        finally:
            connection.close()


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    return None
