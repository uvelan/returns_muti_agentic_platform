"""Associate-first conversational discovery, confirmation lock, and return handoff."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, cast

from neo4j import AsyncDriver
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pymongo import AsyncMongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError

from return_platform.configuration.settings import Settings
from return_platform.operations.models import ReturnCreateRequest, ReturnSessionView
from return_platform.operations.repository import OperationalRepository


class AssociateModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class AnchorType(StrEnum):
    ORDER_NUMBER = "ORDER_NUMBER"
    CUSTOMER_ID = "CUSTOMER_ID"
    PHONE = "PHONE"
    EMAIL = "EMAIL"
    TRACKING_NUMBER = "TRACKING_NUMBER"
    SKU = "SKU"


class ConversationMessage(AssociateModel):
    id: str
    role: str
    content: str
    createdAt: datetime


class OrderLineCandidate(AssociateModel):
    orderLineId: str
    productId: str
    sku: str | None = None
    productDescription: str | None = None
    productType: str | None = None
    shippedQuantity: int | float | None = None


class OrderCandidate(AssociateModel):
    customerReference: str
    customerName: str | None = None
    orderReference: str
    orderStatus: str | None = None
    sellWarehouseId: str | None = None
    shipFromWarehouseId: str | None = None
    shippingMethod: str | None = None
    confidenceMillionths: int = Field(ge=0, le=1_000_000)
    evidenceSource: str
    lines: list[OrderLineCandidate]


class DiscoveryLock(AssociateModel):
    customerReference: str
    orderReference: str
    orderLineId: str
    productId: str
    lockDigest: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmedBy: str
    confirmedAt: datetime


class AssociateConversationView(AssociateModel):
    id: str
    status: str
    anchorType: AnchorType
    anchorValueMasked: str
    messages: list[ConversationMessage]
    candidates: list[OrderCandidate]
    discoveryLock: DiscoveryLock | None = None
    returnDetails: dict[str, Any] | None = None
    returnSessionId: str | None = None
    nextQuestion: str | None = None
    version: int = Field(ge=0)
    createdAt: datetime
    updatedAt: datetime


class StartAssociateConversationRequest(AssociateModel):
    anchorType: AnchorType
    anchorValue: str = Field(min_length=2, max_length=256)

    @field_validator("anchorValue")
    @classmethod
    def normalize_anchor(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("anchorValue must not be blank")
        return normalized


class ConfirmDiscoveryRequest(AssociateModel):
    candidateIndex: int = Field(ge=0, le=99)
    orderLineId: str = Field(min_length=1, max_length=128)
    expectedVersion: int = Field(ge=0)


class ReturnDetailsRequest(AssociateModel):
    reasonCode: str = Field(min_length=1, max_length=64)
    returnQuantity: int = Field(ge=1, le=10_000)
    packageCount: int = Field(ge=1, le=10_000)
    shippingPathExpectation: str = Field(
        pattern=r"^(PPL|BOL|CUSTOMER_SHIP|NO_LABEL|DIRECT_VENDOR|FIELD_SCRAP)$"
    )
    notes: str | None = Field(default=None, max_length=2_000)
    expectedVersion: int = Field(ge=0)


def _now() -> datetime:
    return datetime.now(UTC)


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _mask(value: str, anchor_type: AnchorType) -> str:
    if anchor_type not in {AnchorType.PHONE, AnchorType.EMAIL}:
        return value
    if len(value) <= 4:
        return "****"
    return f"{value[:2]}***{value[-2:]}"


def _nested(document: dict[str, Any], path: str) -> Any:
    value: Any = document
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


class AssociateConversationService:
    """Graph-first discovery with targeted source fallback and immutable confirmation locks."""

    def __init__(
        self,
        *,
        platform_client: AsyncMongoClient[dict[str, object]],
        source_client: AsyncMongoClient[dict[str, object]],
        graph: AsyncDriver,
        settings: Settings,
        repository: OperationalRepository,
    ) -> None:
        self._db = platform_client[settings.mongo_database]
        self._source = source_client[settings.source_mongo_database]
        self._graph = graph
        self._graph_database = settings.neo4j_database
        self._conversations = self._db["associate_conversations"]
        self._locks = self._db["discovery_locks"]
        self._repository = repository

    async def ensure_indexes(self) -> None:
        await self._conversations.create_index([("createdAt", -1)])
        await self._conversations.create_index("status")
        index_info = await self._locks.index_information()
        for obsolete_name in ("lockDigest_1", "orderReference_1_orderLineId_1"):
            if obsolete_name in index_info:
                await self._locks.drop_index(obsolete_name)
        await self._locks.create_index("lockDigest")
        await self._locks.create_index("expiresAt", expireAfterSeconds=0)
        await self._locks.create_index(
            [("lockKey", 1)],
            unique=True,
            partialFilterExpression={"status": "ACTIVE"},
            name="active_discovery_lock_unique",
        )

    @staticmethod
    def _view(document: dict[str, Any]) -> AssociateConversationView:
        return AssociateConversationView.model_validate(
            {
                "id": str(document["_id"]),
                **{key: value for key, value in document.items() if key != "_id"},
            }
        )

    @staticmethod
    def _message(role: str, content: str) -> dict[str, Any]:
        return {
            "id": str(uuid.uuid4()),
            "role": role,
            "content": content,
            "createdAt": _now(),
        }

    async def _graph_candidates(
        self, anchor_type: AnchorType, anchor_value: str
    ) -> list[OrderCandidate]:
        normalized_hash = hashlib.sha256(anchor_value.strip().lower().encode()).hexdigest()
        queries = {
            AnchorType.ORDER_NUMBER: (
                "MATCH (c:Customer)-[:PLACED_ORDER]->(o:SalesOrder {sales_order_number:$value}) "
                "OPTIONAL MATCH (o)-[:HAS_ORDER_LINE]->(l:OrderLine) "
                "OPTIONAL MATCH (l)-[:REFERENCES_PRODUCT]->(p:Product) "
                "RETURN c,o,collect({line:l,product:p}) AS lines LIMIT 20",
                anchor_value,
            ),
            AnchorType.CUSTOMER_ID: (
                "MATCH (c:Customer)-[:PLACED_ORDER]->(o:SalesOrder) "
                "WHERE c.customer_id=$value OR c.customer_key=$value "
                "OPTIONAL MATCH (o)-[:HAS_ORDER_LINE]->(l:OrderLine) "
                "OPTIONAL MATCH (l)-[:REFERENCES_PRODUCT]->(p:Product) "
                "RETURN c,o,collect({line:l,product:p}) AS lines LIMIT 20",
                anchor_value,
            ),
            AnchorType.PHONE: (
                "MATCH (c:Customer {phone_hash:$value})-[:PLACED_ORDER]->(o:SalesOrder) "
                "OPTIONAL MATCH (o)-[:HAS_ORDER_LINE]->(l:OrderLine) "
                "OPTIONAL MATCH (l)-[:REFERENCES_PRODUCT]->(p:Product) "
                "RETURN c,o,collect({line:l,product:p}) AS lines LIMIT 20",
                normalized_hash,
            ),
            AnchorType.EMAIL: (
                "MATCH (c:Customer {email_hash:$value})-[:PLACED_ORDER]->(o:SalesOrder) "
                "OPTIONAL MATCH (o)-[:HAS_ORDER_LINE]->(l:OrderLine) "
                "OPTIONAL MATCH (l)-[:REFERENCES_PRODUCT]->(p:Product) "
                "RETURN c,o,collect({line:l,product:p}) AS lines LIMIT 20",
                normalized_hash,
            ),
            AnchorType.TRACKING_NUMBER: (
                "MATCH (o:SalesOrder)-[:HAS_ORIGINAL_SHIPMENT]->"
                "(:Shipment {tracking_number:$value}) "
                "MATCH (c:Customer)-[:PLACED_ORDER]->(o) "
                "OPTIONAL MATCH (o)-[:HAS_ORDER_LINE]->(l:OrderLine) "
                "OPTIONAL MATCH (l)-[:REFERENCES_PRODUCT]->(p:Product) "
                "RETURN c,o,collect({line:l,product:p}) AS lines LIMIT 20",
                anchor_value,
            ),
            AnchorType.SKU: (
                "MATCH (o:SalesOrder)-[:HAS_ORDER_LINE]->(l:OrderLine) "
                "MATCH (l)-[:REFERENCES_PRODUCT]->(p:Product) "
                "WHERE p.sku=$value OR p.product_id=$value "
                "MATCH (c:Customer)-[:PLACED_ORDER]->(o) "
                "RETURN c,o,collect({line:l,product:p}) AS lines LIMIT 20",
                anchor_value,
            ),
        }
        query, query_value = queries[anchor_type]
        records, _, _ = await self._graph.execute_query(
            query, value=query_value, database_=self._graph_database
        )
        candidates: list[OrderCandidate] = []
        for record in records:
            customer = dict(record["c"])
            order = dict(record["o"])
            lines: list[OrderLineCandidate] = []
            for entry in record["lines"]:
                line = dict(entry["line"]) if entry.get("line") is not None else {}
                product = dict(entry["product"]) if entry.get("product") is not None else {}
                if not line:
                    continue
                lines.append(
                    OrderLineCandidate(
                        orderLineId=str(line.get("order_line_key", "")),
                        productId=str(product.get("product_id", line.get("product_id", ""))),
                        sku=cast(str | None, product.get("sku")),
                        productDescription=cast(str | None, product.get("product_description")),
                        productType=cast(str | None, product.get("product_type")),
                        shippedQuantity=cast(int | float | None, line.get("shipped_quantity")),
                    )
                )
            if lines:
                candidates.append(
                    OrderCandidate(
                        customerReference=str(
                            customer.get("customer_id") or customer.get("customer_key")
                        ),
                        customerName=cast(str | None, customer.get("customer_name")),
                        orderReference=str(order.get("sales_order_number")),
                        orderStatus=cast(str | None, order.get("order_status")),
                        sellWarehouseId=cast(str | None, order.get("sell_warehouse_id")),
                        shipFromWarehouseId=cast(str | None, order.get("ship_from_warehouse_id")),
                        shippingMethod=cast(str | None, order.get("shipping_method")),
                        confidenceMillionths=980_000,
                        evidenceSource="NEO4J_GRAPH",
                        lines=lines,
                    )
                )
        return candidates

    async def _source_documents(
        self, anchor_type: AnchorType, anchor_value: str
    ) -> list[dict[str, Any]]:
        if anchor_type is AnchorType.ORDER_NUMBER:
            query: dict[str, Any] = {"salesHdrEventData.orderId": anchor_value}
        elif anchor_type is AnchorType.CUSTOMER_ID:
            query = {"salesHdr.salesHdrData.custId": anchor_value}
        elif anchor_type in {AnchorType.PHONE, AnchorType.EMAIL}:
            field = "phoneNumber" if anchor_type is AnchorType.PHONE else "email"
            customer = await self._source["customerOutboundCDM"].find_one({field: anchor_value})
            customer_id = customer.get("customerId") if customer else None
            if customer_id is None:
                return []
            query = {"salesHdr.salesHdrData.custId": customer_id}
        elif anchor_type is AnchorType.TRACKING_NUMBER:
            shipment = await self._source["shipmentInfo"].find_one(
                {"shipmentInfoEventData.trkNum": anchor_value}
            )
            order_id = _nested(
                cast(dict[str, Any], shipment or {}),
                "shipmentInfoEventData.trilOrdNum",
            )
            if order_id is None:
                return []
            query = {"salesHdrEventData.orderId": order_id}
        else:
            query = {
                "$or": [
                    {"salesLines.lineData.sku": anchor_value},
                    {"salesLines.lineData.productId": anchor_value},
                ]
            }
        cursor = self._source["salesInv"].find(query).limit(20)
        return [cast(dict[str, Any], item) async for item in cursor]

    @staticmethod
    def _source_candidate(document: dict[str, Any]) -> OrderCandidate | None:
        order_reference = _nested(document, "salesHdrEventData.orderId")
        customer_reference = _nested(document, "salesHdr.salesHdrData.custId")
        if order_reference is None or customer_reference is None:
            return None
        lines: list[OrderLineCandidate] = []
        raw_lines = document.get("salesLines", [])
        if isinstance(raw_lines, list):
            for position, wrapper in enumerate(raw_lines):
                if not isinstance(wrapper, dict):
                    continue
                line = wrapper.get("lineData", wrapper)
                if not isinstance(line, dict) or line.get("productId") is None:
                    continue
                lines.append(
                    OrderLineCandidate(
                        orderLineId=str(
                            line.get("orderLineId") or f"{order_reference}:LINE:{position + 1}"
                        ),
                        productId=str(line["productId"]),
                        sku=cast(str | None, line.get("sku")),
                        productDescription=cast(str | None, line.get("productDesc")),
                        productType=cast(str | None, line.get("productType")),
                        shippedQuantity=cast(int | float | None, line.get("shipQty")),
                    )
                )
        if not lines:
            return None
        return OrderCandidate(
            customerReference=str(customer_reference),
            customerName=cast(str | None, _nested(document, "salesHdr.salesHdrData.custName")),
            orderReference=str(order_reference),
            orderStatus=cast(str | None, _nested(document, "salesHdrEventData.orderStatus")),
            sellWarehouseId=cast(str | None, _nested(document, "salesHdrEventData.sellWhseId")),
            shipFromWarehouseId=cast(
                str | None, _nested(document, "salesHdrEventData.shipFromWhseId")
            ),
            shippingMethod=cast(str | None, _nested(document, "salesHdr.shipping.shipViaCode")),
            confidenceMillionths=900_000,
            evidenceSource="SOURCE_MONGODB_TARGETED_FALLBACK",
            lines=lines,
        )

    async def _targeted_graph_upsert(self, candidates: list[OrderCandidate]) -> None:
        rows = [candidate.model_dump(mode="json") for candidate in candidates]
        if not rows:
            return
        await self._graph.execute_query(
            """
            UNWIND $rows AS row
            MERGE (c:Customer {customer_key: row.customerReference})
            SET c.customer_id=row.customerReference, c.customer_name=row.customerName,
                c.graph_synced_at=$syncedAt, c.sync_run_id=$syncRunId
            MERGE (o:SalesOrder {sales_order_number: row.orderReference})
            SET o.order_status=row.orderStatus, o.sell_warehouse_id=row.sellWarehouseId,
                o.ship_from_warehouse_id=row.shipFromWarehouseId,
                o.shipping_method=row.shippingMethod, o.graph_synced_at=$syncedAt,
                o.sync_run_id=$syncRunId
            MERGE (c)-[:PLACED_ORDER]->(o)
            FOREACH (line IN row.lines |
                MERGE (l:OrderLine {order_line_key: line.orderLineId})
                SET l.shipped_quantity=line.shippedQuantity, l.graph_synced_at=$syncedAt,
                    l.sync_run_id=$syncRunId
                MERGE (p:Product {product_id: line.productId})
                SET p.sku=line.sku, p.product_description=line.productDescription,
                    p.product_type=line.productType, p.graph_synced_at=$syncedAt,
                    p.sync_run_id=$syncRunId
                MERGE (o)-[:HAS_ORDER_LINE]->(l)
                MERGE (l)-[:REFERENCES_PRODUCT]->(p))
            """,
            rows=rows,
            syncedAt=_now().isoformat(),
            syncRunId=f"TARGETED:{uuid.uuid4()}",
            database_=self._graph_database,
        )

    async def start(
        self,
        payload: StartAssociateConversationRequest,
        *,
        actor_id: str,
    ) -> AssociateConversationView:
        await self.ensure_indexes()
        candidates = await self._graph_candidates(payload.anchorType, payload.anchorValue)
        if not candidates:
            documents = await self._source_documents(payload.anchorType, payload.anchorValue)
            candidates = [
                candidate
                for document in documents
                if (candidate := self._source_candidate(document))
            ]
            await self._targeted_graph_upsert(candidates)
        now = _now()
        if candidates:
            assistant_text = (
                f"I found {len(candidates)} candidate order(s). Confirm the customer, "
                "order, and exact order line to lock discovery."
            )
            status = "DISCOVERY_READY"
            next_question = "Which order and order line should be returned?"
        else:
            assistant_text = (
                "No order matched that evidence. Add a stronger anchor such as order "
                "number, customer ID, tracking number, or SKU."
            )
            status = "NO_MATCH"
            next_question = "What additional order evidence can you provide?"
        document: dict[str, Any] = {
            "_id": str(uuid.uuid4()),
            "status": status,
            "anchorType": payload.anchorType.value,
            "anchorValueMasked": _mask(payload.anchorValue, payload.anchorType),
            "anchorDigest": _digest(
                {"type": payload.anchorType.value, "value": payload.anchorValue}
            ),
            "messages": [
                self._message(
                    "ASSOCIATE",
                    f"Start return lookup using {payload.anchorType.value}.",
                ),
                self._message("AI_ASSISTANT", assistant_text),
            ],
            "candidates": [candidate.model_dump(mode="json") for candidate in candidates],
            "discoveryLock": None,
            "returnDetails": None,
            "returnSessionId": None,
            "nextQuestion": next_question,
            "version": 0,
            "createdBy": actor_id,
            "createdAt": now,
            "updatedAt": now,
        }
        await self._conversations.insert_one(document)
        return self._view(document)

    async def list(self, limit: int = 100) -> list[AssociateConversationView]:
        documents = await self._conversations.find({}).sort("createdAt", -1).limit(limit).to_list()
        return [self._view(document) for document in documents]

    async def get(self, conversation_id: str) -> AssociateConversationView | None:
        document = await self._conversations.find_one({"_id": conversation_id})
        return None if document is None else self._view(document)

    async def confirm(
        self,
        conversation_id: str,
        payload: ConfirmDiscoveryRequest,
        *,
        actor_id: str,
    ) -> AssociateConversationView:
        conversation = await self.get(conversation_id)
        if conversation is None:
            raise KeyError(conversation_id)
        if conversation.version != payload.expectedVersion:
            raise RuntimeError("Conversation version conflict")
        if conversation.discoveryLock is not None:
            return conversation
        if payload.candidateIndex >= len(conversation.candidates):
            raise ValueError("candidateIndex is out of range")
        candidate = conversation.candidates[payload.candidateIndex]
        line = next(
            (item for item in candidate.lines if item.orderLineId == payload.orderLineId), None
        )
        if line is None:
            raise ValueError("orderLineId does not belong to the selected order")
        lock_payload = {
            "customerReference": candidate.customerReference,
            "orderReference": candidate.orderReference,
            "orderLineId": line.orderLineId,
            "productId": line.productId,
        }
        lock_key = f"{candidate.orderReference}:{line.orderLineId}"
        lock = DiscoveryLock(
            **lock_payload,
            lockDigest=_digest(lock_payload),
            confirmedBy=actor_id,
            confirmedAt=_now(),
        )
        try:
            await self._locks.insert_one(
                {
                    "_id": str(uuid.uuid4()),
                    **lock.model_dump(mode="json"),
                    "conversationId": conversation_id,
                    "lockKey": lock_key,
                    "status": "ACTIVE",
                    "expiresAt": _now() + timedelta(minutes=30),
                    "returnSessionId": None,
                }
            )
        except DuplicateKeyError as error:
            existing = await self._locks.find_one({"lockKey": lock_key, "status": "ACTIVE"})
            if existing is None or existing.get("conversationId") != conversation_id:
                raise RuntimeError(
                    "Order line is already locked by another return session"
                ) from error
        updated = await self._conversations.find_one_and_update(
            {"_id": conversation_id, "version": payload.expectedVersion, "discoveryLock": None},
            {
                "$set": {
                    "status": "DETAILS_REQUIRED",
                    "discoveryLock": lock.model_dump(mode="json"),
                    "nextQuestion": "Why is the item being returned?",
                    "updatedAt": _now(),
                },
                "$push": {
                    "messages": {
                        "$each": [
                            self._message(
                                "ASSOCIATE",
                                f"Confirmed {candidate.orderReference} / {line.orderLineId}.",
                            ),
                            self._message(
                                "AI_ASSISTANT",
                                "Discovery is locked. Provide reason, quantity, package "
                                "count, shipping path, and optional notes.",
                            ),
                        ]
                    }
                },
                "$inc": {"version": 1},
            },
            return_document=ReturnDocument.AFTER,
        )
        if updated is None:
            raise RuntimeError("Conversation version conflict")
        return self._view(cast(dict[str, Any], updated))

    async def submit_details(
        self,
        conversation_id: str,
        payload: ReturnDetailsRequest,
        *,
        actor_id: str,
        correlation_id: str,
    ) -> tuple[AssociateConversationView, ReturnSessionView]:
        conversation = await self.get(conversation_id)
        if conversation is None:
            raise KeyError(conversation_id)
        if conversation.version != payload.expectedVersion:
            raise RuntimeError("Conversation version conflict")
        lock = conversation.discoveryLock
        if lock is None:
            raise ValueError("Discovery must be confirmed before collecting return details")
        selected_line = next(
            (
                item
                for candidate in conversation.candidates
                if candidate.orderReference == lock.orderReference
                for item in candidate.lines
                if item.orderLineId == lock.orderLineId
            ),
            None,
        )
        if selected_line is None:
            raise RuntimeError("Locked order line is absent from the sealed discovery context")
        if (
            selected_line.shippedQuantity is not None
            and payload.returnQuantity > selected_line.shippedQuantity
        ):
            raise ValueError("Return quantity exceeds the shipped quantity for the locked line")
        details = payload.model_dump(mode="json", exclude={"expectedVersion"})
        session = await self._repository.create_return(
            ReturnCreateRequest(
                customerReference=lock.customerReference,
                orderReference=lock.orderReference,
                itemReferences=[lock.orderLineId],
                productReferences=[lock.productId],
                processingWarehouseReference=next(
                    (
                        candidate.sellWarehouseId or candidate.shipFromWarehouseId
                        for candidate in conversation.candidates
                        if candidate.orderReference == lock.orderReference
                    ),
                    None,
                ),
                productType=selected_line.productType,
                reasonCode=payload.reasonCode,
                returnQuantity=payload.returnQuantity,
                packageCount=payload.packageCount,
                shippingPathExpectation=payload.shippingPathExpectation,
                notes=payload.notes,
                channel="ASSOCIATE",
                idempotencyKey=f"associate:{conversation_id}:{lock.lockDigest}",
            ),
            correlation_id=correlation_id,
            actor_id=actor_id,
        )
        await self._locks.update_one(
            {"lockDigest": lock.lockDigest, "conversationId": conversation_id, "status": "ACTIVE"},
            {
                "$set": {
                    "returnSessionId": session.id,
                    "expiresAt": _now() + timedelta(days=7),
                }
            },
        )
        updated = await self._conversations.find_one_and_update(
            {"_id": conversation_id, "version": payload.expectedVersion},
            {
                "$set": {
                    "status": "SUBMITTED",
                    "returnDetails": details,
                    "returnSessionId": session.id,
                    "nextQuestion": None,
                    "updatedAt": _now(),
                },
                "$push": {
                    "messages": {
                        "$each": [
                            self._message("ASSOCIATE", "Confirmed return handling details."),
                            self._message(
                                "AI_ASSISTANT",
                                f"Return session {session.id} was created and handed to "
                                "Return Support processing.",
                            ),
                        ]
                    }
                },
                "$inc": {"version": 1},
            },
            return_document=ReturnDocument.AFTER,
        )
        if updated is None:
            raise RuntimeError("Conversation version conflict")
        return self._view(cast(dict[str, Any], updated)), session
