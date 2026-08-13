"""The bounded SQL Server pool against a real SQL Server.

`test_sql_connection_pool.py` proves the pool's arithmetic with a connection
double. That is necessary and not sufficient: a ceiling the application believes
in is worth nothing unless the *server* agrees. So the measurement here is SQL
Server's own -- every borrower asks `SELECT @@SPID`, which is the server naming
the session it is being served on, and `sys.dm_exec_sessions` says which of
those sessions still exist.

Session identity rather than a sampled session count, deliberately. Sampling
`COUNT(*)` was tried first and is wrong twice over: it counts sessions the
client has already closed but the server has not yet reaped -- so a reaped or
discarded connection reads as a breach of the ceiling -- and it needs a polling
thread whose load is itself material on a contended server. Distinct SPIDs
across the whole run is both stricter (a fourth connection cannot hide between
samples) and free of wall-clock assumptions.

Real infrastructure, so it runs in the compose stack
(`bash backend/scripts/dev/run_real_infra_suite.sh`), not on a host with no SQL
Server. It is deliberately not skipped when the server is missing: a pool that
cannot reach SQL Server is a failure, and a silent skip is how a suite reports
green for code it never ran.

It uses its own database rather than `test_db` or `return_platform`, because it
asserts which sessions exist against that database and must not be counting
anyone else's.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any

import pymssql
import pytest

from return_platform.configuration.settings import Settings
from return_platform.operations.sql_connection_pool import (
    SQLConnectionPool,
    SQLConnectionPoolClosedError,
    SQLConnectionPoolTimeoutError,
)

#: See `tests/source_connectors/conftest.py` for why `login_timeout` alone is
#: not enough to bound opening a connection: a crashed `sqlservr` leaves the
#: port open and the prelogin response never arrives.
_CONNECT_DEADLINE_SECONDS = 30

PROBE_DATABASE = "return_pool_probe"


def _connect(settings: Settings, database: str) -> Any:
    return pymssql.connect(
        server=settings.sqlserver_host,
        port=str(settings.sqlserver_port),
        user=settings.sqlserver_user,
        password=settings.sqlserver_password.get_secret_value(),
        database=database,
        login_timeout=10,
        timeout=30,
        autocommit=True,
    )


def _connect_within_deadline(settings: Settings, database: str) -> Any:
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pool-probe-connect")
    future = executor.submit(_connect, settings, database)
    try:
        return future.result(timeout=_CONNECT_DEADLINE_SECONDS)
    except FutureTimeoutError:
        raise RuntimeError(
            f"SQL Server at {settings.sqlserver_host}:{settings.sqlserver_port} accepted a "
            f"connection but did not complete login within {_CONNECT_DEADLINE_SECONDS}s."
        ) from None
    finally:
        executor.shutdown(wait=False)


def _open_probe_database(settings: Settings) -> Any:
    """Connect to the probe database, waiting for it to finish coming up.

    `CREATE DATABASE` returns before the database is connectable: it is still
    recovering for a second or two while its files are created, and a
    connection that arrives in that window is refused with `Login failed for
    user 'sa'` -- the same misleading error `tests/source_connectors/conftest.py`
    documents for a database that does not exist at all. Observed here: the
    first two tests in this file failed that way and the rest passed, purely on
    ordering.
    """
    deadline = time.monotonic() + _CONNECT_DEADLINE_SECONDS
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return _connect_within_deadline(settings, PROBE_DATABASE)
        except pymssql.Error as exc:
            last = exc
            time.sleep(0.5)
    raise RuntimeError(
        f"{PROBE_DATABASE} did not become connectable within {_CONNECT_DEADLINE_SECONDS}s: {last}"
    )


@pytest.fixture
def probe_settings(test_settings: Settings) -> Iterator[Settings]:
    """A throwaway database and table, and settings pointed at them."""

    admin = _connect_within_deadline(test_settings, "master")
    with admin:
        with admin.cursor() as cursor:
            cursor.execute(
                "IF DB_ID(%(name)s) IS NULL EXEC('CREATE DATABASE [' + %(name)s + ']')",
                {"name": PROBE_DATABASE},
            )

    settings = test_settings.model_copy(update={"sqlserver_database": PROBE_DATABASE})
    owner = _open_probe_database(settings)
    with owner:
        with owner.cursor() as cursor:
            cursor.execute(
                """
                IF OBJECT_ID('dbo.pool_probe') IS NOT NULL DROP TABLE dbo.pool_probe;
                CREATE TABLE dbo.pool_probe (
                    probe_id NVARCHAR(64) NOT NULL PRIMARY KEY,
                    session_id INT NOT NULL,
                    written_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
                );
                """
            )

    yield settings

    cleanup = _open_probe_database(settings)
    with cleanup:
        with cleanup.cursor() as cursor:
            cursor.execute("IF OBJECT_ID('dbo.pool_probe') IS NOT NULL DROP TABLE dbo.pool_probe")


class _ServerView:
    """Asks SQL Server which sessions exist, from a connection of its own.

    It targets `master`, so it is never one of the sessions it reports on.
    """

    def __init__(self, settings: Settings) -> None:
        self._connection = _connect_within_deadline(settings, "master")

    def live_sessions(self, session_ids: set[int]) -> set[int]:
        if not session_ids:
            return set()
        placeholders = ",".join("%s" for _ in session_ids)
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"SELECT session_id FROM sys.dm_exec_sessions WHERE session_id IN ({placeholders})",
                tuple(session_ids),
            )
            return {int(row[0]) for row in cursor.fetchall() or []}

    def close(self) -> None:
        self._connection.close()


@pytest.fixture
def server(probe_settings: Settings) -> Iterator[_ServerView]:
    view = _ServerView(probe_settings)
    yield view
    view.close()


def _pool(settings: Settings, **tuning: Any) -> SQLConnectionPool:
    return SQLConnectionPool(
        settings.model_copy(
            update={
                "sqlserver_pool_max_size": tuning.get("max_size", 3),
                "sqlserver_pool_acquire_timeout_seconds": tuning.get("acquire_timeout", 30.0),
                "sqlserver_pool_idle_timeout_seconds": tuning.get("idle_timeout", 300.0),
            }
        ),
        start_reaper=tuning.get("start_reaper", False),
    )


def _spid(connection: Any) -> int:
    """The server's own name for the session this connection is using."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT @@SPID")
        return int(cursor.fetchone()[0])


def _wait_until(predicate: Callable[[], bool], *, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return predicate()


def test_the_server_serves_concurrent_writers_from_at_most_max_size_sessions(
    probe_settings: Settings,
    server: _ServerView,
) -> None:
    """Fifteen concurrent writers, three permitted connections.

    Each writer records the SPID SQL Server assigned it. Fifteen writers that
    between them occupy at most three distinct sessions is the ceiling holding,
    stated by the server rather than by the pool.
    """

    max_size = 3
    workers = 15
    pool = _pool(probe_settings, max_size=max_size)

    start = threading.Barrier(workers)
    lock = threading.Lock()
    spids: set[int] = set()
    held = 0
    peak_held = 0

    def write(index: int) -> None:
        nonlocal held, peak_held
        start.wait(timeout=60.0)
        with pool.transaction() as connection:
            with lock:
                held += 1
                peak_held = max(peak_held, held)
            session_id = _spid(connection)
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO dbo.pool_probe (probe_id, session_id) VALUES (%s, %s)",
                    (f"probe-{index}", session_id),
                )
            with lock:
                spids.add(session_id)
                held -= 1

    try:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            list(executor.map(write, range(workers)))

        assert peak_held == max_size, (
            f"only {peak_held} writers ever held a connection at once; "
            "the ceiling was never actually contended"
        )
        assert len(spids) <= max_size, (
            f"{workers} writers occupied {len(spids)} SQL Server sessions "
            f"({sorted(spids)}); the pool permits {max_size}"
        )

        metrics = pool.metrics()
        assert metrics.created_total <= max_size
        assert metrics.created_total >= len(spids)
        assert metrics.acquired_total == workers
        assert metrics.in_use == 0

        with pool.acquire() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT COUNT(*), COUNT(DISTINCT session_id) FROM dbo.pool_probe")
                written, distinct_sessions = cursor.fetchone()
        assert int(written) == workers, "the pooled write path lost rows"
        assert int(distinct_sessions) == len(spids)

        # Every session the writers used is one the server still has open --
        # the pool is holding them, not reopening one per operation.
        assert server.live_sessions(spids) == spids
    finally:
        pool.close(drain_timeout_seconds=30.0)


def test_a_failed_transaction_rolls_back_and_keeps_its_real_session(
    probe_settings: Settings,
    server: _ServerView,
) -> None:
    pool = _pool(probe_settings, max_size=2)
    try:
        session_ids: set[int] = set()

        with pytest.raises(RuntimeError, match="business rule"):
            with pool.transaction() as connection:
                session_ids.add(_spid(connection))
                with connection.cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO dbo.pool_probe (probe_id, session_id) VALUES (%s, 0)",
                        ("rolled-back",),
                    )
                raise RuntimeError("business rule violated")

        # The write is gone, and the session that made it is not.
        with pool.acquire() as connection:
            session_ids.add(_spid(connection))
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT COUNT(*) FROM dbo.pool_probe WHERE probe_id = %s",
                    ("rolled-back",),
                )
                assert int(cursor.fetchone()[0]) == 0, "a failed transaction was not rolled back"

        assert len(session_ids) == 1, "the failed transaction cost the pool its connection"
        assert server.live_sessions(session_ids) == session_ids

        metrics = pool.metrics()
        assert metrics.created_total == 1
        assert metrics.discarded_total == 0
        assert metrics.idle == 1
        assert metrics.in_use == 0
    finally:
        pool.close(drain_timeout_seconds=30.0)


def test_acquire_times_out_against_a_real_saturated_pool(probe_settings: Settings) -> None:
    pool = _pool(probe_settings, max_size=1, acquire_timeout=0.5)
    holding = threading.Event()
    release = threading.Event()

    def hold() -> None:
        with pool.transaction() as connection:
            _spid(connection)
            holding.set()
            release.wait(timeout=60.0)

    holder = threading.Thread(target=hold, daemon=True)
    holder.start()
    try:
        assert holding.wait(timeout=60.0)
        started = time.monotonic()
        with pytest.raises(SQLConnectionPoolTimeoutError):
            with pool.transaction():
                pytest.fail("a saturated pool handed out a second connection")
        elapsed = time.monotonic() - started
        assert elapsed >= 0.4, f"acquire gave up after {elapsed:.3f}s, before its 0.5s timeout"
        assert pool.metrics().acquire_timeout_total == 1
        assert pool.metrics().created_total == 1
    finally:
        release.set()
        holder.join(timeout=60.0)
        pool.close(drain_timeout_seconds=30.0)


def test_shutdown_closes_every_real_session(
    probe_settings: Settings,
    server: _ServerView,
) -> None:
    pool = _pool(probe_settings, max_size=3)
    spids: set[int] = set()
    lock = threading.Lock()
    barrier = threading.Barrier(3)

    def borrow(_: int) -> None:
        with pool.transaction() as connection:
            session_id = _spid(connection)
            with lock:
                spids.add(session_id)
            barrier.wait(timeout=60.0)

    with ThreadPoolExecutor(max_workers=3) as executor:
        list(executor.map(borrow, range(3)))

    assert len(spids) == 3
    assert server.live_sessions(spids) == spids

    pool.close(drain_timeout_seconds=30.0)

    assert _wait_until(lambda: server.live_sessions(spids) == set()), (
        "shutdown left sessions open on the server"
    )
    assert pool.metrics().size == 0

    with pytest.raises(SQLConnectionPoolClosedError):
        with pool.transaction():
            pytest.fail("a closed pool handed out a connection")


def test_the_reaper_closes_a_real_idle_session(
    probe_settings: Settings,
    server: _ServerView,
) -> None:
    pool = _pool(probe_settings, max_size=2, idle_timeout=1.0, start_reaper=True)
    try:
        with pool.transaction() as connection:
            session_id = _spid(connection)

        assert server.live_sessions({session_id}) == {session_id}

        assert _wait_until(lambda: pool.metrics().reaped_idle_total >= 1), (
            "the reaper never closed an idle connection"
        )
        assert _wait_until(lambda: server.live_sessions({session_id}) == set()), (
            "a reaped connection was still open on the server"
        )
        assert pool.metrics().size == 0
    finally:
        pool.close(drain_timeout_seconds=30.0)


def test_sequential_transactions_reuse_one_real_session(
    probe_settings: Settings,
    server: _ServerView,
) -> None:
    """Reuse is the point of a pool, and a reused connection is where one breaks.

    Ten sequential transactions must all commit, and SQL Server must report the
    same session for all ten -- not ten logins.
    """

    pool = _pool(probe_settings, max_size=4)
    try:
        spids: set[int] = set()
        for index in range(10):
            with pool.transaction() as connection:
                session_id = _spid(connection)
                spids.add(session_id)
                with connection.cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO dbo.pool_probe (probe_id, session_id) VALUES (%s, %s)",
                        (f"reuse-{index}", session_id),
                    )

        assert len(spids) == 1, f"ten sequential transactions used {len(spids)} sessions"
        assert server.live_sessions(spids) == spids

        with pool.acquire() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT COUNT(*) FROM dbo.pool_probe WHERE probe_id LIKE %s",
                    ("reuse-%",),
                )
                assert int(cursor.fetchone()[0]) == 10

        metrics = pool.metrics()
        assert metrics.created_total == 1
        assert metrics.acquired_total == 11
    finally:
        pool.close(drain_timeout_seconds=30.0)
