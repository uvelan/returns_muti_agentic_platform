"""The three writers plan sect. 6.5 had not reached, at the wiring level (D27, D28).

> Any write that can change the `CaseDetail` projection must, in the same
> transaction, bump `case.revision` and set `case.updatedAt`.

`case_repository.py`'s four writers took the invariant first. Three did not, for
two different reasons, and neither reason was that they were exempt:

* `update_return_item` and `assign_return_item_to_record` live in
  `operations/repository.py`. `OperationalRepository(CaseRepository)` binds
  `return_items` to the collection that holds *both* the session-scoped items
  and the case-scoped ones, so these two could not simply be moved into the
  mixin -- and being in the other file is why they were missed.
  `assign_return_item_to_record` is the live one:
  `return_case_activities._assign_items_to_record` calls it on every Support
  outcome that maps order lines to an RMA, and `returnRecordId` is the
  attribution `selectedItems` renders.
* `ReturnSupportService.apply_action` writes `support_work_items` directly.
  `SupportProjection` reads `status`, `assignedTo` and `completedAt` off that
  document, and every branch of the action sets at least one of them. A Support
  reply that left the revision untouched is the precise case the invariant
  exists to prevent.

WHAT THIS MODULE CAN AND CANNOT SETTLE
--------------------------------------
The same division `test_case_revision_atomicity.py` draws. A fake collection
cannot roll anything back, so atomicity is settled against a real replica set in
`test_support_writers_revision_atomicity_real_infra.py`. What is settled *here*,
cheaply and on every run, is the shape of the calls: one session is opened, and
the child write and the case bump both carry **that** session rather than one of
them going out on its own.

That distinction is the whole defect. A test that only asserted "the revision
moved" would pass on a second, best-effort `update_one` afterwards -- which is
the repair plan sect. 6.5 explicitly refuses, because a bump that can fail after
the child write commits leaves a case complete on the server and incomplete on
every client that trusts the revision. This one does not pass on that.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest

from return_platform.operations.errors import ConcurrencyConflictError
from return_platform.operations.repository import OperationalRepository
from return_platform.operations.return_support.service import (
    ReturnSupportService,
    SupportAction,
    SupportActionRequest,
    SupportWorkItemStatus,
)

CASE_ID = "CASE-1"
ITEM_ID = "ITEM-1"
RECORD_ID = "RR-1"
WORK_ITEM_ID = "WI-1"


@dataclass
class _Write:
    """One recorded call, with the session it was issued under."""

    collection: str
    operation: str
    query: Mapping[str, Any] | None
    update: Mapping[str, Any] | None
    session: object | None


@dataclass
class _Journal:
    writes: list[_Write] = field(default_factory=list)
    sessions_opened: int = 0

    def to(self, collection: str) -> list[_Write]:
        return [write for write in self.writes if write.collection == collection]


class _FakeSession:
    """Stands in for `AsyncClientSession`. Identity is the whole point of it."""


class _FakeCollection:
    def __init__(
        self, name: str, journal: _Journal, documents: list[dict[str, Any]] | None = None
    ) -> None:
        self._name = name
        self._journal = journal
        self._documents = documents if documents is not None else []

    async def update_one(
        self, query: Mapping[str, Any], update: Mapping[str, Any], session: Any = None
    ) -> Any:
        self._journal.writes.append(_Write(self._name, "update_one", query, update, session))

        class _Result:
            matched_count = 1

        return _Result()

    async def find_one_and_update(
        self,
        query: Mapping[str, Any],
        update: Mapping[str, Any],
        session: Any = None,
        **_: Any,
    ) -> dict[str, Any] | None:
        self._journal.writes.append(
            _Write(self._name, "find_one_and_update", query, update, session)
        )
        for document in self._documents:
            if all(document.get(key) == value for key, value in query.items()):
                return dict(document)
        return None

    async def find_one(
        self, query: Mapping[str, Any], projection: Any = None, session: Any = None
    ) -> dict[str, Any] | None:
        del projection, session
        for document in self._documents:
            if all(document.get(key) == value for key, value in query.items()):
                return dict(document)
        return None


class _Transactional:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def with_transaction(self, callback: Any) -> Any:
        return await callback(self._session)


class _FakeClient:
    """One session per transaction, and it counts them."""

    def __init__(self, journal: _Journal) -> None:
        self._journal = journal

    def start_session(self) -> Any:
        journal = self._journal

        @asynccontextmanager
        async def _session() -> AsyncIterator[Any]:
            journal.sessions_opened += 1
            yield _Transactional(_FakeSession())

        return _session()


def _assert_bumped_with(journal: _Journal, child: str) -> None:
    """The bump happened, and under the child write's own session."""
    child_writes = journal.to(child)
    bumps = journal.to("cases")

    assert len(child_writes) == 1, f"expected one write to {child}, got {child_writes}"
    assert len(bumps) == 1, f"expected exactly one case revision bump, got {bumps}"

    bump = bumps[0]
    assert bump.update is not None
    assert bump.update["$inc"] == {"version": 1}
    assert "updatedAt" in bump.update["$set"]
    assert bump.query == {"caseId": CASE_ID}

    # The assertion the whole invariant rests on: one session, shared. A
    # best-effort second write would show `None` here, or a different object.
    assert journal.sessions_opened == 1
    assert bump.session is not None
    assert bump.session is child_writes[0].session


# ---------------------------------------------------------------------------
# `operations/repository.py`: the item writers (D27)
# ---------------------------------------------------------------------------


def _repository(items: list[dict[str, Any]]) -> tuple[OperationalRepository, _Journal]:
    """An `OperationalRepository` with only the collections these two touch.

    Built without `__init__` because that constructor resolves a database
    handle, a source client and a schema release store, none of which these two
    methods read. What matters is that the class under test is the shipped one,
    so a repair that only fixed the mixin would fail here.
    """
    journal = _Journal()
    repository = OperationalRepository.__new__(OperationalRepository)
    repository.cases = _FakeCollection("cases", journal)  # type: ignore[assignment]
    repository.return_items = _FakeCollection(  # type: ignore[assignment]
        "return_items", journal, items
    )
    repository._client = _FakeClient(journal)  # type: ignore[assignment]
    return repository, journal


@pytest.mark.asyncio
async def test_assigning_an_item_to_a_record_bumps_the_revision_in_one_transaction() -> None:
    """The live writer. `record_support_outcome` calls it on every outcome that
    maps order lines to an RMA, and the attribution it writes is projected."""
    repository, journal = _repository([{"returnItemId": ITEM_ID, "caseId": CASE_ID, "version": 0}])

    await repository.assign_return_item_to_record(
        ITEM_ID, return_record_id=RECORD_ID, expected_version=0
    )

    _assert_bumped_with(journal, "return_items")


@pytest.mark.asyncio
async def test_updating_a_case_item_bumps_the_case_it_names() -> None:
    """The case id comes off the updated document, not off a parameter -- the
    callers hold an item id and nothing else."""
    repository, journal = _repository([{"returnItemId": ITEM_ID, "caseId": CASE_ID, "version": 0}])

    await repository.update_return_item(ITEM_ID, {"quantity": 2}, expected_version=0)

    _assert_bumped_with(journal, "return_items")


@pytest.mark.asyncio
async def test_a_session_item_bumps_nothing() -> None:
    """`operational_return_items` holds both shapes.

    A session-scoped item is on no `CaseDetail` projection and there would be no
    case id to name, so the bump is conditional on the updated document actually
    carrying one. Bumping unconditionally would need a case that does not exist;
    skipping it for case items is the defect.
    """
    repository, journal = _repository(
        [{"returnItemId": ITEM_ID, "sessionId": "ret-1", "version": 0}]
    )

    await repository.update_return_item(ITEM_ID, {"quantity": 2}, expected_version=0)

    assert journal.to("return_items"), "the item write did not happen"
    assert journal.to("cases") == []


@pytest.mark.asyncio
async def test_a_rejected_item_version_check_never_reaches_the_bump() -> None:
    """The loser of the compare-and-set changed nothing and must move no revision.

    Raised from inside the transaction, so against a real replica set the bump
    rolls back with it. Here the weaker property is asserted for what it is: the
    bump is downstream of the child write, so a write that raises cannot reach
    it. Both directions are needed -- a repair that bumped first would satisfy
    the real-infra test and silently double-count under a fake.
    """
    repository, journal = _repository([{"returnItemId": ITEM_ID, "caseId": CASE_ID, "version": 3}])

    with pytest.raises(ConcurrencyConflictError):
        await repository.update_return_item(ITEM_ID, {"quantity": 2}, expected_version=0)

    assert journal.to("cases") == []


# ---------------------------------------------------------------------------
# `return_support/service.py`: the Support action (D28)
# ---------------------------------------------------------------------------


def _work_item(**overrides: Any) -> dict[str, Any]:
    """A Channel B work item as `open_case_thread` writes one.

    Complete rather than minimal: `apply_action` returns a `SupportWorkItemView`
    built from the stored document, so a partial fixture would fail validation
    before reaching the assertion about the bump.
    """
    now = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
    document: dict[str, Any] = {
        "_id": WORK_ITEM_ID,
        "caseId": CASE_ID,
        "sessionId": None,
        "threadId": "TH-1",
        "status": SupportWorkItemStatus.NEW.value,
        "priority": "NORMAL",
        "queue": "RETURNS_SUPPORT",
        "subject": f"Return request for case {CASE_ID}",
        "requestSnapshotDigest": "0" * 64,
        "slaDueAt": now,
        "version": 0,
        "createdAt": now,
        "updatedAt": now,
    }
    document.update(overrides)
    return document


def _service(items: list[dict[str, Any]]) -> tuple[ReturnSupportService, _Journal]:
    """The shipped service with its two collections and its repository stubbed.

    `bump_case_revision` is the real one, off a real `OperationalRepository`, so
    what is asserted is the mechanism `case_repository.py` established rather
    than a second one this file invented.
    """
    journal = _Journal()
    service = ReturnSupportService.__new__(ReturnSupportService)
    service._work_items = _FakeCollection(  # type: ignore[assignment]
        "support_work_items", journal, items
    )
    repository = OperationalRepository.__new__(OperationalRepository)
    repository.cases = _FakeCollection("cases", journal)  # type: ignore[assignment]
    service._repository = repository
    service._client = _FakeClient(journal)  # type: ignore[assignment]
    return service, journal


def _action(
    action: SupportAction = SupportAction.ACKNOWLEDGE, **fields: Any
) -> SupportActionRequest:
    return SupportActionRequest(
        action=action, expectedVersion=0, reason="because Support said so", **fields
    )


@pytest.mark.asyncio
async def test_a_support_action_on_a_case_bumps_the_revision_in_one_transaction() -> None:
    """`status` is projected, so this changed the projection.

    Acknowledging is the smallest action there is and it still moves
    `SupportProjection.status`; if the smallest one has to bump, every branch
    does.
    """
    service, journal = _service([_work_item()])

    await service.apply_action(WORK_ITEM_ID, _action(), actor_id="support-1")

    _assert_bumped_with(journal, "support_work_items")


@pytest.mark.asyncio
async def test_assigning_and_completing_bump_too() -> None:
    """`assignedTo` and `completedAt` are the other two projected fields.

    Parametrised by hand rather than by decorator so each case can start from a
    status the transition table actually allows.
    """
    for action, status, extra in (
        (SupportAction.ASSIGN, SupportWorkItemStatus.NEW, {"assignee": "support-2"}),
        (SupportAction.COMPLETE, SupportWorkItemStatus.READY_FOR_ASSOCIATE, {}),
    ):
        service, journal = _service([_work_item(status=status.value)])

        await service.apply_action(WORK_ITEM_ID, _action(action, **extra), actor_id="support-1")

        _assert_bumped_with(journal, "support_work_items")


@pytest.mark.asyncio
async def test_a_support_action_on_a_session_work_item_bumps_no_case() -> None:
    """The legacy half of the collection. It has no case to invalidate."""
    service, journal = _service([_work_item(caseId=None, sessionId=None)])

    # `sessionId: None` with no case is the degenerate document; the assertion
    # is only that nothing tried to bump a case that is not there.
    await service.apply_action(WORK_ITEM_ID, _action(), actor_id="support-1")

    assert journal.to("support_work_items"), "the work item write did not happen"
    assert journal.to("cases") == []


@pytest.mark.asyncio
async def test_a_refused_support_action_never_reaches_the_bump() -> None:
    """A caller that lost the compare-and-set changed nothing.

    The `find_one_and_update` matches on `(version, status)`, so a stale
    `expectedVersion` returns `None` and raises from inside the transaction --
    taking the bump with it rather than advertising a change that never
    happened.
    """
    service, journal = _service([_work_item(version=7)])

    with pytest.raises(ConcurrencyConflictError):
        await service.apply_action(WORK_ITEM_ID, _action(), actor_id="support-1")

    assert journal.to("cases") == []
