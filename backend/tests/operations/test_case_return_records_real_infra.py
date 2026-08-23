"""RG-08: one case, two RMAs, multiple items — against real SQL Server.

The model this proves could not previously be written down. Every return table
in the platform store is keyed on a *session* (`dbo.return_requests.session_id`
is the primary key and carries one `return_reference`), so "one case, two RMAs"
had no shape in SQL and the canonical case path wrote no SQL at all. Migration
`005_case_return_records.sql` adds that shape; this asserts the properties it
was added for.

What is actually load-bearing here, and why each is its own test:

* Two RMAs on one case stay two rows, each owning its own label, tracking
  reference and return location. RMA-A's label reaching RMA-B is the
  contamination contract C3 exists to forbid.
* A replayed outcome writes the same rows, not a second RMA. Temporal retries
  this activity with identical input.
* An item belongs to one record. A second record claiming a line the first
  already owns must fail the whole transaction, not quietly re-parent the item.
* A failed outcome commits nothing — no half-written case with one of its two
  RMAs missing.

Real infrastructure, so it runs in the compose stack. Not skipped when SQL
Server is absent: a return store that cannot be reached is a failure, and a
silent skip is how a suite reports green for code it never ran.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any

import pymssql
import pytest

from return_platform.configuration.settings import Settings
from return_platform.operations.sql_business_state import (
    CaseReturnRecordsWrite,
    ReturnRecordItemWrite,
    ReturnRecordWrite,
    SQLBusinessStateRepository,
)
from tests.sql_migrations import migration_batches

_CONNECT_DEADLINE_SECONDS = 30
CASE_DATABASE = "return_case_probe"

#: The migrations under test, in the order the CLI applies them. Applied here
#: rather than assumed, so this suite does not depend on whoever last ran the
#: migration CLI against this server.
#:
#: 007 is forward-only over 005: it adds `dbo.return_record.return_method`, the
#: column the completion profile is computed from (D23). Both are applied because
#: `persist_case_return_records` writes that column on every RMA, so a throwaway
#: database built from 005 alone would fail every write here on an invalid column
#: name -- and would be reporting the schema of a release nobody runs.
def _connect(settings: Settings, database: str) -> Any:
    return pymssql.connect(
        server=settings.sqlserver_host,
        port=str(settings.sqlserver_port),
        user=settings.sqlserver_user,
        password=settings.sqlserver_password.get_secret_value(),
        database=database,
        login_timeout=10,
        timeout=30,
        autocommit=True,
    )


def _connect_within_deadline(settings: Settings, database: str) -> Any:
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="case-probe-connect")
    future = executor.submit(_connect, settings, database)
    try:
        return future.result(timeout=_CONNECT_DEADLINE_SECONDS)
    except FutureTimeoutError:
        raise RuntimeError(
            f"SQL Server at {settings.sqlserver_host}:{settings.sqlserver_port} accepted a "
            f"connection but did not complete login within {_CONNECT_DEADLINE_SECONDS}s."
        ) from None
    finally:
        executor.shutdown(wait=False)


def _open_with_retry(settings: Settings, database: str) -> Any:
    """`CREATE DATABASE` returns before the database is connectable.

    See `test_sql_connection_pool_real_infra._open_probe_database` -- the
    refusal is a misleading `Login failed for user 'sa'`.
    """
    import time

    deadline = time.monotonic() + _CONNECT_DEADLINE_SECONDS
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return _connect_within_deadline(settings, database)
        except pymssql.Error as exc:
            last = exc
            time.sleep(0.5)
    raise RuntimeError(f"{database} did not become connectable: {last}")


@pytest.fixture
def case_settings(test_settings: Settings) -> Iterator[Settings]:
    admin = _connect_within_deadline(test_settings, "master")
    with admin:
        with admin.cursor() as cursor:
            cursor.execute(
                "IF DB_ID(%(name)s) IS NULL EXEC('CREATE DATABASE [' + %(name)s + ']')",
                {"name": CASE_DATABASE},
            )

    settings = test_settings.model_copy(update={"sqlserver_database": CASE_DATABASE})
    owner = _open_with_retry(settings, CASE_DATABASE)
    with owner:
        with owner.cursor() as cursor:
            for batch in migration_batches():
                cursor.execute(batch)

    yield settings

    cleanup = _open_with_retry(settings, CASE_DATABASE)
    with cleanup:
        with cleanup.cursor() as cursor:
            # Child-first, so the foreign keys stay satisfied.
            cursor.execute("DELETE FROM dbo.return_record_item")
            cursor.execute("DELETE FROM dbo.return_record")
            cursor.execute("DELETE FROM dbo.return_case")


@pytest.fixture
def repository(case_settings: Settings) -> Iterator[SQLBusinessStateRepository]:
    from return_platform.operations.sql_connection_pool import close_sql_connection_pools

    yield SQLBusinessStateRepository(case_settings)
    close_sql_connection_pools(drain_timeout_seconds=10.0)


def _case_id() -> str:
    return f"case-{uuid.uuid4()}"


def _two_rma_outcome(case_id: str) -> CaseReturnRecordsWrite:
    """One case, two RMAs, two items each — every fulfilment value distinct."""
    return CaseReturnRecordsWrite(
        case_id=case_id,
        tenant_id="tenant-1",
        principal_id="associate-1",
        order_reference="ORD-RG08",
        records=(
            ReturnRecordWrite(
                return_record_id=f"{case_id}-rec-a",
                return_reference="RMA-A",
                label_reference="LABEL-A",
                tracking_reference="TRACK-A",
                return_location="LOC-A",
                shipping_instruction_reference="SHIP-A",
                return_method="PREPAID_PARCEL",
                items=(
                    ReturnRecordItemWrite(
                        return_item_id=f"{case_id}-item-a1",
                        order_line_id="LINE-1",
                        quantity=2,
                        product_id="SKU-1",
                        reason_code="DAMAGED",
                    ),
                    ReturnRecordItemWrite(
                        return_item_id=f"{case_id}-item-a2",
                        order_line_id="LINE-2",
                        quantity=1,
                        product_id="SKU-2",
                        reason_code="DAMAGED",
                    ),
                ),
            ),
            ReturnRecordWrite(
                return_record_id=f"{case_id}-rec-b",
                return_reference="RMA-B",
                label_reference="LABEL-B",
                tracking_reference="TRACK-B",
                return_location="LOC-B",
                shipping_instruction_reference="SHIP-B",
                return_method="CUSTOMER_KEEP",
                items=(
                    ReturnRecordItemWrite(
                        return_item_id=f"{case_id}-item-b1",
                        order_line_id="LINE-3",
                        quantity=5,
                        product_id="SKU-3",
                        reason_code="WRONG_ITEM",
                    ),
                ),
            ),
        ),
    )


@pytest.mark.asyncio
async def test_two_rmas_on_one_case_keep_their_own_label_tracking_and_location(
    repository: SQLBusinessStateRepository,
) -> None:
    case_id = _case_id()
    persisted = await repository.persist_case_return_records(_two_rma_outcome(case_id))
    assert persisted == (f"{case_id}-rec-a", f"{case_id}-rec-b")

    records = await repository.read_case_return_records(case_id)
    assert [record["return_reference"] for record in records] == ["RMA-A", "RMA-B"]

    by_reference = {str(record["return_reference"]): record for record in records}
    rma_a, rma_b = by_reference["RMA-A"], by_reference["RMA-B"]

    # The contamination contract, stated field by field.
    assert (rma_a["label_reference"], rma_b["label_reference"]) == ("LABEL-A", "LABEL-B")
    assert (rma_a["tracking_reference"], rma_b["tracking_reference"]) == ("TRACK-A", "TRACK-B")
    assert (rma_a["return_location"], rma_b["return_location"]) == ("LOC-A", "LOC-B")
    assert (
        rma_a["shipping_instruction_reference"],
        rma_b["shipping_instruction_reference"],
    ) == ("SHIP-A", "SHIP-B")
    # The method is per record for the same reason the label is (D23): one case
    # can hold a `CUSTOMER_KEEP` RMA and a `PREPAID_PARCEL` one, and completion
    # is evaluated against each record's own requirement row. A case-level
    # column would complete the first against the second's requirement set.
    assert (rma_a["return_method"], rma_b["return_method"]) == ("PREPAID_PARCEL", "CUSTOMER_KEEP")

    # Items stay nested under the record that owns them.
    assert [item["order_line_id"] for item in rma_a["items"]] == ["LINE-1", "LINE-2"]
    assert [item["order_line_id"] for item in rma_b["items"]] == ["LINE-3"]
    assert [item["quantity"] for item in rma_b["items"]] == [5]
    assert all(item["return_record_id"] == rma_a["return_record_id"] for item in rma_a["items"])


@pytest.mark.asyncio
async def test_a_replayed_outcome_does_not_duplicate_records_or_items(
    repository: SQLBusinessStateRepository,
) -> None:
    """A Temporal retry re-runs this activity with identical input."""

    case_id = _case_id()
    outcome = _two_rma_outcome(case_id)

    first = await repository.persist_case_return_records(outcome)
    second = await repository.persist_case_return_records(outcome)
    third = await repository.persist_case_return_records(outcome)
    assert first == second == third

    records = await repository.read_case_return_records(case_id)
    assert len(records) == 2, "a replay minted a second RMA"
    assert sum(len(record["items"]) for record in records) == 3, "a replay duplicated items"

    # Rewritten, not re-inserted: the row version advanced on each replay.
    assert all(int(record["row_version"]) > 1 for record in records)


@pytest.mark.asyncio
async def test_an_updated_outcome_rewrites_the_same_rma_in_place(
    repository: SQLBusinessStateRepository,
) -> None:
    case_id = _case_id()
    await repository.persist_case_return_records(_two_rma_outcome(case_id))

    amended = _two_rma_outcome(case_id)
    updated_a = ReturnRecordWrite(
        return_record_id=f"{case_id}-rec-a",
        return_reference="RMA-A",
        label_reference="LABEL-A-REISSUED",
        tracking_reference="TRACK-A-REISSUED",
        return_location="LOC-A",
        shipping_instruction_reference="SHIP-A",
        # Carried through rather than dropped. The upsert below is a whole-row
        # `SET`, so this repository does not merge -- `record_support_outcome`
        # computes the merge once and hands the merged values to both stores,
        # and a caller that omitted a field here would blank its column.
        return_method="PREPAID_PARCEL",
        items=amended.records[0].items,
    )
    await repository.persist_case_return_records(
        CaseReturnRecordsWrite(
            case_id=case_id,
            tenant_id=amended.tenant_id,
            principal_id=amended.principal_id,
            order_reference=amended.order_reference,
            records=(updated_a, amended.records[1]),
        )
    )

    records = await repository.read_case_return_records(case_id)
    assert len(records) == 2
    by_reference = {str(record["return_reference"]): record for record in records}
    assert by_reference["RMA-A"]["label_reference"] == "LABEL-A-REISSUED"
    # And the other RMA was not touched by its neighbour's amendment.
    assert by_reference["RMA-B"]["label_reference"] == "LABEL-B"
    assert by_reference["RMA-B"]["tracking_reference"] == "TRACK-B"
    assert by_reference["RMA-B"]["return_method"] == "CUSTOMER_KEEP"
    assert by_reference["RMA-A"]["return_method"] == "PREPAID_PARCEL"


@pytest.mark.asyncio
async def test_two_rmas_cannot_claim_the_same_order_line(
    repository: SQLBusinessStateRepository,
) -> None:
    """Cross-RMA contamination fails loudly and commits nothing.

    Absorbing it silently would leave the case looking correct while an item
    sat under the wrong RMA -- and therefore under the wrong label.
    """

    case_id = _case_id()
    await repository.persist_case_return_records(_two_rma_outcome(case_id))

    contaminating = CaseReturnRecordsWrite(
        case_id=case_id,
        tenant_id="tenant-1",
        principal_id="associate-1",
        order_reference="ORD-RG08",
        records=(
            ReturnRecordWrite(
                return_record_id=f"{case_id}-rec-c",
                return_reference="RMA-C",
                label_reference="LABEL-C",
                items=(
                    ReturnRecordItemWrite(
                        return_item_id=f"{case_id}-item-c1",
                        order_line_id="LINE-1",  # already owned by RMA-A
                        quantity=1,
                    ),
                ),
            ),
        ),
    )

    with pytest.raises(Exception, match=r"(?i)unique|duplicate|constraint"):
        await repository.persist_case_return_records(contaminating)

    records = await repository.read_case_return_records(case_id)
    assert len(records) == 2, "the rejected outcome still committed its RMA"
    assert "RMA-C" not in {str(record["return_reference"]) for record in records}
    by_reference = {str(record["return_reference"]): record for record in records}
    assert [item["order_line_id"] for item in by_reference["RMA-A"]["items"]] == [
        "LINE-1",
        "LINE-2",
    ], "LINE-1 was re-parented away from the RMA that owns it"


@pytest.mark.asyncio
async def test_a_duplicate_rma_number_across_cases_is_rejected_atomically(
    repository: SQLBusinessStateRepository,
) -> None:
    """One RMA number exists once. The second case's whole outcome rolls back."""

    first_case = _case_id()
    await repository.persist_case_return_records(_two_rma_outcome(first_case))

    second_case = _case_id()
    colliding = CaseReturnRecordsWrite(
        case_id=second_case,
        tenant_id="tenant-1",
        principal_id="associate-2",
        order_reference="ORD-OTHER",
        records=(
            ReturnRecordWrite(
                return_record_id=f"{second_case}-rec-x",
                return_reference="RMA-FRESH",
                items=(
                    ReturnRecordItemWrite(
                        return_item_id=f"{second_case}-item-x",
                        order_line_id="LINE-9",
                        quantity=1,
                    ),
                ),
            ),
            ReturnRecordWrite(
                return_record_id=f"{second_case}-rec-y",
                return_reference="RMA-A",  # already issued on the first case
                items=(),
            ),
        ),
    )

    with pytest.raises(Exception, match=r"(?i)unique|duplicate|constraint"):
        await repository.persist_case_return_records(colliding)

    # Atomicity: the RMA that *would* have been fine is not there either.
    assert await repository.read_case_return_records(second_case) == []
