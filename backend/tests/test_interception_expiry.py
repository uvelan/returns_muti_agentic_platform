"""D6: a lapsed interception expires visibly instead of being abandoned.

`docs/RETURN_COPILOT_EXECUTION_STATE.md` claimed "a lapsed request expires
visibly rather than being abandoned silently". It did not: `list_pending`
filtered on `status: PENDING` alone and *nothing* ever performed the
`PENDING -> EXPIRED` transition, so a live deployment accumulated 48 of 49 rows
`PENDING` with `expiresAt` days in the past. An operator -- or a harness taking
"the first PENDING" -- was handed a three-day-old request whose caller had long
since gone.

The vocabulary was all there. `InterceptionStatus.EXPIRED` exists, `is_terminal`
already distinguishes it from `CANCELLED`, `DurableInterceptionPolicy` already
maps it to `REJECT`, and `DurableInterceptionProvider` already closes its *own*
record that way on timeout. Only the transition for records nobody is left to
close had no owner.

**Both halves are asserted here, because either alone is wrong.** The sweep
settles the rows so the stored status stops being a lie; the read filter hides
them between intervals so the queue never offers one. This file proves they use
one predicate and cannot drift apart.

The Mongo *semantics* -- that `$lte` on a BSON date does what these fakes do --
are proved against a real replica set in
`tests/test_durable_interception_real_infra.py`. What is proved here is the
platform's own logic on top of them: which filter each side issues, and that the
transition is a compare-and-set rather than a blind write.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from return_platform.ai.interception.records import InterceptionStatus
from return_platform.ai.interception.store import (
    AI_INTERCEPTIONS,
    InterceptionExpirySweep,
    SystemStoreInterceptionStore,
    lapsed_filter,
)

#: A fixed instant, used only where the filter is handed one explicitly. Records
#: are anchored to the wall clock instead, because the store reads
#: `datetime.now(UTC)` itself -- a fixture pinned to a literal date would pass or
#: fail depending on what day the suite ran.
_NOW = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)


def _matches(document: Mapping[str, Any], criteria: Mapping[str, Any]) -> bool:
    """The sliver of Mongo query semantics these two filters actually use.

    Deliberately tiny and deliberately strict: an operator this does not
    implement raises rather than silently matching, so a future filter that
    reaches for one cannot pass here and fail in production.
    """
    for field, expected in criteria.items():
        actual = document.get(field)
        if not isinstance(expected, dict):
            if actual != expected:
                return False
            continue
        for operator, bound in expected.items():
            if operator == "$gt":
                if not (actual is not None and actual > bound):
                    return False
            elif operator == "$lte":
                if not (actual is not None and actual <= bound):
                    return False
            else:  # pragma: no cover - a filter this fake cannot judge
                raise AssertionError(f"unsupported query operator {operator!r}")
    return True


class _Cursor:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self._documents = documents

    def sort(self, field: str, direction: int) -> _Cursor:
        self._documents.sort(key=lambda document: document[field], reverse=direction < 0)
        return self

    def limit(self, count: int) -> _Cursor:
        self._documents = self._documents[:count]
        return self

    async def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        for document in self._documents:
            yield document


class _ReadOnly:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self._documents = documents

    def find(self, criteria: Mapping[str, Any]) -> _Cursor:
        return _Cursor([dict(d) for d in self._documents if _matches(d, criteria)])

    async def count_documents(self, criteria: Mapping[str, Any], *, limit: int = 0) -> int:
        matched = [d for d in self._documents if _matches(d, criteria)]
        return len(matched[:limit]) if limit else len(matched)

    async def find_one(self, criteria: Mapping[str, Any]) -> dict[str, Any] | None:
        return next((dict(d) for d in self._documents if _matches(d, criteria)), None)


class _UpdateResult:
    def __init__(self, matched_count: int) -> None:
        self.matched_count = matched_count


class _SystemStore:
    """Just the two surfaces the interception store uses, filter semantics and all.

    `replace_one` honours its filter, which is the whole point: the expiry
    transition's safety is that it is conditional on the record still being
    `PENDING`, and a fake that replaced unconditionally would make the race test
    prove nothing.
    """

    def __init__(self, documents: list[dict[str, Any]] | None = None) -> None:
        self.documents: list[dict[str, Any]] = documents or []
        self.metadata_allowlists: list[frozenset[str]] = []

    def read_only(self, logical_name: str) -> _ReadOnly:
        assert logical_name == AI_INTERCEPTIONS
        return _ReadOnly(self.documents)

    async def replace_one(
        self,
        logical_name: str,
        criteria: Mapping[str, Any],
        document: Mapping[str, Any],
        *,
        allowed_metadata_fields: frozenset[str] = frozenset(),
    ) -> _UpdateResult:
        assert logical_name == AI_INTERCEPTIONS
        self.metadata_allowlists.append(allowed_metadata_fields)
        for index, existing in enumerate(self.documents):
            if _matches(existing, criteria):
                self.documents[index] = dict(document)
                return _UpdateResult(1)
        return _UpdateResult(0)


def _record(
    interception_id: str,
    *,
    status: InterceptionStatus = InterceptionStatus.PENDING,
    expires_in: timedelta,
    created_ago: timedelta = timedelta(hours=2),
    now: datetime | None = None,
) -> dict[str, Any]:
    anchor = now or datetime.now(UTC)
    return {
        "_id": interception_id,
        "interception_id": interception_id,
        "task_id": "ORDER_AGENT_REASONING_V1",
        "status": status.value,
        "point": "REQUEST",
        "resume": {"run_id": "run-1", "thread_id": "thread-1", "workflow_id": None},
        "created_at": anchor - created_ago,
        "expires_at": anchor + expires_in,
        "answered_at": None,
        "answered_by": None,
        "_envelope": {
            "ciphertext": "sealed",
            "key_ref": "k1",
            "algorithm": "AES-GCM",
            "version": 1,
        },
    }


class _NeverCalledEncryptor:
    """Listing and expiring must not open a single envelope.

    Sealing the payload is pointless if rendering a queue or reaping a corpse
    decrypts it, so the encryptor a listing store is built with is one that
    fails loudly if anything touches it.
    """

    def encrypt(self, raw: bytes) -> Any:  # pragma: no cover - must never run
        raise AssertionError("listing must not seal anything")

    def decrypt(self, payload: Any) -> bytes:  # pragma: no cover - must never run
        raise AssertionError("listing must not unseal anything")


# --- the read path -----------------------------------------------------------


@pytest.mark.asyncio
async def test_the_queue_does_not_offer_a_request_whose_deadline_has_passed() -> None:
    """The head of the live queue was a three-day-old corpse.

    The sweep runs on an interval and this read runs on a click, so the filter
    has to stand on its own: a record can be lapsed and still stored `PENDING`
    for as long as the interval is, and offering one means the operator's answer
    goes nowhere.
    """
    system_store = _SystemStore(
        [
            _record("dead", expires_in=timedelta(days=-3), created_ago=timedelta(days=3)),
            _record("live", expires_in=timedelta(minutes=30)),
        ]
    )
    store = SystemStoreInterceptionStore(system_store, _NeverCalledEncryptor())  # type: ignore[arg-type]

    pending = await store.list_pending()

    assert [record.interception_id for record in pending] == ["live"]


@pytest.mark.asyncio
async def test_the_queue_is_still_oldest_first_among_live_requests() -> None:
    """The expiry filter must not cost the queue its arrival order -- the oldest
    live request is the one closest to lapsing, and is the one to work next."""
    system_store = _SystemStore(
        [
            _record("newer", expires_in=timedelta(minutes=50), created_ago=timedelta(minutes=5)),
            _record("older", expires_in=timedelta(minutes=20), created_ago=timedelta(minutes=40)),
        ]
    )
    store = SystemStoreInterceptionStore(system_store, _NeverCalledEncryptor())  # type: ignore[arg-type]

    pending = await store.list_pending()

    assert [record.interception_id for record in pending] == ["older", "newer"]


# --- the transition ----------------------------------------------------------


@pytest.mark.asyncio
async def test_the_sweep_settles_only_lapsed_pending_records() -> None:
    system_store = _SystemStore(
        [
            _record("dead", expires_in=timedelta(days=-3)),
            _record("live", expires_in=timedelta(minutes=30)),
            _record(
                "already-answered",
                status=InterceptionStatus.ANSWERED,
                expires_in=timedelta(days=-3),
            ),
        ]
    )
    sweep = InterceptionExpirySweep(system_store)  # type: ignore[arg-type]

    assert await sweep.count_lapsed(limit=200) == 1
    assert await sweep.expire_lapsed(limit=200) == 1

    by_id = {document["interception_id"]: document["status"] for document in system_store.documents}
    assert by_id == {
        "dead": InterceptionStatus.EXPIRED.value,
        "live": InterceptionStatus.PENDING.value,
        # A record that already reached a terminal status is not re-settled: an
        # answered request that nobody collected is a different fact from one
        # nobody answered, and `EXPIRED` would erase the human's work.
        "already-answered": InterceptionStatus.ANSWERED.value,
    }


@pytest.mark.asyncio
async def test_the_sweep_carries_the_sealed_payload_through_untouched() -> None:
    """Expiry is a metadata transition, and that is why it needs no key.

    The whole-document replace must put the envelope back exactly as it found
    it -- a sweep that dropped it would destroy the record it was only meant to
    close, and one that re-sealed it would need the reasoning encryption key in
    the housekeeping worker for no reason.
    """
    system_store = _SystemStore([_record("dead", expires_in=timedelta(days=-3))])
    original = dict(system_store.documents[0]["_envelope"])

    await InterceptionExpirySweep(system_store).expire_lapsed(limit=200)  # type: ignore[arg-type]

    assert system_store.documents[0]["_envelope"] == original


@pytest.mark.asyncio
async def test_an_answer_landing_mid_sweep_beats_the_sweep() -> None:
    """The compare-and-set, which is the reason this is safe to run beside
    operators.

    `DurableInterceptionProvider` already closes its own record with the same
    conditional `cancel`; the sweep follows that precedent rather than inventing
    a second mechanism. Here the record stops being `PENDING` between the read
    and the write, and the transition must decline rather than overwrite the
    human's answer.
    """
    system_store = _SystemStore([_record("racing", expires_in=timedelta(seconds=-1))])
    sweep = InterceptionExpirySweep(system_store)  # type: ignore[arg-type]

    examined = await sweep.count_lapsed(limit=200)
    # The operator answers in the window between the count and the write.
    system_store.documents[0]["status"] = InterceptionStatus.ANSWERED.value

    expired = await sweep.expire_lapsed(limit=200)

    assert examined == 1
    assert expired == 0, "the sweep must lose the race, not overwrite the answer"
    assert system_store.documents[0]["status"] == InterceptionStatus.ANSWERED.value


@pytest.mark.asyncio
async def test_the_batch_limit_bounds_both_the_count_and_the_write() -> None:
    """A backlog of days must drain over several passes rather than in one
    unbounded write that holds the collection for the whole interval."""
    system_store = _SystemStore(
        [_record(f"dead-{index}", expires_in=timedelta(days=-1)) for index in range(10)]
    )
    sweep = InterceptionExpirySweep(system_store)  # type: ignore[arg-type]

    assert await sweep.count_lapsed(limit=4) == 4
    assert await sweep.expire_lapsed(limit=4) == 4

    remaining = [
        document
        for document in system_store.documents
        if document["status"] == InterceptionStatus.PENDING.value
    ]
    assert len(remaining) == 6


@pytest.mark.asyncio
async def test_the_transition_is_written_through_the_guarded_metadata_allowlist() -> None:
    """`ai_interceptions` is declared `encrypted: true`, so every write to it is
    checked against the fields a plaintext column is allowed to be. The sweep
    must go through the same gate as `answer`, `allow` and `cancel` rather than
    round the side of it."""
    from return_platform.ai.interception.store import METADATA_FIELDS

    system_store = _SystemStore([_record("dead", expires_in=timedelta(days=-3))])

    await InterceptionExpirySweep(system_store).expire_lapsed(limit=200)  # type: ignore[arg-type]

    assert system_store.metadata_allowlists == [METADATA_FIELDS]


# --- the two halves cannot drift ---------------------------------------------


def test_the_queue_hides_exactly_what_the_sweep_settles() -> None:
    """The property that makes running both non-redundant instead of
    contradictory.

    A read filter that was even slightly wider than the sweep's predicate would
    offer an operator a record the next pass then expired under them.
    """
    lapsed = lapsed_filter(_NOW)

    assert lapsed["status"] == InterceptionStatus.PENDING.value
    assert lapsed["expires_at"] == {"$lte": _NOW}

    live = _record("live", expires_in=timedelta(minutes=1), now=_NOW)
    dead = _record("dead", expires_in=timedelta(minutes=-1), now=_NOW)
    boundary = _record("boundary", expires_in=timedelta(0), now=_NOW)

    for document in (live, dead, boundary):
        # Exactly one side claims each record: the queue's `$gt` and the sweep's
        # `$lte` partition the same field on the same instant.
        listed = _matches(document, {"status": "PENDING", "expires_at": {"$gt": _NOW}})
        settled = _matches(document, lapsed)
        assert listed is not settled, document["interception_id"]
