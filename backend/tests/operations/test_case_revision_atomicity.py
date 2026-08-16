"""Plan sect. 6.5's invariant, at the wiring level, in the normal suite.

> Any write that can change the `CaseDetail` projection must, in the same
> transaction, bump `case.revision` and set `case.updatedAt`.

The behaviour this guards is settled against a real replica set in
`test_case_revision_atomicity_real_infra.py` -- a fake collection cannot roll
anything back, so it cannot prove atomicity. What it *can* prove, and what the
sibling module cannot prove cheaply on every run, is the shape of the calls:
that each writer opens one session, and that the child write and the case bump
both carry **that** session rather than one of them going out on its own.

That distinction is the whole defect. Every one of these writers already
changed the projection; what none of them did was move the revision with it,
and the tempting repair -- a second, best-effort `update_one` afterwards -- is
the one plan sect. 6.5 explicitly refuses. A test that only asserted "the
revision moved" would pass on that repair. This one does not.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import pytest
from pymongo.errors import DuplicateKeyError

from return_platform.operations.case_repository import CaseRepository
from return_platform.operations.errors import ConcurrencyConflictError
from return_platform.operations.models import FactAcquisition, FactChannel

CASE_ID = "CASE-1"
RECORD_ID = "RR-1"


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
    """Records what it was asked to do, and under which session.

    `find_one_and_update` answers from `documents` so `update_return_record`
    can reach its bump; everything else answers the minimum its caller reads.
    """

    def __init__(
        self,
        name: str,
        journal: _Journal,
        *,
        documents: list[dict[str, Any]] | None = None,
        insert_raises: Exception | None = None,
    ) -> None:
        self._name = name
        self._journal = journal
        self._documents = documents if documents is not None else []
        self._insert_raises = insert_raises

    async def insert_one(self, document: Mapping[str, Any], session: Any = None) -> None:
        self._journal.writes.append(_Write(self._name, "insert_one", None, document, session))
        if self._insert_raises is not None:
            raise self._insert_raises
        self._documents.append(dict(document))

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


class _Transactional:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def with_transaction(self, callback: Any) -> Any:
        return await callback(self._session)


def _repository(
    *,
    records: list[dict[str, Any]] | None = None,
    fact_insert_raises: Exception | None = None,
) -> tuple[CaseRepository, _Journal]:
    journal = _Journal()
    repository = CaseRepository()
    repository.cases = _FakeCollection("cases", journal)  # type: ignore[assignment]
    repository.case_facts = _FakeCollection(  # type: ignore[assignment]
        "case_facts", journal, insert_raises=fact_insert_raises
    )
    repository.return_records = _FakeCollection(  # type: ignore[assignment]
        "return_records", journal, documents=records
    )
    repository.return_items = _FakeCollection("return_items", journal)  # type: ignore[assignment]
    repository._client = _FakeClient(journal)  # type: ignore[assignment]
    return repository, journal


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


async def _append_fact(repository: CaseRepository) -> None:
    await repository.append_case_fact(
        fact_id="F-1",
        case_id=CASE_ID,
        fact_name="return_reason",
        value="damaged",
        agent_id="agent",
        channel=FactChannel.CHANNEL_A,
        acquisition_method=FactAcquisition.STATED,
    )


@pytest.mark.asyncio
async def test_appending_a_fact_bumps_the_revision_in_the_same_transaction() -> None:
    repository, journal = _repository()

    await _append_fact(repository)

    _assert_bumped_with(journal, "case_facts")


@pytest.mark.asyncio
async def test_creating_a_return_record_bumps_the_revision_in_the_same_transaction() -> None:
    repository, journal = _repository()

    await repository.create_return_record(
        return_record_id=RECORD_ID, case_id=CASE_ID, return_reference="RMA-1"
    )

    _assert_bumped_with(journal, "return_records")


@pytest.mark.asyncio
async def test_creating_a_case_return_item_bumps_the_revision_in_the_same_transaction() -> None:
    repository, journal = _repository()

    await repository.create_case_return_item(
        return_item_id="ITEM-1",
        case_id=CASE_ID,
        return_record_id=None,
        order_line_reference="L1",
    )

    _assert_bumped_with(journal, "return_items")


@pytest.mark.asyncio
async def test_updating_a_return_record_bumps_the_case_it_names() -> None:
    """The case id comes off the updated document, not off a parameter.

    Callers hold a record id and nothing else, so a signature that demanded
    both would have two sources for one truth and no way to notice them
    disagreeing.
    """
    repository, journal = _repository(
        records=[{"returnRecordId": RECORD_ID, "caseId": CASE_ID, "version": 0}]
    )

    await repository.update_return_record(
        RECORD_ID, {"labelReference": "LBL-1"}, expected_version=0
    )

    _assert_bumped_with(journal, "return_records")


@pytest.mark.asyncio
async def test_recording_a_parcel_bumps_the_case_the_record_names() -> None:
    """The seventh writer (D1), enrolled like the six before it.

    `record_case_shipment` puts a carrier's parcel on the RMA's return record,
    which is `returnRecords[].shipments` and therefore `awaiting` and
    `businessComplete` -- the largest change to a case the read model can make
    short of the RMA itself appearing. Like `update_return_record`, the case id
    is read off the record inside the transaction rather than taken as a
    parameter: the caller holds a record id from `dbo.return_record` and
    re-deriving it here is what keeps the two from disagreeing.
    """
    repository, journal = _repository(
        records=[{"returnRecordId": RECORD_ID, "caseId": CASE_ID, "version": 0}]
    )

    recorded = await repository.record_case_shipment(
        return_record_id=RECORD_ID, tracking_reference="1Z-AAA", carrier="UPS"
    )

    assert recorded is True
    _assert_bumped_with(journal, "return_records")


@pytest.mark.asyncio
async def test_a_failed_child_write_never_reaches_the_bump() -> None:
    """A replayed fact must not invalidate a single client's cache.

    Against a real replica set the transaction is what makes this true -- the
    bump would roll back even if it had already been issued. Here the weaker
    property is asserted for what it is: the bump is downstream of the child
    write, so a write that raises does not reach it. Both directions are
    needed; a repair that bumped first and inserted second would satisfy the
    real-infra test and silently double-count under a fake.
    """
    repository, journal = _repository(fact_insert_raises=DuplicateKeyError("replayed factId"))

    with pytest.raises(DuplicateKeyError):
        await _append_fact(repository)

    assert journal.to("cases") == []


@pytest.mark.asyncio
async def test_a_rejected_version_check_never_reaches_the_bump() -> None:
    """The loser of the record's compare-and-set changed nothing."""
    repository, journal = _repository(
        records=[{"returnRecordId": RECORD_ID, "caseId": CASE_ID, "version": 3}]
    )

    with pytest.raises(ConcurrencyConflictError):
        await repository.update_return_record(RECORD_ID, {"status": "ISSUED"}, expected_version=0)

    assert journal.to("cases") == []


@pytest.mark.asyncio
async def test_the_bump_helper_cannot_be_called_without_a_session() -> None:
    """`session` is keyword-required and has no default, on purpose.

    The invariant is not "the revision moves eventually". A writer with no
    transaction to enrol in cannot satisfy plan sect. 6.5, and must not be able
    to call this and believe it has.
    """
    repository, _ = _repository()

    with pytest.raises(TypeError):
        await repository.bump_case_revision(CASE_ID)  # type: ignore[call-arg]
