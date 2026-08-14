"""A hand-built ActiveSchema, kept only as test input.

This was ``data_platform/graph/interim_active_schema.py``: production code that
``sync_service.py`` stopped calling when it moved to
``load_active_schema(settings.dynamic_knowledge_schema_path)``. It then sat in
the shipped package for 36 KB with zero importers under ``backend/src``, which
is what the audit records.

It is not deleted, because it is not unused -- it is *input data* for two tests
that exercise live production machinery:

``test_interim_active_schema.py``
    drives ``GenericSourceRecordExtractor``, ``GenericGraphProjector``,
    ``compile_relationship_reconciliation``, ``required_node_constraints`` and
    ``required_relationship_indexes`` over a realistically shaped schema.
``test_sync_service_pipeline.py``
    is the end-to-end ``GraphSyncService.sync()`` test, and ``GraphSyncService``
    is very much live (``main.py:557``).

The second one is written against *these* source ids -- ``sales_inventory`` and
``customer_outbound`` -- and feeds ``customerOutboundCDM``/``salesInv``
documents shaped for the field paths below. The shipped descriptor names its
sources ``source_sales``/``source_customers`` and has the corrected field paths
this module's notes describe as a later cutover, so pointing that test at the
shipped YAML would not be a rename; it would need new fixture documents and
would quietly turn its run-count assertions into assertions about nothing.

So the schema moved to the test tree rather than being rewritten away. Being
here is the enforcement that used to be a ``FROZEN`` entry in
``tests/test_frozen_modules_gain_no_new_callers.py``: production cannot import
it, because it is not on the production path at all.

Everything below is unchanged from the frozen original.


This is a bridge, not the destination: it matches sync_service.py's CURRENT
(uncorrected) source field paths and identity choices closely enough to
unify sync_service.py onto the generic extractor -> projector -> writer
pipeline (see the source-to-graph alignment plan's Step 8), so the
hand-written MERGE Cypher in sync_service.py can be retired. The real,
field-path-corrected Ferguson schema (composite SalesOrder/OrderLine
identity scoped by logon, CustomerAccount/CustomerContactPoint modeled per
the real customerOutboundCDM shape, etc.) is a separate, later cutover
(Step 12) with its own configuration release and graph generation.

Known, deliberate simplifications versus the old hand-coded Cypher (each
chosen because the generic framework's primitives don't (yet) express the
old code's exact fallback, or because the old behavior was itself already a
latent correctness gap that this pipeline naturally avoids):

- Customer identity is COALESCE(party_id, customer_id) computed once from
  customerOutboundCDM; SalesOrder no longer independently upserts/merges a
  second Customer node keyed off custId the way the old code did (it only
  writes a customer_id PROPERTY on SalesOrder). PLACED_ORDER is formed by
  Stage B joining Customer.customer_key against SalesOrder.customer_id.
  This removes a real dual-write ambiguity the old code had, rather than
  reproducing it.
- CustomerAccount's key is the bare account_number (no "CUSTOMER_CDM:"
  prefix); the old accounts/custAccts array-name fallback is dropped in
  favor of accounts alone -- verified against real seeded customerOutboundCDM
  documents, which use "accounts", not "custAccts".
- OrderLine's key is orderLineId alone (no position-based fallback key).
  The old lineData-vs-wrapper unwrap is *not* dropped -- salesLines[] entries
  are wrapped in a lineData object in the real seeded data, so every
  order_line field's physical_path is ["lineData", <field>]. order_line
  still stays SEED_ONLY per the approved plan, since its real (non-seed)
  source shape was never independently verified.
- Product is authoritatively upserted only from lkpSearchProduct's own
  Stage A, not also re-created from embedded salesLines product fields;
  REFERENCES_PRODUCT simply doesn't form for a product_id that
  lkpSearchProduct hasn't (yet) produced, instead of materializing a
  partial on-the-fly Product node.
- Warehouse and Bay are deferred entirely (not modeled here at all).
  platform.bay_configuration has no timestamp column in the old code's own
  query (`ORDER BY priority, bay_id`, no incremental bound), so there is no
  honest incremental_cursor_field to configure for it -- putting it in this
  schema's CONNECTED_SYNC sources would make full_sync raise instead of
  silently mis-syncing. This mirrors the approved plan's own precedent of
  explicitly deferring ProductWarehouse rather than half-implementing it.
  SOLD_BY_WAREHOUSE / SHIPPED_FROM_WAREHOUSE / USES_BAY /
  LOCATED_IN_WAREHOUSE are dropped along with it; sell_warehouse_id /
  ship_from_warehouse_id / warehouse_id / bay_id remain as inert string
  properties on the entities that carry them.
- BayAssignment -> ReturnItem's link is resolved by a composite
  (return_reference, order_line_id) match rather than the old SQL-side
  LEFT JOIN-derived return_item_id, since the generic connector queries one
  table at a time. ReturnItem -[:ASSIGNED_TO_BAY]-> Bay is dropped with
  Warehouse/Bay above; the same relationship is still reachable as a 2-hop
  BayAssignment path.
"""

from __future__ import annotations

from typing import Any

from return_platform.dynamic_knowledge.schema import ActiveSchema

_ASSOCIATE = ("associate",)


def _capabilities(
    *,
    searchable: bool = False,
    filterable: bool = False,
    distinct: bool = False,
    aggregatable: bool = False,
    displayable: bool = True,
    operators: tuple[str, ...] = ("EXACT",),
) -> dict[str, Any]:
    return {
        "searchable": searchable,
        "filterable": filterable,
        "distinct": distinct,
        "aggregatable": aggregatable,
        "displayable": displayable,
        "on_demand_sync_anchor": False,
        "operators": list(operators),
        "aggregations": [],
    }


def _permissions(*, searchable: bool = False) -> dict[str, Any]:
    permissions: dict[str, Any] = {"displayable_by": list(_ASSOCIATE)}
    if searchable:
        permissions["searchable_by"] = list(_ASSOCIATE)
    return permissions


def _field(
    field_id: str,
    *,
    physical_path: list[str] | None = None,
    derive: dict[str, Any] | None = None,
    path_origin: str = "CURRENT_RECORD",
    graph_property: str | None = None,
    data_type: str = "STRING",
    nullable: bool = True,
    searchable: bool = False,
    filterable: bool = False,
    distinct: bool = False,
) -> dict[str, Any]:
    return {
        "field_id": field_id,
        "physical_path": physical_path,
        "derive": derive,
        "path_origin": path_origin,
        "graph_property": graph_property or field_id,
        "data_type": data_type,
        "nullable": nullable,
        "capabilities": _capabilities(
            searchable=searchable, filterable=filterable, distinct=distinct
        ),
        "permissions": _permissions(searchable=searchable),
    }


def _node(
    projection_id: str, entity_id: str, label: str, *, key: str, properties: list[str]
) -> dict[str, Any]:
    return {
        "projection_id": projection_id,
        "entity_id": entity_id,
        "label": label,
        "key_fields": [key],
        "property_fields": properties,
    }


def _relationship(
    relationship_id: str,
    relationship_type: str,
    *,
    source_entity_id: str,
    target_entity_id: str,
    source_match_fields: list[str],
    target_match_fields: list[str],
    cardinality: str = "MANY_TO_MANY",
    access: str = "CONNECTED_SYNC",
) -> dict[str, Any]:
    return {
        "relationship_id": relationship_id,
        "relationship_type": relationship_type,
        "source_entity_id": source_entity_id,
        "target_entity_id": target_entity_id,
        "source_match_fields": source_match_fields,
        "target_match_fields": target_match_fields,
        "cardinality": cardinality,
        "access": access,
    }


def _entity(
    entity_id: str,
    source_asset_id: str,
    fields: dict[str, dict[str, Any]],
    *,
    natural_key: list[str],
    record_path: list[str] | None = None,
    explode: bool = False,
    where: list[dict[str, Any]] | None = None,
    source_contract_status: str = "VERIFIED",
    source_access: str = "CONNECTED_SYNC",
    ownership_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "entity_id": entity_id,
        "source_asset_id": source_asset_id,
        "fields": fields,
        "natural_key": natural_key,
        "strong_anchors": {},
        "source_contract_status": source_contract_status,
        "source_access": source_access,
        "record_path": record_path or [],
        "explode": explode,
        "where": where or [],
        "ownership_policy": ownership_policy,
    }


def _source(
    source_asset_id: str,
    connector_type: str,
    object_ref: dict[str, str],
    *,
    incremental_cursor_field: str | None = None,
) -> dict[str, Any]:
    return {
        "source_asset_id": source_asset_id,
        "connector_type": connector_type,
        "connection_ref": f"vault://return-platform/sources#{source_asset_id}",
        "object_ref": object_ref,
        "incremental_cursor_field": incremental_cursor_field,
    }


_CONTACT_KEY_REFERENCE = "vault://return-platform/contact-lookup#hmac_key"


def _contact_digest(source_field: str, contact_kind: str) -> dict[str, Any]:
    return {
        "operation": "CONTACT_LOOKUP_DIGEST",
        "source_field": source_field,
        "contact_kind": contact_kind,
        "key_reference": _CONTACT_KEY_REFERENCE,
        "key_version": 1,
    }


_SOURCES = {
    # incremental_cursor_field is required on every Mongo source here (not left
    # to the connector's ObjectId-based default) -- verified against the real
    # seed data, whose _id values are business-key strings ("CUST-1001"), never
    # ObjectIds, so an ObjectId-bounded scan would silently match nothing.
    "customer_outbound": _source(
        "customer_outbound",
        "MONGODB",
        {"database": "return_source", "name": "customerOutboundCDM"},
        incremental_cursor_field="source_updated_at",
    ),
    "sales_inventory": _source(
        "sales_inventory",
        "MONGODB",
        {"database": "return_source", "name": "salesInv"},
        incremental_cursor_field="source_updated_at",
    ),
    "shipment_info": _source(
        "shipment_info",
        "MONGODB",
        {"database": "return_source", "name": "shipmentInfo"},
        incremental_cursor_field="source_updated_at",
    ),
    "product_lookup": _source(
        "product_lookup",
        "MONGODB",
        {"database": "return_source", "name": "lkpSearchProduct"},
        incremental_cursor_field="source_updated_at",
    ),
    "sql_returns": _source(
        "sql_returns",
        "MSSQL",
        {"database": "return_platform", "namespace": "dbo", "name": "return_requests"},
        incremental_cursor_field="source_updated_at",
    ),
    "sql_return_items": _source(
        "sql_return_items",
        "MSSQL",
        {"database": "return_platform", "namespace": "dbo", "name": "return_items"},
        incremental_cursor_field="source_updated_at",
    ),
    "sql_return_tracking": _source(
        "sql_return_tracking",
        "MSSQL",
        {"database": "return_platform", "namespace": "dbo", "name": "return_tracking"},
        incremental_cursor_field="event_at",
    ),
    "sql_bay_assignment": _source(
        "sql_bay_assignment",
        "MSSQL",
        {"database": "return_platform", "namespace": "platform", "name": "bay_assignment"},
        incremental_cursor_field="source_updated_at",
    ),
    "sql_support_ticket": _source(
        "sql_support_ticket",
        "MSSQL",
        {
            "database": "return_platform",
            "namespace": "integration",
            "name": "return_support_ticket",
        },
        incremental_cursor_field="updated_at",
    ),
}

_ENTITIES = {
    "customer": _entity(
        "customer",
        "customer_outbound",
        {
            "party_id": _field("party_id", physical_path=["partyId"]),
            "customer_id": _field("customer_id", physical_path=["customerId"]),
            "customer_key": _field(
                "customer_key",
                derive={"operation": "COALESCE", "fields": ["party_id", "customer_id"]},
                nullable=False,
                searchable=True,
                filterable=True,
                distinct=True,
            ),
            "customer_name": _field(
                "customer_name", physical_path=["customerName"], searchable=True, filterable=True
            ),
            "phone_number": _field("phone_number", physical_path=["phoneNumber"]),
            "email": _field("email", physical_path=["email"]),
            "phone_hash": _field(
                "phone_hash", derive=_contact_digest("phone_number", "PHONE"), searchable=True
            ),
            "email_hash": _field(
                "email_hash", derive=_contact_digest("email", "EMAIL"), searchable=True
            ),
            "source_updated_at": _field(
                "source_updated_at", physical_path=["updatedAt"], data_type="DATETIME"
            ),
            "seed_version": _field("seed_version", physical_path=["seedVersion"]),
            "seed_digest": _field("seed_digest", physical_path=["seedDigest"]),
        },
        natural_key=["customer_key"],
    ),
    "customer_account": _entity(
        "customer_account",
        "customer_outbound",
        {
            "account_number": _field(
                "account_number",
                physical_path=["accountNumber"],
                nullable=False,
                searchable=True,
                filterable=True,
            ),
            "party_id_root": _field(
                "party_id_root", physical_path=["partyId"], path_origin="ROOT_DOCUMENT"
            ),
            "customer_id_root": _field(
                "customer_id_root", physical_path=["customerId"], path_origin="ROOT_DOCUMENT"
            ),
            "customer_key": _field(
                "customer_key",
                derive={
                    "operation": "COALESCE",
                    "fields": ["party_id_root", "customer_id_root"],
                },
                searchable=True,
                filterable=True,
            ),
            # customer_outbound's incremental_cursor_field is source_updated_at;
            # every entity sharing that source must define it, including this one.
            "source_updated_at": _field(
                "source_updated_at",
                physical_path=["updatedAt"],
                path_origin="ROOT_DOCUMENT",
                data_type="DATETIME",
            ),
        },
        natural_key=["account_number"],
        # The live seed data (and the old sync_service.py's own primary lookup)
        # uses "accounts", not "custAccts" -- verified against real seeded
        # customerOutboundCDM documents.
        record_path=["accounts"],
        explode=True,
        ownership_policy={"mode": "REPLACE_CHILD_SET", "owner_identity": "SOURCE_DOCUMENT"},
    ),
    "sales_order": _entity(
        "sales_order",
        "sales_inventory",
        {
            "order_id": _field(
                "order_id",
                physical_path=["salesHdrEventData", "orderId"],
                nullable=False,
                searchable=True,
                filterable=True,
            ),
            "customer_id": _field(
                "customer_id",
                physical_path=["salesHdr", "salesHdrData", "custId"],
                searchable=True,
                filterable=True,
            ),
            "customer_name": _field(
                "customer_name", physical_path=["salesHdr", "salesHdrData", "custName"]
            ),
            "order_status": _field(
                "order_status", physical_path=["salesHdrEventData", "orderStatus"], filterable=True
            ),
            "sell_warehouse_id": _field(
                "sell_warehouse_id", physical_path=["salesHdrEventData", "sellWhseId"]
            ),
            "ship_from_warehouse_id": _field(
                "ship_from_warehouse_id", physical_path=["salesHdrEventData", "shipFromWhseId"]
            ),
            "shipping_method": _field(
                "shipping_method", physical_path=["salesHdr", "shipping", "shipViaCode"]
            ),
            "delivered_at": _field(
                "delivered_at", physical_path=["deliveredAt"], data_type="DATETIME"
            ),
            "source_system": _field(
                "source_system", physical_path=["salesHdrEventData", "srcSysCode"]
            ),
            "source_updated_at": _field(
                "source_updated_at", physical_path=["updatedAt"], data_type="DATETIME"
            ),
            "seed_version": _field("seed_version", physical_path=["seedVersion"]),
            "seed_digest": _field("seed_digest", physical_path=["seedDigest"]),
        },
        natural_key=["order_id"],
    ),
    "order_line": _entity(
        "order_line",
        "sales_inventory",
        {
            # Every field below (except sales_order_id, which is ROOT_DOCUMENT-
            # origin) reads through the "lineData" wrapper each salesLines[]
            # entry carries in the real seeded data -- verified against real
            # seeded salesInv documents.
            "order_line_id": _field(
                "order_line_id",
                physical_path=["lineData", "orderLineId"],
                nullable=False,
                searchable=True,
                filterable=True,
            ),
            "sales_order_id": _field(
                "sales_order_id",
                physical_path=["salesHdrEventData", "orderId"],
                path_origin="ROOT_DOCUMENT",
                searchable=True,
                filterable=True,
            ),
            "product_id": _field(
                "product_id",
                physical_path=["lineData", "productId"],
                searchable=True,
                filterable=True,
            ),
            "master_product_id": _field(
                "master_product_id", physical_path=["lineData", "masterProductId"]
            ),
            "sku": _field("sku", physical_path=["lineData", "sku"], searchable=True),
            "product_description": _field(
                "product_description", physical_path=["lineData", "productDesc"]
            ),
            "product_type": _field("product_type", physical_path=["lineData", "productType"]),
            "ordered_quantity": _field(
                "ordered_quantity", physical_path=["lineData", "orderQty"], data_type="INTEGER"
            ),
            "shipped_quantity": _field(
                "shipped_quantity", physical_path=["lineData", "shipQty"], data_type="INTEGER"
            ),
            # sales_inventory's incremental_cursor_field is source_updated_at;
            # every entity sharing that source must define it, including this one.
            "source_updated_at": _field(
                "source_updated_at",
                physical_path=["updatedAt"],
                path_origin="ROOT_DOCUMENT",
                data_type="DATETIME",
            ),
        },
        natural_key=["order_line_id"],
        record_path=["salesLines"],
        explode=True,
        source_contract_status="UNVERIFIED",
        source_access="SEED_ONLY",
    ),
    "shipment": _entity(
        "shipment",
        "shipment_info",
        {
            "tracking_number": _field(
                "tracking_number",
                physical_path=["shipmentInfoEventData", "trkNum"],
                nullable=False,
                searchable=True,
                filterable=True,
            ),
            "sales_order_id": _field(
                "sales_order_id",
                physical_path=["shipmentInfoEventData", "trilOrdNum"],
                searchable=True,
                filterable=True,
            ),
            "carrier_code": _field(
                "carrier_code", physical_path=["shipmentInfoEventData", "carrierCode"]
            ),
            "shipped_at": _field(
                "shipped_at",
                physical_path=["shipmentInfoEventData", "shippedAt"],
                data_type="DATETIME",
            ),
            "source_updated_at": _field(
                "source_updated_at", physical_path=["updatedAt"], data_type="DATETIME"
            ),
            "seed_version": _field("seed_version", physical_path=["seedVersion"]),
            "seed_digest": _field("seed_digest", physical_path=["seedDigest"]),
        },
        natural_key=["tracking_number"],
    ),
    "product": _entity(
        "product",
        "product_lookup",
        {
            "product_id": _field(
                "product_id",
                physical_path=["productId"],
                nullable=False,
                searchable=True,
                filterable=True,
            ),
            "sku": _field("sku", physical_path=["sku"], searchable=True, filterable=True),
            "master_product_id": _field("master_product_id", physical_path=["masterProductId"]),
            "product_description": _field(
                "product_description", physical_path=["productDescription"], searchable=True
            ),
            "product_type": _field("product_type", physical_path=["productType"], filterable=True),
            "source_updated_at": _field(
                "source_updated_at", physical_path=["updatedAt"], data_type="DATETIME"
            ),
            "seed_version": _field("seed_version", physical_path=["seedVersion"]),
            "seed_digest": _field("seed_digest", physical_path=["seedDigest"]),
        },
        natural_key=["product_id"],
    ),
    "return": _entity(
        "return",
        "sql_returns",
        {
            "return_reference": _field(
                "return_reference",
                physical_path=["return_reference"],
                nullable=False,
                searchable=True,
                filterable=True,
            ),
            "session_id": _field("session_id", physical_path=["session_id"]),
            "sales_order_id": _field(
                "sales_order_id",
                physical_path=["order_reference"],
                searchable=True,
                filterable=True,
            ),
            "customer_reference": _field(
                "customer_reference", physical_path=["customer_reference"]
            ),
            "return_status": _field(
                "return_status", physical_path=["return_status"], filterable=True
            ),
            "eligibility_decision": _field(
                "eligibility_decision", physical_path=["eligibility_decision"]
            ),
            "source_updated_at": _field(
                "source_updated_at",
                physical_path=["updated_at"],
                data_type="DATETIME",
                nullable=False,
            ),
        },
        natural_key=["return_reference"],
    ),
    "return_item": _entity(
        "return_item",
        "sql_return_items",
        {
            "return_item_id": _field(
                "return_item_id",
                physical_path=["return_item_id"],
                nullable=False,
                searchable=True,
                filterable=True,
            ),
            "return_reference": _field(
                "return_reference",
                physical_path=["return_reference"],
                searchable=True,
                filterable=True,
            ),
            "order_line_id": _field(
                "order_line_id", physical_path=["order_line_id"], searchable=True, filterable=True
            ),
            "product_id": _field("product_id", physical_path=["product_id"], searchable=True),
            "quantity": _field("quantity", physical_path=["quantity"], data_type="INTEGER"),
            "reason_code": _field("reason_code", physical_path=["reason_code"], filterable=True),
            "item_status": _field("item_status", physical_path=["item_status"], filterable=True),
            "source_updated_at": _field(
                "source_updated_at",
                physical_path=["created_at"],
                data_type="DATETIME",
                nullable=False,
            ),
        },
        natural_key=["return_item_id"],
    ),
    "return_tracking": _entity(
        "return_tracking",
        "sql_return_tracking",
        {
            "tracking_id": _field(
                "tracking_id",
                physical_path=["tracking_id"],
                nullable=False,
                searchable=True,
                filterable=True,
            ),
            "return_reference": _field(
                "return_reference",
                physical_path=["return_reference"],
                searchable=True,
                filterable=True,
            ),
            "tracking_type": _field(
                "tracking_type", physical_path=["tracking_type"], filterable=True
            ),
            "tracking_reference": _field(
                "tracking_reference", physical_path=["tracking_reference"], searchable=True
            ),
            "carrier_code": _field("carrier_code", physical_path=["carrier_code"]),
            "tracking_status": _field(
                "tracking_status", physical_path=["tracking_status"], filterable=True
            ),
            "event_at": _field(
                "event_at", physical_path=["event_at"], data_type="DATETIME", nullable=False
            ),
        },
        natural_key=["tracking_id"],
    ),
    "bay_assignment": _entity(
        "bay_assignment",
        "sql_bay_assignment",
        {
            "assignment_id": _field(
                "assignment_id",
                physical_path=["assignment_id"],
                nullable=False,
                searchable=True,
                filterable=True,
            ),
            "return_reference": _field(
                "return_reference",
                physical_path=["return_reference"],
                searchable=True,
                filterable=True,
            ),
            "order_line_id": _field(
                "order_line_id", physical_path=["order_line_id"], searchable=True, filterable=True
            ),
            "item_number": _field("item_number", physical_path=["item_number"]),
            "package_count": _field(
                "package_count", physical_path=["package_count"], data_type="INTEGER"
            ),
            "warehouse_id": _field("warehouse_id", physical_path=["warehouse_id"]),
            "bay_id": _field("bay_id", physical_path=["bay_id"]),
            "status": _field("status", physical_path=["status"], filterable=True),
            "confirmed_by_associate_id": _field(
                "confirmed_by_associate_id", physical_path=["confirmed_by_associate_id"]
            ),
            "source_updated_at": _field(
                "source_updated_at",
                physical_path=["created_at"],
                data_type="DATETIME",
                nullable=False,
            ),
        },
        natural_key=["assignment_id"],
    ),
    "support_ticket": _entity(
        "support_ticket",
        "sql_support_ticket",
        {
            "ticket_id": _field(
                "ticket_id",
                physical_path=["ticket_id"],
                nullable=False,
                searchable=True,
                filterable=True,
            ),
            "session_id": _field("session_id", physical_path=["session_id"]),
            "status": _field("status", physical_path=["status"], filterable=True),
            "external_reference": _field(
                "external_reference", physical_path=["external_reference"], searchable=True
            ),
            "return_reference": _field(
                "return_reference",
                physical_path=["return_reference"],
                searchable=True,
                filterable=True,
            ),
            "updated_at": _field(
                "updated_at", physical_path=["updated_at"], data_type="DATETIME", nullable=False
            ),
        },
        natural_key=["ticket_id"],
    ),
}

_NODES = {
    "node_customer": _node(
        "node_customer",
        "customer",
        "Customer",
        key="customer_key",
        properties=[
            "party_id",
            "customer_id",
            "customer_name",
            "phone_hash",
            "email_hash",
            "source_updated_at",
            "seed_version",
            "seed_digest",
        ],
    ),
    "node_customer_account": _node(
        "node_customer_account",
        "customer_account",
        "CustomerAccount",
        key="account_number",
        properties=["customer_key"],
    ),
    "node_sales_order": _node(
        "node_sales_order",
        "sales_order",
        "SalesOrder",
        key="order_id",
        properties=[
            "customer_id",
            "customer_name",
            "order_status",
            "sell_warehouse_id",
            "ship_from_warehouse_id",
            "shipping_method",
            "delivered_at",
            "source_system",
            "source_updated_at",
            "seed_version",
            "seed_digest",
        ],
    ),
    "node_order_line": _node(
        "node_order_line",
        "order_line",
        "OrderLine",
        key="order_line_id",
        properties=[
            "sales_order_id",
            "product_id",
            "master_product_id",
            "sku",
            "product_description",
            "product_type",
            "ordered_quantity",
            "shipped_quantity",
        ],
    ),
    "node_shipment": _node(
        "node_shipment",
        "shipment",
        "Shipment",
        key="tracking_number",
        properties=[
            "sales_order_id",
            "carrier_code",
            "shipped_at",
            "source_updated_at",
            "seed_version",
            "seed_digest",
        ],
    ),
    "node_product": _node(
        "node_product",
        "product",
        "Product",
        key="product_id",
        properties=[
            "sku",
            "master_product_id",
            "product_description",
            "product_type",
            "source_updated_at",
            "seed_version",
            "seed_digest",
        ],
    ),
    "node_return": _node(
        "node_return",
        "return",
        "Return",
        key="return_reference",
        properties=[
            "session_id",
            "sales_order_id",
            "customer_reference",
            "return_status",
            "eligibility_decision",
            "source_updated_at",
        ],
    ),
    "node_return_item": _node(
        "node_return_item",
        "return_item",
        "ReturnItem",
        key="return_item_id",
        properties=[
            "return_reference",
            "order_line_id",
            "product_id",
            "quantity",
            "reason_code",
            "item_status",
            "source_updated_at",
        ],
    ),
    "node_return_tracking": _node(
        "node_return_tracking",
        "return_tracking",
        "ReturnTracking",
        key="tracking_id",
        properties=[
            "return_reference",
            "tracking_type",
            "tracking_reference",
            "carrier_code",
            "tracking_status",
            "event_at",
        ],
    ),
    "node_bay_assignment": _node(
        "node_bay_assignment",
        "bay_assignment",
        "BayAssignment",
        key="assignment_id",
        properties=[
            "return_reference",
            "order_line_id",
            "item_number",
            "package_count",
            "warehouse_id",
            "bay_id",
            "status",
            "confirmed_by_associate_id",
            "source_updated_at",
        ],
    ),
    "node_support_ticket": _node(
        "node_support_ticket",
        "support_ticket",
        "SupportTicket",
        key="ticket_id",
        properties=["session_id", "status", "external_reference", "return_reference", "updated_at"],
    ),
}

_RELATIONSHIPS = {
    "customer_has_account": _relationship(
        "customer_has_account",
        "HAS_ACCOUNT",
        source_entity_id="customer",
        target_entity_id="customer_account",
        source_match_fields=["customer_key"],
        target_match_fields=["customer_key"],
        cardinality="ONE_TO_MANY",
    ),
    "customer_placed_order": _relationship(
        "customer_placed_order",
        "PLACED_ORDER",
        source_entity_id="customer",
        target_entity_id="sales_order",
        source_match_fields=["customer_key"],
        target_match_fields=["customer_id"],
        cardinality="ONE_TO_MANY",
    ),
    "sales_order_has_order_line": _relationship(
        "sales_order_has_order_line",
        "HAS_ORDER_LINE",
        source_entity_id="sales_order",
        target_entity_id="order_line",
        source_match_fields=["order_id"],
        target_match_fields=["sales_order_id"],
        cardinality="ONE_TO_MANY",
        access="SEED_ONLY",
    ),
    "order_line_references_product": _relationship(
        "order_line_references_product",
        "REFERENCES_PRODUCT",
        source_entity_id="order_line",
        target_entity_id="product",
        source_match_fields=["product_id"],
        target_match_fields=["product_id"],
        cardinality="MANY_TO_ONE",
        access="SEED_ONLY",
    ),
    "sales_order_has_original_shipment": _relationship(
        "sales_order_has_original_shipment",
        "HAS_ORIGINAL_SHIPMENT",
        source_entity_id="sales_order",
        target_entity_id="shipment",
        source_match_fields=["order_id"],
        target_match_fields=["sales_order_id"],
        cardinality="ONE_TO_MANY",
    ),
    "sales_order_has_return": _relationship(
        "sales_order_has_return",
        "HAS_RETURN",
        source_entity_id="sales_order",
        target_entity_id="return",
        source_match_fields=["order_id"],
        target_match_fields=["sales_order_id"],
        cardinality="ONE_TO_MANY",
    ),
    "return_has_return_item": _relationship(
        "return_has_return_item",
        "HAS_RETURN_ITEM",
        source_entity_id="return",
        target_entity_id="return_item",
        source_match_fields=["return_reference"],
        target_match_fields=["return_reference"],
        cardinality="ONE_TO_MANY",
    ),
    "return_item_for_line": _relationship(
        "return_item_for_line",
        "RETURN_ITEM_FOR_LINE",
        source_entity_id="return_item",
        target_entity_id="order_line",
        source_match_fields=["order_line_id"],
        target_match_fields=["order_line_id"],
        cardinality="MANY_TO_ONE",
        access="SEED_ONLY",
    ),
    "return_has_tracking": _relationship(
        "return_has_tracking",
        "HAS_TRACKING",
        source_entity_id="return",
        target_entity_id="return_tracking",
        source_match_fields=["return_reference"],
        target_match_fields=["return_reference"],
        cardinality="ONE_TO_MANY",
    ),
    "bay_assignment_binds_return_item": _relationship(
        "bay_assignment_binds_return_item",
        "BINDS_RETURN_ITEM",
        source_entity_id="bay_assignment",
        target_entity_id="return_item",
        source_match_fields=["return_reference", "order_line_id"],
        target_match_fields=["return_reference", "order_line_id"],
    ),
    "return_tracked_by_support_ticket": _relationship(
        "return_tracked_by_support_ticket",
        "TRACKED_BY_SUPPORT_TICKET",
        source_entity_id="return",
        target_entity_id="support_ticket",
        source_match_fields=["return_reference"],
        target_match_fields=["return_reference"],
        cardinality="ONE_TO_MANY",
    ),
}


def build_interim_active_schema(
    *,
    configuration_release_id: str,
    configuration_checksum: str,
    approved_by: str,
    approved_at: Any,
) -> ActiveSchema:
    """Construct the interim ActiveSchema. Caller supplies release identity/approval
    metadata (never hardcoded here) so every activation is independently attributable."""

    return ActiveSchema.model_validate(
        {
            "configuration_release_id": configuration_release_id,
            "configuration_checksum": configuration_checksum,
            "release_status": "ACTIVE",
            "approved_by": approved_by,
            "approved_at": approved_at,
            "schema_version": "interim-sync-service-2026.08",
            "policy_version": "interim-sync-service-2026.08",
            "prompt_version": "interim-sync-service-2026.08",
            "compiler_version": "1.0.0",
            "runtime_mode": "CONNECTED_SYNC",
            "sources": _SOURCES,
            "entities": _ENTITIES,
            "graph": {
                "database": "business_knowledge",
                "nodes": _NODES,
                "relationships": _RELATIONSHIPS,
                "constraints": (),
                "indexes": (),
            },
            "agent_policies": {
                "sync_service_agent": {
                    "agent_id": "sync_service_agent",
                    "task_queue": "sync-service-agent-queue",
                    "allowed_business_capabilities": ["order-discovery", "return-tracking"],
                    "allowed_roles": list(_ASSOCIATE),
                    "allowed_entity_ids": list(_ENTITIES),
                    "standard_model_refs": ["model://standard/sync-service"],
                }
            },
        }
    )
