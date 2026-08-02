"""Production runtime adapters for canonical order source reads and graph writes."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, cast

from neo4j import AsyncDriver, AsyncManagedTransaction
from pymongo import AsyncMongoClient

from return_platform.v2.models import (
    AnchorType,
    OrderAnchor,
    OrderLineProjection,
    OrderProjection,
    SourceOrderRecord,
)
from return_platform.v2.services import OrderProjectionStore, OrderSourceGateway


def _nested(document: Mapping[str, Any], path: str) -> Any:
    current: Any = document
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _first(document: Mapping[str, Any], paths: Sequence[str]) -> Any:
    for path in paths:
        value = _nested(document, path)
        if value not in (None, "", []):
            return value
    return None


def _strings(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    items = value if isinstance(value, (list, tuple, set)) else (value,)
    return tuple(str(item).strip() for item in items if str(item).strip())


class MongoOrderSourceGateway(OrderSourceGateway):
    """Read configured Ferguson order anchors and orders without persisting source documents."""

    def __init__(
        self,
        client: AsyncMongoClient[dict[str, object]],
        database: str,
        *,
        sales_collection: str = "salesInv",
        shipment_collection: str = "shipmentInfo",
        invoice_collection: str = "invoiceMemosCDM",
    ) -> None:
        self._database = client[database]
        self._sales_collection = sales_collection
        self._shipment_collection = shipment_collection
        self._invoice_collection = invoice_collection

    @staticmethod
    def _full_id_from_document(document: Mapping[str, Any]) -> str | None:
        explicit = _first(
            document,
            (
                "fullOrderId",
                "canonical.fullOrderId",
                "salesHdr.salesHdrData.fullOrderId",
            ),
        )
        if explicit:
            parts = str(explicit).strip().upper().split("*")
            return "*".join(parts[:2]) if len(parts) >= 2 else None
        identifier = str(document.get("_id", "")).strip().upper()
        if "*" in identifier:
            parts = identifier.split("*")
            if len(parts) >= 2 and parts[0] and parts[1]:
                return f"{parts[0]}*{parts[1]}"
        account = _first(
            document,
            (
                "accountNumber",
                "account",
                "logon",
                "salesHdr.salesHdrData.accountNumber",
                "salesHdr.salesHdrData.account",
                "salesHdr.salesHdrData.logon",
            ),
        )
        order = _first(
            document,
            (
                "orderNumber",
                "orderId",
                "salesHdr.salesHdrData.orderNumber",
                "salesHdr.salesHdrData.orderId",
            ),
        )
        return f"{str(account).strip().upper()}*{str(order).strip().upper()}" if account and order else None

    async def _sales_ids(self, query: Mapping[str, Any], limit: int) -> list[str]:
        identifiers: list[str] = []
        cursor = self._database[self._sales_collection].find(dict(query)).limit(limit)
        async for raw in cursor:
            identifier = self._full_id_from_document(cast(Mapping[str, Any], raw))
            if identifier and identifier not in identifiers:
                identifiers.append(identifier)
            if len(identifiers) >= limit:
                break
        return identifiers

    async def _resolve_order_reference(self, value: str, limit: int) -> list[str]:
        normalized = value.strip().upper()
        if normalized.count("*") == 1:
            return [normalized] if await self.fetch(normalized) is not None else []
        escaped = re.escape(normalized)
        return await self._sales_ids(
            {
                "$or": [
                    {"salesHdr.salesHdrData.orderId": normalized},
                    {"salesHdr.salesHdrData.orderNumber": normalized},
                    {"orderNumber": normalized},
                    {"_id": {"$regex": rf"^[^*]+\*{escaped}(?:\*[^*]+)?$", "$options": "i"}},
                ]
            },
            limit,
        )

    async def resolve(self, anchor: OrderAnchor, limit: int) -> list[str]:
        value = anchor.value.strip()
        if anchor.type in {AnchorType.FULL_ORDER_ID, AnchorType.ORDER_REFERENCE}:
            matches = await self._resolve_order_reference(value, limit)
        elif anchor.type is AnchorType.TRACKING_NUMBER:
            references: list[str] = []
            cursor = self._database[self._shipment_collection].find(
                {
                    "$or": [
                        {"shipmentInfoEventData.trkNum": value},
                        {"trackingNumber": value},
                    ]
                }
            ).limit(limit)
            async for raw in cursor:
                document = cast(Mapping[str, Any], raw)
                reference = _first(
                    document,
                    (
                        "fullOrderId",
                        "shipmentInfoEventData.fullOrderId",
                        "shipmentInfoEventData.trilOrdNum",
                        "orderNumber",
                    ),
                )
                if reference:
                    references.append(str(reference))
            matches = []
            for reference in references:
                for identifier in await self._resolve_order_reference(reference, limit):
                    if identifier not in matches:
                        matches.append(identifier)
        elif anchor.type is AnchorType.INVOICE_NUMBER:
            matches = []
            cursor = self._database[self._invoice_collection].find(
                {"invoiceNumber": value}
            ).limit(limit)
            async for raw in cursor:
                document = cast(Mapping[str, Any], raw)
                invoice_identifier = self._full_id_from_document(document)
                if invoice_identifier is None:
                    account = _first(document, ("accountNumber", "account", "logon"))
                    order = _first(document, ("orderNumber", "orderId"))
                    invoice_identifier = (
                        f"{str(account).strip().upper()}*{str(order).strip().upper()}"
                        if account and order
                        else None
                    )
                if invoice_identifier and invoice_identifier not in matches:
                    matches.append(invoice_identifier)
        else:
            field = (
                "salesHdr.salesHdrData.deliveryTicketNumber"
                if anchor.type is AnchorType.DELIVERY_TICKET
                else "salesHdr.salesHdrData.custPONumber"
            )
            matches = await self._sales_ids({field: value}, limit)
        if anchor.account_scope:
            account = anchor.account_scope.strip().upper()
            matches = [item for item in matches if item.split("*", 1)[0] == account]
        return matches[:limit]

    @staticmethod
    def _line_documents(document: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        candidate = _first(document, ("salesDtl", "lines", "orderLines"))
        if isinstance(candidate, list):
            return [cast(Mapping[str, Any], item) for item in candidate if isinstance(item, Mapping)]
        return [document]

    @classmethod
    def _line(cls, document: Mapping[str, Any]) -> dict[str, Any] | None:
        data = _first(document, ("salesDtlData", "line", "salesDtl.salesDtlData"))
        source = cast(Mapping[str, Any], data) if isinstance(data, Mapping) else document
        number = _first(source, ("lineNumber", "lineNum", "orderLineNumber", "sourceLineId"))
        if number is None:
            identifier = str(document.get("_id", ""))
            parts = identifier.split("*")
            number = parts[2] if len(parts) >= 3 else None
        if number is None:
            return None
        return {
            "lineNumber": str(number),
            "itemNumber": _first(source, ("itemNumber", "itemId", "sku")),
            "description": _first(source, ("itemDesc", "description", "itemDescription")),
            "quantityOrdered": _first(source, ("quantityOrdered", "orderQty", "qtyOrdered")),
            "quantityReturned": _first(source, ("quantityReturned", "returnedQty", "qtyReturned")) or 0,
        }

    async def fetch(self, full_order_id: str) -> SourceOrderRecord | None:
        escaped = re.escape(full_order_id)
        cursor = self._database[self._sales_collection].find(
            {
                "$or": [
                    {"_id": full_order_id},
                    {"_id": {"$regex": rf"^{escaped}\*[^*]+$", "$options": "i"}},
                    {"fullOrderId": full_order_id},
                ]
            }
        )
        documents = [cast(Mapping[str, Any], item) async for item in cursor]
        if not documents:
            return None
        header = documents[0]
        account, order_number = full_order_id.split("*", 1)
        lines: dict[str, dict[str, Any]] = {}
        for document in documents:
            for line_document in self._line_documents(document):
                line = self._line(line_document)
                if line is not None:
                    lines[str(line["lineNumber"])] = line
        revision = _first(
            header,
            (
                "sourceRevision",
                "eventMeta.sourceTransactionId",
                "eventMeta.sourceTrId",
                "updatedAt",
            ),
        ) or str(header.get("_id", full_order_id))
        return SourceOrderRecord(
            account=account,
            order_number=order_number,
            customer_id=(
                str(value)
                if (value := _first(header, ("salesHdr.salesHdrData.orderCust", "customerId")))
                else None
            ),
            customer_name=(
                str(value)
                if (value := _first(header, ("salesHdr.salesHdrData.custName", "customerName")))
                else None
            ),
            customer_po=(
                str(value)
                if (value := _first(header, ("salesHdr.salesHdrData.custPONumber", "customerPo")))
                else None
            ),
            delivery_ticket=(
                str(value)
                if (value := _first(header, ("salesHdr.salesHdrData.deliveryTicketNumber", "deliveryTicket")))
                else None
            ),
            invoice_numbers=_strings(_first(header, ("invoiceNumbers", "invoiceNumber"))),
            tracking_numbers=_strings(_first(header, ("trackingNumbers", "trackingNumber"))),
            source_revision=str(revision),
            lines=tuple(lines[key] for key in sorted(lines)),
        )


class Neo4jOrderProjectionStore(OrderProjectionStore):
    """Persist only allowlisted minimal order discovery properties in Neo4j."""

    def __init__(self, driver: AsyncDriver, database: str) -> None:
        self._driver = driver
        self._database = database

    @staticmethod
    def _order_parameters(order: OrderProjection) -> dict[str, Any]:
        return {
            "full_order_id": order.full_order_id,
            "account": order.account,
            "order_number": order.order_number,
            "customer_id": order.customer_id,
            "customer_name": order.customer_name,
            "customer_po": order.customer_po,
            "delivery_ticket": order.delivery_ticket,
            "invoice_numbers": list(order.invoice_numbers),
            "tracking_numbers": list(order.tracking_numbers),
            "source_revision": order.source_revision,
        }

    @staticmethod
    async def _upsert_order(
        transaction: AsyncManagedTransaction, parameters: Mapping[str, Any]
    ) -> None:
        result = await transaction.run(
            """
            MERGE (o:SalesOrder {fullOrderId: $full_order_id})
            SET o.account = $account,
                o.orderNumber = $order_number,
                o.customerId = $customer_id,
                o.customerName = $customer_name,
                o.customerPo = $customer_po,
                o.deliveryTicket = $delivery_ticket,
                o.invoiceNumbers = $invoice_numbers,
                o.trackingNumbers = $tracking_numbers,
                o.sourceRevision = $source_revision,
                o.projectionProfile = 'ORDER_DISCOVERY_MINIMAL'
            """,
            parameters=dict(parameters),
        )
        await result.consume()

    async def upsert_candidates(self, orders: Sequence[OrderProjection]) -> int:
        async with self._driver.session(database=self._database) as session:
            for order in orders:
                await session.execute_write(self._upsert_order, self._order_parameters(order))
        return len(orders)

    @staticmethod
    async def _replace_lines(
        transaction: AsyncManagedTransaction,
        parameters: Mapping[str, Any],
        lines: list[dict[str, Any]],
    ) -> None:
        await Neo4jOrderProjectionStore._upsert_order(transaction, parameters)
        result = await transaction.run(
            """
            MATCH (o:SalesOrder {fullOrderId: $full_order_id})
            OPTIONAL MATCH (o)-[:CONTAINS_LINE]->(stale:OrderLine)
            WHERE NOT stale.fullOrderLineId IN $line_ids
            DETACH DELETE stale
            WITH o
            UNWIND $lines AS line
            MERGE (l:OrderLine {fullOrderLineId: line.fullOrderLineId})
            SET l.lineNumber = line.lineNumber,
                l.itemNumber = line.itemNumber,
                l.description = line.description,
                l.quantityOrdered = line.quantityOrdered,
                l.quantityReturned = line.quantityReturned,
                l.sourceRevision = $source_revision
            MERGE (o)-[:CONTAINS_LINE]->(l)
            """,
            parameters={
                **dict(parameters),
                "line_ids": [line["fullOrderLineId"] for line in lines],
                "lines": lines,
            },
        )
        await result.consume()

    async def replace_full_order(self, order: OrderProjection) -> int:
        lines = [item.model_dump(mode="json", by_alias=True) for item in order.lines]
        async with self._driver.session(database=self._database) as session:
            await session.execute_write(
                self._replace_lines,
                self._order_parameters(order),
                lines,
            )
        return 1 + len(lines)

    async def get(self, full_order_id: str) -> OrderProjection | None:
        async with self._driver.session(database=self._database) as session:
            result = await session.run(
                """
                MATCH (o:SalesOrder {fullOrderId: $full_order_id})
                OPTIONAL MATCH (o)-[:CONTAINS_LINE]->(l:OrderLine)
                RETURN properties(o) AS order,
                       [item IN collect(properties(l)) WHERE item.fullOrderLineId IS NOT NULL] AS lines
                """,
                full_order_id=full_order_id,
            )
            record = await result.single()
        if record is None:
            return None
        order = cast(Mapping[str, Any], record["order"])
        raw_lines = cast(list[Mapping[str, Any]], record["lines"])
        return OrderProjection(
            full_order_id=str(order["fullOrderId"]),
            account=str(order["account"]),
            order_number=str(order["orderNumber"]),
            customer_id=cast(str | None, order.get("customerId")),
            customer_name=cast(str | None, order.get("customerName")),
            customer_po=cast(str | None, order.get("customerPo")),
            delivery_ticket=cast(str | None, order.get("deliveryTicket")),
            invoice_numbers=_strings(order.get("invoiceNumbers")),
            tracking_numbers=_strings(order.get("trackingNumbers")),
            source_revision=str(order["sourceRevision"]),
            lines=tuple(
                OrderLineProjection.model_validate(dict(line))
                for line in sorted(raw_lines, key=lambda item: str(item.get("lineNumber", "")))
            ),
        )
