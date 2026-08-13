"""A multi-item return, through the API, without artifacts crossing records.

`ReturnSessionView` carries one `returnReference`, one `trackingReference`, one
`labelReference` and one `bayReference`. A return of five items can produce two
RMAs with different labels going to different locations, and that shape cannot
be said in singular scalars -- nor in per-item ones, since one RMA covers many
items.

The three scenarios below are the ones that distinguish a working model from a
plausible-looking one, and the assertion that matters in all three is negative:
**no artifact appears under a record it does not belong to.** A flat response
would leave that to the client to get right, which is how the console ended up
joining an order reference in the browser in the first place.

Shaped as a projection test over the real repository rather than an HTTP test:
what is under test is the grouping, and standing up the app would add a lifespan
and an auth surface without exercising anything more of it.
"""

from __future__ import annotations

import os
import uuid
from typing import Any
from urllib.parse import quote

import pytest
import pytest_asyncio
from pydantic import SecretStr
from pymongo import AsyncMongoClient

from return_platform.configuration.settings import Settings
from return_platform.operations.repository import OperationalRepository

# Live infrastructure: this module opens a real MongoDB client. It is not named
# `*_real_infra.py`, so this marker is what keeps it out of the default run
# and inside `scripts/dev/run_real_infra_suite.sh`.
pytestmark = [pytest.mark.asyncio(loop_scope="module"), pytest.mark.live_infra]


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Required test environment variable is not set: {name}")
    return value


def _mongo_dsn() -> str:
    """`directConnection=true` -- see `test_return_record_sync_real_infra._mongo_dsn`."""
    username = quote(_required_env("MONGO_ROOT_USERNAME"), safe="")
    password = quote(_required_env("MONGO_ROOT_PASSWORD"), safe="")
    host = os.getenv("PLATFORM_TEST_MONGO_HOST", "localhost")
    return f"mongodb://{username}:{password}@{host}:27017/return_platform?authSource=admin&directConnection=true"


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def repository() -> Any:
    database = f"case_detail_test_{uuid.uuid4().hex[:12]}"
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(_mongo_dsn())
    settings = Settings(
        environment="test",
        mongo_dsn=SecretStr(_mongo_dsn()),
        mongo_database=database,
        source_mongo_database=database,
    )
    made = OperationalRepository(client, settings)
    await made.ensure_indexes()
    try:
        yield made
    finally:
        await client.drop_database(database)
        await client.close()


async def _case(repository: OperationalRepository) -> str:
    case = await repository.create_case(
        case_id=str(uuid.uuid4()),
        tenant_id="tenant-a",
        principal_id=f"associate-{uuid.uuid4().hex[:8]}",
    )
    return str(case["caseId"])


async def _record(
    repository: OperationalRepository, case_id: str, reference: str, **artifacts: Any
) -> str:
    record = await repository.create_return_record(
        return_record_id=str(uuid.uuid4()), case_id=case_id, return_reference=reference
    )
    if artifacts:
        await repository.update_return_record(
            record["returnRecordId"], artifacts, expected_version=0
        )
    return str(record["returnRecordId"])


async def _item(
    repository: OperationalRepository, case_id: str, record_id: str | None, line: str
) -> str:
    item = await repository.create_case_return_item(
        return_item_id=str(uuid.uuid4()),
        case_id=case_id,
        return_record_id=record_id,
        order_line_reference=line,
    )
    return str(item["returnItemId"])


async def _grouped(
    repository: OperationalRepository, case_id: str
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """The same grouping `GET /api/cases/{id}` performs, keyed by RMA."""
    items = await repository.list_case_return_items(case_id)
    records = await repository.list_return_records(case_id)
    by_record: dict[str, dict[str, Any]] = {}
    for record in records:
        record_id = str(record["returnRecordId"])
        by_record[str(record["returnReference"])] = {
            "record": record,
            "lines": sorted(
                str(item["orderLineId"])
                for item in items
                if item.get("returnRecordId") == record_id
            ),
        }
    unassigned = sorted(
        str(item["orderLineId"]) for item in items if not item.get("returnRecordId")
    )
    return by_record, unassigned


async def test_two_items_one_rma_one_label(repository: OperationalRepository) -> None:
    case_id = await _case(repository)
    record_id = await _record(
        repository, case_id, "RMA-1", labelReference="LBL-1", returnLocation="DOCK-3"
    )
    await _item(repository, case_id, record_id, "L1")
    await _item(repository, case_id, record_id, "L2")

    grouped, unassigned = await _grouped(repository, case_id)

    assert list(grouped) == ["RMA-1"]
    assert grouped["RMA-1"]["lines"] == ["L1", "L2"]
    assert grouped["RMA-1"]["record"]["labelReference"] == "LBL-1"
    assert not unassigned


async def test_two_items_two_rmas_two_labels(repository: OperationalRepository) -> None:
    case_id = await _case(repository)
    first = await _record(repository, case_id, "RMA-1", labelReference="LBL-1")
    second = await _record(repository, case_id, "RMA-2", labelReference="LBL-2")
    await _item(repository, case_id, first, "L1")
    await _item(repository, case_id, second, "L2")

    grouped, _ = await _grouped(repository, case_id)

    assert grouped["RMA-1"]["lines"] == ["L1"]
    assert grouped["RMA-2"]["lines"] == ["L2"]
    # The negative assertion: labels do not cross.
    assert grouped["RMA-1"]["record"]["labelReference"] == "LBL-1"
    assert grouped["RMA-2"]["record"]["labelReference"] == "LBL-2"


async def test_three_items_two_rmas_different_return_locations(
    repository: OperationalRepository,
) -> None:
    """The shape the singular scalars get most obviously wrong.

    Two RMAs going to two different places. A case-level `returnLocation` would
    have to pick one, and whichever it picked would send half the shipment to
    the wrong dock.
    """
    case_id = await _case(repository)
    first = await _record(
        repository, case_id, "RMA-1", returnLocation="DOCK-3", trackingReference="TRK-1"
    )
    second = await _record(
        repository, case_id, "RMA-2", returnLocation="BAY-12", trackingReference="TRK-2"
    )
    await _item(repository, case_id, first, "L1")
    await _item(repository, case_id, first, "L2")
    await _item(repository, case_id, second, "L3")

    grouped, unassigned = await _grouped(repository, case_id)

    assert grouped["RMA-1"]["lines"] == ["L1", "L2"]
    assert grouped["RMA-2"]["lines"] == ["L3"]
    assert grouped["RMA-1"]["record"]["returnLocation"] == "DOCK-3"
    assert grouped["RMA-2"]["record"]["returnLocation"] == "BAY-12"
    assert grouped["RMA-1"]["record"]["trackingReference"] == "TRK-1"
    assert grouped["RMA-2"]["record"]["trackingReference"] == "TRK-2"
    assert not unassigned


async def test_items_named_before_support_replies_are_unassigned_not_guessed(
    repository: OperationalRepository,
) -> None:
    """The associate names lines before Support says how many RMAs they become.

    Folding those into the first record would render as fact something nobody
    has decided.
    """
    case_id = await _case(repository)
    record_id = await _record(repository, case_id, "RMA-1")
    await _item(repository, case_id, record_id, "L1")
    await _item(repository, case_id, None, "L2")

    grouped, unassigned = await _grouped(repository, case_id)

    assert grouped["RMA-1"]["lines"] == ["L1"]
    assert unassigned == ["L2"]


async def test_an_item_moves_to_its_record_once_support_says_which(
    repository: OperationalRepository,
) -> None:
    case_id = await _case(repository)
    record_id = await _record(repository, case_id, "RMA-1")
    item_id = await _item(repository, case_id, None, "L1")

    await repository.assign_return_item_to_record(
        item_id, return_record_id=record_id, expected_version=0
    )

    grouped, unassigned = await _grouped(repository, case_id)
    assert grouped["RMA-1"]["lines"] == ["L1"]
    assert not unassigned
