"""The shipment write path's deadlock retry, proven without needing a race.

`test_return_shipment_concurrency_real_infra.py` asserts the *outcome* against a
real SQL Server: eight simultaneous duplicates all answer, one APPLIED and seven
DUPLICATE. What it cannot do is prove the mechanism, because it cannot make SQL
Server choose a victim on demand -- before the fix the deadlock appeared in
roughly two trials in ten, which is exactly why SHIP-CONC-01 sat in that file as
a tolerated observation rather than an assertion.

This file supplies the half a race cannot. The driver connection is a double
whose lock statement is chosen as a deadlock victim a fixed number of times, so
"the victim is retried", "it is retried a bounded number of times", "the retry
answers DUPLICATE when the winner got there first" and "only a victim is
retried" are each decidable rather than probable.

Only `pymssql` is replaced. The statement text, the bounded pool, the
`asyncio.to_thread` hop and the commit are the production ones, and the test
enters through `record_shipment_update` rather than through the private retry
helper -- a test that called the helper directly would prove the helper works
and say nothing about whether the write path uses it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pymssql
import pytest

from return_platform.configuration.settings import Settings
from return_platform.operations.sql_business_state import (
    _DEADLOCK_MAX_ATTEMPTS,
    SHIPMENT_UPDATE_APPLIED,
    SHIPMENT_UPDATE_DUPLICATE,
    SHIPMENT_UPDATE_STALE,
    ShipmentUpdate,
    SQLBusinessStateRepository,
)
from return_platform.operations.sql_connection_pool import SQLConnectionPool

#: `event_at` is `DATETIME2(3)` and carries no zone, so the route converts to
#: naive UTC before the value ever reaches the driver. These mirror that.
_1400 = datetime(2026, 8, 14, 14, 0, tzinfo=UTC).replace(tzinfo=None)
_1500 = datetime(2026, 8, 14, 15, 0, tzinfo=UTC).replace(tzinfo=None)


def _deadlock() -> pymssql.OperationalError:
    """The error SQL Server hands the side it rolled back.

    Number first, exactly as the driver reports it -- that is what the
    production check matches on, rather than on the message, which is localized
    and carries a process id.
    """
    return pymssql.OperationalError(
        1205, b"Transaction (Process ID 57) was deadlocked on lock resources"
    )


class _ShipmentTable:
    """The rows `record_shipment_update` touches, plus a victim counter.

    Deliberately not a general SQL emulator. It understands exactly the four
    statements that method issues, and raises on anything else, so a change to
    the write path that this double silently mis-models fails loudly here
    instead of passing against a fiction.
    """

    def __init__(self, *, deadlocks: int = 0) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.deadlocks_remaining = deadlocks
        self.deadlocks_served = 0
        #: How many times the operation reached its first statement -- which is
        #: the number of attempts, since the lock is taken before anything else.
        self.attempts = 0
        #: Raised instead of a deadlock, for the "only a victim is retried" case.
        self.failure: BaseException | None = None

    def seed(self, update: ShipmentUpdate, tracking_id: str) -> None:
        """Put the row a winning writer would have committed."""
        self.rows[tracking_id] = {
            "tracking_id": tracking_id,
            "return_reference": update.return_reference,
            "tracking_type": update.tracking_type,
            "tracking_reference": update.tracking_reference,
            "carrier_code": update.carrier_code,
            "tracking_status": update.shipment_status,
            "event_at": update.status_at,
            "shipment_details": update.shipment_details,
            "row_version": 1,
        }


class _FakeCursor:
    def __init__(self, table: _ShipmentTable) -> None:
        self._table = table
        self._result: dict[str, Any] | None = None

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def execute(self, statement: str, parameters: Any = None) -> None:
        table = self._table
        params = tuple(parameters or ())
        text = statement.strip()

        if "UPDLOCK, HOLDLOCK" in text:
            # The first statement of the transaction, and where a lock
            # conversion picks its victim.
            table.attempts += 1
            if table.failure is not None:
                raise table.failure
            if table.deadlocks_remaining > 0:
                table.deadlocks_remaining -= 1
                table.deadlocks_served += 1
                raise _deadlock()
            row = table.rows.get(str(params[0]))
            self._result = None if row is None else {"event_at": row["event_at"]}
            return

        if text.startswith("INSERT INTO dbo.return_tracking"):
            (
                tracking_id,
                return_reference,
                tracking_type,
                tracking_reference,
                carrier_code,
                tracking_status,
                event_at,
                shipment_details,
            ) = params
            table.rows[str(tracking_id)] = {
                "tracking_id": str(tracking_id),
                "return_reference": return_reference,
                "tracking_type": tracking_type,
                "tracking_reference": tracking_reference,
                "carrier_code": carrier_code,
                "tracking_status": tracking_status,
                "event_at": event_at,
                "shipment_details": shipment_details,
                "row_version": 1,
            }
            self._result = None
            return

        if text.startswith("UPDATE dbo.return_tracking"):
            status, carrier, details, event_at, tracking_id, incoming = params
            row = table.rows[str(tracking_id)]
            # The production predicate: strictly newer, decided here rather
            # than by a read this code performed.
            applied = 1 if incoming > row["event_at"] else 0
            if applied:
                row.update(
                    tracking_status=status,
                    carrier_code=carrier,
                    shipment_details=details,
                    event_at=event_at,
                    row_version=int(row["row_version"]) + 1,
                )
            self._result = {"applied": applied}
            return

        if text.startswith("SELECT tracking_status"):
            self._result = dict(table.rows[str(params[0])])
            return

        raise AssertionError(f"the double was asked an unmodelled statement: {text}")

    def fetchone(self) -> Any:
        return self._result

    def fetchall(self) -> list[Any]:
        return []


class _FakeConnection:
    def __init__(self, table: _ShipmentTable) -> None:
        self._table = table
        self.commits = 0
        self.rollbacks = 0

    def cursor(self, as_dict: bool = False) -> _FakeCursor:
        del as_dict
        return _FakeCursor(self._table)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        return None


class _Driver:
    """Hands out connections onto one shared table, and remembers them."""

    def __init__(self, table: _ShipmentTable) -> None:
        self._table = table
        self.connections: list[_FakeConnection] = []

    def __call__(self) -> _FakeConnection:
        connection = _FakeConnection(self._table)
        self.connections.append(connection)
        return connection


@pytest.fixture
def build(test_settings: Settings) -> Any:
    """Build repositories over a real pool and a fake driver, always closed."""
    pools: list[SQLConnectionPool] = []

    def factory(table: _ShipmentTable) -> SQLBusinessStateRepository:
        pool = SQLConnectionPool(
            test_settings.model_copy(
                update={
                    "sqlserver_pool_max_size": 4,
                    "sqlserver_pool_acquire_timeout_seconds": 5.0,
                }
            ),
            connection_factory=_Driver(table),
            start_reaper=False,
        )
        pools.append(pool)
        return SQLBusinessStateRepository(test_settings, pool=pool)

    yield factory

    for pool in pools:
        pool.close(drain_timeout_seconds=1.0)


def _update(status: str = "IN_TRANSIT", at: datetime = _1400) -> ShipmentUpdate:
    return ShipmentUpdate(
        return_reference="RMA-DEADLOCK-1",
        tracking_reference="TRK-DEADLOCK-1",
        shipment_status=status,
        status_at=at,
        tracking_type="PPL",
        carrier_code="UPS",
        shipment_details="Picked up",
    )


def _tracking_id(update: ShipmentUpdate) -> str:
    """The row identity the production method derives, derived the same way."""
    import uuid

    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"return-shipment:{update.return_reference}:{update.tracking_reference}",
        )
    )


@pytest.mark.asyncio
async def test_a_deadlock_victim_is_retried_and_still_records_the_observation(
    build: Any,
) -> None:
    """The victim asks again, and the observation lands.

    Before SHIP-CONC-01 was fixed this raised out to the caller and became an
    HTTP 500 for a request that was entirely well formed.
    """
    table = _ShipmentTable(deadlocks=1)
    repository = build(table)
    update = _update()

    outcome = await repository.record_shipment_update(update)

    assert table.deadlocks_served == 1, "the double never got to be the victim"
    assert table.attempts == 2, f"the victim was not retried exactly once: {table.attempts}"
    assert outcome.outcome == SHIPMENT_UPDATE_APPLIED
    assert outcome.current_status == "IN_TRANSIT"
    assert outcome.current_status_at == _1400
    assert len(table.rows) == 1, "the retry inserted a second row for one shipment"


@pytest.mark.asyncio
async def test_a_victim_whose_winner_already_committed_answers_duplicate(
    build: Any,
) -> None:
    """The production shape of the race, and the contract the route publishes.

    The winner commits the row; the loser is rolled back and asks again, and
    finds the observation already stored. `DUPLICATE` -- a correct outcome of a
    well-formed request -- rather than the 500 a caller could do nothing with.
    """
    table = _ShipmentTable(deadlocks=1)
    repository = build(table)
    update = _update()
    table.seed(update, _tracking_id(update))

    outcome = await repository.record_shipment_update(update)

    assert table.deadlocks_served == 1
    assert outcome.outcome == SHIPMENT_UPDATE_DUPLICATE
    assert len(table.rows) == 1
    assert outcome.current_status_at == _1400


@pytest.mark.asyncio
async def test_the_retry_does_not_let_a_stale_observation_through(
    build: Any,
) -> None:
    """Retrying must not become a second chance to regress the stored truth.

    The re-run re-evaluates the predicate from scratch against whatever is
    stored *now*, so a victim carrying an older observation than the winner's
    is rejected on its retry exactly as it would have been sequentially.
    """
    table = _ShipmentTable(deadlocks=1)
    repository = build(table)
    stale = _update(at=_1400)
    table.seed(_update(status="DELIVERED", at=_1500), _tracking_id(stale))

    outcome = await repository.record_shipment_update(stale)

    assert table.deadlocks_served == 1
    assert outcome.outcome == SHIPMENT_UPDATE_STALE
    assert outcome.current_status == "DELIVERED", "a retry overwrote a newer observation"
    assert outcome.current_status_at == _1500


@pytest.mark.asyncio
async def test_the_deadlock_retry_is_bounded(build: Any) -> None:
    """A wedged path fails fast rather than retrying forever.

    A deadlock resolves the moment the winner commits, so a re-run contends
    with strictly fewer writers than the attempt that lost. Something that
    still cannot get through after the bound is not contending, and converting
    that into an unbounded retry would turn a fast error into a slow one while
    holding a pooled connection for the duration.
    """
    table = _ShipmentTable(deadlocks=_DEADLOCK_MAX_ATTEMPTS + 5)
    repository = build(table)

    with pytest.raises(pymssql.Error) as raised:
        await repository.record_shipment_update(_update())

    assert raised.value.args[0] == 1205, "the surfaced error was not the deadlock"
    assert table.attempts == _DEADLOCK_MAX_ATTEMPTS, (
        f"the retry was not bounded at {_DEADLOCK_MAX_ATTEMPTS}: {table.attempts}"
    )


@pytest.mark.asyncio
async def test_only_a_deadlock_victim_is_retried(build: Any) -> None:
    """The retry is scoped to the one condition that is safe to re-run.

    A unique-constraint violation means the request was wrong -- two RMAs
    claiming one tracking reference, which the concurrency suite asserts
    separately -- and re-running it three more times would delay the same
    refusal rather than resolve anything. Only the victim, whose transaction
    was rolled back whole and which therefore changed nothing, is retried.
    """
    table = _ShipmentTable()
    table.failure = pymssql.IntegrityError(
        2627, b"Violation of UNIQUE KEY constraint 'UQ_return_tracking_reference'"
    )
    repository = build(table)

    with pytest.raises(pymssql.Error) as raised:
        await repository.record_shipment_update(_update())

    assert raised.value.args[0] == 2627
    assert table.attempts == 1, f"a constraint violation was retried: {table.attempts}"
