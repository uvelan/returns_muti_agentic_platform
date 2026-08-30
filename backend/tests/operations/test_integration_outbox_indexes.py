"""One owner for the integration outbox's indexes, and the union it must build.

The defect these close was a definition that had drifted three ways. Three
`ensure_indexes` methods built indexes on `integration_outbox` and no two agreed:

    OperationalRepository.ensure_indexes   idempotencyKey, (status, nextAttemptAt)
    IntegrationOutboxDispatcher            idempotencyKey, (status, nextAttemptAt), leaseUntil
    ReturnSupportService.ensure_indexes    idempotencyKey, (status, nextAttemptAt), (createdAt DESC)

Only the dispatcher built `leaseUntil` -- the index behind its own `claim()`
lease predicate. Only `ReturnSupportService` built `(createdAt DESC)` -- the
index behind `api/integration_outbox.py`'s `.sort("createdAt", -1)`. The
repository built neither, and the repository is the one the orchestrator calls,
so which indexes a deployment ended up with depended on which processes had
booted against that database first.

The trap in cleaning it up is that the obvious move -- delete the third,
redundant-looking copy in a Support service that owns none of these fields --
silently turns the operator listing into a collection scan plus an in-memory
sort. `test_the_read_surface_sort_and_the_lease_predicate_both_survive` is that
claim asserted rather than assumed; its other half, that MongoDB really plans
those two queries against an index, is
`test_integration_outbox_index_plans_real_infra.py`, because a double cannot
answer what the query planner does.

MongoDB is a double here for the same reason it is one in
`test_durable_support_events.py`: what is under test is which declarations the
three owners make, and a recorder that refuses a conflicting redeclaration --
the way the real `create_index` raises `IndexOptionsConflict` -- enforces the
idempotency claim without a server.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from pymongo import ASCENDING, DESCENDING, AsyncMongoClient
from pymongo.errors import OperationFailure

from return_platform.configuration.return_configuration import (
    ReturnPlatformConfiguration,
    load_return_configuration,
)
from return_platform.configuration.settings import (
    DEFAULT_RETURN_CONFIGURATION_PATH,
    Settings,
)
from return_platform.operations.integrations.outbox import (
    CLAIMABLE_STATUSES,
    INTEGRATION_OUTBOX_COLLECTION,
    IntegrationOutboxDispatcher,
    ensure_integration_outbox_indexes,
)
from return_platform.operations.repository import OperationalRepository
from return_platform.operations.return_support.service import ReturnSupportService

#: One key spec per index, in the normalized `((field, direction), ...)` shape
#: MongoDB reports, paired with the options that make it what it is.
#:
#: Written out here rather than imported from the module under test. An
#: expectation that reads itself off the implementation cannot fail, and the two
#: entries that matter most are exactly the ones a single owner used to build
#: alone.
EXPECTED_UNION: dict[tuple[tuple[str, int], ...], dict[str, Any]] = {
    # One command per business event.
    (("idempotencyKey", ASCENDING),): {"unique": True},
    # `claim()`, before the partial index below existed: status equality, then
    # the nextAttemptAt range. Retained as the fallback plan for a database
    # indexed by an older release.
    (("status", ASCENDING), ("nextAttemptAt", ASCENDING)): {},
    # `claim()` in full (D25): its `nextAttemptAt` range and *both* keys of its
    # sort, over only the statuses it can claim. The partial predicate is the
    # load-bearing part -- it is what lets `status` leave the key, which is what
    # lets the index deliver the sort instead of the server sorting in memory.
    # `test_integration_outbox_index_plans_real_infra.py` is where that is
    # explained against a planner.
    (("nextAttemptAt", ASCENDING), ("createdAt", ASCENDING)): {
        "partialFilterExpression": {"status": {"$in": ["PENDING", "RETRY"]}}
    },
    # `claim()`'s lease `$or`. Only the dispatcher used to build this.
    (("leaseUntil", ASCENDING),): {},
    # `api/integration_outbox.py`'s `.sort("createdAt", -1)`. Only
    # `ReturnSupportService` used to build this.
    (("createdAt", DESCENDING),): {},
    # `_dead_letter` writes both fields together; Phase 10 sweeps on both.
    (("status", ASCENDING), ("reconciliationState", ASCENDING)): {},
    # S2 ordering (contracts.md sect. 7): unique `(case, stream, sequence)`
    # identity for ordered commands, partial so the commands written before
    # streams existed cost nothing. Named because no deployed default-named
    # copy exists to conflict with.
    (("aggregateId", ASCENDING), ("stream", ASCENDING), ("streamSequence", ASCENDING)): {
        "unique": True,
        "partialFilterExpression": {"stream": {"$type": "string"}},
        "name": "case_stream_sequence_unique",
    },
    # The predecessor lookup: `_ordering_hold` resolves
    # `requiredPredecessorIds` by `eventId`, and enqueue validation depends on
    # an event id naming at most one command.
    (("eventId", ASCENDING),): {
        "unique": True,
        "partialFilterExpression": {"eventId": {"$type": "string"}},
        "name": "case_stream_event_id_unique",
    },
}

#: `leaseOwner` is in `claim()`'s `$or` and is deliberately *not* indexed (D26).
#: Asserted as an absence so that adding one has to come with a reason recorded
#: here; the measurement behind the decision -- indexing it changes `claim()`'s
#: winning plan not at all -- is in the real-infra module.
DELIBERATELY_UNINDEXED: tuple[tuple[tuple[str, int], ...], ...] = ((("leaseOwner", ASCENDING),),)


def _key_spec(keys: Any) -> tuple[tuple[str, int], ...]:
    """Normalize `create_index`'s two accepted key forms into one."""
    if isinstance(keys, str):
        return ((keys, ASCENDING),)
    return tuple((str(field), int(direction)) for field, direction in keys)


class _IndexRecorder:
    """A collection that records index declarations and refuses a conflicting one.

    The refusal is the point. `create_index` against a real server is a no-op
    for an identical definition and raises `IndexOptionsConflict` (code 85) for
    one that differs only in its options, so a double that merely appended would
    let a non-idempotent `ensure_*` pass and would not notice two owners
    declaring the same key pattern with different options -- which is the class
    of drift this whole consolidation is about.
    """

    def __init__(self, name: str, database: _FakeDatabase) -> None:
        self.name = name
        self.database = database
        self.indexes: dict[tuple[tuple[str, int], ...], dict[str, Any]] = {}
        self.calls: list[tuple[tuple[tuple[str, int], ...], dict[str, Any]]] = []

    async def create_index(self, keys: Any, **options: Any) -> str:
        spec = _key_spec(keys)
        declared = dict(options)
        existing = self.indexes.get(spec)
        if existing is not None and existing != declared:
            raise OperationFailure(
                f"Index already exists with different options on {self.name}", 85
            )
        self.indexes[spec] = declared
        self.calls.append((spec, declared))
        return "_".join(f"{field}_{direction}" for field, direction in spec)

    async def index_information(self) -> dict[str, Any]:
        return {
            "_id_": {"key": [("_id", ASCENDING)]},
            **{
                "_".join(f"{field}_{direction}" for field, direction in spec): {"key": list(spec)}
                for spec in self.indexes
            },
        }

    async def drop_index(self, name: str) -> None:  # pragma: no cover - never conflicts here
        for spec in list(self.indexes):
            if "_".join(f"{field}_{direction}" for field, direction in spec) == name:
                del self.indexes[spec]


class _FakeDatabase:
    def __init__(self) -> None:
        self.collections: dict[str, _IndexRecorder] = {}

    def __getitem__(self, name: str) -> _IndexRecorder:
        if name not in self.collections:
            self.collections[name] = _IndexRecorder(name, self)
        return self.collections[name]


class _FakeClient:
    def __init__(self) -> None:
        self.databases: dict[str, _FakeDatabase] = {}

    def __getitem__(self, name: str) -> _FakeDatabase:
        if name not in self.databases:
            self.databases[name] = _FakeDatabase()
        return self.databases[name]


@pytest.fixture
def database() -> _FakeDatabase:
    return _FakeDatabase()


@pytest.fixture
def client(database: _FakeDatabase, test_settings: Settings) -> _FakeClient:
    """One client whose platform database is the recorder above.

    Shared by all three owners so that "who declared what on the same
    collection" is answerable, which is the question the drift made unanswerable
    in production.
    """
    fake = _FakeClient()
    fake.databases[test_settings.mongo_database] = database
    return fake


@pytest.fixture(scope="module")
def shipped_configuration() -> ReturnPlatformConfiguration:
    return load_return_configuration(DEFAULT_RETURN_CONFIGURATION_PATH).configuration


def _outbox(database: _FakeDatabase) -> _IndexRecorder:
    return database[INTEGRATION_OUTBOX_COLLECTION]


@pytest.mark.asyncio
async def test_the_union_is_every_index_the_outbox_needs(database: _FakeDatabase) -> None:
    """All six, including the two nobody built.

    `reconciliationState` was unindexed entirely. `_dead_letter` writes it
    beside `status=DEAD_LETTER`, and Phase 10's sweep filters on that pair, so
    the index covers both equality terms rather than leaving one as a residual
    filter over every command the outbox has ever delivered.

    The partial `(nextAttemptAt, createdAt)` is the second (D25). Nothing built
    it because nothing had noticed that `claim()`'s `$in` on a leading key costs
    it the index's ordering.
    """
    await ensure_integration_outbox_indexes(cast(Any, database))

    assert _outbox(database).indexes == EXPECTED_UNION


@pytest.mark.asyncio
async def test_the_claim_index_is_partial_on_exactly_what_claim_filters(
    database: _FakeDatabase,
) -> None:
    """The index's partial predicate and `claim()`'s status filter are one list.

    A partial index serves a query only when the query's predicate implies the
    index's. So a status added to `claim()` and not to the index does not fail
    -- MongoDB falls back to the plan the partial index exists to replace, a
    blocking in-memory sort of every claimable command, and the only symptom is
    that the outbox gets slower as it gets busier. Both read `CLAIMABLE_STATUSES`
    for that reason, and this is the assertion that says so.
    """
    await ensure_integration_outbox_indexes(cast(Any, database))

    declared = _outbox(database).indexes[(("nextAttemptAt", ASCENDING), ("createdAt", ASCENDING))]

    assert declared["partialFilterExpression"] == {"status": {"$in": list(CLAIMABLE_STATUSES)}}
    assert tuple(CLAIMABLE_STATUSES) == ("PENDING", "RETRY")


@pytest.mark.asyncio
async def test_lease_owner_stays_unindexed(database: _FakeDatabase) -> None:
    """D26, written down as an assertion rather than as a comment.

    `leaseOwner` is one branch of `claim()`'s `$or` and has no index. That looks
    like an oversight and is not: on a real server, adding `leaseOwner_1` leaves
    `claim()`'s winning plan, its stages and its examined counts unchanged,
    because the planner enters through the `nextAttemptAt` range and applies the
    whole `$or` as a residual filter either way. The index would cost a write on
    every claim, delivery and failure and buy nothing.

    Failing here is not "you may not add it" -- it is "say why, and delete this".
    """
    await ensure_integration_outbox_indexes(cast(Any, database))

    declared = _outbox(database).indexes
    for spec in DELIBERATELY_UNINDEXED:
        assert spec not in declared


@pytest.mark.asyncio
async def test_declaring_the_union_three_times_declares_the_same_five(
    database: _FakeDatabase,
) -> None:
    """Three processes call this against one database. It has to be a no-op twice.

    The recorder raises `IndexOptionsConflict` for a redeclaration that differs
    in its options, exactly as the server does, so a second owner that had
    drifted would fail here rather than at somebody's boot.
    """
    for _ in range(3):
        await ensure_integration_outbox_indexes(cast(Any, database))

    outbox = _outbox(database)
    assert outbox.indexes == EXPECTED_UNION
    # Declared every time, converged on five: idempotent, not skipped.
    assert len(outbox.calls) == 3 * len(EXPECTED_UNION)


@pytest.mark.asyncio
async def test_the_read_surface_sort_and_the_lease_predicate_both_survive(
    database: _FakeDatabase,
) -> None:
    """The two indexes a single owner used to build alone.

    Deleting `ReturnSupportService`'s copy without the union first would have
    dropped `(createdAt DESC)` and turned `list_outbox` into a collection scan
    and an in-memory sort of every command ever written. Deleting the
    dispatcher's would have dropped `leaseUntil`, which is the only index
    behind its lease `$or`. Both are named here so that a future tidy-up of the
    union has to delete an assertion to lose them.
    """
    await ensure_integration_outbox_indexes(cast(Any, database))

    declared = _outbox(database).indexes

    # `api/integration_outbox.py`: `.sort("createdAt", -1)`.
    assert (("createdAt", DESCENDING),) in declared
    # `IntegrationOutboxDispatcher.claim()`: the `$or` over `leaseUntil`.
    assert (("leaseUntil", ASCENDING),) in declared
    # And its equality/range/sort prefix, which every owner did build.
    assert (("status", ASCENDING), ("nextAttemptAt", ASCENDING)) in declared


@pytest.mark.asyncio
async def test_the_repository_builds_the_union_the_orchestrator_needs(
    client: _FakeClient,
    database: _FakeDatabase,
    test_settings: Settings,
) -> None:
    """The owner that used to build the smallest subset.

    `orchestrator.py` and the API lifespan both reach the outbox's indexes only
    through here, so this method building two of five was the reason a
    Temporal-worker-free deployment had no lease index at all.
    """
    repository = OperationalRepository(cast(AsyncMongoClient[Any], client), test_settings)

    await repository.ensure_indexes()

    assert _outbox(database).indexes == EXPECTED_UNION


@pytest.mark.asyncio
async def test_the_dispatcher_builds_the_union_not_just_its_own_three(
    client: _FakeClient,
    database: _FakeDatabase,
    test_settings: Settings,
) -> None:
    """An outbox worker booting into a fresh database creates the read surface's
    sort index too, which it never used to."""
    dispatcher = IntegrationOutboxDispatcher(
        cast(AsyncMongoClient[Any], client),
        test_settings,
        {},
        worker_id="outbox-test",
    )

    await dispatcher.ensure_indexes()

    assert _outbox(database).indexes == EXPECTED_UNION


@pytest.mark.asyncio
async def test_the_api_process_builds_the_outbox_indexes_once(
    client: _FakeClient,
    database: _FakeDatabase,
    test_settings: Settings,
    shipped_configuration: ReturnPlatformConfiguration,
) -> None:
    """Both calls the API lifespan makes, in the order it makes them.

    `main.py` calls `OperationalRepository.ensure_indexes()` and then builds a
    `ReturnSupportService` and calls its `ensure_indexes()`. Both used to
    declare the outbox's indexes -- differently -- so every API boot rebuilt the
    collection's indexes twice. Support keeps its own queue's indexes and
    declares nothing on a collection whose fields it does not own.
    """
    repository = OperationalRepository(cast(AsyncMongoClient[Any], client), test_settings)
    await repository.ensure_indexes()
    calls_after_the_repository = len(_outbox(database).calls)

    await ReturnSupportService(
        client=cast(AsyncMongoClient[Any], client),
        settings=test_settings,
        configuration=shipped_configuration,
        operational_repository=repository,
    ).ensure_indexes()

    assert calls_after_the_repository == len(EXPECTED_UNION)
    assert len(_outbox(database).calls) == len(EXPECTED_UNION)
    assert _outbox(database).indexes == EXPECTED_UNION
    # Support's own queue is still indexed by Support.
    assert database["support_work_items"].indexes
    assert database["support_messages"].indexes
