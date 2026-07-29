"""Graph projection synchronization from governed MongoDB and SQL Server sources."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

import pymssql
from neo4j import AsyncDriver
from pydantic import BaseModel, ConfigDict, Field
from pymongo import AsyncMongoClient

from return_platform.configuration.settings import Settings
from return_platform.data_platform.graph.schema import GraphSchemaManager
from return_platform.data_platform.schema_registry import SchemaRegistry
from return_platform.security.contact_evidence import contact_lookup_digest


class GraphSyncScope(StrEnum):
    FULL = "FULL"
    SOURCE_MONGODB = "SOURCE_MONGODB"
    SQLSERVER = "SQLSERVER"


class GraphSyncModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GraphSyncRequest(GraphSyncModel):
    mode: GraphSyncScope = GraphSyncScope.FULL
    maxRecordsPerAsset: int = Field(default=1_000, ge=1, le=100_000)
    applySchema: bool = True


class GraphSyncRunView(GraphSyncModel):
    id: str
    mode: str
    status: Literal["RUNNING", "COMPLETED", "FAILED"]
    schemaVersion: str
    sourceCounts: dict[str, int]
    nodeWrites: int = Field(ge=0)
    relationshipWrites: int = Field(ge=0)
    constraintsApplied: list[str]
    configurationDigest: str
    errorCode: str | None = None
    startedBy: str
    startedAt: datetime
    completedAt: datetime | None = None


def _now() -> datetime:
    return datetime.now(UTC)


def _nested(document: dict[str, Any], path: str) -> Any:
    current: Any = document
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if value is None:
        return None
    return str(value)


class GraphSyncService:
    """Rebuildable graph projection writer. Source systems remain authoritative."""

    def __init__(
        self,
        *,
        platform_client: AsyncMongoClient[dict[str, object]],
        source_client: AsyncMongoClient[dict[str, object]],
        driver: AsyncDriver,
        settings: Settings,
        registry: SchemaRegistry,
    ) -> None:
        self._settings = settings
        self._registry = registry
        self._platform_db = platform_client[settings.mongo_database]
        self._source_db = source_client[settings.source_mongo_database]
        self._driver = driver
        self._runs = self._platform_db["graph_sync_runs"]
        self._schema_manager = GraphSchemaManager(driver, settings.neo4j_database, registry.graph)

    async def ensure_indexes(self) -> None:
        await self._runs.create_index([("startedAt", -1)])
        await self._runs.create_index("status")

    async def remove_source_mongodb_records(
        self, records: Sequence[tuple[str, Mapping[str, object]]]
    ) -> None:
        """Remove projections for authoritative source records that were rolled back."""
        keys: dict[str, list[str]] = {
            "customers": [],
            "orders": [],
            "products": [],
            "shipments": [],
        }
        for asset_id, payload in records:
            if asset_id == "source.mongodb.customer_outbound_cdm":
                for field in ("partyId", "customerId"):
                    if value := _text(payload.get(field)):
                        keys["customers"].append(value)
            elif asset_id == "source.mongodb.sales_inv":
                if value := _text(payload.get("salesHdrEventData.orderId")):
                    keys["orders"].append(value)
                if value := _text(payload.get("salesHdr.salesHdrData.custId")):
                    keys["customers"].append(value)
            elif asset_id == "source.mongodb.product_search":
                if value := _text(payload.get("productId")):
                    keys["products"].append(value)
            elif asset_id == "source.mongodb.shipment_info":
                if value := _text(payload.get("shipmentInfoEventData.trkNum")):
                    keys["shipments"].append(value)

        await self._write(
            """
            UNWIND $rows AS row
            MATCH (s:Shipment {tracking_number: row.key})
            DETACH DELETE s
            """,
            [{"key": key} for key in keys["shipments"]],
        )
        await self._write(
            """
            UNWIND $rows AS row
            MATCH (o:SalesOrder {sales_order_number: row.key})
            OPTIONAL MATCH (o)-[:HAS_ORDER_LINE]->(line:OrderLine)
            DETACH DELETE line, o
            """,
            [{"key": key} for key in keys["orders"]],
        )
        await self._write(
            """
            UNWIND $rows AS row
            MATCH (p:Product {product_id: row.key})
            DETACH DELETE p
            """,
            [{"key": key} for key in keys["products"]],
        )
        await self._write(
            """
            UNWIND $rows AS row
            MATCH (c:Customer {customer_key: row.key})
            OPTIONAL MATCH (c)-[:HAS_ACCOUNT]->(account:CustomerAccount)
            DETACH DELETE account, c
            """,
            [{"key": key} for key in keys["customers"]],
        )

    @staticmethod
    def _view(document: dict[str, Any]) -> GraphSyncRunView:
        return GraphSyncRunView.model_validate(
            {
                "id": str(document["_id"]),
                "mode": document["mode"],
                "status": document["status"],
                "schemaVersion": document["schemaVersion"],
                "sourceCounts": document.get("sourceCounts", {}),
                "nodeWrites": document.get("nodeWrites", 0),
                "relationshipWrites": document.get("relationshipWrites", 0),
                "constraintsApplied": document.get("constraintsApplied", []),
                "configurationDigest": document["configurationDigest"],
                "errorCode": document.get("errorCode"),
                "startedBy": document["startedBy"],
                "startedAt": document["startedAt"],
                "completedAt": document.get("completedAt"),
            }
        )

    def _configuration_digest(self) -> str:
        encoded = json.dumps(
            self._registry.graph.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(encoded).hexdigest()

    async def list_runs(self, limit: int = 100) -> list[GraphSyncRunView]:
        documents = await self._runs.find({}).sort("startedAt", -1).limit(limit).to_list()
        return [self._view(document) for document in documents]

    async def get_run(self, run_id: str) -> GraphSyncRunView | None:
        document = await self._runs.find_one({"_id": run_id})
        return self._view(document) if document else None

    async def sync(
        self,
        request: GraphSyncRequest,
        *,
        actor_id: str,
        source_filter: Mapping[str, object] | None = None,
    ) -> GraphSyncRunView:
        limit = min(request.maxRecordsPerAsset, self._settings.graph_sync_max_records)
        run_id = str(uuid.uuid4())
        now = _now()
        document: dict[str, Any] = {
            "_id": run_id,
            "mode": request.mode,
            "status": "RUNNING",
            "schemaVersion": self._registry.schema_version,
            "sourceCounts": {},
            "nodeWrites": 0,
            "relationshipWrites": 0,
            "constraintsApplied": [],
            "configurationDigest": self._configuration_digest(),
            "errorCode": None,
            "startedBy": actor_id,
            "startedAt": now,
            "completedAt": None,
        }
        await self._runs.insert_one(document)
        try:
            constraints = await self._schema_manager.apply() if request.applySchema else []
            source_counts: dict[str, int] = {}
            node_writes = 0
            relationship_writes = 0
            if request.mode in {"FULL", "SOURCE_MONGODB"}:
                counts, nodes, relationships = await self._sync_mongodb(
                    run_id,
                    limit,
                    source_filter,
                )
                source_counts.update(counts)
                node_writes += nodes
                relationship_writes += relationships
            if request.mode in {"FULL", "SQLSERVER"}:
                counts, nodes, relationships = await self._sync_sqlserver(run_id, limit)
                source_counts.update(counts)
                node_writes += nodes
                relationship_writes += relationships
            completed = _now()
            document.update(
                {
                    "status": "COMPLETED",
                    "sourceCounts": source_counts,
                    "nodeWrites": node_writes,
                    "relationshipWrites": relationship_writes,
                    "constraintsApplied": constraints,
                    "completedAt": completed,
                }
            )
            await self._runs.update_one({"_id": run_id}, {"$set": document})
            return self._view(document)
        except Exception as error:
            document.update(
                {
                    "status": "FAILED",
                    "errorCode": type(error).__name__.upper()[:100],
                    "completedAt": _now(),
                }
            )
            await self._runs.update_one({"_id": run_id}, {"$set": document})
            raise

    async def _write(self, query: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        batch_size = self._settings.graph_sync_batch_size
        async with self._driver.session(database=self._settings.neo4j_database) as session:
            for offset in range(0, len(rows), batch_size):
                result = await session.run(query, rows=rows[offset : offset + batch_size])
                await result.consume()

    async def _sync_mongodb(
        self,
        run_id: str,
        limit: int,
        source_filter: Mapping[str, object] | None = None,
    ) -> tuple[dict[str, int], int, int]:
        now = _now().isoformat()
        query = dict(source_filter or {})
        counts: dict[str, int] = {}
        node_writes = 0
        relationship_writes = 0

        customer_documents = (
            await self._source_db["customerOutboundCDM"].find(query).limit(limit).to_list()
        )
        counts["customerOutboundCDM"] = len(customer_documents)
        customers: list[dict[str, Any]] = []
        accounts: list[dict[str, Any]] = []
        for document in customer_documents:
            party_id = _text(document.get("partyId"))
            customer_id = _text(document.get("customerId"))
            if party_id is None and customer_id is None:
                continue
            customer_key = party_id or customer_id
            assert customer_key is not None
            customers.append(
                {
                    "customer_key": customer_key,
                    "party_id": party_id,
                    "customer_id": customer_id,
                    "customer_name": _text(document.get("customerName")),
                    "phone_hash": (
                        contact_lookup_digest(
                            str(document.get("phoneNumber", "")),
                            "PHONE",
                            self._settings.contact_lookup_hmac_key.get_secret_value(),
                        )
                        if _text(document.get("phoneNumber"))
                        else None
                    ),
                    "email_hash": (
                        contact_lookup_digest(
                            str(document.get("email", "")),
                            "EMAIL",
                            self._settings.contact_lookup_hmac_key.get_secret_value(),
                        )
                        if _text(document.get("email"))
                        else None
                    ),
                    "source_system": "CUSTOMER_CDM",
                    "source_record_id": _text(document.get("_id")),
                    "source_updated_at": _iso(document.get("updatedAt")),
                    "graph_synced_at": now,
                    "sync_run_id": run_id,
                }
            )
            raw_accounts = document.get("custAccts", document.get("accounts", []))
            if isinstance(raw_accounts, list):
                for account in raw_accounts:
                    if not isinstance(account, dict):
                        continue
                    number = _text(account.get("accountNumber"))
                    if number:
                        accounts.append(
                            {
                                "account_key": f"CUSTOMER_CDM:{number}",
                                "customer_key": customer_key,
                                "account_number": number,
                                "customer_id": customer_id or number,
                                "graph_synced_at": now,
                                "sync_run_id": run_id,
                            }
                        )
        await self._write(
            """
            UNWIND $rows AS row
            MERGE (c:Customer {customer_key: row.customer_key})
            SET c += row
            """,
            customers,
        )
        await self._write(
            """
            UNWIND $rows AS row
            MATCH (c:Customer {customer_key: row.customer_key})
            MERGE (a:CustomerAccount {account_key: row.account_key})
            SET a += row
            MERGE (c)-[:HAS_ACCOUNT]->(a)
            """,
            accounts,
        )
        node_writes += len(customers) + len(accounts)
        relationship_writes += len(accounts)

        orders_docs = await self._source_db["salesInv"].find(query).limit(limit).to_list()
        counts["salesInv"] = len(orders_docs)
        order_rows: list[dict[str, Any]] = []
        line_rows: list[dict[str, Any]] = []
        for document in orders_docs:
            order_id = _text(_nested(document, "salesHdrEventData.orderId"))
            customer_id = _text(_nested(document, "salesHdr.salesHdrData.custId"))
            if order_id is None or customer_id is None:
                continue
            sell_wh = _text(_nested(document, "salesHdrEventData.sellWhseId"))
            ship_wh = _text(_nested(document, "salesHdrEventData.shipFromWhseId"))
            order_rows.append(
                {
                    "sales_order_number": order_id,
                    "customer_key": customer_id,
                    "customer_id": customer_id,
                    "customer_name": _text(_nested(document, "salesHdr.salesHdrData.custName")),
                    "source_system": (
                        _text(_nested(document, "salesHdrEventData.srcSysCode")) or "SALES_INV"
                    ),
                    "order_status": _text(_nested(document, "salesHdrEventData.orderStatus")),
                    "sell_warehouse_id": sell_wh,
                    "ship_from_warehouse_id": ship_wh,
                    "shipping_method": _text(_nested(document, "salesHdr.shipping.shipViaCode")),
                    "delivered_at": _iso(document.get("deliveredAt")),
                    "source_updated_at": _iso(document.get("updatedAt")),
                    "graph_synced_at": now,
                    "sync_run_id": run_id,
                }
            )
            raw_lines = document.get("salesLines", [])
            if isinstance(raw_lines, list):
                for position, line_wrapper in enumerate(raw_lines):
                    if not isinstance(line_wrapper, dict):
                        continue
                    line = line_wrapper.get("lineData", line_wrapper)
                    if not isinstance(line, dict):
                        continue
                    product_id = _text(line.get("productId"))
                    if product_id is None:
                        continue
                    line_key = _text(line.get("orderLineId")) or f"{order_id}:LINE:{position + 1}"
                    line_rows.append(
                        {
                            "order_line_key": line_key,
                            "sales_order_number": order_id,
                            "product_id": product_id,
                            "master_product_id": _text(line.get("masterProductId")),
                            "sku": _text(line.get("sku")),
                            "product_description": _text(line.get("productDesc")),
                            "product_type": _text(line.get("productType")) or "STANDARD",
                            "ordered_quantity": line.get("orderQty"),
                            "shipped_quantity": line.get("shipQty"),
                            "source_updated_at": _iso(document.get("updatedAt")),
                            "graph_synced_at": now,
                            "sync_run_id": run_id,
                        }
                    )
        await self._write(
            """
            UNWIND $rows AS row
            MERGE (c:Customer {customer_key: row.customer_key})
            ON CREATE SET c.customer_id=row.customer_id, c.customer_name=row.customer_name
            MERGE (o:SalesOrder {sales_order_number: row.sales_order_number})
            SET o += row
            MERGE (c)-[:PLACED_ORDER]->(o)
            FOREACH (_ IN CASE WHEN row.sell_warehouse_id IS NULL THEN [] ELSE [1] END |
              MERGE (w:Warehouse {warehouse_id: row.sell_warehouse_id})
              MERGE (o)-[:SOLD_BY_WAREHOUSE]->(w))
            FOREACH (_ IN CASE WHEN row.ship_from_warehouse_id IS NULL THEN [] ELSE [1] END |
              MERGE (w2:Warehouse {warehouse_id: row.ship_from_warehouse_id})
              MERGE (o)-[:SHIPPED_FROM_WAREHOUSE]->(w2))
            """,
            order_rows,
        )
        await self._write(
            """
            UNWIND $rows AS row
            MATCH (o:SalesOrder {sales_order_number: row.sales_order_number})
            MERGE (l:OrderLine {order_line_key: row.order_line_key})
            SET l += row
            MERGE (p:Product {product_id: row.product_id})
            SET p.sku=row.sku, p.master_product_id=row.master_product_id,
                p.product_description=row.product_description, p.product_type=row.product_type,
                p.graph_synced_at=row.graph_synced_at, p.sync_run_id=row.sync_run_id
            MERGE (o)-[:HAS_ORDER_LINE]->(l)
            MERGE (l)-[:REFERENCES_PRODUCT]->(p)
            """,
            line_rows,
        )
        node_writes += len(order_rows) + (2 * len(line_rows))
        relationship_writes += len(order_rows) + (2 * len(line_rows))

        shipment_docs = await self._source_db["shipmentInfo"].find(query).limit(limit).to_list()
        counts["shipmentInfo"] = len(shipment_docs)
        shipments = []
        for document in shipment_docs:
            tracking = _text(_nested(document, "shipmentInfoEventData.trkNum"))
            order_id = _text(_nested(document, "shipmentInfoEventData.trilOrdNum"))
            if tracking and order_id:
                shipments.append(
                    {
                        "tracking_number": tracking,
                        "sales_order_number": order_id,
                        "carrier_code": _text(
                            _nested(document, "shipmentInfoEventData.carrierCode")
                        ),
                        "shipped_at": _iso(_nested(document, "shipmentInfoEventData.shippedAt")),
                        "source_updated_at": _iso(document.get("updatedAt")),
                        "graph_synced_at": now,
                        "sync_run_id": run_id,
                    }
                )
        await self._write(
            """
            UNWIND $rows AS row
            MERGE (o:SalesOrder {sales_order_number: row.sales_order_number})
            MERGE (s:Shipment {tracking_number: row.tracking_number})
            SET s += row
            MERGE (o)-[:HAS_ORIGINAL_SHIPMENT]->(s)
            """,
            shipments,
        )
        node_writes += len(shipments)
        relationship_writes += len(shipments)

        product_docs = await self._source_db["lkpSearchProduct"].find(query).limit(limit).to_list()
        counts["lkpSearchProduct"] = len(product_docs)
        products = []
        for document in product_docs:
            product_id = _text(document.get("productId"))
            if product_id:
                products.append(
                    {
                        "product_id": product_id,
                        "sku": _text(document.get("sku")),
                        "master_product_id": _text(document.get("masterProductId")),
                        "product_description": _text(document.get("productDescription")),
                        "product_type": _text(document.get("productType")),
                        "source_updated_at": _iso(document.get("updatedAt")),
                        "graph_synced_at": now,
                        "sync_run_id": run_id,
                    }
                )
        await self._write(
            "UNWIND $rows AS row MERGE (p:Product {product_id: row.product_id}) SET p += row",
            products,
        )
        node_writes += len(products)
        return counts, node_writes, relationship_writes

    async def _sql_rows(self, query: str) -> list[dict[str, Any]]:
        def operation() -> list[dict[str, Any]]:
            timeout = max(1, int(self._settings.operation_timeout_seconds))
            with pymssql.connect(
                server=self._settings.sqlserver_host,
                port=str(self._settings.sqlserver_port),
                user=self._settings.sqlserver_user,
                password=self._settings.sqlserver_password.get_secret_value(),
                database=self._settings.sqlserver_database,
                login_timeout=timeout,
                timeout=timeout,
                autocommit=True,
            ) as connection:
                with connection.cursor(as_dict=True) as cursor:
                    cursor.execute(query)
                    return [dict(row) for row in cursor.fetchall()]

        async with asyncio.timeout(self._settings.operation_timeout_seconds):
            return await asyncio.to_thread(operation)

    async def _sync_sqlserver(self, run_id: str, limit: int) -> tuple[dict[str, int], int, int]:
        now = _now().isoformat()
        counts: dict[str, int] = {}
        node_writes = 0
        relationship_writes = 0

        returns = await self._sql_rows(
            f"SELECT TOP ({limit}) * FROM [dbo].[return_requests] ORDER BY updated_at DESC"
        )
        counts["dbo.return_requests"] = len(returns)
        return_rows = [
            {
                "return_reference": _text(row.get("return_reference")),
                "session_id": _text(row.get("session_id")),
                "sales_order_number": _text(row.get("order_reference")),
                "customer_reference": _text(row.get("customer_reference")),
                "return_status": _text(row.get("return_status")),
                "eligibility_decision": _text(row.get("eligibility_decision")),
                "source_updated_at": _iso(row.get("updated_at")),
                "graph_synced_at": now,
                "sync_run_id": run_id,
            }
            for row in returns
            if _text(row.get("return_reference")) and _text(row.get("order_reference"))
        ]
        await self._write(
            """
            UNWIND $rows AS row
            MERGE (o:SalesOrder {sales_order_number: row.sales_order_number})
            MERGE (r:Return {return_reference: row.return_reference})
            SET r += row
            MERGE (o)-[:HAS_RETURN]->(r)
            """,
            return_rows,
        )
        node_writes += len(return_rows)
        relationship_writes += len(return_rows)

        items = await self._sql_rows(
            f"SELECT TOP ({limit}) * FROM [dbo].[return_items] ORDER BY created_at DESC"
        )
        counts["dbo.return_items"] = len(items)
        item_rows = [
            {
                "return_item_id": _text(row.get("return_item_id")),
                "return_reference": _text(row.get("return_reference")),
                "order_line_id": _text(row.get("order_line_id")),
                "product_id": _text(row.get("product_id")),
                "quantity": row.get("quantity"),
                "reason_code": _text(row.get("reason_code")),
                "item_status": _text(row.get("item_status")),
                "source_updated_at": _iso(row.get("created_at")),
                "graph_synced_at": now,
                "sync_run_id": run_id,
            }
            for row in items
            if _text(row.get("return_item_id")) and _text(row.get("return_reference"))
        ]
        await self._write(
            """
            UNWIND $rows AS row
            MATCH (r:Return {return_reference: row.return_reference})
            MERGE (i:ReturnItem {return_item_id: row.return_item_id})
            SET i += row
            MERGE (r)-[:HAS_RETURN_ITEM]->(i)
            FOREACH (_ IN CASE WHEN row.order_line_id IS NULL THEN [] ELSE [1] END |
              MERGE (l:OrderLine {order_line_key: row.order_line_id})
              MERGE (i)-[:RETURN_ITEM_FOR_LINE]->(l))
            """,
            item_rows,
        )
        node_writes += len(item_rows)
        relationship_writes += len(item_rows)

        tracking = await self._sql_rows(
            f"SELECT TOP ({limit}) * FROM [dbo].[return_tracking] ORDER BY event_at DESC"
        )
        counts["dbo.return_tracking"] = len(tracking)
        tracking_rows = [
            {
                "tracking_id": _text(row.get("tracking_id")),
                "return_reference": _text(row.get("return_reference")),
                "tracking_type": _text(row.get("tracking_type")),
                "tracking_reference": _text(row.get("tracking_reference")),
                "carrier_code": _text(row.get("carrier_code")),
                "tracking_status": _text(row.get("tracking_status")),
                "event_at": _iso(row.get("event_at")),
                "graph_synced_at": now,
                "sync_run_id": run_id,
            }
            for row in tracking
            if _text(row.get("tracking_id")) and _text(row.get("return_reference"))
        ]
        await self._write(
            """
            UNWIND $rows AS row
            MATCH (r:Return {return_reference: row.return_reference})
            MERGE (t:ReturnTracking {tracking_id: row.tracking_id})
            SET t += row
            MERGE (r)-[:HAS_TRACKING]->(t)
            """,
            tracking_rows,
        )
        node_writes += len(tracking_rows)
        relationship_writes += len(tracking_rows)

        bays = await self._sql_rows(
            f"SELECT TOP ({limit}) * FROM [platform].[bay_configuration] ORDER BY priority, bay_id"
        )
        counts["platform.bay_configuration"] = len(bays)
        bay_rows = [
            {
                "bay_id": _text(row.get("bay_id")),
                "bay_name": _text(row.get("bay_name")),
                "warehouse_id": _text(row.get("warehouse_id")),
                "branch_id": _text(row.get("branch_id")),
                "bay_type": _text(row.get("bay_type")),
                "active": bool(row.get("active")),
                "priority": row.get("priority"),
                "max_package_count": row.get("max_package_count"),
                "overflow_bay_id": _text(row.get("overflow_bay_id")),
                "graph_synced_at": now,
                "sync_run_id": run_id,
            }
            for row in bays
            if _text(row.get("bay_id")) and _text(row.get("warehouse_id"))
        ]
        await self._write(
            """
            UNWIND $rows AS row
            MERGE (w:Warehouse {warehouse_id: row.warehouse_id})
            ON CREATE SET w.branch_id=row.branch_id
            MERGE (b:Bay {bay_id: row.bay_id})
            SET b += row
            MERGE (b)-[:LOCATED_IN_WAREHOUSE]->(w)
            """,
            bay_rows,
        )
        node_writes += len(bay_rows)
        relationship_writes += len(bay_rows)

        assignments = await self._sql_rows(
            f"""
            SELECT TOP ({limit}) assignment.*, item.return_item_id
            FROM [platform].[bay_assignment] AS assignment
            LEFT JOIN [dbo].[return_items] AS item
              ON item.return_reference=assignment.return_reference
             AND item.order_line_id=assignment.order_line_id
            ORDER BY assignment.created_at DESC
            """
        )
        counts["platform.bay_assignment"] = len(assignments)
        assignment_rows = [
            {
                "assignment_id": _text(row.get("assignment_id")),
                "return_reference": _text(row.get("return_reference")),
                "return_item_id": _text(row.get("return_item_id")),
                "order_line_id": _text(row.get("order_line_id")),
                "item_number": _text(row.get("item_number")),
                "package_count": row.get("package_count"),
                "warehouse_id": _text(row.get("warehouse_id")),
                "bay_id": _text(row.get("bay_id")),
                "status": _text(row.get("status")),
                "confirmed_by_associate_id": _text(row.get("confirmed_by_associate_id")),
                "graph_synced_at": now,
                "sync_run_id": run_id,
            }
            for row in assignments
            if _text(row.get("assignment_id")) and _text(row.get("bay_id"))
        ]
        await self._write(
            """
            UNWIND $rows AS row
            MERGE (a:BayAssignment {assignment_id: row.assignment_id})
            SET a += row
            MATCH (b:Bay {bay_id: row.bay_id})
            MERGE (a)-[:USES_BAY]->(b)
            FOREACH (_ IN CASE WHEN row.return_item_id IS NULL THEN [] ELSE [1] END |
              MERGE (i:ReturnItem {return_item_id: row.return_item_id})
              MERGE (a)-[:BINDS_RETURN_ITEM]->(i)
              MERGE (i)-[:ASSIGNED_TO_BAY]->(b))
            """,
            assignment_rows,
        )
        node_writes += len(assignment_rows)
        relationship_writes += 2 * len(assignment_rows)

        tickets = await self._sql_rows(
            f"SELECT TOP ({limit}) * FROM [integration].[return_support_ticket] "
            "ORDER BY updated_at DESC"
        )
        counts["integration.return_support_ticket"] = len(tickets)
        ticket_rows = [
            {
                "ticket_id": _text(row.get("ticket_id")),
                "session_id": _text(row.get("session_id")),
                "status": _text(row.get("status")),
                "external_reference": _text(row.get("external_reference")),
                "return_reference": _text(row.get("return_reference")),
                "updated_at": _iso(row.get("updated_at")),
                "graph_synced_at": now,
                "sync_run_id": run_id,
            }
            for row in tickets
            if _text(row.get("ticket_id"))
        ]
        await self._write(
            """
            UNWIND $rows AS row
            MERGE (t:SupportTicket {ticket_id: row.ticket_id})
            SET t += row
            FOREACH (_ IN CASE WHEN row.return_reference IS NULL THEN [] ELSE [1] END |
              MERGE (r:Return {return_reference: row.return_reference})
              MERGE (r)-[:TRACKED_BY_SUPPORT_TICKET]->(t))
            """,
            ticket_rows,
        )
        node_writes += len(ticket_rows)
        relationship_writes += sum(1 for row in ticket_rows if row["return_reference"])
        return counts, node_writes, relationship_writes
