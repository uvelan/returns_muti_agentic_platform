"""RG-09: return shipment updates are idempotent and never regress — real SQL Server.

The gate, stated exactly: submit a duplicate update and nothing changes; submit
DELIVERED at 15:00 and then IN_TRANSIT at 14:00 and the current truth must still
be DELIVERED -- by the canonical rule in `record_shipment_update`, not by an
accident of which write happened to land last.

The last part is why the ordering test below submits the older status *after*
the newer one and then asserts the stored row. A rule that only works when
events arrive in order is not a rule; out-of-order arrival is the case it exists
for.

Real infrastructure. Not skipped when SQL Server is absent.
"""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import UTC, datetime
from importlib.resources import files
from typing import Any

import pymssql
import pytest

from return_platform.configuration.settings import Settings
from return_platform.operations.sql_business_state import (
    SHIPMENT_UPDATE_APPLIED,
    SHIPMENT_UPDATE_DUPLICATE,
    SHIPMENT_UPDATE_STALE,
    ShipmentUpdate,
    SQLBusinessStateRepository,
)

_CONNECT_DEADLINE_SECONDS = 30
SHIPMENT_DATABASE = "return_shipment_probe"

#: `dbo.return_tracking` is created by 002 and extended by 006. Both are applied
#: here, in order, so this suite proves the migration chain rather than a
#: hand-copied table definition.
_MIGRATIONS = (
    "001_return_business_state.sql",
    "002_domain_models.sql",
    "006_return_shipment_state.sql",
)


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
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="shipment-probe-connect")
    future = executor.submit(_connect, settings, database)
    try:
        return future.result(timeout=_CONNECT_DEADLINE_SECONDS)
    except FutureTimeoutError:
        raise RuntimeError(
            f"SQL Server at {settings.sqlserver_host}:{settings.sqlserver_port} did not "
            f"complete login within {_CONNECT_DEADLINE_SECONDS}s."
        ) from None
    finally:
        executor.shutdown(wait=False)


def _open_with_retry(settings: Settings, database: str) -> Any:
    deadline = time.monotonic() + _CONNECT_DEADLINE_SECONDS
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return _connect_within_deadline(settings, database)
        except pymssql.Error as exc:
            last = exc
            time.sleep(0.5)
    raise RuntimeError(f"{database} did not become connectable: {last}")


def _batches(migration: str) -> tuple[str, ...]:
    """The migration's own SQL, with its `USE` stripped by line.

    By line and not by batch: these files open with a comment block, so `USE`
    shares batch one with it and a batch-level filter silently lets it through
    -- which points every statement at the application's own database.
    """
    text = (
        files("return_platform")
        .joinpath("configuration/sql_migrations")
        .joinpath(migration)
        .read_text(encoding="utf-8")
    )
    without_use = re.sub(
        r"^\s*USE\s+\[?[A-Za-z0-9_]+\]?\s*;?\s*$", "", text, flags=re.IGNORECASE | re.MULTILINE
    )
    return tuple(
        batch.strip()
        for batch in re.split(r"^\s*GO\s*$", without_use, flags=re.IGNORECASE | re.MULTILINE)
        if batch.strip()
    )


@pytest.fixture
def shipment_settings(test_settings: Settings) -> Iterator[Settings]:
    admin = _connect_within_deadline(test_settings, "master")
    with admin:
        with admin.cursor() as cursor:
            cursor.execute(
                "IF DB_ID(%(name)s) IS NULL EXEC('CREATE DATABASE [' + %(name)s + ']')",
                {"name": SHIPMENT_DATABASE},
            )

    settings = test_settings.model_copy(update={"sqlserver_database": SHIPMENT_DATABASE})
    owner = _open_with_retry(settings, SHIPMENT_DATABASE)
    with owner:
        with owner.cursor() as cursor:
            for migration in _MIGRATIONS:
                for batch in _batches(migration):
                    cursor.execute(batch)

    yield settings

    cleanup = _open_with_retry(settings, SHIPMENT_DATABASE)
    with cleanup:
        with cleanup.cursor() as cursor:
            cursor.execute("DELETE FROM dbo.return_tracking")


@pytest.fixture
def repository(shipment_settings: Settings) -> Iterator[SQLBusinessStateRepository]:
    from return_platform.operations.sql_connection_pool import close_sql_connection_pools

    yield SQLBusinessStateRepository(shipment_settings)
    close_sql_connection_pools(drain_timeout_seconds=10.0)


def _rma() -> str:
    return f"RMA-{uuid.uuid4().hex[:12]}"


def _update(
    rma: str,
    status: str,
    at: datetime,
    *,
    tracking: str | None = None,
    details: str | None = None,
) -> ShipmentUpdate:
    return ShipmentUpdate(
        return_reference=rma,
        tracking_reference=tracking or f"TRK-{rma}",
        shipment_status=status,
        status_at=at,
        carrier_code="UPS",
        shipment_details=details,
    )


_1400 = datetime(2026, 8, 14, 14, 0, tzinfo=UTC).replace(tzinfo=None)
_1500 = datetime(2026, 8, 14, 15, 0, tzinfo=UTC).replace(tzinfo=None)
_1600 = datetime(2026, 8, 14, 16, 0, tzinfo=UTC).replace(tzinfo=None)


@pytest.mark.asyncio
async def test_a_first_shipment_update_creates_the_rma_scoped_state(
    repository: SQLBusinessStateRepository,
) -> None:
    rma = _rma()
    outcome = await repository.record_shipment_update(
        _update(rma, "IN_TRANSIT", _1400, details="Picked up")
    )
    assert outcome.outcome == SHIPMENT_UPDATE_APPLIED
    assert outcome.applied is True
    assert outcome.current_status == "IN_TRANSIT"

    state = await repository.read_shipment_state(rma)
    assert len(state) == 1
    assert state[0]["tracking_status"] == "IN_TRANSIT"
    assert state[0]["carrier_code"] == "UPS"
    assert state[0]["shipment_details"] == "Picked up"
    assert state[0]["event_at"] == _1400


@pytest.mark.asyncio
async def test_a_duplicate_update_is_idempotent(
    repository: SQLBusinessStateRepository,
) -> None:
    """RG-09, first half. The same observation twice is not two observations."""

    rma = _rma()
    first = await repository.record_shipment_update(_update(rma, "IN_TRANSIT", _1400))
    assert first.outcome == SHIPMENT_UPDATE_APPLIED

    for _ in range(3):
        repeat = await repository.record_shipment_update(_update(rma, "IN_TRANSIT", _1400))
        assert repeat.outcome == SHIPMENT_UPDATE_DUPLICATE
        assert repeat.applied is False
        assert repeat.row_version == first.row_version, "a duplicate advanced the row version"

    state = await repository.read_shipment_state(rma)
    assert len(state) == 1, "a duplicate update created a second shipment row"
    assert state[0]["row_version"] == first.row_version


@pytest.mark.asyncio
async def test_an_older_status_never_regresses_the_current_truth(
    repository: SQLBusinessStateRepository,
) -> None:
    """RG-09, second half: DELIVERED @15:00, then IN_TRANSIT @14:00.

    The older event is submitted last, on purpose. If the rule were "last write
    wins" this would end IN_TRANSIT, and a delivered return would read as still
    on the road.
    """

    rma = _rma()
    delivered = await repository.record_shipment_update(_update(rma, "DELIVERED", _1500))
    assert delivered.outcome == SHIPMENT_UPDATE_APPLIED

    regressive = await repository.record_shipment_update(_update(rma, "IN_TRANSIT", _1400))
    assert regressive.outcome == SHIPMENT_UPDATE_STALE
    assert regressive.applied is False
    # The outcome reports the truth that stands, not the update that was refused.
    assert regressive.current_status == "DELIVERED"
    assert regressive.current_status_at == _1500

    state = await repository.read_shipment_state(rma)
    assert len(state) == 1
    assert state[0]["tracking_status"] == "DELIVERED"
    assert state[0]["event_at"] == _1500
    assert state[0]["row_version"] == delivered.row_version, "a stale update touched the row"


@pytest.mark.asyncio
async def test_a_newer_status_still_advances_after_a_stale_one_was_rejected(
    repository: SQLBusinessStateRepository,
) -> None:
    """Rejecting stale must not wedge the shipment against genuine progress."""

    rma = _rma()
    await repository.record_shipment_update(_update(rma, "DELIVERED", _1500))
    await repository.record_shipment_update(_update(rma, "IN_TRANSIT", _1400))

    later = await repository.record_shipment_update(
        _update(rma, "RECEIVED_AT_WAREHOUSE", _1600, details="Booked in")
    )
    assert later.outcome == SHIPMENT_UPDATE_APPLIED
    assert later.current_status == "RECEIVED_AT_WAREHOUSE"

    state = await repository.read_shipment_state(rma)
    assert state[0]["tracking_status"] == "RECEIVED_AT_WAREHOUSE"
    assert state[0]["event_at"] == _1600
    assert state[0]["shipment_details"] == "Booked in"


@pytest.mark.asyncio
async def test_shipment_state_is_scoped_to_its_own_rma(
    repository: SQLBusinessStateRepository,
) -> None:
    """Contract C4 is RMA-scoped. One RMA's shipment cannot answer for another's."""

    first, second = _rma(), _rma()
    await repository.record_shipment_update(_update(first, "DELIVERED", _1500))
    await repository.record_shipment_update(_update(second, "IN_TRANSIT", _1400))

    first_state = await repository.read_shipment_state(first)
    second_state = await repository.read_shipment_state(second)

    assert [row["tracking_status"] for row in first_state] == ["DELIVERED"]
    assert [row["tracking_status"] for row in second_state] == ["IN_TRANSIT"]
    assert first_state[0]["tracking_reference"] != second_state[0]["tracking_reference"]

    # And a stale update on one leaves the other entirely alone.
    await repository.record_shipment_update(_update(first, "IN_TRANSIT", _1400))
    assert (await repository.read_shipment_state(second))[0]["tracking_status"] == "IN_TRANSIT"
    assert (await repository.read_shipment_state(first))[0]["tracking_status"] == "DELIVERED"


@pytest.mark.asyncio
async def test_one_rma_can_carry_several_shipments(
    repository: SQLBusinessStateRepository,
) -> None:
    """A split return is two tracking numbers on one RMA, each with its own state."""

    rma = _rma()
    await repository.record_shipment_update(
        _update(rma, "DELIVERED", _1500, tracking=f"TRK-{rma}-A")
    )
    await repository.record_shipment_update(
        _update(rma, "IN_TRANSIT", _1400, tracking=f"TRK-{rma}-B")
    )

    state = await repository.read_shipment_state(rma)
    assert len(state) == 2
    # Newest status first.
    assert [row["tracking_status"] for row in state] == ["DELIVERED", "IN_TRANSIT"]

    # Advancing one does not disturb the other.
    await repository.record_shipment_update(
        _update(rma, "DELIVERED", _1600, tracking=f"TRK-{rma}-B")
    )
    by_tracking = {
        str(row["tracking_reference"]): row for row in await repository.read_shipment_state(rma)
    }
    assert by_tracking[f"TRK-{rma}-A"]["event_at"] == _1500
    assert by_tracking[f"TRK-{rma}-B"]["tracking_status"] == "DELIVERED"
