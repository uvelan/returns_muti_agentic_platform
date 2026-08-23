"""The §29 matrix's last row: duplicate shipment update, genuinely concurrent.

`test_return_shipment_state_real_infra.py` already proves the *sequential*
rules -- a repeated update answers DUPLICATE, an older one answers STALE. Those
are the rules; this file asks whether they survive being applied at the same
instant, which is the arrival pattern a carrier feed actually produces when it
is replayed or when two consumers share a subscription.

The distinction is not pedantic. A read-then-write implementation satisfies
every sequential test and loses under a race: two callers both read "no row for
this RMA", both decide they are the first, and both insert. Contract C4 says
shipment state is idempotent and stale-update safe, and only a contended test
can tell whether it is idempotent *because of* the write's own predicate or
merely because nothing has yet arrived twice at once.

Its own probe database, so a concurrent run of the sequential suite cannot see
these rows or delete them mid-assertion.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import UTC, datetime
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
from tests.sql_migrations import migration_batches

_CONNECT_DEADLINE_SECONDS = 30
SHIPMENT_DATABASE = "return_shipment_concurrency_probe"

#: `dbo.return_tracking` is created by 002 and extended by 006, so the chain is
#: applied here rather than a hand-copied table definition -- the same choice
#: the sequential suite makes, and for the same reason.
CONTENDERS = 8


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
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="shipment-conc-connect")
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
            for batch in migration_batches():
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
        tracking_type="PPL",
        carrier_code="UPS",
        shipment_details=details,
    )


_1400 = datetime(2026, 8, 14, 14, 0, tzinfo=UTC).replace(tzinfo=None)
_1500 = datetime(2026, 8, 14, 15, 0, tzinfo=UTC).replace(tzinfo=None)
_1600 = datetime(2026, 8, 14, 16, 0, tzinfo=UTC).replace(tzinfo=None)


async def _released_together(count: int, work: Any) -> list[Any]:
    """`work(index)`, `count` times, all released from one barrier.

    `SQLBusinessStateRepository` drives pymssql synchronously inside
    `asyncio.to_thread`, so these are real OS threads contending on one row --
    which is the arrival pattern being tested, not a simulation of it.
    """
    barrier = asyncio.Barrier(count)

    async def _run(index: int) -> Any:
        await barrier.wait()
        return await work(index)

    return await asyncio.gather(*(_run(index) for index in range(count)), return_exceptions=True)


#: SQL Server's deadlock-victim error.
#:
#: No longer tolerated as an outcome -- `record_shipment_update` retries it, so
#: every writer must now answer. It is still named here because
#: `test_one_tracking_reference_cannot_be_claimed_by_two_rmas` asserts that the
#: refusal it expects is the *constraint* and not a deadlock wearing its clothes.
_DEADLOCK_VICTIM = 1205


def _is_deadlock(error: BaseException) -> bool:
    return bool(getattr(error, "args", ())) and error.args[0] == _DEADLOCK_VICTIM


@pytest.mark.asyncio
async def test_simultaneous_identical_updates_apply_at_most_once_and_never_duplicate_the_row(
    repository: SQLBusinessStateRepository,
) -> None:
    """The duplicate-shipment-update row of the matrix, contended.

    Eight copies of one carrier observation arrive together. The durable claim
    is one part of what this asserts: **one row, one APPLIED, and no writer that
    claims to have applied an observation it did not**. An RMA carrying the same
    observation twice is a return that looks like two shipments to every
    fulfilment reader, and that is the corruption this must exclude.

    The other part is that **every writer gets an answer**. This was
    SHIP-CONC-01, and this test tolerated a deadlock victim rather than
    asserting against one until it was fixed. `record_shipment_update` takes
    `UPDLOCK, HOLDLOCK` and then inserts, which is a lock conversion SQL Server
    resolves by rolling one side back (error 1205); nothing retried it, so a
    genuinely simultaneous duplicate answered HTTP 500 instead of the
    `DUPLICATE` 200 the route's docstring promises. It was never a correctness
    fault -- one row, correct status, in every trial including the deadlocked
    ones -- which is precisely why the loser can simply ask again. The
    repository now retries the victim, so the tolerance is gone and the
    coin flip with it.

    The locking is deliberately unchanged. `UPDLOCK, HOLDLOCK` is what puts the
    APPLIED/DUPLICATE/STALE decision inside the UPDATE's WHERE, and weakening it
    to avoid the deadlock would trade an error-reporting defect for a
    correctness one.
    """
    rma = _rma()
    update = _update(rma, "IN_TRANSIT", _1400, details="Picked up")

    results = await _released_together(
        CONTENDERS, lambda _: repository.record_shipment_update(update)
    )
    failures = [result for result in results if isinstance(result, BaseException)]
    assert not failures, f"a duplicate observation failed instead of answering: {failures}"

    outcomes = [result.outcome for result in results]
    assert outcomes.count(SHIPMENT_UPDATE_APPLIED) == 1, (
        f"{outcomes.count(SHIPMENT_UPDATE_APPLIED)} writers each applied the same observation: "
        f"{outcomes}"
    )
    # Everything that did not apply must have said DUPLICATE -- the contract the
    # route publishes, now reachable under genuine contention and not only
    # sequentially.
    assert outcomes.count(SHIPMENT_UPDATE_DUPLICATE) == CONTENDERS - 1, outcomes

    # The durable claim, which held under every trial including the deadlocked
    # ones before the retry existed: one row, and the observation recorded
    # exactly as submitted.
    state = await repository.read_shipment_state(rma)
    assert len(state) == 1
    assert state[0]["tracking_status"] == "IN_TRANSIT"
    assert state[0]["event_at"] == _1400
    assert state[0]["shipment_details"] == "Picked up"


@pytest.mark.asyncio
async def test_the_newest_observation_wins_however_the_race_is_ordered(
    repository: SQLBusinessStateRepository,
) -> None:
    """C4's stale-update safety, decided by the predicate rather than by arrival.

    Three observations of one RMA at 14:00, 15:00 and 16:00 are submitted
    together, so the order they reach SQL Server is genuinely undetermined. The
    stored truth must be 16:00 whichever of them commits last -- a last-writer-
    wins update would leave a delivered parcel reading IN_TRANSIT roughly two
    times in three.
    """
    rma = _rma()
    submissions = (
        _update(rma, "IN_TRANSIT", _1400, details="Picked up"),
        _update(rma, "OUT_FOR_DELIVERY", _1500, details="On the van"),
        _update(rma, "DELIVERED", _1600, details="Signed for"),
    )

    results = await _released_together(
        len(submissions), lambda index: repository.record_shipment_update(submissions[index])
    )
    failures = [result for result in results if isinstance(result, BaseException)]
    assert not failures, f"an out-of-order observation raised: {failures}"

    # Every outcome is a legitimate verdict: whichever arrived after a newer one
    # must say STALE rather than quietly overwriting it.
    assert all(
        result.outcome in {SHIPMENT_UPDATE_APPLIED, SHIPMENT_UPDATE_STALE} for result in results
    ), [result.outcome for result in results]

    state = await repository.read_shipment_state(rma)
    assert len(state) == 1
    assert state[0]["tracking_status"] == "DELIVERED", (
        "a newer observation was overwritten by an older one that arrived after it"
    )
    assert state[0]["event_at"] == _1600
    assert state[0]["shipment_details"] == "Signed for"


@pytest.mark.asyncio
async def test_contended_updates_stay_inside_their_own_rma(
    repository: SQLBusinessStateRepository,
) -> None:
    """RMA scope holds under contention.

    Contract C3 gives each return record its own shipment association. Eight
    RMAs updated at once must produce eight independent rows; a write keyed on
    something coarser than the RMA would collapse them, and a row-identity
    scheme that collided would surface here as one of the eight losing its
    update to another RMA's.
    """
    rmas = tuple(_rma() for _ in range(CONTENDERS))

    results = await _released_together(
        CONTENDERS,
        lambda index: repository.record_shipment_update(
            # Each RMA carries its own tracking reference, because the schema
            # says it must: `UQ_return_tracking_reference` makes a tracking
            # number belong to exactly one return. Sharing one across eight
            # RMAs is not a concurrency scenario, it is an invalid feed -- and
            # the constraint that rejects it is asserted separately below.
            _update(rmas[index], "IN_TRANSIT", _1400)
        ),
    )
    failures = [result for result in results if isinstance(result, BaseException)]
    assert not failures, f"a concurrent update on a distinct RMA failed: {failures}"
    assert all(result.outcome == SHIPMENT_UPDATE_APPLIED for result in results), [
        result.outcome for result in results
    ]

    for rma in rmas:
        state = await repository.read_shipment_state(rma)
        assert len(state) == 1, f"{rma} did not keep its own shipment row"
        assert state[0]["return_reference"] == rma
        assert state[0]["tracking_reference"] == f"TRK-{rma}"


@pytest.mark.asyncio
async def test_one_tracking_reference_cannot_be_claimed_by_two_rmas(
    repository: SQLBusinessStateRepository,
) -> None:
    """`UQ_return_tracking_reference`, asserted rather than assumed.

    Two RMAs racing to claim one tracking number is a real feed defect -- a
    carrier reusing a number, or a mis-keyed manual entry. The constraint must
    be what refuses it, so that the loser fails loudly instead of silently
    overwriting the other return's shipment.
    """
    first, second = _rma(), _rma()
    shared = f"TRK-{uuid.uuid4().hex[:12]}"
    pair = (first, second)

    results = await _released_together(
        len(pair),
        lambda index: repository.record_shipment_update(
            _update(pair[index], "IN_TRANSIT", _1400, tracking=shared)
        ),
    )
    answered = [result for result in results if not isinstance(result, BaseException)]
    refused = [result for result in results if isinstance(result, BaseException)]
    assert len(answered) == 1, "two RMAs both claimed one tracking reference"
    assert len(refused) == 1
    assert not _is_deadlock(refused[0]), (
        f"the shared reference was refused as a deadlock rather than a constraint: {refused[0]}"
    )
