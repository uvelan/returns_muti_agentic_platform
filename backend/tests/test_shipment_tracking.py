"""The return-shipment store against the catalog the release declares.

A fake collection stands in for Mongo: the store's contract -- config-driven
names, idempotent seeding, append-only events, catalog-validated transitions --
is what is under test, not the driver.
"""

from __future__ import annotations

from typing import Any

import pytest

from return_platform.configuration.return_configuration import (
    ShipmentStatusConfiguration,
    ShipmentTrackingConfiguration,
)
from return_platform.operations.shipment_tracking import (
    ShipmentSeed,
    ShipmentTrackingStore,
    TransitionRejected,
)


def _catalog(**overrides: Any) -> ShipmentTrackingConfiguration:
    base: dict[str, Any] = dict(
        initial_status_parcel="label_created",
        initial_status_freight="bol_created",
        freight_methods=("BRANCH_LTL",),
        collection="shipmentInfo",
        fields={},
        statuses=(
            ShipmentStatusConfiguration(
                code="label_created",
                label="Label created",
                ladder="parcel",
                ordinal=0,
                allowed_next=("picked_up",),
            ),
            ShipmentStatusConfiguration(
                code="picked_up",
                label="Picked up",
                ladder="parcel",
                ordinal=1,
                allowed_next=("delivered", "exception"),
            ),
            ShipmentStatusConfiguration(
                code="delivered", label="Delivered", ladder="parcel", ordinal=2, terminal=True
            ),
            ShipmentStatusConfiguration(
                code="exception",
                label="Exception",
                ladder="parcel",
                ordinal=1,
                exception_state=True,
                allowed_next=("picked_up",),
            ),
            ShipmentStatusConfiguration(
                code="bol_created",
                label="BOL created",
                ladder="freight",
                ordinal=0,
                allowed_next=("delivered",),
            ),
            ShipmentStatusConfiguration(
                code="delivered", label="Delivered", ladder="freight", ordinal=1, terminal=True
            ),
        ),
    )
    base.update(overrides)
    return ShipmentTrackingConfiguration(**base)


class _FakeCollection:
    """The four operations the store uses, over a list of dicts."""

    def __init__(self) -> None:
        self.documents: list[dict[str, Any]] = []

    def _matches(self, document: dict[str, Any], query: dict[str, Any]) -> bool:
        for key, expected in query.items():
            if key == "$or":
                if not any(self._matches(document, alternative) for alternative in expected):
                    return False
                continue
            actual = document.get(key)
            if isinstance(expected, dict) and "$regex" in expected:
                import re

                if not isinstance(actual, str) or not re.search(expected["$regex"], actual, re.I):
                    return False
            elif actual != expected:
                return False
        return True

    async def update_one(self, query, update, upsert=False):
        class Result:
            upserted_id = None

        result = Result()
        for document in self.documents:
            if self._matches(document, query):
                return result
        if upsert:
            self.documents.append(dict(update["$setOnInsert"]))
            result.upserted_id = "new"
        return result

    async def find_one(self, query):
        for document in self.documents:
            if self._matches(document, query):
                return dict(document)
        return None

    async def find_one_and_update(self, query, update, return_document=None):
        for document in self.documents:
            if self._matches(document, query):
                for key, value in update.get("$push", {}).items():
                    document.setdefault(key, []).append(value)
                for key, value in update.get("$set", {}).items():
                    document[key] = value
                return dict(document)
        return None

    def find(self, query):
        matches = [dict(d) for d in self.documents if self._matches(d, query)]

        class Cursor:
            def sort(self, *_args):
                return self

            async def to_list(self, length):
                return matches[:length]

        return Cursor()


def _store(catalog: ShipmentTrackingConfiguration | None = None):
    collection = _FakeCollection()
    resolved = catalog or _catalog()
    return ShipmentTrackingStore(
        collection_of=lambda name: collection, configuration=lambda: resolved
    ), collection


def _seed(**overrides: Any) -> ShipmentSeed:
    base: dict[str, Any] = dict(
        case_id="case-1",
        return_record_id="rec-1",
        rma_reference="RMA-1",
        tracking_reference="TRK-1",
        return_method="BRANCH_UPS",
    )
    base.update(overrides)
    return ShipmentSeed(**base)


@pytest.mark.asyncio
async def test_seeding_is_idempotent_on_record_and_tracking() -> None:
    store, collection = _store()
    first = await store.seed(_seed())
    replay = await store.seed(_seed())
    assert first is not None
    assert replay is None
    assert len(collection.documents) == 1


@pytest.mark.asyncio
async def test_the_ladder_and_initial_status_follow_the_method() -> None:
    store, collection = _store()
    await store.seed(_seed())
    assert collection.documents[0]["mode"] == "parcel"
    assert collection.documents[0]["current_status"] == "label_created"
    await store.seed(
        _seed(
            return_record_id="rec-2",
            tracking_reference="PRO-9",
            return_method="BRANCH_LTL",
            bol_reference="BOL-9",
        )
    )
    freight = collection.documents[1]
    assert freight["mode"] == "freight"
    assert freight["current_status"] == "bol_created"
    assert freight["pro_number"] == "PRO-9"
    assert freight["bol_reference"] == "BOL-9"


@pytest.mark.asyncio
async def test_a_seed_without_tracking_is_refused_not_placeholdered() -> None:
    store, _ = _store()
    with pytest.raises(ValueError):
        await store.seed(_seed(tracking_reference=""))


@pytest.mark.asyncio
async def test_events_append_and_transitions_follow_the_catalog() -> None:
    store, collection = _store()
    await store.seed(_seed())
    shipment_id = collection.documents[0]["shipment_id"]
    updated = await store.append_event(shipment_id, status="picked_up", actor="tester")
    assert updated["current_status"] == "picked_up"
    assert len(updated["events"]) == 1
    with pytest.raises(TransitionRejected):
        await store.append_event(shipment_id, status="label_created", actor="tester")
    # the rejected transition appended nothing
    assert len(collection.documents[0]["events"]) == 1


@pytest.mark.asyncio
async def test_terminal_status_refuses_updates_without_override() -> None:
    store, collection = _store()
    await store.seed(_seed())
    shipment_id = collection.documents[0]["shipment_id"]
    await store.append_event(shipment_id, status="picked_up", actor="tester")
    await store.append_event(shipment_id, status="delivered", actor="tester")
    with pytest.raises(TransitionRejected):
        await store.append_event(shipment_id, status="picked_up", actor="tester")
    reopened = await store.append_event(
        shipment_id,
        status="picked_up",
        actor="tester",
        override=True,
        override_reason="reopen for test",
    )
    assert reopened["current_status"] == "picked_up"
    assert reopened["events"][-1]["override"] is True
    assert reopened["events"][-1]["override_reason"] == "reopen for test"


@pytest.mark.asyncio
async def test_field_mapping_renames_the_stored_keys() -> None:
    catalog = _catalog(fields={"tracking_reference": "trackingNo", "current_status": "statusCode"})
    store, collection = _store(catalog)
    await store.seed(_seed())
    document = collection.documents[0]
    assert document["trackingNo"] == "TRK-1"
    assert document["statusCode"] == "label_created"
    assert "tracking_reference" not in document
    found = await store.find("TRK-1")
    assert found is not None


@pytest.mark.asyncio
async def test_lookup_by_every_identifier() -> None:
    store, _collection = _store()
    await store.seed(
        _seed(
            return_method="BRANCH_LTL",
            tracking_reference="PRO-7",
            bol_reference="BOL-7",
            rma_reference="RMA-7",
            case_id="case-7",
            return_record_id="rec-7",
        )
    )
    for identifier in ("PRO-7", "BOL-7", "RMA-7", "case-7"):
        assert await store.find(identifier) is not None, identifier


def test_the_catalog_refuses_terminal_rungs_with_next_codes() -> None:
    with pytest.raises(ValueError):
        _catalog(
            statuses=(
                ShipmentStatusConfiguration(
                    code="delivered",
                    label="Delivered",
                    ladder="parcel",
                    ordinal=0,
                    terminal=True,
                    allowed_next=("picked_up",),
                ),
                ShipmentStatusConfiguration(
                    code="picked_up", label="Picked", ladder="parcel", ordinal=1
                ),
                ShipmentStatusConfiguration(
                    code="bol_created", label="B", ladder="freight", ordinal=0
                ),
            ),
            initial_status_parcel="delivered",
            initial_status_freight="bol_created",
        )


def test_the_catalog_refuses_cross_ladder_transitions() -> None:
    with pytest.raises(ValueError):
        _catalog(
            statuses=(
                ShipmentStatusConfiguration(
                    code="label_created",
                    label="L",
                    ladder="parcel",
                    ordinal=0,
                    allowed_next=("bol_created",),
                ),
                ShipmentStatusConfiguration(
                    code="bol_created", label="B", ladder="freight", ordinal=0
                ),
            ),
            initial_status_parcel="label_created",
            initial_status_freight="bol_created",
        )
