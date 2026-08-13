"""The bounded SQL Server pool, exercised with real threads.

Nothing here asserts a constant back at itself. Capacity is proven by counting
how many connections are *simultaneously* open while more threads than that ask
for one at the same time; the acquire timeout is proven by measuring that a
blocked caller gives up rather than waiting forever; drain is proven by a
borrower that is still working when shutdown starts.

The connection object is a local double, deliberately. What is under test is the
pool's own arithmetic -- capacity, hand-back, reaping, drain, counters -- which
is the pool's behaviour and not `pymssql`'s. That the same code works against a
real SQL Server is proven separately, and against a real server, in
`test_sql_connection_pool_real_infra.py`.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import SecretStr

from return_platform.configuration.settings import Settings
from return_platform.operations.models import ReturnSessionView
from return_platform.operations.sql_business_state import SQLBusinessStateRepository
from return_platform.operations.sql_connection_pool import (
    SQLConnectionPool,
    SQLConnectionPoolClosedError,
    SQLConnectionPoolTimeoutError,
    close_sql_connection_pools,
    get_sql_connection_pool,
    sql_connection_pool_metrics,
)


class _FakeCursor:
    def __init__(self, connection: _FakeConnection) -> None:
        self._connection = connection
        self.rowcount = 1

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def execute(self, statement: str, parameters: Any = None) -> None:
        if self._connection.dead:
            raise RuntimeError("connection was closed by the server")
        self._connection.statements.append(statement)

    def executemany(self, statement: str, rows: Any) -> None:
        self.execute(statement, rows)

    def fetchone(self) -> Any:
        return None

    def fetchall(self) -> list[Any]:
        return []


class _FakeConnection:
    """A connection double that tracks whether it is open, and counts commits."""

    def __init__(self, tracker: _ConnectionTracker) -> None:
        self._tracker = tracker
        self.closed = False
        self.dead = False
        self.commits = 0
        self.rollbacks = 0
        self.commit_fails = False
        self.rollback_fails = False
        self.statements: list[str] = []

    def cursor(self, as_dict: bool = False) -> _FakeCursor:
        del as_dict
        return _FakeCursor(self)

    def commit(self) -> None:
        if self.commit_fails:
            raise RuntimeError("commit refused")
        self.commits += 1

    def rollback(self) -> None:
        if self.rollback_fails:
            raise RuntimeError("rollback refused")
        self.rollbacks += 1

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self._tracker.record_close()


class _ConnectionTracker:
    """Counts connections that are open *right now*, and the high-water mark.

    This is the number the ceiling is actually about: not how many were created
    over the run, but how many existed at the same instant.
    """

    def __init__(self, *, connect_delay: float = 0.0) -> None:
        self._lock = threading.Lock()
        self.connect_delay = connect_delay
        self.live = 0
        self.peak_live = 0
        self.created = 0
        self.connections: list[_FakeConnection] = []
        self.fail_next = 0

    def __call__(self) -> _FakeConnection:
        with self._lock:
            if self.fail_next > 0:
                self.fail_next -= 1
                raise RuntimeError("SQL Server refused the login")
        if self.connect_delay:
            # A real connect is not instantaneous, and the window it opens is
            # exactly where an off-by-one in slot reservation would show up.
            time.sleep(self.connect_delay)
        connection = _FakeConnection(self)
        with self._lock:
            self.live += 1
            self.created += 1
            self.peak_live = max(self.peak_live, self.live)
            self.connections.append(connection)
        return connection

    def record_close(self) -> None:
        with self._lock:
            self.live -= 1


class _Clock:
    """A monotonic clock the test advances by hand."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.now = 1_000.0

    def __call__(self) -> float:
        with self._lock:
            return self.now

    def advance(self, seconds: float) -> None:
        with self._lock:
            self.now += seconds


def _pool_settings(
    base: Settings,
    *,
    max_size: int = 4,
    acquire_timeout: float = 5.0,
    idle_timeout: float = 300.0,
) -> Settings:
    return base.model_copy(
        update={
            "sqlserver_pool_max_size": max_size,
            "sqlserver_pool_acquire_timeout_seconds": acquire_timeout,
            "sqlserver_pool_idle_timeout_seconds": idle_timeout,
        }
    )


@pytest.fixture
def tracker() -> _ConnectionTracker:
    return _ConnectionTracker()


@pytest.fixture
def make_pool(test_settings: Settings) -> Iterator[Any]:
    """Build pools that are always closed again, whatever the test does."""

    built: list[SQLConnectionPool] = []

    def factory(
        *,
        tracker: _ConnectionTracker,
        max_size: int = 4,
        acquire_timeout: float = 5.0,
        idle_timeout: float = 300.0,
        monotonic: Any = time.monotonic,
        start_reaper: bool = False,
    ) -> SQLConnectionPool:
        pool = SQLConnectionPool(
            _pool_settings(
                test_settings,
                max_size=max_size,
                acquire_timeout=acquire_timeout,
                idle_timeout=idle_timeout,
            ),
            connection_factory=tracker,
            monotonic=monotonic,
            start_reaper=start_reaper,
        )
        built.append(pool)
        return pool

    yield factory

    for pool in built:
        pool.close(drain_timeout_seconds=2.0)


def test_capacity_is_never_exceeded_under_concurrent_acquisition(
    make_pool: Any,
    tracker: _ConnectionTracker,
) -> None:
    """Twenty-four threads, four permitted connections, one barrier.

    The barrier makes the contention real: every worker arrives before any
    worker is allowed to proceed, so the pool is asked for twenty-four
    connections in the same instant rather than twenty-four times in sequence.
    """

    max_size = 4
    workers = 24
    tracker.connect_delay = 0.01
    pool = make_pool(tracker=tracker, max_size=max_size, acquire_timeout=10.0)

    start = threading.Barrier(workers)
    held_lock = threading.Lock()
    held = 0
    peak_held = 0

    def borrow(_: int) -> None:
        nonlocal held, peak_held
        start.wait(timeout=10.0)
        with pool.transaction() as connection:
            with held_lock:
                held += 1
                peak_held = max(peak_held, held)
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            time.sleep(0.005)
            with held_lock:
                held -= 1

    with ThreadPoolExecutor(max_workers=workers) as executor:
        list(executor.map(borrow, range(workers)))

    assert peak_held == max_size, "contention did not actually saturate the pool"
    assert tracker.peak_live <= max_size
    assert tracker.created <= max_size

    metrics = pool.metrics()
    assert metrics.acquired_total == workers
    assert metrics.created_total <= max_size
    assert metrics.size <= max_size
    assert metrics.in_use == 0


def test_acquire_timeout_fires_rather_than_hanging(
    make_pool: Any,
    tracker: _ConnectionTracker,
) -> None:
    pool = make_pool(tracker=tracker, max_size=1, acquire_timeout=0.2)
    release = threading.Event()
    holding = threading.Event()

    def hold() -> None:
        with pool.transaction():
            holding.set()
            release.wait(timeout=10.0)

    holder = threading.Thread(target=hold, daemon=True)
    holder.start()
    assert holding.wait(timeout=5.0)

    started = time.monotonic()
    with pytest.raises(SQLConnectionPoolTimeoutError):
        with pool.transaction():
            pytest.fail("a saturated pool must not hand out a second connection")
    elapsed = time.monotonic() - started

    assert 0.15 <= elapsed < 5.0, f"acquire waited {elapsed:.3f}s, not its 0.2s timeout"
    assert pool.metrics().acquire_timeout_total == 1

    release.set()
    holder.join(timeout=5.0)
    # The pool is usable again the moment the borrower hands its connection back.
    with pool.transaction():
        pass
    assert tracker.created == 1


def test_a_failed_transaction_returns_its_connection_to_the_pool(
    make_pool: Any,
    tracker: _ConnectionTracker,
) -> None:
    """The property that decides whether a bounded pool survives an outage.

    If a failing operation cost the process a connection, a repeated business
    error would drain the pool to zero and the failure would stop being about
    the operation.
    """

    pool = make_pool(tracker=tracker, max_size=2)

    for _ in range(5):
        with pytest.raises(RuntimeError, match="business rule"):
            with pool.transaction() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("UPDATE dbo.return_requests SET return_status=%s")
                raise RuntimeError("business rule violated")

    assert tracker.created == 1, "a failed transaction leaked its connection"
    assert tracker.live == 1
    connection = tracker.connections[0]
    assert connection.commits == 0, "a failed transaction must not commit"
    assert connection.rollbacks == 5

    metrics = pool.metrics()
    assert metrics.in_use == 0
    assert metrics.idle == 1
    assert metrics.size == 1
    assert metrics.discarded_total == 0


def test_a_failed_commit_returns_its_connection_to_the_pool(
    make_pool: Any,
    tracker: _ConnectionTracker,
) -> None:
    pool = make_pool(tracker=tracker, max_size=2)

    with pool.transaction():
        pass
    pooled = tracker.connections[0]
    assert pooled.commits == 1

    pooled.commit_fails = True
    with pytest.raises(RuntimeError, match="commit refused"):
        with pool.transaction():
            pass

    assert pooled.rollbacks == 1, "a connection whose commit failed was not rolled back"
    assert pooled.closed is False
    metrics = pool.metrics()
    assert metrics.idle == 1
    assert metrics.in_use == 0
    assert tracker.created == 1


def test_a_connection_that_will_not_roll_back_is_discarded_not_pooled(
    make_pool: Any,
    tracker: _ConnectionTracker,
) -> None:
    """Rollback failure is the one case where the connection's state is unknown."""

    pool = make_pool(tracker=tracker, max_size=2)

    with pytest.raises(RuntimeError, match="business rule"):
        with pool.transaction() as connection:
            connection.rollback_fails = True
            raise RuntimeError("business rule violated")

    assert tracker.connections[0].closed is True
    metrics = pool.metrics()
    assert metrics.discarded_total == 1
    assert metrics.size == 0
    assert metrics.idle == 0
    assert metrics.in_use == 0

    # And the freed slot is genuinely reusable.
    with pool.transaction():
        pass
    assert tracker.created == 2


def test_a_dead_pooled_connection_is_replaced_rather_than_handed_out(
    make_pool: Any,
    tracker: _ConnectionTracker,
) -> None:
    clock = _Clock()
    pool = make_pool(tracker=tracker, max_size=2, monotonic=clock)

    with pool.transaction():
        pass
    pooled = tracker.connections[0]

    # The server drops the session while the connection sits idle.
    pooled.dead = True
    clock.advance(30.0)

    with pool.transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")

    assert connection is not pooled
    assert pooled.closed is True
    assert tracker.created == 2
    assert pool.metrics().discarded_total == 1
    assert pool.metrics().size == 1


def test_a_failed_connect_frees_the_slot_it_reserved(
    make_pool: Any,
    tracker: _ConnectionTracker,
) -> None:
    pool = make_pool(tracker=tracker, max_size=1, acquire_timeout=0.5)
    tracker.fail_next = 1

    with pytest.raises(RuntimeError, match="refused the login"):
        with pool.transaction():
            pytest.fail("no connection should have been produced")

    assert pool.metrics().size == 0
    assert pool.metrics().in_use == 0

    # A reserved-but-never-opened slot that was not returned would make the
    # pool permanently one connection smaller -- here, permanently unusable.
    with pool.transaction():
        pass
    assert tracker.created == 1


def test_idle_connections_are_reaped(
    make_pool: Any,
    tracker: _ConnectionTracker,
) -> None:
    clock = _Clock()
    pool = make_pool(tracker=tracker, max_size=3, idle_timeout=60.0, monotonic=clock)

    with ThreadPoolExecutor(max_workers=3) as executor:
        barrier = threading.Barrier(3)

        def borrow(_: int) -> None:
            with pool.transaction():
                barrier.wait(timeout=10.0)

        list(executor.map(borrow, range(3)))

    assert pool.metrics().idle == 3
    assert tracker.live == 3

    clock.advance(30.0)
    assert pool.reap_idle() == 0, "a connection under its idle timeout was reaped"

    clock.advance(31.0)
    assert pool.reap_idle() == 3

    assert tracker.live == 0
    assert all(connection.closed for connection in tracker.connections)
    metrics = pool.metrics()
    assert metrics.reaped_idle_total == 3
    assert metrics.size == 0
    assert metrics.idle == 0


def test_the_background_reaper_closes_idle_connections_without_being_asked(
    test_settings: Settings,
    tracker: _ConnectionTracker,
) -> None:
    """The reaper thread itself, on the real clock.

    Reaping that only happens when someone calls `reap_idle()` is not idle
    cleanup -- an idle process is precisely the case where nobody calls it.
    """

    pool = SQLConnectionPool(
        _pool_settings(test_settings, max_size=2, idle_timeout=1.0),
        connection_factory=tracker,
        start_reaper=True,
    )
    try:
        with pool.transaction():
            pass
        assert pool.metrics().idle == 1

        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and pool.metrics().reaped_idle_total == 0:
            time.sleep(0.05)

        metrics = pool.metrics()
        assert metrics.reaped_idle_total == 1, "the reaper thread never ran"
        assert metrics.idle == 0
        assert metrics.size == 0
        assert tracker.live == 0
    finally:
        pool.close(drain_timeout_seconds=2.0)


def test_graceful_shutdown_drains_in_flight_borrowers(
    make_pool: Any,
    tracker: _ConnectionTracker,
) -> None:
    pool = make_pool(tracker=tracker, max_size=2)
    holding = threading.Event()
    finished = threading.Event()

    def hold() -> None:
        with pool.transaction() as connection:
            holding.set()
            time.sleep(0.3)
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
        finished.set()

    holder = threading.Thread(target=hold, daemon=True)
    holder.start()
    assert holding.wait(timeout=5.0)

    pool.close(drain_timeout_seconds=5.0)

    assert finished.is_set(), "close() returned while a borrower was still writing"
    assert tracker.live == 0, "shutdown left a connection open"
    assert all(connection.closed for connection in tracker.connections)
    assert pool.closed is True

    with pytest.raises(SQLConnectionPoolClosedError):
        with pool.transaction():
            pytest.fail("a closed pool must not hand out connections")

    holder.join(timeout=5.0)


def test_shutdown_wakes_a_blocked_waiter_instead_of_letting_it_wait_out(
    make_pool: Any,
    tracker: _ConnectionTracker,
) -> None:
    pool = make_pool(tracker=tracker, max_size=1, acquire_timeout=30.0)
    holding = threading.Event()
    release = threading.Event()
    outcome: list[BaseException | None] = []

    def hold() -> None:
        with pool.transaction():
            holding.set()
            release.wait(timeout=10.0)

    def wait_for_a_connection() -> None:
        try:
            with pool.transaction():
                outcome.append(None)
        except BaseException as exc:
            outcome.append(exc)

    holder = threading.Thread(target=hold, daemon=True)
    holder.start()
    assert holding.wait(timeout=5.0)

    waiter = threading.Thread(target=wait_for_a_connection, daemon=True)
    waiter.start()

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and pool.metrics().waiting == 0:
        time.sleep(0.01)
    assert pool.metrics().waiting == 1, "the second caller never blocked"

    started = time.monotonic()
    closer = threading.Thread(
        target=lambda: pool.close(drain_timeout_seconds=5.0),
        daemon=True,
    )
    closer.start()

    waiter.join(timeout=5.0)
    elapsed = time.monotonic() - started
    assert outcome and isinstance(outcome[0], SQLConnectionPoolClosedError)
    assert elapsed < 5.0, "the waiter sat out its own 30s timeout instead of being woken"

    release.set()
    holder.join(timeout=5.0)
    closer.join(timeout=10.0)


def test_metrics_report_measured_values_not_configuration(
    make_pool: Any,
    tracker: _ConnectionTracker,
) -> None:
    pool = make_pool(tracker=tracker, max_size=2, acquire_timeout=0.1)

    empty = pool.metrics()
    assert (empty.size, empty.in_use, empty.idle, empty.waiting) == (0, 0, 0, 0)
    assert (empty.created_total, empty.acquired_total) == (0, 0)
    assert empty.closed is False

    holding = threading.Event()
    release = threading.Event()

    def hold() -> None:
        with pool.transaction():
            holding.set()
            release.wait(timeout=10.0)

    holders = [threading.Thread(target=hold, daemon=True) for _ in range(2)]
    for holder in holders:
        holder.start()
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and pool.metrics().in_use < 2:
        time.sleep(0.01)

    saturated = pool.metrics()
    assert saturated.in_use == 2
    assert saturated.size == 2
    assert saturated.idle == 0
    assert saturated.created_total == 2
    assert saturated.saturated is True
    assert saturated.available == 0

    with pytest.raises(SQLConnectionPoolTimeoutError):
        with pool.transaction():
            pytest.fail("unreachable")
    assert pool.metrics().acquire_timeout_total == 1

    release.set()
    for holder in holders:
        holder.join(timeout=5.0)

    drained = pool.metrics()
    assert drained.in_use == 0
    assert drained.idle == 2
    assert drained.acquired_total == 2
    assert drained.as_dict()["idle"] == 2

    pool.close(drain_timeout_seconds=2.0)
    assert pool.metrics().closed is True
    assert pool.metrics().size == 0


# -- production wiring ---------------------------------------------------


def _session() -> ReturnSessionView:
    now = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
    return ReturnSessionView(
        id="ret-pool-1",
        correlationId="corr-pool-1",
        customerReference="CUST-1",
        orderReference="ORD-1",
        itemReferences=["LINE-1"],
        productReferences=["SKU-1"],
        reasonCode="DAMAGED",
        returnQuantity=1,
        packageCount=1,
        shippingPathExpectation="BRANCH_UPS",
        channel="ASSOCIATE",
        status="QUEUED",
        currentStage="INTAKE",
        progressPercentage=0,
        version=0,
        createdAt=now,
        updatedAt=now,
    )


def test_the_repository_shares_one_process_wide_pool(test_settings: Settings) -> None:
    """`SQLBusinessStateRepository` is constructed per request.

    `api/seed.py` and `api/warehouse_placement.py` both build one inside the
    request handler. A pool owned by the instance would therefore be a new pool
    per request, which is the unbounded connection count this replaced -- so
    what has to hold is that separately-constructed repositories resolve the
    *same* pool.
    """

    settings = _pool_settings(test_settings, max_size=3)
    close_sql_connection_pools(drain_timeout_seconds=1.0)
    try:
        first = SQLBusinessStateRepository(settings)
        second = SQLBusinessStateRepository(settings)

        pool = first._pool()
        assert pool is second._pool()
        assert pool is get_sql_connection_pool(settings)
        assert pool.max_size == 3

        reading = sql_connection_pool_metrics()
        key = f"{settings.sqlserver_host}:{settings.sqlserver_port}/{settings.sqlserver_database}"
        assert reading[key]["max_size"] == 3

        # A rotated credential must not keep reusing connections opened with
        # the old one.
        rotated = settings.model_copy(update={"sqlserver_password": SecretStr("rotated-secret")})
        assert get_sql_connection_pool(rotated) is not pool
    finally:
        close_sql_connection_pools(drain_timeout_seconds=1.0)

    assert sql_connection_pool_metrics() == {}


def test_a_closed_registry_pool_is_replaced_on_the_next_lookup(
    test_settings: Settings,
) -> None:
    settings = _pool_settings(test_settings, max_size=2)
    close_sql_connection_pools(drain_timeout_seconds=1.0)
    try:
        first = get_sql_connection_pool(settings)
        first.close(drain_timeout_seconds=1.0)

        replacement = get_sql_connection_pool(settings)
        assert replacement is not first
        assert replacement.closed is False
    finally:
        close_sql_connection_pools(drain_timeout_seconds=1.0)


@pytest.mark.asyncio
async def test_concurrent_business_writes_never_exceed_the_pool_ceiling(
    test_settings: Settings,
    tracker: _ConnectionTracker,
) -> None:
    """Sixteen concurrent `record_return_decision` calls, four connections.

    This enters through the production repository method, not through the pool
    API: the statement text, the `asyncio.to_thread` hop and the commit are all
    the real ones. Only the driver connection is a double.
    """

    tracker.connect_delay = 0.01
    pool = SQLConnectionPool(
        _pool_settings(test_settings, max_size=4, acquire_timeout=10.0),
        connection_factory=tracker,
        start_reaper=False,
    )
    repository = SQLBusinessStateRepository(test_settings, pool=pool)
    try:
        session = _session()
        await asyncio.gather(
            *(
                repository.record_return_decision(
                    session,
                    decision="APPROVE",
                    return_reference=f"RMA-{index}",
                    status="APPROVED",
                )
                for index in range(16)
            )
        )

        assert tracker.peak_live <= 4, "the write path opened more connections than permitted"
        assert tracker.created <= 4
        assert tracker.created < 16, "every write opened its own connection -- nothing was pooled"
        assert sum(connection.commits for connection in tracker.connections) == 16
        statements = [
            statement for connection in tracker.connections for statement in connection.statements
        ]
        assert len(statements) == 16
        assert all("dbo.return_requests" in statement for statement in statements)

        metrics = pool.metrics()
        assert metrics.acquired_total == 16
        assert metrics.in_use == 0
        assert metrics.idle == metrics.size
    finally:
        pool.close(drain_timeout_seconds=2.0)

    assert tracker.live == 0
