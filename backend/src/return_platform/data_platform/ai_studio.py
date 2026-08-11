"""Governed Data Console AI Studio proposal generation and sandbox apply service."""

from __future__ import annotations

import asyncio
import builtins
import hashlib
import json
import random
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import pymssql
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pymongo import AsyncMongoClient, ReplaceOne, ReturnDocument
from pymongo.errors import DuplicateKeyError

from return_platform.configuration.settings import Settings
from return_platform.data_platform.operational_generation.deterministic_values import (
    get_synthetic_name,
)
from return_platform.data_platform.schema_registry import DataAssetSchema, SchemaRegistry

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
DIRECT_MONGO_COLLECTIONS = frozenset(
    {
        "salesInv",
        "customerOutboundCDM",
        "shipmentInfo",
        "lkpSearchProduct",
        "customers",
        "products",
        "orders",
        "workspaces",
        "sandbox_records",
        "scenarios",
        "scenario_records",
    }
)
DIRECT_SQL_ASSETS = frozenset(
    {
        "platform.sql.bay_configuration",
    }
)
RELATIONAL_CUSTOMER_ASSETS = (
    "source.mongodb.customer_outbound_cdm",
    "source.mongodb.customers",
)
RELATIONAL_PRODUCT_ASSETS = (
    "source.mongodb.product_search",
    "source.mongodb.products",
)
RELATIONAL_ORDER_ASSETS = (
    "source.mongodb.sales_inv",
    "source.mongodb.shipment_info",
    "source.mongodb.orders",
)
RELATIONAL_REFERENCE_ASSETS = (
    "sandbox.mongodb.warehouses",
    "platform.sql.bay_configuration",
)
SANDBOX_WAREHOUSES = (
    ("WH-CHENNAI-01", "CHENNAI"),
    ("WH-ATLANTA-01", "ATLANTA"),
    ("WH-DALLAS-01", "DALLAS"),
)

SUPPORTED_GENERATORS = frozenset(
    {
        "actor_type",
        "agent_name",
        "associate_reference",
        "audit_operation",
        "base_model_number",
        "bay_assignment_status",
        "bay_name",
        "bay_reference",
        "bay_type",
        "branch_reference",
        "brand_type",
        "carrier_code",
        "category",
        "cdm_parties",
        "city",
        "clarification",
        "confidence",
        "configuration_version",
        "correlation_id",
        "customer_addresses",
        "customer_document_id",
        "customer_po_number",
        "customer_reference",
        "customer_tier",
        "decision",
        "decision_explanation",
        "decision_type",
        "department_description",
        "eligibility_stage",
        "empty_array",
        "entity_type",
        "erp_code",
        "evidence_references",
        "external_ticket_reference",
        "false",
        "fulfillment_reference",
        "fulfillment_status",
        "job_name",
        "legacy_items",
        "master_product_reference",
        "model_name",
        "null",
        "one",
        "order_line_reference",
        "order_reference",
        "order_status",
        "package_capacity",
        "package_count",
        "party_id",
        "party_type",
        "past_datetime",
        "person_name",
        "phone",
        "postal_code",
        "priority",
        "product_description",
        "product_document_id",
        "product_long_description",
        "product_reference",
        "product_types_json",
        "provider_name",
        "quantity",
        "reason_code",
        "recent_datetime",
        "recommendation",
        "region",
        "return_item_status",
        "return_reference",
        "return_session_payload",
        "return_status",
        "return_window_days",
        "review_status",
        "running_status",
        "sales_doc_type",
        "sales_invoice_id",
        "sales_lines",
        "sales_type",
        "scenario_id",
        "schema_version",
        "seed_version",
        "session_document_id",
        "session_event_type",
        "sha256",
        "ship_via_code",
        "ship_via_description",
        "shipment_document_id",
        "shipping_paths_json",
        "sku",
        "source_code",
        "state",
        "success_status",
        "support_ticket_status",
        "ticket_reference",
        "tracking_reference",
        "tracking_status",
        "tracking_type",
        "true",
        "unit_of_measure",
        "unit_of_measure_description",
        "upc_code",
        "uuid",
        "vendor_name",
        "warehouse_reference",
        "web_display_name",
        "workflow_stage",
        "zero",
    }
)


class StudioModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class AIStudioGenerationRequest(StudioModel):
    assetIds: list[str] = Field(min_length=1, max_length=50)
    recordsPerAsset: int = Field(default=5, ge=1, le=500)
    seed: int = Field(default=20260724, ge=0, le=2_147_483_647)
    mode: Literal["DETERMINISTIC", "AI_ASSISTED"] = "DETERMINISTIC"
    scenarioName: str = Field(default="return-sandbox", min_length=3, max_length=128)

    @field_validator("assetIds")
    @classmethod
    def unique_assets(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item for item in normalized) or len(normalized) != len(set(normalized)):
            raise ValueError("assetIds must contain unique non-blank IDs")
        return normalized


class AIStudioApplyRequest(StudioModel):
    expectedDigest: str = Field(pattern=r"^[a-f0-9]{64}$")


class AIStudioPromptRequest(StudioModel):
    prompt: str = Field(min_length=10, max_length=1_000)
    seed: int = Field(default=20260724, ge=0, le=2_147_483_647)
    scenarioName: str = Field(default="customer-order-sandbox", min_length=3, max_length=128)


class AIStudioProposalView(StudioModel):
    id: str
    scenarioName: str
    mode: str
    seed: int
    assetIds: list[str]
    recordsPerAsset: int
    digest: str
    status: Literal["DRAFT", "APPLIED", "PARTIALLY_APPLIED", "REJECTED"]
    recordCounts: dict[str, int]
    appliedAssets: list[str] = Field(default_factory=list)
    blockedAssets: list[str] = Field(default_factory=list)
    applyErrors: dict[str, str] = Field(default_factory=dict)
    createdBy: str
    createdAt: datetime
    appliedBy: str | None = None
    appliedAt: datetime | None = None
    generationPrompt: str | None = None
    generationPlan: dict[str, int] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ScenarioContext:
    index: int
    customer_reference: str
    customer_name: str
    party_id: str
    phone: str
    email: str
    order_reference: str
    order_line_reference: str
    product_reference: str
    master_product_reference: str
    sku: str
    product_description: str
    product_type: str
    warehouse_reference: str
    branch_reference: str
    tracking_reference: str
    return_reference: str
    fulfillment_reference: str
    ticket_reference: str
    bay_reference: str
    session_id: str
    correlation_id: str
    delivered_at: datetime
    updated_at: datetime


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _canonical_digest(records: dict[str, list[dict[str, Any]]]) -> str:
    payload = json.dumps(records, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _scenario_context(index: int, rng: random.Random, *, seed: int) -> ScenarioContext:
    suffix = f"{rng.randrange(100000, 999999)}{index:03d}"
    customer = f"CUST-{suffix}"
    order = f"SO-{suffix}"
    product = f"PRD-{rng.randrange(1000, 9999)}"
    now = _utc_now().replace(microsecond=0)
    delivered = now - timedelta(days=rng.randrange(1, 40))
    return ScenarioContext(
        index=index,
        customer_reference=customer,
        customer_name=get_synthetic_name(index, seed=seed),
        party_id=f"PTY-{suffix}",
        phone=f"+1-555-{rng.randrange(100, 999)}-{rng.randrange(1000, 9999)}",
        email=f"sandbox.customer.{suffix}@example.invalid",
        order_reference=order,
        order_line_reference=f"{order}-L1",
        product_reference=product,
        master_product_reference=f"M-{product}",
        sku=f"SKU-{rng.randrange(100000, 999999)}",
        product_description=f"Sandbox return product {index + 1}",
        product_type=rng.choice(("STANDARD", "BULKY", "HAZARDOUS_REVIEW")),
        warehouse_reference=(
            f"WH-{rng.choice(('CHENNAI', 'ATLANTA', 'DALLAS'))}-{rng.randrange(1, 4):02d}"
        ),
        branch_reference=f"BR-{rng.randrange(100, 999)}",
        tracking_reference=f"1Z{rng.randrange(10**14, 10**15 - 1)}",
        return_reference=f"RMA-{suffix}",
        fulfillment_reference=f"FUL-{suffix}",
        ticket_reference=f"RST-{suffix}",
        bay_reference=f"BAY-{rng.choice(('PPL', 'BOL', 'HOLD'))}-{rng.randrange(1, 10):02d}",
        session_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"return-studio:{suffix}")),
        correlation_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"return-studio-correlation:{suffix}")),
        delivered_at=delivered,
        updated_at=now,
    )


def _bulk_order_context(
    customer_index: int,
    order_index: int,
    *,
    seed: int,
) -> ScenarioContext:
    customer_rng = random.Random(f"{seed}:customer:{customer_index}")
    order_rng = random.Random(f"{seed}:customer:{customer_index}:order:{order_index}")
    customer_suffix = f"{customer_rng.randrange(100000, 999999)}{customer_index:04d}"
    order_suffix = f"{customer_index:04d}{order_index:04d}{order_rng.randrange(1000, 9999)}"
    customer = f"CUST-{customer_suffix}"
    order = f"SO-{order_suffix}"
    product_rng = random.Random(f"{seed}:product:{order_index}")
    product = f"PRD-{order_index + 1:04d}"
    warehouse_city = order_rng.choice(("CHENNAI", "ATLANTA", "DALLAS"))
    warehouse = f"WH-{warehouse_city}-01"
    bay_type = order_rng.choice(("PPL", "BOL", "HOLD"))
    now = _utc_now().replace(microsecond=0)
    delivered = now - timedelta(days=order_rng.randrange(1, 40))
    return ScenarioContext(
        index=(customer_index * 100_000) + order_index,
        customer_reference=customer,
        customer_name=get_synthetic_name(customer_index, seed=seed),
        party_id=f"PTY-{customer_suffix}",
        phone=(f"+1-555-{customer_rng.randrange(100, 999)}-{customer_rng.randrange(1000, 9999)}"),
        email=f"sandbox.customer.{customer_suffix}@example.invalid",
        order_reference=order,
        order_line_reference=f"{order}-L1",
        product_reference=product,
        master_product_reference=f"M-{product}",
        sku=f"SKU-{product_rng.randrange(100000, 999999)}",
        product_description=f"Sandbox order product {order_index + 1}",
        product_type=product_rng.choice(("STANDARD", "BULKY", "HAZARDOUS_REVIEW")),
        warehouse_reference=warehouse,
        branch_reference=f"BR-{order_rng.randrange(100, 999)}",
        tracking_reference=f"1Z{order_rng.randrange(10**14, 10**15 - 1)}",
        return_reference=f"RMA-{order_suffix}",
        fulfillment_reference=f"FUL-{order_suffix}",
        ticket_reference=f"RST-{order_suffix}",
        bay_reference=f"BAY-{warehouse_city}-{bay_type}-01",
        session_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"return-studio:{order_suffix}")),
        correlation_id=str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"return-studio-correlation:{order_suffix}")
        ),
        delivered_at=delivered,
        updated_at=now,
    )


def _parse_customer_order_prompt(prompt: str) -> tuple[int, int]:
    normalized = " ".join(prompt.lower().split())
    customer_match = re.search(r"(\d[\d,]*)\s+cus\w*", normalized)
    per_customer_match = re.search(
        r"(\d[\d,]*)\s+orders?\s+(?:for|per)\s+(?:each|every)\s+cus\w*",
        normalized,
    )
    if customer_match is None or per_customer_match is None:
        raise ValueError(
            "Describe the request as '<count> customers and <count> orders for each customer'."
        )
    customer_count = int(customer_match.group(1).replace(",", ""))
    orders_per_customer = int(per_customer_match.group(1).replace(",", ""))
    if not 1 <= customer_count <= 500:
        raise ValueError("Customer count must be between 1 and 500")
    if not 1 <= orders_per_customer <= 100:
        raise ValueError("Orders per customer must be between 1 and 100")
    if customer_count * orders_per_customer > 50_000:
        raise ValueError("The governed bulk proposal limit is 50,000 orders")
    return customer_count, orders_per_customer


def _nested_set(document: dict[str, Any], path: str, value: Any) -> None:
    current = document
    parts = path.split(".")
    for part in parts[:-1]:
        nested = current.get(part)
        if not isinstance(nested, dict):
            nested = {}
            current[part] = nested
        current = nested
    current[parts[-1]] = value


def _value(generator: str | None, context: ScenarioContext, rng: random.Random) -> Any:
    if generator not in SUPPORTED_GENERATORS:
        raise ValueError(f"Unsupported AI Studio generator: {generator!r}")
    if generator == "null":
        return None
    values: dict[str, Any] = {
        "uuid": context.session_id,
        "sales_invoice_id": f"SANDBOX*{context.order_reference}",
        "customer_document_id": f"CUSTOMER*{context.customer_reference}",
        "shipment_document_id": f"SHIPMENT*{context.tracking_reference}",
        "product_document_id": f"PRODUCT*{context.product_reference}",
        "order_reference": context.order_reference,
        "customer_reference": context.customer_reference,
        "person_name": context.customer_name,
        "party_id": context.party_id,
        "phone": context.phone,
        "email": context.email,
        "order_line_reference": context.order_line_reference,
        "product_reference": context.product_reference,
        "master_product_reference": context.master_product_reference,
        "sku": context.sku,
        "product_description": context.product_description,
        "product_type": context.product_type,
        "warehouse_reference": context.warehouse_reference,
        "branch_reference": context.branch_reference,
        "tracking_reference": context.tracking_reference,
        "return_reference": context.return_reference,
        "fulfillment_reference": context.fulfillment_reference,
        "ticket_reference": context.ticket_reference,
        "external_ticket_reference": f"SUPPORT-{context.ticket_reference}",
        "bay_reference": context.bay_reference,
        "associate_reference": "associate-demo",
        # Vocabularies from the real Ferguson documents, matching
        # `operational_generation.generator._LITERAL_GENERATORS` so the two
        # generation paths do not disagree about what a valid value looks like.
        "sales_doc_type": "headerLines",
        "sales_type": "INV",
        "party_type": "PARTY",
        "brand_type": "National Brand",
        "unit_of_measure": "EA",
        "unit_of_measure_description": "EACH",
        # Descriptive and identifying fields the source carries per record.
        # Derived from the scenario context rather than fixed, so records in one
        # run stay distinguishable from each other.
        "base_model_number": f"MODEL-{context.sku}",
        "upc_code": f"7818{context.index:08d}",
        "vendor_name": "SANDBOX SUPPLY CO",
        "web_display_name": context.product_description.title(),
        "product_long_description": f"{context.product_description.title()} (sandbox record)",
        "department_description": "PLUMBING",
        "customer_po_number": f"PO-{context.order_reference}",
        "job_name": f"JOB-{context.customer_reference}",
        "city": "CHARLOTTE",
        "state": "NC",
        "postal_code": "28202",
        "erp_code": "OMC",
        "source_code": "SANDBOX",
        "order_status": "DELIVERED",
        "ship_via_code": "PPL",
        "ship_via_description": "Prepaid parcel label",
        "carrier_code": "UPS",
        "past_datetime": context.delivered_at,
        "recent_datetime": context.updated_at,
        "zero": 0,
        "one": 1,
        "true": True,
        "false": False,
        "quantity": 1,
        "package_count": 1,
        "package_capacity": 50,
        "priority": 10,
        "reason_code": "DAMAGED",
        "decision": "APPROVE",
        "return_status": "APPROVED",
        "return_item_status": "CREATED",
        "fulfillment_status": "TRACKING_ACTIVE",
        "tracking_type": "PPL",
        "tracking_status": "LABEL_CREATED",
        "support_ticket_status": "RETURN_CREATED",
        "bay_assignment_status": "CREATED",
        "bay_name": f"Sandbox {context.bay_reference}",
        "bay_type": context.bay_reference.split("-")[-2],
        "shipping_paths_json": '["PPL","BOL","NO_LABEL"]',
        "product_types_json": '["STANDARD","BULKY","HAZARDOUS_REVIEW"]',
        "recommendation": "Keep the confirmed order-line question before support handoff.",
        "review_status": "REVIEW_PENDING",
        "clarification": "Confirm package count and item condition.",
        "sha256": hashlib.sha256(context.session_id.encode()).hexdigest(),
        "carrier": "UPS",
        "customer_tier": "STANDARD",
        "region": "SANDBOX",
        "return_window_days": 30,
        "category": context.product_type,
        "seed_version": "ai-studio-v1",
        "scenario_id": f"AI-STUDIO-{context.index + 1:03d}",
        "session_document_id": f"RETURN_SESSION:{context.session_id}",
        "schema_version": "1.0",
        "actor_type": "ASSOCIATE",
        "audit_operation": "RETURN_SESSION_CREATED",
        "entity_type": "RETURN_SESSION",
        "success_status": "SUCCESS",
        "session_event_type": "RETURN_SESSION_CREATED",
        "workflow_stage": "INTAKE",
        "running_status": "RUNNING",
        "agent_name": "eligibility-agent",
        "eligibility_stage": "ELIGIBILITY_EVALUATION",
        "decision_type": "RETURN_ELIGIBILITY",
        "decision_explanation": "Generated schema-bound eligibility evidence.",
        "confidence": 0.95,
        "provider_name": "SIMULATOR",
        "model_name": "deterministic-eligibility-v1",
        "configuration_version": "ai-studio-v1",
    }
    if generator == "sales_lines":
        return [
            {
                "lineData": {
                    "orderLineId": context.order_line_reference,
                    "productId": context.product_reference,
                    "masterProductId": context.master_product_reference,
                    "sku": context.sku,
                    "productDesc": context.product_description,
                    "productType": context.product_type,
                    "orderQty": 1,
                    "shipQty": 1,
                }
            }
        ]
    if generator == "customer_accounts":
        return [{"accountNumber": context.customer_reference, "status": "ACTIVE"}]
    if generator == "customer_addresses":
        # `customer.address[]` on the order, which the active schema explodes
        # into contact_point rows. One entry per contact, each repeating the
        # same postal address -- the shape the real documents have.
        return [
            {
                "addressType": "SHIPPING",
                "street": "100 Sandbox Way",
                "city": "CHARLOTTE",
                "state": "NC",
                "zipCode": "28202",
                "emailAddress": context.email,
                "phoneNumber": context.phone,
            }
        ]
    if generator == "cdm_parties":
        # `party[]` on customerOutboundCDM. The nesting is load-bearing: the
        # documented bridge back to salesInv is
        # party[].custAccts[].additionalCustomerInfo[].customerId, so a flatter
        # approximation would join to nothing.
        return [
            {
                "partyNumber": context.party_id,
                "partyName": context.customer_name,
                "organizationName": context.customer_name,
                "custAccts": [
                    {
                        "accountName": context.customer_name,
                        "additionalCustomerInfo": [
                            {
                                "customerId": context.customer_reference,
                                "custBranchId": context.branch_reference,
                                "shipToPhone": context.phone,
                            }
                        ],
                    }
                ],
            }
        ]
    if generator == "legacy_items":
        return [
            {
                "itemReference": context.order_line_reference,
                "sku": context.sku,
                "quantity": 1,
                "unitPrice": 100.0,
            }
        ]
    if generator == "empty_array":
        return []
    if generator == "evidence_references":
        return [f"SESSION:{context.session_id}"]
    if generator == "return_session_payload":
        return {
            "session_id": context.session_id,
            "correlation_id": context.correlation_id,
            "current_stage": "INTAKE",
            "status": "RUNNING",
            "created_at": context.updated_at,
            "updated_at": context.updated_at,
        }
    if generator in values:
        return values[generator]
    raise AssertionError(f"Generator {generator!r} is declared but not implemented")


def generate_asset_record(
    asset: DataAssetSchema,
    context: ScenarioContext,
    rng: random.Random,
) -> dict[str, Any]:
    record: dict[str, Any] = {}
    for field in asset.fields:
        generated = _value(field.generator, context, rng)
        if asset.engine == "MONGODB":
            _nested_set(record, field.name, generated)
        else:
            record[field.name] = generated
    return record


def _reference_records(
    bay_asset: DataAssetSchema,
    *,
    seed: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    warehouses = [
        {
            "_id": warehouse_id,
            "warehouseId": warehouse_id,
            "name": f"Sandbox {city.title()} Warehouse",
            "region": city,
            "active": True,
        }
        for warehouse_id, city in SANDBOX_WAREHOUSES
    ]
    bays: list[dict[str, Any]] = []
    for warehouse_index, (warehouse_id, city) in enumerate(SANDBOX_WAREHOUSES):
        for bay_index, bay_type in enumerate(("PPL", "BOL", "HOLD")):
            context = _bulk_order_context(
                warehouse_index,
                bay_index,
                seed=seed,
            )
            bay_id = f"BAY-{city}-{bay_type}-01"
            record = generate_asset_record(
                bay_asset,
                context,
                random.Random(f"{seed}:bay:{warehouse_id}:{bay_type}"),
            )
            record.update(
                {
                    "bay_id": bay_id,
                    "bay_name": f"Sandbox {city.title()} {bay_type} Bay",
                    "warehouse_id": warehouse_id,
                    "branch_id": f"BR-{city}",
                    "bay_type": bay_type,
                    "active": True,
                }
            )
            bays.append(record)
    return warehouses, bays


class AIStudioService:
    """Generate schema-bound proposals and apply only explicit sandbox-safe assets."""

    def __init__(
        self,
        *,
        client: AsyncMongoClient[dict[str, object]],
        source_client: AsyncMongoClient[dict[str, object]],
        settings: Settings,
        registry: SchemaRegistry,
    ) -> None:
        self._client = client
        self._source_client = source_client
        self._settings = settings
        self._registry = registry
        self._db = client[settings.mongo_database]
        self._source_db = source_client[settings.source_mongo_database]
        self._proposals = self._db["ai_studio_proposals"]

    async def ensure_indexes(self) -> None:
        await self._proposals.create_index([("createdAt", -1)])
        await self._proposals.create_index("digest", unique=True)

    @staticmethod
    def _view(document: dict[str, Any]) -> AIStudioProposalView:
        return AIStudioProposalView.model_validate(
            {
                "id": str(document["_id"]),
                "scenarioName": document["scenarioName"],
                "mode": document["mode"],
                "seed": document["seed"],
                "assetIds": document["assetIds"],
                "recordsPerAsset": document["recordsPerAsset"],
                "digest": document["digest"],
                "status": document["status"],
                "recordCounts": document["recordCounts"],
                "appliedAssets": document.get("appliedAssets", []),
                "blockedAssets": document.get("blockedAssets", []),
                "applyErrors": document.get("applyErrors", {}),
                "createdBy": document["createdBy"],
                "createdAt": document["createdAt"],
                "appliedBy": document.get("appliedBy"),
                "appliedAt": document.get("appliedAt"),
                "generationPrompt": document.get("generationPrompt"),
                "generationPlan": document.get("generationPlan", {}),
            }
        )

    async def generate(
        self, request: AIStudioGenerationRequest, *, actor_id: str
    ) -> AIStudioProposalView:
        if request.recordsPerAsset > self._settings.ai_studio_max_records:
            raise ValueError("recordsPerAsset exceeds configured AI Studio limit")
        if request.mode == "AI_ASSISTED" and self._settings.environment == "production":
            raise ValueError("AI-assisted synthetic data generation is disabled in production")
        assets = [self._registry.asset(asset_id) for asset_id in request.assetIds]
        rng = random.Random(request.seed)
        contexts = [
            _scenario_context(index, rng, seed=request.seed)
            for index in range(request.recordsPerAsset)
        ]
        records = {
            asset.asset_id: [generate_asset_record(asset, context, rng) for context in contexts]
            for asset in assets
        }
        digest = _canonical_digest(records)
        now = _utc_now()
        proposal_id = str(uuid.uuid4())
        document: dict[str, Any] = {
            "_id": proposal_id,
            "scenarioName": request.scenarioName,
            "mode": request.mode,
            "seed": request.seed,
            "assetIds": request.assetIds,
            "recordsPerAsset": request.recordsPerAsset,
            "digest": digest,
            "status": "DRAFT",
            "recordCounts": {asset_id: len(items) for asset_id, items in records.items()},
            "records": records,
            "appliedAssets": [],
            "blockedAssets": [],
            "applyErrors": {},
            "createdBy": actor_id,
            "createdAt": now,
            "updatedAt": now,
            "version": 0,
        }
        try:
            await self._proposals.insert_one(document)
        except DuplicateKeyError:
            existing = await self._proposals.find_one({"digest": digest})
            if existing is None:
                raise
            return self._view(existing)
        return self._view(document)

    async def generate_from_prompt(
        self,
        request: AIStudioPromptRequest,
        *,
        actor_id: str,
    ) -> AIStudioProposalView:
        customer_count, orders_per_customer = _parse_customer_order_prompt(request.prompt)
        customer_assets = [
            self._registry.asset(asset_id) for asset_id in RELATIONAL_CUSTOMER_ASSETS
        ]
        product_assets = [self._registry.asset(asset_id) for asset_id in RELATIONAL_PRODUCT_ASSETS]
        order_assets = [self._registry.asset(asset_id) for asset_id in RELATIONAL_ORDER_ASSETS]
        bay_asset = self._registry.asset("platform.sql.bay_configuration")
        preview_customers = min(customer_count, 5)
        preview_orders = min(orders_per_customer, 2)
        warehouses, bays = _reference_records(bay_asset, seed=request.seed)
        records: dict[str, list[dict[str, Any]]] = {
            asset.asset_id: [] for asset in (*customer_assets, *product_assets, *order_assets)
        }
        records["sandbox.mongodb.warehouses"] = warehouses
        records[bay_asset.asset_id] = bays
        for customer_index in range(preview_customers):
            customer_context = _bulk_order_context(customer_index, 0, seed=request.seed)
            for asset in customer_assets:
                records[asset.asset_id].append(
                    generate_asset_record(
                        asset,
                        customer_context,
                        random.Random(f"{request.seed}:preview:{asset.asset_id}:{customer_index}"),
                    )
                )
            for order_index in range(preview_orders):
                order_context = _bulk_order_context(
                    customer_index,
                    order_index,
                    seed=request.seed,
                )
                for asset in order_assets:
                    records[asset.asset_id].append(
                        generate_asset_record(
                            asset,
                            order_context,
                            random.Random(
                                f"{request.seed}:preview:{asset.asset_id}:"
                                f"{customer_index}:{order_index}"
                            ),
                        )
                    )
        for product_index in range(preview_orders):
            product_context = _bulk_order_context(0, product_index, seed=request.seed)
            for asset in product_assets:
                records[asset.asset_id].append(
                    generate_asset_record(
                        asset,
                        product_context,
                        random.Random(f"{request.seed}:preview:{asset.asset_id}:{product_index}"),
                    )
                )
        record_counts = {
            **{asset.asset_id: customer_count for asset in customer_assets},
            **{asset.asset_id: orders_per_customer for asset in product_assets},
            **{asset.asset_id: customer_count * orders_per_customer for asset in order_assets},
            "sandbox.mongodb.warehouses": len(warehouses),
            bay_asset.asset_id: len(bays),
        }
        plan = {
            "customers": customer_count,
            "ordersPerCustomer": orders_per_customer,
            "totalOrders": customer_count * orders_per_customer,
            "products": orders_per_customer,
            "warehouses": len(warehouses),
            "bays": len(bays),
            "relatedRecords": sum(record_counts.values()),
            "previewCustomers": preview_customers,
            "previewOrdersPerCustomer": preview_orders,
        }
        digest = hashlib.sha256(
            json.dumps(
                {
                    "prompt": " ".join(request.prompt.split()),
                    "seed": request.seed,
                    "plan": plan,
                    "preview": records,
                },
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode()
        ).hexdigest()
        now = _utc_now()
        document: dict[str, Any] = {
            "_id": str(uuid.uuid4()),
            "scenarioName": request.scenarioName,
            "mode": "AI_ASSISTED",
            "seed": request.seed,
            "assetIds": list(record_counts),
            "recordsPerAsset": customer_count,
            "digest": digest,
            "status": "DRAFT",
            "recordCounts": record_counts,
            "records": records,
            "generationPrompt": request.prompt,
            "generationPlan": plan,
            "appliedAssets": [],
            "blockedAssets": [],
            "applyErrors": {},
            "createdBy": actor_id,
            "createdAt": now,
            "updatedAt": now,
            "version": 0,
        }
        try:
            await self._proposals.insert_one(document)
        except DuplicateKeyError:
            existing = await self._proposals.find_one({"digest": digest})
            if existing is None:
                raise
            return self._view(existing)
        return self._view(document)

    async def list(self, limit: int = 100) -> builtins.list[AIStudioProposalView]:
        documents = await self._proposals.find({}).sort("createdAt", -1).limit(limit).to_list()
        return [self._view(document) for document in documents]

    async def get(
        self,
        proposal_id: str,
    ) -> tuple[AIStudioProposalView, dict[str, builtins.list[dict[str, Any]]]] | None:
        document = await self._proposals.find_one({"_id": proposal_id})
        if document is None:
            return None
        records = document.get("records", {})
        if not isinstance(records, dict):
            records = {}
        return self._view(document), records

    async def apply(
        self,
        proposal_id: str,
        request: AIStudioApplyRequest,
        *,
        actor_id: str,
    ) -> AIStudioProposalView:
        if self._settings.environment not in {"development", "test"}:
            raise PermissionError("AI Studio apply is restricted to development and test")
        result = await self.get(proposal_id)
        if result is None:
            raise KeyError(proposal_id)
        view, records = result
        if view.digest != request.expectedDigest:
            raise RuntimeError("Proposal digest conflict")
        if view.generationPlan:
            return await self._apply_customer_order_plan(
                proposal_id,
                view,
                actor_id=actor_id,
            )
        registry_order = {
            asset.asset_id: index for index, asset in enumerate(self._registry.assets)
        }
        status: Literal["APPLIED", "PARTIALLY_APPLIED", "REJECTED"]
        ordered_records = sorted(
            records.items(), key=lambda item: registry_order.get(item[0], 2**31 - 1)
        )
        blocked: builtins.list[str] = []
        applied: builtins.list[str] = []
        for asset_id, _asset_records in ordered_records:
            asset = self._registry.asset(asset_id)
            if not asset.writable_in_sandbox:
                blocked.append(asset_id)
            elif asset.engine == "MONGODB" and asset.name not in DIRECT_MONGO_COLLECTIONS:
                blocked.append(asset_id)
            elif asset.engine == "SQLSERVER" and asset_id not in DIRECT_SQL_ASSETS:
                blocked.append(asset_id)

        for asset_id, asset_records in ordered_records:
            if asset_id in blocked:
                continue
            asset = self._registry.asset(asset_id)
            try:
                if asset.engine == "MONGODB":
                    await self._apply_mongodb(asset, asset_records)
                else:
                    await self._apply_sql(asset, asset_records)
            except Exception as error:
                status = "PARTIALLY_APPLIED" if applied else "REJECTED"
                await self._record_apply_result(
                    proposal_id=proposal_id,
                    expected_digest=request.expectedDigest,
                    actor_id=actor_id,
                    status=status,
                    applied=applied,
                    blocked=blocked,
                    errors={asset_id: type(error).__name__},
                )
                raise RuntimeError(
                    f"AI Studio apply failed for asset {asset_id}; inspect proposal evidence."
                ) from error
            applied.append(asset_id)

        status = "APPLIED" if not blocked else ("PARTIALLY_APPLIED" if applied else "REJECTED")
        updated = await self._record_apply_result(
            proposal_id=proposal_id,
            expected_digest=request.expectedDigest,
            actor_id=actor_id,
            status=status,
            applied=applied,
            blocked=blocked,
            errors={},
        )
        return self._view(updated)

    async def _apply_customer_order_plan(
        self,
        proposal_id: str,
        view: AIStudioProposalView,
        *,
        actor_id: str,
    ) -> AIStudioProposalView:
        customer_count = int(view.generationPlan.get("customers", 0))
        orders_per_customer = int(view.generationPlan.get("ordersPerCustomer", 0))
        if not 1 <= customer_count <= 500 or not 1 <= orders_per_customer <= 100:
            raise ValueError("Stored customer/order generation plan is invalid")
        customer_assets = [
            self._registry.asset(asset_id) for asset_id in RELATIONAL_CUSTOMER_ASSETS
        ]
        product_assets = [self._registry.asset(asset_id) for asset_id in RELATIONAL_PRODUCT_ASSETS]
        order_assets = [self._registry.asset(asset_id) for asset_id in RELATIONAL_ORDER_ASSETS]
        bay_asset = self._registry.asset("platform.sql.bay_configuration")
        sandbox_database = self._settings.ai_studio_mongo_database
        if not sandbox_database:
            raise PermissionError("AI Studio Mongo apply requires a dedicated sandbox database.")
        if sandbox_database in {
            self._settings.mongo_database,
            self._settings.source_mongo_database,
        }:
            raise PermissionError(
                "AI Studio Mongo apply cannot target operational or source databases."
            )
        target_db = self._client[sandbox_database]

        async def flush(
            collection_name: str,
            records: list[dict[str, Any]],
        ) -> None:
            if not records:
                return
            await target_db[collection_name].bulk_write(
                [ReplaceOne({"_id": record["_id"]}, record, upsert=True) for record in records],
                ordered=False,
            )

        batches: dict[str, list[dict[str, Any]]] = {
            asset.asset_id: [] for asset in (*customer_assets, *product_assets, *order_assets)
        }
        collections = {
            asset.asset_id: asset.name
            for asset in (*customer_assets, *product_assets, *order_assets)
        }

        async def append(asset: DataAssetSchema, record: dict[str, Any]) -> None:
            batch = batches[asset.asset_id]
            batch.append(record)
            if len(batch) >= 500:
                await flush(collections[asset.asset_id], batch)
                batch.clear()

        warehouses, bays = _reference_records(bay_asset, seed=view.seed)
        await flush("warehouses", warehouses)
        await flush(
            bay_asset.name,
            [
                {
                    "_id": record["bay_id"],
                    **record,
                    "_sandboxAssetId": bay_asset.asset_id,
                }
                for record in bays
            ],
        )
        for customer_index in range(customer_count):
            customer_context = _bulk_order_context(customer_index, 0, seed=view.seed)
            for asset in customer_assets:
                await append(
                    asset,
                    generate_asset_record(
                        asset,
                        customer_context,
                        random.Random(f"{view.seed}:apply:{asset.asset_id}:{customer_index}"),
                    ),
                )
            for order_index in range(orders_per_customer):
                order_context = _bulk_order_context(
                    customer_index,
                    order_index,
                    seed=view.seed,
                )
                for asset in order_assets:
                    await append(
                        asset,
                        generate_asset_record(
                            asset,
                            order_context,
                            random.Random(
                                f"{view.seed}:apply:{asset.asset_id}:{customer_index}:{order_index}"
                            ),
                        ),
                    )
        for product_index in range(orders_per_customer):
            product_context = _bulk_order_context(0, product_index, seed=view.seed)
            for asset in product_assets:
                await append(
                    asset,
                    generate_asset_record(
                        asset,
                        product_context,
                        random.Random(f"{view.seed}:apply:{asset.asset_id}:{product_index}"),
                    ),
                )
        for asset_id, batch in batches.items():
            await flush(collections[asset_id], batch)
        applied_assets = [
            *RELATIONAL_CUSTOMER_ASSETS,
            *RELATIONAL_PRODUCT_ASSETS,
            *RELATIONAL_ORDER_ASSETS,
            *RELATIONAL_REFERENCE_ASSETS,
        ]
        updated = await self._record_apply_result(
            proposal_id=proposal_id,
            expected_digest=view.digest,
            actor_id=actor_id,
            status="APPLIED",
            applied=applied_assets,
            blocked=[],
            errors={},
        )
        return self._view(updated)

    async def _record_apply_result(
        self,
        *,
        proposal_id: str,
        expected_digest: str,
        actor_id: str,
        status: Literal["APPLIED", "PARTIALLY_APPLIED", "REJECTED"],
        applied: builtins.list[str],
        blocked: builtins.list[str],
        errors: dict[str, str],
    ) -> dict[str, Any]:
        now = _utc_now()
        updated = await self._proposals.find_one_and_update(
            {"_id": proposal_id, "digest": expected_digest},
            {
                "$set": {
                    "status": status,
                    "appliedAssets": applied,
                    "blockedAssets": blocked,
                    "applyErrors": errors,
                    "appliedBy": actor_id,
                    "appliedAt": now,
                    "updatedAt": now,
                },
                "$inc": {"version": 1},
            },
            return_document=ReturnDocument.AFTER,
        )
        if updated is None:
            raise RuntimeError("Proposal changed during apply")
        return updated

    async def _apply_mongodb(
        self, asset: DataAssetSchema, records: builtins.list[dict[str, Any]]
    ) -> None:
        sandbox_database = self._settings.ai_studio_mongo_database
        if not sandbox_database:
            raise PermissionError("AI Studio Mongo apply requires a dedicated sandbox database.")
        if sandbox_database in {
            self._settings.mongo_database,
            self._settings.source_mongo_database,
        }:
            raise PermissionError(
                "AI Studio Mongo apply cannot target operational or source databases."
            )
        target_db = self._client[sandbox_database]
        collection = target_db[asset.name]
        key_fields = [field.name for field in asset.fields if field.key]
        if key_fields != ["_id"]:
            raise ValueError(f"Direct Mongo apply requires _id key: {asset.asset_id}")
        for record in records:
            record_id = record.get("_id")
            if not isinstance(record_id, str) or not record_id:
                raise ValueError(f"Generated record missing _id: {asset.asset_id}")
            await collection.replace_one({"_id": record_id}, record, upsert=True)

    async def _apply_sql(
        self, asset: DataAssetSchema, records: builtins.list[dict[str, Any]]
    ) -> None:
        sandbox_host = self._settings.ai_studio_sqlserver_host
        sandbox_user = self._settings.ai_studio_sqlserver_user
        sandbox_password = self._settings.ai_studio_sqlserver_password
        sandbox_database = self._settings.ai_studio_sqlserver_database
        if (
            sandbox_host is None
            or sandbox_user is None
            or sandbox_password is None
            or sandbox_database is None
        ):
            raise PermissionError(
                "AI Studio SQL apply requires dedicated sandbox connection settings."
            )
        if (
            sandbox_host == self._settings.sqlserver_host
            or sandbox_user == self._settings.sqlserver_user
            or sandbox_database == self._settings.sqlserver_database
        ):
            raise PermissionError(
                "AI Studio SQL apply must use a separate host, credential, and database."
            )
        if asset.namespace is None:
            raise ValueError("SQL asset namespace is required")
        for identifier in (asset.namespace, asset.name, *(field.name for field in asset.fields)):
            if not _SAFE_IDENTIFIER.fullmatch(identifier):
                raise ValueError(f"Unsafe SQL identifier in registry: {identifier}")
        key_fields = [field.name for field in asset.fields if field.key]
        if len(key_fields) != 1:
            raise ValueError(f"Direct SQL apply requires one key field: {asset.asset_id}")
        key = key_fields[0]
        columns = [field.name for field in asset.fields]
        qualified = f"[{asset.namespace}].[{asset.name}]"
        placeholders = ",".join("%s" for _ in columns)
        column_sql = ",".join(f"[{column}]" for column in columns)
        mutable_columns = [column for column in columns if column != key]
        update_sql = (
            f"UPDATE {qualified} WITH (UPDLOCK, SERIALIZABLE) SET "
            + ",".join(f"[{column}]=%s" for column in mutable_columns)
            + f" WHERE [{key}]=%s"
        )
        insert_sql = f"INSERT INTO {qualified} ({column_sql}) VALUES ({placeholders})"

        def operation() -> None:
            timeout = max(1, int(self._settings.operation_timeout_seconds))
            with pymssql.connect(
                server=sandbox_host,
                port=str(self._settings.ai_studio_sqlserver_port),
                user=sandbox_user,
                password=sandbox_password.get_secret_value(),
                database=sandbox_database,
                login_timeout=timeout,
                timeout=timeout,
                autocommit=False,
            ) as connection:
                with connection.cursor() as cursor:
                    for record in records:
                        cursor.execute(
                            update_sql,
                            (*tuple(record[column] for column in mutable_columns), record[key]),
                        )
                        if cursor.rowcount == 0:
                            cursor.execute(insert_sql, tuple(record[column] for column in columns))
                connection.commit()

        async with asyncio.timeout(self._settings.operation_timeout_seconds):
            await asyncio.to_thread(operation)
