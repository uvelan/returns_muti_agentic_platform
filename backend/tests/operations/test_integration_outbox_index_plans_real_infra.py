"""What MongoDB actually does with the two queries only one owner used to index.

`test_integration_outbox_indexes.py` proves which indexes
`ensure_integration_outbox_indexes` declares. It cannot prove that the queries
they exist for are planned against them, because a double has no query planner
-- and "the index is declared" and "the query uses an index" are exactly the
two things the consolidation had to keep from coming apart:

* `api/integration_outbox.py` sorts on `createdAt` descending. That index was
  built by `ReturnSupportService` alone -- the copy that looks most redundant,
  in the service with the least claim to the collection. Deleting it without
  first building the union turns the operator listing into a collection scan
  and a blocking in-memory sort.
* `IntegrationOutboxDispatcher.claim()` filters on status, `nextAttemptAt` and
  a `leaseUntil` `$or`. That was the dispatcher's alone.

So both are explained here against a real server, on a throwaway database, and
what is asserted is the absence of `COLLSCAN` in the winning plan -- the
failure that would actually be felt -- plus the presence of the index the
planner chose.

Two later findings are settled here for the same reason, because both are
claims about the planner that reading index definitions cannot answer:

* **D25.** `claim()`'s `status: {$in: [...]}` sits on the leading key of
  `(status, nextAttemptAt)`, so that index could not deliver its sort order and
  the server sorted every claimable command in memory. The partial
  `(nextAttemptAt, createdAt)` index removes `status` from the key and the sort
  becomes the index's own order. Explained as the `findAndModify` the dispatcher
  actually issues, with `executionStats`, because the cost of a blocking sort is
  a count and not a shape.
* **D26.** `leaseOwner` is unindexed. Rather than assert that as a preference,
  the index is created, `claim()` is explained again, and the two plans are
  required to be identical -- which is the reason the index is not worth its
  writes.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
import pytest_asyncio
from pymongo import ASCENDING, AsyncMongoClient

from return_platform.configuration.settings import Settings
from return_platform.operations.integrations.outbox import (
    CLAIMABLE_STATUSES,
    DEAD_LETTER_STATUS,
    INTEGRATION_OUTBOX_COLLECTION,
    REQUIRES_RECONCILIATION,
    ensure_integration_outbox_indexes,
)

pytestmark = pytest.mark.live_infra


def _stages(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Every stage in a winning plan, whatever shape the planner gave it.

    `$or` produces a `SUBPLAN`/`OR` with `inputStages`, an ordinary predicate a
    single `inputStage`; flattening both is what lets one assertion cover the
    two queries.
    """
    found = [plan]
    for key in ("inputStage", "innerStage", "outerStage", "queryPlan"):
        child = plan.get(key)
        if isinstance(child, dict):
            found.extend(_stages(child))
    for child in plan.get("inputStages", ()) or ():
        if isinstance(child, dict):
            found.extend(_stages(child))
    return found


def _stage_names(plan: dict[str, Any]) -> set[str]:
    return {str(stage.get("stage")) for stage in _stages(plan) if stage.get("stage")}


def _index_names(plan: dict[str, Any]) -> set[str]:
    return {str(stage["indexName"]) for stage in _stages(plan) if "indexName" in stage}


@pytest_asyncio.fixture
async def probe_database(test_settings: Settings) -> Any:
    """A throwaway database carrying a populated `integration_outbox`.

    The collection name is fixed by the module under test, so the isolation has
    to be the database. Populated because a query planner given an empty
    collection is not being asked the question this test asks.
    """
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        test_settings.mongo_dsn.get_secret_value()
    )
    name = f"outbox_index_probe_{uuid.uuid4().hex[:10]}"
    database = client[name]
    try:
        await ensure_integration_outbox_indexes(database)
        now = datetime.now(UTC)
        await database[INTEGRATION_OUTBOX_COLLECTION].insert_many(
            [
                {
                    "_id": f"cmd-{index}",
                    "topic": "return-case.support-response.signal",
                    "aggregateType": "RETURN_CASE",
                    "aggregateId": f"case-{index}",
                    "idempotencyKey": f"support-response:case-{index}",
                    "payload": {},
                    "status": ("PENDING", "RETRY", "DELIVERED", DEAD_LETTER_STATUS)[index % 4],
                    "reconciliationState": (REQUIRES_RECONCILIATION if index % 4 == 3 else None),
                    "attemptCount": index % 3,
                    "nextAttemptAt": now - timedelta(seconds=index),
                    "leaseUntil": None,
                    "leaseOwner": None,
                    "createdAt": now - timedelta(seconds=index),
                    "updatedAt": now - timedelta(seconds=index),
                }
                for index in range(400)
            ]
        )
        yield database
    finally:
        await client.drop_database(name)
        await client.close()


@pytest.mark.asyncio
async def test_the_union_lands_as_six_indexes_on_the_server(probe_database: Any) -> None:
    information = await probe_database[INTEGRATION_OUTBOX_COLLECTION].index_information()

    keys = {name: list(definition["key"]) for name, definition in information.items()}
    assert keys == {
        "_id_": [("_id", 1)],
        "idempotencyKey_1": [("idempotencyKey", 1)],
        "status_1_nextAttemptAt_1": [("status", 1), ("nextAttemptAt", 1)],
        "nextAttemptAt_1_createdAt_1": [("nextAttemptAt", 1), ("createdAt", 1)],
        "leaseUntil_1": [("leaseUntil", 1)],
        "createdAt_-1": [("createdAt", -1)],
        "status_1_reconciliationState_1": [("status", 1), ("reconciliationState", 1)],
    }
    assert information["idempotencyKey_1"].get("unique") is True
    # The partial predicate as the server stored it, which is what the planner
    # compares `claim()`'s filter against. An index that landed non-partial
    # would still be usable and would still be the wrong index: `status` would
    # have to come back into the key, and the sort would go back to memory.
    assert information["nextAttemptAt_1_createdAt_1"]["partialFilterExpression"] == {
        "status": {"$in": list(CLAIMABLE_STATUSES)}
    }
    # D26. `leaseOwner` is in `claim()`'s `$or` and has no index, by decision.
    assert "leaseOwner_1" not in information


@pytest.mark.asyncio
async def test_the_operator_listing_sort_is_served_from_an_index(probe_database: Any) -> None:
    """`list_outbox`'s `.sort("createdAt", -1).limit(200)`, exactly as it runs.

    A `SORT` stage in this plan is the regression: it means the server pulled
    every command into memory to order them, which is what deleting the only
    owner that built `createdAt` would have caused.
    """
    explained = await probe_database.command(
        {
            "explain": {
                "find": INTEGRATION_OUTBOX_COLLECTION,
                "filter": {},
                "sort": {"createdAt": -1},
                "limit": 200,
            },
            "verbosity": "queryPlanner",
        }
    )
    plan = explained["queryPlanner"]["winningPlan"]

    assert "COLLSCAN" not in _stage_names(plan)
    assert "SORT" not in _stage_names(plan)
    assert "createdAt_-1" in _index_names(plan)


def _claim_filter(now: datetime, *, worker_id: str = "outbox-probe") -> dict[str, Any]:
    """`IntegrationOutboxDispatcher.claim()`'s filter, copied verbatim."""
    return {
        "status": {"$in": list(CLAIMABLE_STATUSES)},
        "nextAttemptAt": {"$lte": now},
        "$or": [
            {"leaseUntil": None},
            {"leaseUntil": {"$exists": False}},
            {"leaseUntil": {"$lt": now}},
            {"leaseOwner": worker_id},
        ],
    }


CLAIM_SORT = {"nextAttemptAt": 1, "createdAt": 1}


async def _explain_claim(database: Any, now: datetime) -> dict[str, Any]:
    """`claim()` as the dispatcher actually issues it: a `findAndModify`.

    Not a `find`. The write half is part of the plan -- the `UPDATE` stage sits
    above the access path -- and a `find` that happened to plan well would not
    prove the claim the dispatcher makes. `executionStats` because what the
    blocking sort costs is counted, not described: it shows up as thousands of
    keys and documents examined to return one.
    """
    return cast(
        dict[str, Any],
        await database.command(
            {
                "explain": {
                    "findAndModify": INTEGRATION_OUTBOX_COLLECTION,
                    "query": _claim_filter(now),
                    "sort": CLAIM_SORT,
                    "update": {
                        "$set": {
                            "status": "DISPATCHING",
                            "leaseOwner": "outbox-probe",
                            "leaseUntil": now + timedelta(seconds=60),
                            "updatedAt": now,
                        },
                        "$inc": {"attemptCount": 1},
                    },
                    "new": True,
                },
                "verbosity": "executionStats",
            }
        ),
    )


@pytest.mark.asyncio
async def test_the_dispatchers_claim_takes_its_sort_from_an_index(
    probe_database: Any,
) -> None:
    """D25: `claim()` no longer sorts every claimable command in memory.

    `status: {$in: [...]}` is a multi-point range on the leading key of
    `(status, nextAttemptAt)`, and an index cannot deliver rows in
    `nextAttemptAt` order across two separate ranges of `status`. So the server
    used to fetch every claimable command and sort it to pick one -- on this
    fixture, `SORT -> FETCH -> IXSCAN`, 2,000 keys and 2,000 documents examined
    to return a single record. Harmless at a queue of ten and quadratic-feeling
    at a queue of a hundred thousand.

    The partial `(nextAttemptAt, createdAt)` index takes `status` out of the key
    -- the partial predicate carries it instead -- so the sort is the index's
    own order and the `limit` stops after one row.

    Both halves are asserted: no `SORT` stage, and the counts that would betray
    one reappearing under a different plan.
    """
    explained = await _explain_claim(probe_database, datetime.now(UTC))
    plan = explained["queryPlanner"]["winningPlan"]
    stats = explained["executionStats"]

    assert "COLLSCAN" not in _stage_names(plan)
    assert "SORT" not in _stage_names(plan)
    assert "nextAttemptAt_1_createdAt_1" in _index_names(plan)
    # One key, one document, one row. The fixture holds 400 commands, 200 of
    # them claimable, so a re-emerged blocking sort cannot hide inside these.
    assert stats["nReturned"] == 1
    assert stats["totalKeysExamined"] <= 5
    assert stats["totalDocsExamined"] <= 5


@pytest.mark.asyncio
async def test_indexing_lease_owner_would_change_nothing(probe_database: Any) -> None:
    """D26: the measurement behind leaving `leaseOwner` unindexed.

    `leaseOwner` is one branch of `claim()`'s `$or`, and the `$or` is applied as
    a residual filter over the access path rather than as a seek. That reads
    like an oversight, so it is measured here rather than asserted by opinion:
    the same `claim()` is explained with and without `leaseOwner_1` present, and
    the winning plan, its stages and its examined counts have to come out
    identical. They do -- the planner enters through the `nextAttemptAt` range
    either way.

    Which makes the index pure cost: a write on every claim, every delivery and
    every failure, for the rare branch where a worker meets a lease it took
    before crashing. If this test ever fails, the trade has changed and the
    decision is worth re-taking.
    """
    now = datetime.now(UTC)
    collection = probe_database[INTEGRATION_OUTBOX_COLLECTION]

    def fingerprint(explained: dict[str, Any]) -> tuple[Any, ...]:
        plan = explained["queryPlanner"]["winningPlan"]
        stats = explained["executionStats"]
        return (
            tuple(sorted(_stage_names(plan))),
            tuple(sorted(_index_names(plan))),
            stats["nReturned"],
            stats["totalKeysExamined"],
            stats["totalDocsExamined"],
        )

    without = fingerprint(await _explain_claim(probe_database, now))
    await collection.create_index("leaseOwner", name="leaseOwner_1")
    try:
        with_index = fingerprint(await _explain_claim(probe_database, now))
    finally:
        await collection.drop_index("leaseOwner_1")

    assert with_index == without
    assert "leaseOwner_1" not in _index_names(
        (await _explain_claim(probe_database, now))["queryPlanner"]["winningPlan"]
    )


@pytest.mark.asyncio
async def test_the_reconciliation_sweep_is_served_from_an_index(probe_database: Any) -> None:
    """Phase 10's `status=DEAD_LETTER` / `reconciliationState=REQUIRES_RECONCILIATION`.

    `reconciliationState` had no index at all before the union, so this filter
    was a scan of every command the outbox had ever delivered.
    """
    explained = await probe_database.command(
        {
            "explain": {
                "find": INTEGRATION_OUTBOX_COLLECTION,
                "filter": {
                    "status": DEAD_LETTER_STATUS,
                    "reconciliationState": REQUIRES_RECONCILIATION,
                },
            },
            "verbosity": "queryPlanner",
        }
    )
    plan = explained["queryPlanner"]["winningPlan"]

    assert "COLLSCAN" not in _stage_names(plan)
    assert "status_1_reconciliationState_1" in _index_names(plan)


@pytest.mark.asyncio
async def test_rebuilding_the_union_against_a_populated_collection_is_a_no_op(
    probe_database: Any,
) -> None:
    """Three processes call this against one database, in any order, forever.

    Named indexes would have made that a boot failure the first time -- every
    deployment already carries these key patterns under MongoDB's default
    names, and `create_index` raises `IndexOptionsConflict` for the same keys
    under a new name.
    """
    before = await probe_database[INTEGRATION_OUTBOX_COLLECTION].index_information()

    for _ in range(3):
        await ensure_integration_outbox_indexes(probe_database)

    after = await probe_database[INTEGRATION_OUTBOX_COLLECTION].index_information()
    assert sorted(after) == sorted(before)
    assert len(after) == 7


@pytest.mark.asyncio
async def test_the_lease_index_exists_for_the_only_query_that_reads_it(
    probe_database: Any,
) -> None:
    """`leaseUntil` alone, which is what `claim()`'s `$or` branches on."""
    explained = await probe_database.command(
        {
            "explain": {
                "find": INTEGRATION_OUTBOX_COLLECTION,
                "filter": {"leaseUntil": {"$lt": datetime.now(UTC)}},
            },
            "verbosity": "queryPlanner",
        }
    )
    plan = explained["queryPlanner"]["winningPlan"]

    assert "COLLSCAN" not in _stage_names(plan)
    assert "leaseUntil_1" in _index_names(plan)
    assert list(
        (await probe_database[INTEGRATION_OUTBOX_COLLECTION].index_information())["leaseUntil_1"][
            "key"
        ]
    ) == [("leaseUntil", ASCENDING)]
