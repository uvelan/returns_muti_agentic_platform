"""The fencing token, made real, and the adoption path that gets production onto it.

Until GRAPH-01 the token was the literal `1` everywhere: `sync_service.py` passed
it for every write and the orchestrator opened every rebuild with it. Both fences
built on it were therefore decorative -- Neo4j's exact-match check on the
GraphGeneration marker, and `MongoSyncCheckpointStore`'s refusal to let a lower
token rewind a cursor. Neither could ever fire, because there was never a second
value to lose to.

Three properties are what make it real, and each is tested here:

* tokens are **allocated**, from a durable counter, strictly increasing, and
  strictly above the legacy constant -- so a writer holding the constant is
  already behind;
* claiming ownership **raises the marker's token**, which is what turns "an
  earlier owner is still running" into "an earlier owner's writes are rejected";
* the raise is **monotonic at the marker**, so a stale claimer cannot rewind it
  and hand ownership back to itself.

The rejection itself is exercised against a real Neo4j in
`test_graph_sync_cutover_real_infra.py`. What is proved here is the arithmetic
and the protocol around it, which is where the constant actually lived.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from return_platform.dynamic_knowledge.graph.generation import (
    FENCING_TOKEN_FLOOR,
    LEGACY_FENCING_TOKEN,
    ActiveRuntimeSnapshot,
    GraphGenerationStatus,
)
from return_platform.dynamic_knowledge.graph.generation_writer import (
    GenerationMarkerMissing,
    Neo4jGenerationWriter,
    StaleFencingToken,
)
from return_platform.dynamic_knowledge.graph.write_compiler import (
    compile_generation_fence,
    compile_generation_fence_claim,
)
from return_platform.dynamic_knowledge.lifecycle.mongo_store import MongoFencingTokenAllocator
from return_platform.dynamic_knowledge.lifecycle.orchestrator import (
    ActivationError,
    adopt_existing_generation,
)

SNAPSHOT = "ORDER_DISCOVERY"


# --- the allocator ----------------------------------------------------------


class _FakeCounterCollection:
    """`find_one_and_update` with `$inc` and `upsert`, as MongoDB implements it."""

    def __init__(self) -> None:
        self.documents: dict[str, dict[str, Any]] = {}

    async def find_one_and_update(
        self, filter: dict[str, Any], update: dict[str, Any], **kwargs: Any
    ) -> dict[str, Any]:
        del kwargs
        key = str(filter["_id"])
        document = self.documents.setdefault(key, {"_id": key, "next_token": 0})
        for field, amount in update["$inc"].items():
            document[field] = int(document.get(field, 0)) + int(amount)
        return dict(document)

    async def update_one(
        self, filter: dict[str, Any], update: dict[str, Any], **kwargs: Any
    ) -> None:
        del kwargs
        key = str(filter["_id"])
        document = self.documents.setdefault(key, {"_id": key, "next_token": 0})
        for field, value in update.get("$max", {}).items():
            document[field] = max(int(document.get(field, 0)), int(value))


@pytest.mark.asyncio
async def test_the_first_allocated_token_outranks_the_legacy_constant() -> None:
    """The one value the allocator must never return first.

    A counter starting from zero hands out `1`, which is exactly the token every
    pre-existing marker already carries -- so the first "real" owner would be
    indistinguishable from the stale writer it exists to fence off, and adoption
    would fence nothing.
    """
    allocator = MongoFencingTokenAllocator(_FakeCounterCollection())

    first = await allocator.allocate(scope=SNAPSHOT)

    assert first > LEGACY_FENCING_TOKEN
    assert first == FENCING_TOKEN_FLOOR + 1


@pytest.mark.asyncio
async def test_allocation_is_strictly_increasing_and_never_repeats() -> None:
    allocator = MongoFencingTokenAllocator(_FakeCounterCollection())

    issued = [await allocator.allocate(scope=SNAPSHOT) for _ in range(25)]

    assert issued == sorted(issued)
    assert len(set(issued)) == len(issued)


@pytest.mark.asyncio
async def test_a_restarted_allocator_does_not_reissue_a_token() -> None:
    """Durability is the point. An in-process counter would reset on deploy and
    hand the next process a token an earlier one already used -- which is
    precisely a stale writer being handed its successor's authority."""
    collection = _FakeCounterCollection()
    before = [
        await MongoFencingTokenAllocator(collection).allocate(scope=SNAPSHOT) for _ in range(3)
    ]

    after = await MongoFencingTokenAllocator(collection).allocate(scope=SNAPSHOT)

    assert after > max(before)


@pytest.mark.asyncio
async def test_a_floor_lifts_a_counter_that_is_behind_the_marker() -> None:
    """The counter and the token it authorizes live in different databases.

    Platform Mongo holds the counter; Neo4j holds the marker it stamps. They can
    start out of step -- a graph adopted from outside this counter's history, a
    platform database restored older than the graph in front of it -- and every
    claim would then lose to the marker permanently. The floor is how adoption
    reconciles them, and it must land strictly above the marker, not merely
    equal to it: an equal token is the previous owner's token.
    """
    allocator = MongoFencingTokenAllocator(_FakeCounterCollection())

    token = await allocator.allocate(scope=SNAPSHOT, floor=97)

    assert token == 98
    assert await allocator.allocate(scope=SNAPSHOT) > token


@pytest.mark.asyncio
async def test_a_floor_below_the_counter_never_rewinds_it() -> None:
    """A stale floor must not be able to walk the counter backwards -- that would
    hand a superseded owner a token it could claim with."""
    allocator = MongoFencingTokenAllocator(_FakeCounterCollection())
    for _ in range(10):
        await allocator.allocate(scope=SNAPSHOT)
    high = await allocator.allocate(scope=SNAPSHOT)

    after = await allocator.allocate(scope=SNAPSHOT, floor=2)

    assert after > high


@pytest.mark.asyncio
async def test_scopes_have_independent_counters() -> None:
    collection = _FakeCounterCollection()
    allocator = MongoFencingTokenAllocator(collection)

    await allocator.allocate(scope="A")
    await allocator.allocate(scope="A")
    other = await allocator.allocate(scope="B")

    assert other == FENCING_TOKEN_FLOOR + 1


# --- the claim --------------------------------------------------------------


def test_the_claim_is_parameterized_and_never_interpolates() -> None:
    """Preserved from the compiler's existing discipline: values are parameters,
    always. A generation id reaching the query text would be the one place in
    this module where an identifier is not schema-derived."""
    statement = compile_generation_fence_claim(
        graph_generation_id="gen-'; MATCH (n) DETACH DELETE n //", fencing_token=7
    )

    assert "MATCH (n) DETACH DELETE n" not in statement.cypher
    assert statement.parameters["generationId"] == "gen-'; MATCH (n) DETACH DELETE n //"
    assert statement.parameters["fencingToken"] == 7


def test_the_claim_only_ever_raises_the_token() -> None:
    """Monotonic at the marker, not merely at the allocator. Two claimers racing
    must agree on one winner without a transaction, and a claimer arriving late
    with a lower token must not be able to rewind the marker and take ownership
    back."""
    statement = compile_generation_fence_claim(graph_generation_id="gen-1", fencing_token=9)

    assert "CASE WHEN g.fencing_token < $fencingToken" in statement.cypher
    assert "THEN $fencingToken ELSE g.fencing_token END" in statement.cypher


class _FakeMarkerTransaction:
    """One GraphGeneration marker, with the claim and fence semantics the real
    Cypher has: the claim moves the token up only, and the fence matches on
    (id, token, status) exactly."""

    def __init__(self, *, token: int, status: GraphGenerationStatus) -> None:
        self.token = token
        self.status = status
        self.exists = True

    async def run(self, query: str, parameters: dict[str, Any]) -> Any:
        if not self.exists:
            return _rows([])
        if "SET g.fencing_token = CASE" in query:
            self.token = max(self.token, int(parameters["fencingToken"]))
            return _rows([{"status": self.status.value, "fencing_token": self.token}])
        if "RETURN count(g) AS matched" in query:
            matched = (
                1
                if parameters["fencingToken"] == self.token
                and parameters["expectedStatus"] == self.status.value
                else 0
            )
            return _rows([{"matched": matched}])
        return _rows([{"status": self.status.value, "fencing_token": self.token}])


def _rows(records: list[dict[str, Any]]) -> Any:
    class _Result:
        def __aiter__(self) -> Any:
            return self._iterate()

        async def _iterate(self) -> Any:
            for record in records:
                yield record

    return _Result()


class _FakeMarkerSession:
    def __init__(self, tx: _FakeMarkerTransaction) -> None:
        self._tx = tx

    async def __aenter__(self) -> _FakeMarkerSession:
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        return None

    async def execute_write(self, work: Any, **kwargs: Any) -> Any:
        return await work(self._tx, **kwargs)

    async def execute_read(self, work: Any, **kwargs: Any) -> Any:
        return await work(self._tx, **kwargs)


class _FakeMarkerDriver:
    def __init__(self, tx: _FakeMarkerTransaction) -> None:
        self.tx = tx

    def session(self, *, database: str | None = None) -> _FakeMarkerSession:
        del database
        return _FakeMarkerSession(self.tx)


@pytest.mark.asyncio
async def test_claiming_ownership_supersedes_the_previous_owners_token() -> None:
    """The mechanism, in one assertion: after a claim, the token an earlier owner
    is still carrying no longer matches the marker, so its fence fails."""
    tx = _FakeMarkerTransaction(token=LEGACY_FENCING_TOKEN, status=GraphGenerationStatus.ACTIVE)
    writer = Neo4jGenerationWriter(_FakeMarkerDriver(tx))

    status, token = await writer.claim_write_ownership(
        graph_generation_id="gen-1", fencing_token=42
    )

    assert (status, token) == (GraphGenerationStatus.ACTIVE, 42)
    stale = compile_generation_fence(
        graph_generation_id="gen-1",
        fencing_token=LEGACY_FENCING_TOKEN,
        expected_status=GraphGenerationStatus.ACTIVE.value,
    )
    rows = [row async for row in await tx.run(stale.cypher, stale.parameters)]
    assert rows[0]["matched"] == 0, "the superseded token still matches; nothing was fenced"


@pytest.mark.asyncio
async def test_a_stale_claim_is_rejected_rather_than_silently_losing() -> None:
    """A claimer whose token is behind must stop, not carry on writing under a
    token the marker will reject one statement at a time."""
    tx = _FakeMarkerTransaction(token=50, status=GraphGenerationStatus.ACTIVE)
    writer = Neo4jGenerationWriter(_FakeMarkerDriver(tx))

    with pytest.raises(StaleFencingToken) as caught:
        await writer.claim_write_ownership(graph_generation_id="gen-1", fencing_token=7)

    assert caught.value.observed == 50
    assert caught.value.requested == 7
    assert tx.token == 50, "a losing claim must not have moved the marker"


@pytest.mark.asyncio
async def test_claiming_a_generation_with_no_marker_is_distinguishable() -> None:
    tx = _FakeMarkerTransaction(token=2, status=GraphGenerationStatus.ACTIVE)
    tx.exists = False

    with pytest.raises(GenerationMarkerMissing):
        await Neo4jGenerationWriter(_FakeMarkerDriver(tx)).claim_write_ownership(
            graph_generation_id="gen-1", fencing_token=9
        )


# --- adoption ---------------------------------------------------------------


class _FakeSnapshotStore:
    def __init__(self, snapshot: ActiveRuntimeSnapshot | None = None) -> None:
        self.snapshot = snapshot
        self.swaps = 0

    async def read(self, *, snapshot_name: str) -> ActiveRuntimeSnapshot | None:
        del snapshot_name
        return self.snapshot

    async def compare_and_swap(
        self,
        *,
        snapshot_name: str,
        expected_activation_version: int | None,
        new_snapshot: ActiveRuntimeSnapshot,
    ) -> bool:
        del snapshot_name
        self.swaps += 1
        current = None if self.snapshot is None else self.snapshot.activation_version
        if current != expected_activation_version:
            return False
        self.snapshot = new_snapshot
        return True


async def _adopt(store: _FakeSnapshotStore, tx: _FakeMarkerTransaction) -> ActiveRuntimeSnapshot:
    return await adopt_existing_generation(
        snapshot_store=store,  # type: ignore[arg-type]
        generation_writer=Neo4jGenerationWriter(_FakeMarkerDriver(tx)),
        fencing_tokens=MongoFencingTokenAllocator(_FakeCounterCollection()),
        graph_generation_id="legacy-live",
        snapshot_name=SNAPSHOT,
        configuration_release_id="release-1",
        schema_fingerprint="a" * 64,
    )


@pytest.mark.asyncio
async def test_adoption_outranks_a_marker_the_counter_has_never_seen() -> None:
    """Adoption is the one step entitled to reconcile the two stores, so it must
    actually do it: a marker already holding a token above the fresh counter
    would otherwise reject the adopting owner's own claim."""
    store = _FakeSnapshotStore()
    tx = _FakeMarkerTransaction(token=500, status=GraphGenerationStatus.ACTIVE)

    await _adopt(store, tx)

    assert tx.token > 500


@pytest.mark.asyncio
async def test_adoption_publishes_the_existing_generation_as_version_one() -> None:
    """The backward-compatibility path. A deployment already serving from one
    always-ACTIVE generation must not be orphaned by the first rebuild: adopting
    it gives that rebuild a predecessor to cut over from, drain and retire."""
    store = _FakeSnapshotStore()
    tx = _FakeMarkerTransaction(token=LEGACY_FENCING_TOKEN, status=GraphGenerationStatus.ACTIVE)

    snapshot = await _adopt(store, tx)

    assert snapshot.graph_generation_id == "legacy-live"
    assert snapshot.activation_version == 1
    assert store.snapshot is snapshot
    assert tx.token > LEGACY_FENCING_TOKEN, "adoption must claim a real token on the marker"


@pytest.mark.asyncio
async def test_adoption_is_idempotent_and_never_re_swaps() -> None:
    """Called on every run that finds no snapshot, so a second call after one
    exists must change nothing at all -- including not re-claiming the token,
    which would fence off the run that adopted it."""
    store = _FakeSnapshotStore()
    tx = _FakeMarkerTransaction(token=LEGACY_FENCING_TOKEN, status=GraphGenerationStatus.ACTIVE)
    first = await _adopt(store, tx)
    token_after_first = tx.token

    second = await _adopt(store, tx)

    assert second == first
    assert store.swaps == 1
    assert tx.token == token_after_first


@pytest.mark.asyncio
async def test_adoption_refuses_a_generation_that_was_never_serving() -> None:
    """Adoption asserts "this is what has been live". Publishing a snapshot that
    points at a BUILDING or FAILED generation would send every reader there
    immediately."""
    store = _FakeSnapshotStore()
    tx = _FakeMarkerTransaction(token=LEGACY_FENCING_TOKEN, status=GraphGenerationStatus.BUILDING)

    with pytest.raises(ActivationError) as caught:
        await _adopt(store, tx)

    assert caught.value.stage == "ADOPT"
    assert store.snapshot is None


@pytest.mark.asyncio
async def test_adoption_refuses_a_generation_with_no_marker() -> None:
    store = _FakeSnapshotStore()
    tx = _FakeMarkerTransaction(token=LEGACY_FENCING_TOKEN, status=GraphGenerationStatus.ACTIVE)
    tx.exists = False

    with pytest.raises(ActivationError) as caught:
        await _adopt(store, tx)

    assert caught.value.stage == "ADOPT"


@pytest.mark.asyncio
async def test_losing_the_adoption_race_returns_the_winners_snapshot() -> None:
    """Two instances adopting at once is expected, not a fault. The loser must
    read the winner's snapshot rather than overwrite it -- two snapshots claiming
    version 1 would split readers across generations."""

    winner = ActiveRuntimeSnapshot(
        snapshot_name=SNAPSHOT,
        configuration_release_id="release-1",
        schema_fingerprint="a" * 64,
        graph_generation_id="legacy-live",
        search_index_release_id="none",
        activation_id="winner",
        activation_version=1,
        activated_at=datetime(2026, 8, 7, tzinfo=UTC),
    )

    class _RacingStore(_FakeSnapshotStore):
        async def compare_and_swap(self, **kwargs: Any) -> bool:
            self.swaps += 1
            self.snapshot = winner
            return False

    store = _RacingStore()
    tx = _FakeMarkerTransaction(token=LEGACY_FENCING_TOKEN, status=GraphGenerationStatus.ACTIVE)

    adopted = await _adopt(store, tx)

    assert adopted.activation_id == "winner"
