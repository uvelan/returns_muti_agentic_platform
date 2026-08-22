"""The shared issuance seam: identity, mapping, and what it refuses to write.

`operations/return_issuance.py` exists because two paths issue RMAs and only one
of them wrote the authoritative SQL store. These tests pin the parts that must
not differ between them -- item identity, the mapping onto the write contract,
and the rule that issuance never fabricates a tracking observation.

No database. The seam is deliberately separable from the adapter so the mapping
is assertable without one; `persist_case_return_records` is exercised against
real SQL by the live-infra suite.
"""

from __future__ import annotations

from typing import Any

import pytest

from return_platform.operations.return_issuance import (
    IssuanceIntent,
    IssuanceItem,
    IssuanceRecord,
    ReturnIssuance,
    build_case_return_records_write,
    derive_return_item_id,
)


class _RecordingStore:
    """Captures the write instead of performing it."""

    def __init__(self, answer: tuple[str, ...] = ()) -> None:
        self.writes: list[Any] = []
        self._answer = answer

    async def persist_case_return_records(self, write: Any) -> tuple[str, ...]:
        self.writes.append(write)
        return self._answer


def _intent(**overrides: Any) -> IssuanceIntent:
    base: dict[str, Any] = {
        "case_id": "case-1",
        "tenant_id": "tenant-1",
        "principal_id": "principal-1",
        "order_reference": "CQ800002",
        "records": (
            IssuanceRecord(
                return_record_id="rec-1",
                return_reference="RMA-1",
                items=(
                    IssuanceItem(
                        order_line_id="1",
                        quantity=2,
                        product_id="4000096",
                        reason_code="ORDERED_IN_ERROR",
                    ),
                ),
                label_reference="LBL-1",
                tracking_reference="TRK-1",
                carrier="UPS",
                return_method="PREPAID_PARCEL",
            ),
        ),
    }
    base.update(overrides)
    return IssuanceIntent(**base)


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def test_an_item_id_is_derived_from_its_record_and_line_not_minted() -> None:
    """A retry must rewrite the item row, not insert a second one."""
    first = derive_return_item_id("rec-1", "1")
    second = derive_return_item_id("rec-1", "1")

    assert first == second


def test_two_lines_on_one_record_are_different_items() -> None:
    assert derive_return_item_id("rec-1", "1") != derive_return_item_id("rec-1", "2")


def test_the_same_line_on_two_records_is_two_items() -> None:
    """Two RMAs on one case can each carry the same order line."""
    assert derive_return_item_id("rec-1", "1") != derive_return_item_id("rec-2", "1")


def test_issuing_the_same_intent_twice_produces_the_same_item_ids() -> None:
    first = build_case_return_records_write(_intent())
    second = build_case_return_records_write(_intent())

    assert [item.return_item_id for item in first.records[0].items] == [
        item.return_item_id for item in second.records[0].items
    ]


# ---------------------------------------------------------------------------
# Mapping
# ---------------------------------------------------------------------------


def test_the_record_carries_its_own_fulfilment_identity() -> None:
    """Label, tracking and method belong to the record, never the case."""
    write = build_case_return_records_write(_intent())
    record = write.records[0]

    assert record.return_record_id == "rec-1"
    assert record.return_reference == "RMA-1"
    assert record.label_reference == "LBL-1"
    assert record.tracking_reference == "TRK-1"
    assert record.carrier == "UPS"
    assert record.return_method == "PREPAID_PARCEL"


def test_the_case_identity_reaches_the_write() -> None:
    write = build_case_return_records_write(_intent())

    assert write.case_id == "case-1"
    assert write.tenant_id == "tenant-1"
    assert write.principal_id == "principal-1"
    assert write.order_reference == "CQ800002"


def test_item_facts_are_carried_rather_than_defaulted() -> None:
    item = build_case_return_records_write(_intent()).records[0].items[0]

    assert item.order_line_id == "1"
    assert item.quantity == 2
    assert item.product_id == "4000096"
    assert item.reason_code == "ORDERED_IN_ERROR"


def test_a_line_with_no_recorded_quantity_returns_one_unit() -> None:
    """Matches what the workflow already did, rather than inventing a new rule."""
    intent = _intent(
        records=(
            IssuanceRecord(
                return_record_id="rec-1",
                return_reference="RMA-1",
                items=(IssuanceItem(order_line_id="1"),),
            ),
        )
    )

    assert build_case_return_records_write(intent).records[0].items[0].quantity == 1


# ---------------------------------------------------------------------------
# What it refuses
# ---------------------------------------------------------------------------


def test_issuance_cannot_express_a_tracking_observation() -> None:
    """The seam has no way to write `dbo.return_tracking`, by construction.

    Support states a tracking number before any carrier has filed a scan, and
    that row requires a `tracking_type` and an `event_at` nothing has observed.
    The reference lives on the record; the observation is written later by
    `record_shipment_update`. If a field for one ever appears here, this test
    should fail and the decision should be made deliberately.
    """
    write = build_case_return_records_write(_intent())
    record = write.records[0]

    assert not hasattr(record, "tracking_type")
    assert not hasattr(record, "event_at")
    assert hasattr(record, "tracking_reference")


@pytest.mark.asyncio
async def test_an_outcome_that_issued_no_rma_writes_nothing() -> None:
    """A real outcome, not an error -- so it must not reach the store at all."""
    store = _RecordingStore()

    persisted = await ReturnIssuance(store).issue(_intent(records=()))

    assert persisted == ()
    assert store.writes == []


# ---------------------------------------------------------------------------
# Persisting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_issue_hands_the_store_one_whole_case_write() -> None:
    """T-14: the records of one outcome commit together or not at all."""
    store = _RecordingStore(answer=("rec-1",))

    persisted = await ReturnIssuance(store).issue(_intent())

    assert persisted == ("rec-1",)
    assert len(store.writes) == 1
    assert [record.return_record_id for record in store.writes[0].records] == ["rec-1"]


@pytest.mark.asyncio
async def test_issue_answers_with_the_ids_the_store_committed() -> None:
    """The caller synchronizes exactly what committed, never a wider set."""
    store = _RecordingStore(answer=("rec-1", "rec-2"))

    assert await ReturnIssuance(store).issue(_intent()) == ("rec-1", "rec-2")
