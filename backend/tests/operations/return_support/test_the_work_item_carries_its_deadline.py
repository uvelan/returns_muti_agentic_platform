"""`open_case_thread` writes the deadline it is given, or the desk's own (D2).

The activity side of this is asserted in `tests/policy/test_case_policy_gate.py`
-- that the delivery-claim reporting window is read from the case and handed
over rather than recomputed. What is asserted here is the other half, on the
shipped writer: that a supplied deadline actually reaches the stored
`slaDueAt`, and that supplying none still produces the acknowledgement SLA the
ordinary path has always had.

Both halves are needed. Before this, the activity could hand over anything it
liked and the writer would overwrite it with `now + sla_minutes`, which is
precisely the shape D2 had: a value computed correctly and then discarded.

WHY THE COLLECTIONS ARE DOUBLES
-------------------------------
The same division `test_support_writers_hold_the_revision_invariant.py` draws.
Durability, the transaction and the unique indexes belong to a real replica set
and are exercised in `test_case_support_thread_real_infra.py`. What is settled
here, on every run, is which value lands in the document -- a question a fake
collection answers exactly as well as a real one, and one no live-infra-gated
test should be the only place to ask.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from return_platform.operations.return_support.service import ReturnSupportService

CASE_ID = "CASE-1"
NOW = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
DESK_SLA_MINUTES = 5


class _Collection:
    """Insert-only, and remembers what it was handed."""

    def __init__(self) -> None:
        self.documents: list[dict[str, Any]] = []

    async def find_one(self, _query: Any, **_options: Any) -> dict[str, Any] | None:
        return None

    async def insert_one(self, document: dict[str, Any], **_options: Any) -> None:
        self.documents.append(document)


class _Session:
    async def with_transaction(
        self, callback: Callable[[Any], Coroutine[Any, Any, Any]]
    ) -> Any:
        return await callback(self)


class _Client:
    @asynccontextmanager
    async def start_session(self) -> AsyncIterator[_Session]:
        yield _Session()


def _service() -> tuple[ReturnSupportService, _Collection]:
    """The shipped service with its two collections and its configuration stubbed."""
    service = ReturnSupportService.__new__(ReturnSupportService)
    work_items = _Collection()
    service._work_items = work_items  # type: ignore[assignment]
    service._messages = _Collection()  # type: ignore[assignment]
    service._client = _Client()  # type: ignore[assignment]
    service._config = SimpleNamespace(  # type: ignore[assignment]
        workflow=SimpleNamespace(sla_minutes={"support_acknowledgement": DESK_SLA_MINUTES})
    )
    return service, work_items


async def _open(service: ReturnSupportService, **overrides: Any) -> None:
    arguments: dict[str, Any] = {
        "case_id": CASE_ID,
        "tenant_id": "acme",
        "principal_id": "associate-1",
        "support_draft": "Please raise the RMA.",
        "idempotency_key": f"support:{CASE_ID}",
    }
    await service.open_case_thread(**{**arguments, **overrides})


@pytest.mark.asyncio
async def test_a_supplied_deadline_is_the_work_items_deadline() -> None:
    """The delivery-claim reporting window, landing where the queue reads it."""
    service, work_items = _service()
    reporting_deadline = datetime(2026, 8, 18, 21, 0, tzinfo=UTC)

    await _open(service, sla_due_at=reporting_deadline)

    (document,) = work_items.documents
    assert document["slaDueAt"] == reporting_deadline


@pytest.mark.asyncio
async def test_no_supplied_deadline_leaves_the_desk_sla_standing() -> None:
    """The ordinary path is untouched: absence means the acknowledgement SLA."""
    service, work_items = _service()
    before = datetime.now(UTC)

    await _open(service)

    (document,) = work_items.documents
    due: datetime = document["slaDueAt"]
    assert before + timedelta(minutes=DESK_SLA_MINUTES) <= due
    assert due <= datetime.now(UTC) + timedelta(minutes=DESK_SLA_MINUTES)


@pytest.mark.asyncio
async def test_a_naive_deadline_is_refused_rather_than_stored() -> None:
    """A naive instant stored beside aware ones sorts as if it were UTC.

    Every other writer on this collection produces aware instants and the queue
    is ordered on `slaDueAt`, so guessing a zone here would silently reorder a
    human's work queue. Refused at the boundary instead, before anything is
    written.
    """
    service, work_items = _service()

    with pytest.raises(ValueError, match="timezone-aware"):
        await _open(service, sla_due_at=datetime(2026, 8, 18, 17, 0))

    assert work_items.documents == [], "a refused deadline still opened a thread"


@pytest.mark.asyncio
async def test_a_composed_subject_is_the_work_items_subject() -> None:
    """The row a human picks this return out of a queue by.

    Composed by the caller, from the same facts as the message, because this
    service is handed a finished message and an id and has no idea what the
    return is about.
    """
    service, work_items = _service()

    await _open(service, subject="Return CQ800002 line 4 · 1 HDL PRESS BAL · DUANE HOPKINS")

    (document,) = work_items.documents
    assert document["subject"] == "Return CQ800002 line 4 · 1 HDL PRESS BAL · DUANE HOPKINS"


@pytest.mark.asyncio
async def test_no_subject_leaves_the_case_id_fallback_standing() -> None:
    """What every row used to say, kept for a caller that composes nothing."""
    service, work_items = _service()

    await _open(service)

    (document,) = work_items.documents
    assert document["subject"] == f"Return request for case {CASE_ID}"


@pytest.mark.asyncio
async def test_a_blank_subject_is_not_a_blank_row() -> None:
    """An empty string is the shape a failed composition takes, and a queue row
    with nothing in it is unclickable. Absent and blank mean the same thing."""
    service, work_items = _service()

    await _open(service, subject="   ")

    (document,) = work_items.documents
    assert document["subject"] == f"Return request for case {CASE_ID}"
