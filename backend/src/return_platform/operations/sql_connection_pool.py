"""Bounded SQL Server connection pool for the authoritative business write path.

Every other datastore in this platform pools for us: `AsyncMongoClient`,
`neo4j.AsyncDriver` and the Valkey client each hold one internally and are
created once per process in `main.py`'s lifespan. `pymssql` has no equivalent,
so `SQLBusinessStateRepository` called `pymssql.connect` per operation and
`RuntimeResources` had nothing to close but a one-worker probe executor. Nothing
capped how many connections existed at once, and a saturated SQL Server answers
that with `Login failed for user 'sa'` -- a write failure whose real cause is
only visible in the server's own log.

The pool is **synchronous and thread-safe**, not async, because the code it
serves is. `pymssql` is a blocking C extension, so every caller already runs
inside `asyncio.to_thread`; a connection is therefore checked out on the worker
thread that is about to use it, and `threading.Condition` -- not
`asyncio.Semaphore` -- is what bounds it. That also means one pool serves the
API process and its Temporal worker alike without either owning an event loop
the other can see.

Connections are opened and validated *outside* the lock. A `pymssql.connect`
against an unreachable server blocks for `login_timeout`, and holding the pool
lock for that long would convert one slow connect into a stall for every other
caller.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import pymssql

from return_platform.configuration.settings import Settings

logger = logging.getLogger("return_platform.operations.sql_connection_pool")

#: How long a pooled connection may sit idle before it is round-tripped once
#: (`SELECT 1`) on the way out of the pool.
#:
#: A connection the server has since closed -- failover, an idle-session policy,
#: a `KILL`, a container restart -- looks perfectly healthy from the client side
#: until the first statement fails, and by then the caller owns a transaction it
#: cannot complete. Validating unconditionally would add a round trip to every
#: checkout including the hot reuse-immediately case, which is the case a pool
#: exists to make cheap; validating only after a pause targets the connections
#: that actually had time to die.
_VALIDATION_GRACE_SECONDS = 1.0

#: Upper bound on how often the reaper wakes. Derived from the configured idle
#: timeout (half of it, so a connection is closed within ~1.5x its deadline)
#: and clamped so a 1-second idle timeout does not produce a spin loop and a
#: one-hour one does not produce a thread that never checks.
_MIN_REAP_INTERVAL_SECONDS = 0.05
_MAX_REAP_INTERVAL_SECONDS = 30.0


class SQLConnectionPoolError(RuntimeError):
    """A connection could not be obtained from the pool."""


class SQLConnectionPoolTimeoutError(SQLConnectionPoolError):
    """The pool was saturated for longer than the configured acquire timeout."""


class SQLConnectionPoolClosedError(SQLConnectionPoolError):
    """The pool has been shut down and will not hand out further connections."""


@dataclass(frozen=True, slots=True)
class SQLConnectionPoolMetrics:
    """A point-in-time reading of one pool. Every field is a measured value."""

    max_size: int
    size: int
    in_use: int
    idle: int
    waiting: int
    created_total: int
    acquired_total: int
    discarded_total: int
    reaped_idle_total: int
    acquire_timeout_total: int
    closed: bool

    @property
    def available(self) -> int:
        """Connections that could be handed out right now without opening one."""
        return self.idle

    @property
    def saturated(self) -> bool:
        """Every permitted connection is checked out."""
        return self.in_use >= self.max_size

    def as_dict(self) -> dict[str, int | bool]:
        return {
            "max_size": self.max_size,
            "size": self.size,
            "in_use": self.in_use,
            "idle": self.idle,
            "waiting": self.waiting,
            "created_total": self.created_total,
            "acquired_total": self.acquired_total,
            "discarded_total": self.discarded_total,
            "reaped_idle_total": self.reaped_idle_total,
            "acquire_timeout_total": self.acquire_timeout_total,
            "closed": self.closed,
        }


@dataclass(slots=True)
class _PooledConnection:
    """One live driver connection plus the bookkeeping the pool needs."""

    connection: Any
    created_at: float
    idle_since: float
    uses: int = 0


def open_sqlserver_connection(settings: Settings) -> Any:
    """Open one raw connection using the platform's configured SQL Server.

    `login_timeout` and the statement `timeout` are separate concerns and are
    resolved from the two keys that already mean them: reaching the server is
    bounded by `dependency_connect_timeout_seconds` (as
    `configuration/cli/apply_sql_migrations.py` already does), and a statement
    by `operation_timeout_seconds`. Previously both came from
    `operation_timeout_seconds`, which made a login wait as long as a query.

    `autocommit=False` is deliberate and unchanged: every business operation on
    this path writes several statements that must land together.
    """
    return pymssql.connect(
        server=settings.sqlserver_host,
        port=str(settings.sqlserver_port),
        user=settings.sqlserver_user,
        password=settings.sqlserver_password.get_secret_value(),
        database=settings.sqlserver_database,
        login_timeout=max(1, int(settings.dependency_connect_timeout_seconds)),
        timeout=max(1, int(settings.operation_timeout_seconds)),
        autocommit=False,
    )


def _close_quietly(connection: Any) -> None:
    """Close one connection without letting teardown interrupt teardown."""
    try:
        connection.close()
    except Exception as exc:
        logger.warning(
            "sql_connection_close_failed",
            extra={"dependency": "sqlserver", "error_type": type(exc).__name__},
        )


class SQLConnectionPool:
    """A bounded, reaping, thread-safe pool of `pymssql` connections.

    Capacity is enforced by reserving the slot *before* the connection is
    opened, so the count of live connections never exceeds `max_size` even
    while several threads are mid-connect.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        connection_factory: Callable[[], Any] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        start_reaper: bool = True,
        name: str = "sqlserver",
    ) -> None:
        self._max_size = int(settings.sqlserver_pool_max_size)
        self._acquire_timeout = float(settings.sqlserver_pool_acquire_timeout_seconds)
        self._idle_timeout = float(settings.sqlserver_pool_idle_timeout_seconds)
        self._name = name
        self._monotonic = monotonic
        self._connection_factory: Callable[[], Any] = connection_factory or (
            lambda: open_sqlserver_connection(settings)
        )

        self._condition = threading.Condition(threading.Lock())
        self._idle: deque[_PooledConnection] = deque()
        self._size = 0
        self._in_use = 0
        self._waiting = 0
        self._closed = False

        self._created_total = 0
        self._acquired_total = 0
        self._discarded_total = 0
        self._reaped_idle_total = 0
        self._acquire_timeout_total = 0

        self._reap_interval = min(
            _MAX_REAP_INTERVAL_SECONDS,
            max(_MIN_REAP_INTERVAL_SECONDS, self._idle_timeout / 2.0),
        )
        self._stop = threading.Event()
        self._reaper: threading.Thread | None = None
        if start_reaper:
            self._reaper = threading.Thread(
                target=self._reap_loop,
                name=f"sql-pool-reaper-{name}",
                daemon=True,
            )
            self._reaper.start()

    # -- introspection ----------------------------------------------------

    @property
    def max_size(self) -> int:
        return self._max_size

    @property
    def closed(self) -> bool:
        with self._condition:
            return self._closed

    def metrics(self) -> SQLConnectionPoolMetrics:
        """Read every counter under one lock so the snapshot is self-consistent."""
        with self._condition:
            return SQLConnectionPoolMetrics(
                max_size=self._max_size,
                size=self._size,
                in_use=self._in_use,
                idle=len(self._idle),
                waiting=self._waiting,
                created_total=self._created_total,
                acquired_total=self._acquired_total,
                discarded_total=self._discarded_total,
                reaped_idle_total=self._reaped_idle_total,
                acquire_timeout_total=self._acquire_timeout_total,
                closed=self._closed,
            )

    # -- public checkout --------------------------------------------------

    @contextmanager
    def acquire(self, *, timeout_seconds: float | None = None) -> Iterator[Any]:
        """Lend one connection for a read.

        The connection is always rolled back on return. With `autocommit=False`
        even a bare `SELECT` opens a transaction, so a connection returned
        without one would carry an open read transaction into whichever caller
        borrowed it next.
        """
        entry = self._checkout(timeout_seconds)
        try:
            yield entry.connection
        finally:
            self._release_clean(entry)

    @contextmanager
    def transaction(self, *, timeout_seconds: float | None = None) -> Iterator[Any]:
        """Lend one connection for a write, committing on success.

        On any exception the transaction is rolled back and the connection goes
        back to the pool; only a connection whose rollback itself failed is
        discarded, because that is the only case where its state is unknown. A
        failed business operation must never cost the process a connection --
        that is precisely how a bounded pool turns into an outage.
        """
        entry = self._checkout(timeout_seconds)
        try:
            yield entry.connection
        except BaseException:
            self._release_clean(entry)
            raise
        else:
            try:
                entry.connection.commit()
            except BaseException:
                self._release_clean(entry)
                raise
            self._checkin(entry)

    # -- lifecycle --------------------------------------------------------

    def reap_idle(self) -> int:
        """Close idle connections that have outlived the configured idle timeout."""
        expired: list[_PooledConnection] = []
        with self._condition:
            if self._closed:
                return 0
            now = self._monotonic()
            # `_idle` is ordered oldest-first; checkout pops the newest, so the
            # left end is exactly the set that has genuinely gone cold.
            while self._idle and now - self._idle[0].idle_since >= self._idle_timeout:
                entry = self._idle.popleft()
                self._size -= 1
                self._reaped_idle_total += 1
                expired.append(entry)
            if expired:
                self._condition.notify_all()
        for entry in expired:
            _close_quietly(entry.connection)
        if expired:
            logger.info(
                "sql_connection_pool_reaped_idle",
                extra={"dependency": "sqlserver", "pool": self._name, "reaped": len(expired)},
            )
        return len(expired)

    def close(self, *, drain_timeout_seconds: float = 5.0) -> None:
        """Stop handing out connections, wait for borrowers, then close everything.

        Waiters blocked in `_checkout` are woken and fail with
        `SQLConnectionPoolClosedError` rather than waiting out their own
        timeout against a pool that will never serve them.
        """
        with self._condition:
            already_closed = self._closed
            self._closed = True
            self._condition.notify_all()

        self._stop.set()
        reaper = self._reaper
        if reaper is not None and reaper is not threading.current_thread():
            reaper.join(timeout=max(1.0, self._reap_interval + 1.0))

        if already_closed:
            return

        deadline = self._monotonic() + max(0.0, drain_timeout_seconds)
        undrained = 0
        with self._condition:
            while self._in_use > 0:
                remaining = deadline - self._monotonic()
                if remaining <= 0:
                    undrained = self._in_use
                    break
                self._condition.wait(remaining)
            entries = list(self._idle)
            self._idle.clear()
            self._size -= len(entries)

        for entry in entries:
            _close_quietly(entry.connection)

        if undrained:
            logger.warning(
                "sql_connection_pool_drain_timeout",
                extra={"dependency": "sqlserver", "pool": self._name, "in_use": undrained},
            )
        else:
            logger.info(
                "sql_connection_pool_closed",
                extra={"dependency": "sqlserver", "pool": self._name, "closed": len(entries)},
            )

    # -- internals --------------------------------------------------------

    def _reap_loop(self) -> None:
        while not self._stop.wait(self._reap_interval):
            try:
                self.reap_idle()
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception(
                    "sql_connection_pool_reap_failed",
                    extra={"dependency": "sqlserver", "error_type": type(exc).__name__},
                )

    def _checkout(self, timeout_seconds: float | None) -> _PooledConnection:
        timeout = self._acquire_timeout if timeout_seconds is None else float(timeout_seconds)
        deadline = self._monotonic() + max(0.0, timeout)
        while True:
            entry = self._reserve(deadline)
            if entry is None:
                entry = self._open_reserved()
            elif not self._validate(entry):
                # A dead connection frees its slot and the loop tries again
                # against the same deadline -- the caller asked for a working
                # connection, not for one attempt at one.
                self._drop_reserved(entry)
                continue
            with self._condition:
                self._acquired_total += 1
            return entry

    def _reserve(self, deadline: float) -> _PooledConnection | None:
        """Take an idle connection, or reserve a slot to open a new one.

        Returns the borrowed entry, or `None` when the caller now owns a
        reserved-but-unopened slot. `_in_use` and `_size` are already updated
        for both outcomes, which is what keeps capacity exact while a connect
        is in flight outside the lock.
        """
        with self._condition:
            self._waiting += 1
            try:
                while True:
                    if self._closed:
                        raise SQLConnectionPoolClosedError("SQL Server connection pool is closed.")
                    if self._idle:
                        entry = self._idle.pop()
                        self._in_use += 1
                        return entry
                    if self._size < self._max_size:
                        self._size += 1
                        self._in_use += 1
                        return None
                    remaining = deadline - self._monotonic()
                    if remaining <= 0:
                        self._acquire_timeout_total += 1
                        raise SQLConnectionPoolTimeoutError(
                            "Timed out waiting for a SQL Server connection: all "
                            f"{self._max_size} pooled connections are in use."
                        )
                    self._condition.wait(remaining)
            finally:
                self._waiting -= 1

    def _open_reserved(self) -> _PooledConnection:
        try:
            connection = self._connection_factory()
        except BaseException:
            self._free_slot()
            raise
        now = self._monotonic()
        with self._condition:
            self._created_total += 1
        return _PooledConnection(connection=connection, created_at=now, idle_since=now, uses=1)

    def _validate(self, entry: _PooledConnection) -> bool:
        entry.uses += 1
        if self._monotonic() - entry.idle_since < _VALIDATION_GRACE_SECONDS:
            return True
        try:
            with entry.connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchall()
            entry.connection.rollback()
        except Exception as exc:
            logger.info(
                "sql_connection_discarded_on_validation",
                extra={"dependency": "sqlserver", "error_type": type(exc).__name__},
            )
            return False
        return True

    def _free_slot(self) -> None:
        """Give back a reserved slot whose connection never came into existence."""
        with self._condition:
            self._size -= 1
            self._in_use -= 1
            self._condition.notify()

    def _drop_reserved(self, entry: _PooledConnection) -> None:
        """Close a checked-out connection and free its slot."""
        with self._condition:
            self._size -= 1
            self._in_use -= 1
            self._discarded_total += 1
            self._condition.notify()
        _close_quietly(entry.connection)

    def _release_clean(self, entry: _PooledConnection) -> None:
        """Roll back, then return the connection -- or discard it if it will not."""
        try:
            entry.connection.rollback()
        except Exception as exc:
            logger.warning(
                "sql_connection_rollback_failed",
                extra={"dependency": "sqlserver", "error_type": type(exc).__name__},
            )
            self._drop_reserved(entry)
            return
        self._checkin(entry)

    def _checkin(self, entry: _PooledConnection) -> None:
        entry.idle_since = self._monotonic()
        with self._condition:
            self._in_use -= 1
            if self._closed:
                self._size -= 1
                self._condition.notify_all()
                closing = True
            else:
                self._idle.append(entry)
                self._condition.notify()
                closing = False
        if closing:
            _close_quietly(entry.connection)


# -- process-wide registry -----------------------------------------------
#
# `SQLBusinessStateRepository` is constructed per request in `api/seed.py` and
# `api/warehouse_placement.py`, and once per orchestrator in
# `operations/orchestrator.py`. A pool owned by the repository instance would
# therefore be a *new* pool per request, which is the unbounded connection
# count this exists to prevent. One pool per distinct SQL Server identity per
# process is the unit that actually bounds anything.

_REGISTRY_LOCK = threading.Lock()
_POOLS: dict[tuple[object, ...], SQLConnectionPool] = {}


def _pool_key(settings: Settings) -> tuple[object, ...]:
    """Identify the server, credential and tuning a pool was built for.

    The password contributes as a digest rather than as itself: a rotated
    credential must produce a new pool instead of silently reusing connections
    opened with the old one, and a secret does not belong in a registry key.
    """
    digest = hashlib.sha256(settings.sqlserver_password.get_secret_value().encode("utf-8"))
    return (
        settings.sqlserver_host,
        settings.sqlserver_port,
        settings.sqlserver_user,
        settings.sqlserver_database,
        digest.hexdigest(),
        settings.sqlserver_pool_max_size,
        settings.sqlserver_pool_acquire_timeout_seconds,
        settings.sqlserver_pool_idle_timeout_seconds,
        settings.dependency_connect_timeout_seconds,
        settings.operation_timeout_seconds,
    )


def get_sql_connection_pool(settings: Settings) -> SQLConnectionPool:
    """Return the process-wide pool for this SQL Server configuration."""
    key = _pool_key(settings)
    with _REGISTRY_LOCK:
        pool = _POOLS.get(key)
        if pool is not None and not pool.closed:
            return pool
        pool = SQLConnectionPool(settings)
        _POOLS[key] = pool
        return pool


def close_sql_connection_pools(*, drain_timeout_seconds: float = 5.0) -> None:
    """Drain and close every registered pool. Safe to call more than once."""
    with _REGISTRY_LOCK:
        pools = list(_POOLS.values())
        _POOLS.clear()
    for pool in pools:
        pool.close(drain_timeout_seconds=drain_timeout_seconds)


def sql_connection_pool_metrics() -> dict[str, dict[str, int | bool]]:
    """Health reading for every registered pool, keyed by `host:port/database`."""
    with _REGISTRY_LOCK:
        registered = list(_POOLS.items())
    return {f"{key[0]}:{key[1]}/{key[3]}": pool.metrics().as_dict() for key, pool in registered}
