"""Reading the confirmed order's date, and every way that can come to nothing.

The resolver's contract is narrow and the failure semantics are the interesting
part: an order this cannot date must reach the evaluator as "no basis", never as
an evaluation error and never as a guessed date. Both alternatives are worse
than a review -- the first parks a return for a supervisor with nothing to act
on, and the second decides a 30-day boundary from a number nobody published.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from return_platform.operations.seed_manifest import SOURCE_SALES_DATASET
from return_platform.workflows.case_order_date import resolve_confirmed_order_purchase_date

pytestmark = pytest.mark.asyncio

ORDER_DATE_PATHS = ("salesHdr.salesHdrData.orderDate",)
NEW_YORK = ZoneInfo("America/New_York")

_DATASET = Path(__file__).resolve().parents[2] / "fixtures" / "reference_dataset" / "salesInv1.json"


@pytest.fixture(scope="module")
def cq363350() -> dict[str, Any]:
    orders = json.loads(_DATASET.read_text(encoding="utf-8"))
    return next(order for order in orders if order["_id"] == "CHARLOTTE*CQ363350")


class _Collection:
    def __init__(self, documents: list[dict[str, Any]] | None = None) -> None:
        self.documents = documents or []
        self.filters: list[dict[str, Any]] = []

    async def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        self.filters.append(query)
        for document in self.documents:
            if all(_at(document, key) == value for key, value in query.items()):
                return document
        return None


def _at(document: Any, dotted: str) -> Any:
    current = document
    for segment in dotted.split("."):
        if not isinstance(current, dict) or segment not in current:
            return None
        current = current[segment]
    return current


class _Repository:
    def __init__(
        self,
        *,
        case: dict[str, Any] | None,
        collection: _Collection | None = None,
        case_error: Exception | None = None,
        dataset_error: Exception | None = None,
    ) -> None:
        self._case = case
        self.collection = collection or _Collection()
        self._case_error = case_error
        self._dataset_error = dataset_error
        self.datasets: list[str] = []

    async def get_case(self, case_id: str) -> dict[str, Any] | None:
        if self._case_error is not None:
            raise self._case_error
        return self._case

    async def source_dataset(self, dataset: str) -> Any:
        self.datasets.append(dataset)
        if self._dataset_error is not None:
            raise self._dataset_error
        return self.collection


async def _resolve(repository: _Repository, paths: tuple[str, ...] = ORDER_DATE_PATHS):
    return await resolve_confirmed_order_purchase_date(
        repository,  # type: ignore[arg-type]
        case_id="case-1",
        order_date_paths=paths,
    )


# ---------------------------------------------------------------------------
# The happy path, against the real document
# ---------------------------------------------------------------------------


async def test_the_confirmed_order_dates_the_window(cq363350: dict[str, Any]) -> None:
    repository = _Repository(
        case={"confirmedOrderReference": "CQ363350"},
        collection=_Collection([cq363350]),
    )

    resolved = await _resolve(repository)

    assert resolved is not None
    assert resolved.astimezone(NEW_YORK).date() == datetime(2025, 10, 14).date()


async def test_the_order_is_looked_up_on_the_indexed_key(cq363350: dict[str, Any]) -> None:
    """The same filter `OperationalRepository.source_order` uses, on the field
    the sales collection is uniquely indexed on."""
    repository = _Repository(
        case={"confirmedOrderReference": " CQ363350 "},
        collection=_Collection([cq363350]),
    )

    await _resolve(repository)

    assert repository.datasets == [SOURCE_SALES_DATASET]
    assert repository.collection.filters == [{"salesHdrEventData.orderId": "CQ363350"}]


# ---------------------------------------------------------------------------
# Every way it comes to nothing, and none of them raises
# ---------------------------------------------------------------------------


async def test_a_release_that_binds_no_path_leaves_the_window_undated(
    cq363350: dict[str, Any],
) -> None:
    repository = _Repository(
        case={"confirmedOrderReference": "CQ363350"}, collection=_Collection([cq363350])
    )

    assert await _resolve(repository, paths=()) is None
    assert repository.datasets == []


async def test_a_case_that_has_confirmed_no_order_is_not_looked_up() -> None:
    """Discovery is not confirmation. There is no order to date yet, and asking
    the source for one keyed on `None` would be a read with no meaning."""
    repository = _Repository(case={"confirmedOrderReference": None})

    assert await _resolve(repository) is None
    assert repository.datasets == []


@pytest.mark.parametrize("case", [None, {}, {"confirmedOrderReference": "   "}])
async def test_a_case_with_no_usable_reference_resolves_to_nothing(
    case: dict[str, Any] | None,
) -> None:
    assert await _resolve(_Repository(case=case)) is None


async def test_an_order_the_source_does_not_hold_resolves_to_nothing() -> None:
    repository = _Repository(
        case={"confirmedOrderReference": "CQ000000"}, collection=_Collection([])
    )

    assert await _resolve(repository) is None


async def test_an_order_carrying_no_date_resolves_to_nothing() -> None:
    repository = _Repository(
        case={"confirmedOrderReference": "CQ363350"},
        collection=_Collection(
            [{"salesHdrEventData": {"orderId": "CQ363350"}, "salesHdr": {"salesHdrData": {}}}]
        ),
    )

    assert await _resolve(repository) is None


@pytest.mark.parametrize(
    "repository",
    [
        _Repository(
            case={"confirmedOrderReference": "CQ363350"},
            dataset_error=RuntimeError("source binding unresolved"),
        ),
        _Repository(case=None, case_error=RuntimeError("mongo is unavailable")),
    ],
    ids=["source-unreadable", "case-unreadable"],
)
async def test_a_failed_read_is_an_undated_window_and_not_a_failed_evaluation(
    repository: _Repository,
) -> None:
    """The evaluation still runs and still answers.

    An `EVALUATION_FAILED` here would park the case for a supervisor who has
    nothing to do about a source read, when the correct answer -- review,
    because the purchase date is unknown -- is one the evaluator already gives.
    """
    assert await _resolve(repository) is None
