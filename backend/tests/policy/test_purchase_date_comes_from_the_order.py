"""Where the return window's date comes from, and where it must not.

`case_policy_facts` has always refused to read the conversation's
`approximate_purchase_date` as `purchase_date`, on the grounds that "a window
boundary decided from a date the associate described as approximate is a
boundary nobody can defend". That reasoning is intact and is re-asserted here.

The confirmed order is the other thing. It is the authoritative record of when
the purchase happened, there is nothing for the associate to state about it, and
it is on every order in the extract. These tests run against the real reference
document for `CQ363350` -- not a hand-written fixture -- so the field choice is
checked against the data rather than against an assumption about it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from return_platform.workflows.case_policy_facts import (
    CONFIRMED_ORDER_PURCHASE_DATE,
    assemble_policy_evaluation_input,
    purchase_date_from_confirmed_order,
)

#: The path the release binds. Asserted against the packaged configuration in
#: `test_window_policy_is_configuration.py`; repeated here as a literal so this
#: module tests the resolver rather than the release.
ORDER_DATE_PATHS = ("salesHdr.salesHdrData.orderDate",)

NEW_YORK = ZoneInfo("America/New_York")
NOW = datetime(2025, 11, 1, 15, 0, tzinfo=UTC)

_DATASET = Path(__file__).resolve().parents[2] / "fixtures" / "reference_dataset" / "salesInv1.json"


@pytest.fixture(scope="module")
def orders() -> list[dict[str, Any]]:
    return json.loads(_DATASET.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def cq363350(orders: list[dict[str, Any]]) -> dict[str, Any]:
    return next(order for order in orders if order["_id"] == "CHARLOTTE*CQ363350")


# ---------------------------------------------------------------------------
# The field choice, against the real extract
# ---------------------------------------------------------------------------


def test_the_real_order_carries_a_real_date(cq363350: dict[str, Any]) -> None:
    resolved = purchase_date_from_confirmed_order(cq363350, paths=ORDER_DATE_PATHS)

    assert resolved is not None
    assert resolved.astimezone(UTC).date() == datetime(2025, 10, 14, tzinfo=UTC).date()


def test_every_order_in_the_extract_carries_one(orders: list[dict[str, Any]]) -> None:
    """The reason `orderDate` was chosen over its neighbours: it is always there.

    If this ever stops being true the resolver answers `None` and those cases
    review on `PURCHASE_DATE_UNKNOWN`, which is correct -- but it would be a
    material change in how many returns the window can decide, and it should
    fail a test rather than show up as a queue.
    """
    missing = [
        order["_id"]
        for order in orders
        if purchase_date_from_confirmed_order(order, paths=ORDER_DATE_PATHS) is None
    ]

    assert missing == []


def test_the_invoice_date_is_not_the_purchase_date(orders: list[dict[str, Any]]) -> None:
    """Why there is no fallback to a neighbouring field.

    `invoiceDate` disagrees with `orderDate` on most of the extract and is
    missing from one order outright. Reading it when `orderDate` is absent would
    answer a 30-day boundary with a date that is not the purchase, which is the
    fabrication this package exists to prevent.
    """
    order_dates = [
        purchase_date_from_confirmed_order(order, paths=ORDER_DATE_PATHS) for order in orders
    ]
    invoice_dates = [
        purchase_date_from_confirmed_order(
            order, paths=("salesHdr.salesHdrData.invoiceDate",)
        )
        for order in orders
    ]

    assert sum(invoice is None for invoice in invoice_dates) == 1
    disagreements = sum(
        1
        for ordered, invoiced in zip(order_dates, invoice_dates, strict=True)
        if invoiced is not None and ordered != invoiced
    )
    assert disagreements > len(orders) // 2


def test_the_event_data_path_the_search_stack_reads_is_never_populated(
    orders: list[dict[str, Any]],
) -> None:
    """`associate_flow` reads `salesHdrEventData.orderDate`. No order has it.

    Recorded here because it is why this module binds the header path instead,
    and because a release that "fixed" the binding by copying the other spelling
    would silently undate every window.
    """
    resolved = [
        purchase_date_from_confirmed_order(order, paths=("salesHdrEventData.orderDate",))
        for order in orders
    ]

    assert resolved == [None] * len(orders)


# ---------------------------------------------------------------------------
# A date the source wrote as a date keeps its day
# ---------------------------------------------------------------------------


def test_a_midnight_utc_source_date_keeps_its_calendar_day_in_the_business_zone(
    cq363350: dict[str, Any],
) -> None:
    """The source stores `2025-10-14T00:00:00.000Z` and means the 14th.

    Read literally that instant is the 13th in `America/New_York`, which is the
    zone the release declares, and the evaluator counts *local calendar days* --
    so a literal reading would quietly cost every customer a day of their
    window.
    """
    resolved = purchase_date_from_confirmed_order(cq363350, paths=ORDER_DATE_PATHS)

    assert resolved is not None
    assert resolved.astimezone(NEW_YORK).date() == datetime(2025, 10, 14).date()


def test_an_instant_that_carries_a_time_of_day_is_left_alone() -> None:
    """The correction applies to a date written as a date, and to nothing else."""
    document = {"h": {"d": datetime(2025, 10, 14, 3, 30, tzinfo=UTC)}}

    assert purchase_date_from_confirmed_order(document, paths=("h.d",)) == datetime(
        2025, 10, 14, 3, 30, tzinfo=UTC
    )


def test_mongo_extended_json_is_read_as_the_driver_would() -> None:
    document = {"h": {"d": {"$date": "2025-10-14T00:00:00.000Z"}}}
    resolved = purchase_date_from_confirmed_order(document, paths=("h.d",))

    assert resolved is not None
    assert resolved.astimezone(NEW_YORK).date() == datetime(2025, 10, 14).date()


# ---------------------------------------------------------------------------
# Absence is absence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("document", "paths"),
    [
        (None, ORDER_DATE_PATHS),
        ({}, ORDER_DATE_PATHS),
        ({"salesHdr": {"salesHdrData": {}}}, ORDER_DATE_PATHS),
        ({"salesHdr": {"salesHdrData": {"orderDate": None}}}, ORDER_DATE_PATHS),
        ({"salesHdr": {"salesHdrData": {"orderDate": "not a date"}}}, ORDER_DATE_PATHS),
        ({"salesHdr": {"salesHdrData": {"orderDate": 20251014}}}, ORDER_DATE_PATHS),
        ({"salesHdr": "not a mapping"}, ORDER_DATE_PATHS),
        ({"salesHdr": {"salesHdrData": {"orderDate": "2025-10-14"}}}, ()),
    ],
)
def test_nothing_usable_resolves_to_nothing(
    document: dict[str, Any] | None, paths: tuple[str, ...]
) -> None:
    """Never a guessed date. An order the platform cannot date leaves the window
    undated, and the case reviews."""
    assert purchase_date_from_confirmed_order(document, paths=paths) is None


def test_the_first_path_that_resolves_wins() -> None:
    """Preference lives in the release, not in the resolver."""
    document = {
        "a": {"d": {"$date": "2025-10-14T00:00:00.000Z"}},
        "b": {"d": {"$date": "2025-09-01T00:00:00.000Z"}},
    }

    first = purchase_date_from_confirmed_order(document, paths=("a.d", "b.d"))
    second = purchase_date_from_confirmed_order(document, paths=("z.z", "b.d"))

    assert first is not None
    assert second is not None
    assert first.astimezone(UTC).date() == datetime(2025, 10, 14, tzinfo=UTC).date()
    assert second.astimezone(UTC).date() == datetime(2025, 9, 1, tzinfo=UTC).date()


# ---------------------------------------------------------------------------
# How it reaches the evaluator
# ---------------------------------------------------------------------------


def _fact(name: str, value: Any, *, acquisition: str = "STATED") -> dict[str, Any]:
    return {
        "factId": f"fact-{name}",
        "factName": name,
        "value": value,
        "acquisitionMethod": acquisition,
        "sourceSystem": "CONVERSATION",
        "sourcePath": "CONVERSATION_MESSAGE",
        "observedAt": NOW - timedelta(hours=1),
        "recordedAt": NOW - timedelta(hours=1),
    }


def test_the_order_date_becomes_the_evaluation_basis(cq363350: dict[str, Any]) -> None:
    purchase = purchase_date_from_confirmed_order(cq363350, paths=ORDER_DATE_PATHS)

    assembled = assemble_policy_evaluation_input(
        [], request_date=NOW, confirmed_order_purchase_date=purchase
    )

    assert assembled.facts.purchase_date == purchase
    assert assembled.admitted == (CONFIRMED_ORDER_PURCHASE_DATE,)


def test_the_record_says_which_of_the_two_dates_decided_the_window() -> None:
    """`policy_facts_admitted` on the case must not read the same either way.

    A window decided from the order and one decided from a fact somebody typed
    are different decisions with different defensibility, and an operator who
    cannot tell them apart cannot act on either.
    """
    order_sourced = assemble_policy_evaluation_input(
        [], request_date=NOW, confirmed_order_purchase_date=datetime(2025, 10, 14, tzinfo=UTC)
    )
    log_sourced = assemble_policy_evaluation_input(
        [_fact("purchase_date", "2025-10-14T00:00:00+00:00")], request_date=NOW
    )

    assert order_sourced.admitted == (CONFIRMED_ORDER_PURCHASE_DATE,)
    assert log_sourced.admitted == ("purchase_date",)
    assert order_sourced.facts.purchase_date == log_sourced.facts.purchase_date


def test_the_order_outranks_a_purchase_date_on_the_log() -> None:
    """A source-of-record date beats one that reached the log some other way --
    and the displaced fact is reported rather than dropped, so the disagreement
    is visible instead of resolved in silence."""
    authoritative = datetime(2025, 10, 14, 12, 0, tzinfo=UTC)

    assembled = assemble_policy_evaluation_input(
        [_fact("purchase_date", "2025-09-02T00:00:00+00:00")],
        request_date=NOW,
        confirmed_order_purchase_date=authoritative,
    )

    assert assembled.facts.purchase_date == authoritative
    assert assembled.admitted == (CONFIRMED_ORDER_PURCHASE_DATE,)
    assert assembled.excluded == (("purchase_date", "SUPERSEDED_BY_CONFIRMED_ORDER"),)


def test_omitting_it_leaves_the_log_exactly_as_it_was() -> None:
    assembled = assemble_policy_evaluation_input(
        [_fact("purchase_date", "2025-10-14T00:00:00+00:00")], request_date=NOW
    )

    assert assembled.facts.purchase_date == datetime(2025, 10, 14, tzinfo=UTC)
    assert assembled.admitted == ("purchase_date",)
    assert assembled.excluded == ()


def test_an_approximate_date_is_still_not_a_purchase_date() -> None:
    """Unchanged, and deliberately so. The order made the associate's estimate
    unnecessary; it did not make it admissible."""
    assembled = assemble_policy_evaluation_input(
        [_fact("approximate_purchase_date", "2025-10-01T10:00:00+00:00")],
        request_date=NOW,
    )

    assert assembled.facts.purchase_date is None
    assert "approximate_purchase_date" not in assembled.admitted
