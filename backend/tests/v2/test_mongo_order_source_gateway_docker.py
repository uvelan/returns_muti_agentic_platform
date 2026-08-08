"""Real MongoDB tests for MongoOrderSourceGateway -- no mocks.

There was zero prior test coverage for this class anywhere in the repo
before Phase 8 / Wave C1 redirected its raw pymongo calls onto
source_connectors.mongodb.find_many() -- this is the first real proof that
the redirect preserved v2's order-sync business logic (OR-of-conditions
anchor resolution, cross-collection tracking-number lookup, business
identity synthesis, line unwrapping) exactly.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from pymongo import AsyncMongoClient

from return_platform.configuration.settings import Settings
from return_platform.v2.models import AnchorType, OrderAnchor
from return_platform.v2.runtime_adapters import MongoOrderSourceGateway


@pytest_asyncio.fixture
async def gateway(test_settings: Settings) -> AsyncIterator[MongoOrderSourceGateway]:
    client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
        test_settings.mongo_dsn.get_secret_value()
    )
    suffix = uuid.uuid4().hex[:12]
    sales_collection = f"v2_test_sales_{suffix}"
    shipment_collection = f"v2_test_shipment_{suffix}"
    invoice_collection = f"v2_test_invoice_{suffix}"
    database = client["return_platform"]

    await database[sales_collection].insert_many(
        [
            {
                "_id": "ACC1*ORD1",
                "salesHdr": {
                    "salesHdrData": {
                        "orderId": "ORD1",
                        "orderNumber": "ORD1",
                        "orderCust": "CUST-1",
                        "custName": "Test Customer",
                        "custPONumber": "PO-1",
                        "deliveryTicketNumber": "DT-1",
                    }
                },
                "salesDtl": [
                    {
                        "salesDtlData": {
                            "lineNumber": "1",
                            "itemNumber": "ITEM-1",
                            "itemDesc": "Widget",
                            "quantityOrdered": 5,
                        }
                    }
                ],
            }
        ]
    )
    await database[shipment_collection].insert_many(
        [{"shipmentInfoEventData": {"trkNum": "TRACK-1", "fullOrderId": "ACC1*ORD1"}}]
    )
    await database[invoice_collection].insert_many(
        [{"invoiceNumber": "INV-1", "fullOrderId": "ACC1*ORD1"}]
    )

    gateway = MongoOrderSourceGateway(
        client,
        "return_platform",
        sales_collection=sales_collection,
        shipment_collection=shipment_collection,
        invoice_collection=invoice_collection,
    )
    try:
        yield gateway
    finally:
        await database[sales_collection].drop()
        await database[shipment_collection].drop()
        await database[invoice_collection].drop()
        await client.close()


@pytest.mark.asyncio
async def test_fetch_returns_header_and_lines_for_exact_full_order_id(
    gateway: MongoOrderSourceGateway,
) -> None:
    record = await gateway.fetch("ACC1*ORD1")
    assert record is not None
    assert record.account == "ACC1"
    assert record.order_number == "ORD1"
    assert record.customer_id == "CUST-1"
    assert record.customer_po == "PO-1"
    assert record.delivery_ticket == "DT-1"
    assert len(record.lines) == 1
    assert record.lines[0]["itemNumber"] == "ITEM-1"


@pytest.mark.asyncio
async def test_fetch_returns_none_for_unknown_order(gateway: MongoOrderSourceGateway) -> None:
    assert await gateway.fetch("ACC1*NOPE") is None


@pytest.mark.asyncio
async def test_resolve_order_reference_matches_via_or_conditions(
    gateway: MongoOrderSourceGateway,
) -> None:
    matches = await gateway.resolve(OrderAnchor(type=AnchorType.ORDER_REFERENCE, value="ORD1"), 10)
    assert matches == ["ACC1*ORD1"]


@pytest.mark.asyncio
async def test_resolve_full_order_id_short_circuits_to_a_single_fetch(
    gateway: MongoOrderSourceGateway,
) -> None:
    matches = await gateway.resolve(
        OrderAnchor(type=AnchorType.FULL_ORDER_ID, value="ACC1*ORD1"), 10
    )
    assert matches == ["ACC1*ORD1"]


@pytest.mark.asyncio
async def test_resolve_tracking_number_resolves_across_collections(
    gateway: MongoOrderSourceGateway,
) -> None:
    matches = await gateway.resolve(
        OrderAnchor(type=AnchorType.TRACKING_NUMBER, value="TRACK-1"), 10
    )
    assert matches == ["ACC1*ORD1"]


@pytest.mark.asyncio
async def test_resolve_invoice_number_resolves_via_invoice_collection(
    gateway: MongoOrderSourceGateway,
) -> None:
    matches = await gateway.resolve(OrderAnchor(type=AnchorType.INVOICE_NUMBER, value="INV-1"), 10)
    assert matches == ["ACC1*ORD1"]


@pytest.mark.asyncio
async def test_resolve_delivery_ticket_matches_sales_field(
    gateway: MongoOrderSourceGateway,
) -> None:
    matches = await gateway.resolve(OrderAnchor(type=AnchorType.DELIVERY_TICKET, value="DT-1"), 10)
    assert matches == ["ACC1*ORD1"]


@pytest.mark.asyncio
async def test_resolve_respects_account_scope_filtering(gateway: MongoOrderSourceGateway) -> None:
    matches = await gateway.resolve(
        OrderAnchor(type=AnchorType.ORDER_REFERENCE, value="ORD1", account_scope="OTHER-ACCT"), 10
    )
    assert matches == []
