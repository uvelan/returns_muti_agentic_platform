"""FencedMongoTransactionGuard re-verifies the lease inside the same transaction as the
write it guards, so a stale holder -- one whose fencing_token has been superseded by a
new acquisition -- is rejected atomically, with no partial write ever landing.

Also proves the transient-vs-fatal distinction (Slice 3R.1): a genuine Mongo
transient-transaction error retries the whole transaction and re-runs the fence
predicate, while a real LeaseLost (the predicate genuinely not matching) is never
retried.

The core fencing tests run against a real MongoDB replica set: this is exactly the
kind of race a hand-rolled session mock cannot prove (design doc §13.7; see also the
concurrent-activation review that found a fake session hides real transaction bugs).
The transient-vs-fatal retry classification is proven separately, driving the real
`pymongo.errors.OperationFailure`/`TransientTransactionError` label mechanism directly
-- reproducing that exact race live, on demand, turned out to be highly sensitive to
how long the two transactions stay open relative to each other (confirmed empirically:
a single non-transactional write to the same document blocks behind an open
transaction's document lock rather than conflicting; two concurrent transactions can
genuinely conflict, but reliably keeping both open long enough to overlap -- without
resynchronizing every retry into a livelock -- was not something to force with a timing
trick where the underlying database behavior is what should be tested, not the trick)."""

from __future__ import annotations

import asyncio

import pytest
from pymongo import AsyncMongoClient
from pymongo.errors import OperationFailure

from return_platform.configuration.settings import Settings
from return_platform.platform.system_store.locking import LeaseLost, bounded_retry_with_jitter
from return_platform.platform.system_store.mongo import (
    FencedMongoTransactionGuard,
    MongoLeaseStore,
    _is_mongo_transient_transaction_error,
)

# Live infrastructure: this module opens a real MongoDB client. It is not named
# `*_real_infra.py`, so this marker is what keeps it out of the default run
# and inside `scripts/dev/run_real_infra_suite.sh`.
pytestmark = pytest.mark.live_infra


async def _clean(client: AsyncMongoClient[dict[str, object]]) -> None:
    db = client.get_database("platform")
    await db.get_collection("platform_bootstrap_locks").delete_many({})
    await db.get_collection("platform_fencing_tokens").delete_many({})
    await db.get_collection("fenced_writer_probe").delete_many({})


@pytest.mark.asyncio
async def test_guarded_write_succeeds_for_the_current_lease_holder(test_settings: Settings) -> None:
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        test_settings.mongo_dsn.get_secret_value()
    )
    await _clean(client)
    db = client.get_database("platform")

    lease_store = MongoLeaseStore(client, database="platform")
    guard = FencedMongoTransactionGuard(client, database="platform")

    lease = await lease_store.acquire("probe-lock", owner_instance_id="owner-a", ttl_seconds=30.0)

    async def _write(session: object) -> str:
        await db.get_collection("fenced_writer_probe").insert_one(
            {"_id": "written-by-a"}, session=session
        )
        return "ok"

    result = await guard.assert_and_lock(lease, _write)

    assert result == "ok"
    assert (
        await db.get_collection("fenced_writer_probe").find_one({"_id": "written-by-a"}) is not None
    )


@pytest.mark.asyncio
async def test_guarded_write_rejects_a_lease_whose_token_was_superseded(
    test_settings: Settings,
) -> None:
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        test_settings.mongo_dsn.get_secret_value()
    )
    await _clean(client)
    db = client.get_database("platform")

    lease_store = MongoLeaseStore(client, database="platform")
    guard = FencedMongoTransactionGuard(client, database="platform")

    stale_lease = await lease_store.acquire(
        "probe-lock", owner_instance_id="owner-a", ttl_seconds=30.0
    )

    # Simulate the stale holder pausing past its TTL while a second instance takes over
    # the same lock -- this must mint a strictly greater fencing_token.
    await db.get_collection("platform_bootstrap_locks").update_one(
        {"_id": "probe-lock"}, {"$set": {"expires_at": stale_lease.acquired_at}}
    )
    new_lease = await lease_store.acquire(
        "probe-lock", owner_instance_id="owner-b", ttl_seconds=30.0
    )
    assert new_lease.fencing_token > stale_lease.fencing_token

    async def _write(session: object) -> None:
        await db.get_collection("fenced_writer_probe").insert_one(
            {"_id": "written-by-stale-a"}, session=session
        )

    with pytest.raises(LeaseLost):
        await guard.assert_and_lock(stale_lease, _write)

    # The rejected write must never have landed -- rejection and the write attempt are
    # one atomic transaction, not "check then maybe write."
    assert (
        await db.get_collection("fenced_writer_probe").find_one({"_id": "written-by-stale-a"})
        is None
    )


@pytest.mark.asyncio
async def test_genuine_lease_lost_is_never_retried_as_transient(test_settings: Settings) -> None:
    """A real fencing failure (predicate genuinely doesn't match) must surface
    immediately -- not be silently retried as if it were a transient conflict, which
    would waste the guard's retry budget masking a fail-closed condition."""
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        test_settings.mongo_dsn.get_secret_value()
    )
    await _clean(client)

    lease_store = MongoLeaseStore(client, database="platform")
    guard = FencedMongoTransactionGuard(
        client, database="platform", max_attempts=5, base_delay_seconds=1.0, max_delay_seconds=1.0
    )

    stale_lease = await lease_store.acquire(
        "probe-lock", owner_instance_id="owner-a", ttl_seconds=30.0
    )
    await (
        client.get_database("platform")
        .get_collection("platform_bootstrap_locks")
        .update_one({"_id": "probe-lock"}, {"$set": {"expires_at": stale_lease.acquired_at}})
    )
    await lease_store.acquire("probe-lock", owner_instance_id="owner-b", ttl_seconds=30.0)

    async def _write(session: object) -> None:
        return None

    loop = asyncio.get_running_loop()
    start = loop.time()
    with pytest.raises(LeaseLost):
        await guard.assert_and_lock(stale_lease, _write)
    elapsed = loop.time() - start

    # If LeaseLost were mistakenly retried with the configured (large) backoff, this
    # would take seconds; failing closed on the first attempt takes well under a second.
    assert elapsed < 1.0


@pytest.mark.asyncio
async def test_transient_transaction_error_is_retried_and_eventually_succeeds() -> None:
    """A genuine Mongo transient-transaction error -- confirmed empirically to occur
    when two concurrent transactions race to `find_one_and_update` the same document,
    raised as a real `pymongo.errors.OperationFailure` carrying the
    `TransientTransactionError` label -- must be retried, re-running the whole guarded
    operation from the top, and succeed once the underlying contention clears.

    Constructs the real pymongo exception type/label (not a fake error class) and
    drives it through the actual `bounded_retry_with_jitter` +
    `_is_mongo_transient_transaction_error` pair that `FencedMongoTransactionGuard` uses
    -- this is the exact classification and retry mechanism under test, exercised
    deterministically rather than by hoping a live two-transaction race reproduces on a
    schedule (empirically confirmed to be highly timing-sensitive: forcing it
    reproducibly turned out to depend on exact transaction-hold-open timing that varies
    run to run)."""
    attempts = 0

    async def _flaky_twice_then_succeeds() -> str:
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            raise OperationFailure(
                "Write conflict during plan execution and yielding is disabled.",
                112,
                {"errorLabels": ["TransientTransactionError"], "codeName": "WriteConflict"},
            )
        return "ok"

    result = await bounded_retry_with_jitter(
        _flaky_twice_then_succeeds,
        max_attempts=5,
        base_delay_seconds=0.01,
        max_delay_seconds=0.05,
        is_retryable=_is_mongo_transient_transaction_error,
    )

    assert result == "ok"
    assert attempts == 3


@pytest.mark.asyncio
async def test_transient_transaction_error_gives_up_after_max_attempts() -> None:
    attempts = 0

    async def _always_conflicts() -> str:
        nonlocal attempts
        attempts += 1
        raise OperationFailure(
            "Write conflict during plan execution and yielding is disabled.",
            112,
            {"errorLabels": ["TransientTransactionError"], "codeName": "WriteConflict"},
        )

    with pytest.raises(OperationFailure):
        await bounded_retry_with_jitter(
            _always_conflicts,
            max_attempts=3,
            base_delay_seconds=0.01,
            max_delay_seconds=0.05,
            is_retryable=_is_mongo_transient_transaction_error,
        )

    assert attempts == 3


@pytest.mark.asyncio
async def test_a_non_transient_error_is_never_retried() -> None:
    """LeaseLost (and any other exception not carrying TransientTransactionError) must
    fail on the first attempt -- the classification, not just the retry count, is what
    keeps a real fencing failure from being masked by the retry loop."""
    attempts = 0

    async def _fails_with_lease_lost() -> None:
        nonlocal attempts
        attempts += 1
        raise LeaseLost("fencing_token superseded")

    with pytest.raises(LeaseLost):
        await bounded_retry_with_jitter(
            _fails_with_lease_lost,
            max_attempts=5,
            base_delay_seconds=1.0,
            max_delay_seconds=1.0,
            is_retryable=_is_mongo_transient_transaction_error,
        )

    assert attempts == 1
