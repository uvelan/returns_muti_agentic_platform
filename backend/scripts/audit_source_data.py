"""Report what the source collections cannot answer, before a graph build does.

A graph build is silent about absence. A field whose physical path resolves on
no document projects as `null`, an entity whose record path is missing projects
as nothing at all, and both look exactly like a source that legitimately had no
data. The copilot then answers "I could not find any orders" and the defect
reads as a broken agent.

Three things are checked, all read-only:

1. **Every declared path, against every document.** `generate_seed_data._verify`
   does this against one document to prove a shape; this does it across the
   whole collection to show how often the shape is actually populated. A path
   that resolves on 3% of documents is a field the agent will offer to search on
   and then find nothing with.
2. **The joins.** Order line to product, order to customer, shipment to order.
   A dataset whose joins do not resolve produces a graph with nodes and no
   edges, which is worse than an empty one: it looks built.
3. **The fields a return cannot proceed without** -- the order number, the
   customer name, the line's SKU, description and quantity. Absent here is not
   a statistic, it is an order nobody can raise a return against.

    python backend/scripts/audit_source_data.py
    python backend/scripts/audit_source_data.py --database return_source_smoke
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT / "src"))

from pymongo import AsyncMongoClient  # noqa: E402

from return_platform.configuration.settings import Settings  # noqa: E402
from return_platform.dynamic_knowledge.config_loader import load_active_schema  # noqa: E402
from return_platform.dynamic_knowledge.schema import ActiveSchema, PathOrigin  # noqa: E402

#: Source asset id -> the collection it is extracted from. The schema states
#: this in `object_ref.name`; repeated here only for the ones this audit reads.
COLLECTIONS: dict[str, str] = {
    "source_sales": "salesInv",
    "source_customers": "customerOutboundCDM",
    "source_products": "lkpSearchProduct",
    "source_shipments": "shipmentInfo",
}

#: Fields a return cannot be raised without. Reported as counts of documents,
#: never as a percentage: one order the copilot cannot name is one an associate
#: is standing in front of a customer failing to find.
REQUIRED: dict[str, tuple[str, ...]] = {
    "sales_order": ("sales_order_number", "customer_name", "account_id", "order_date"),
    "order_line": ("sales_order_number", "sku", "product_description", "ordered_quantity"),
    "customer": ("customer_id", "customer_name", "account_id"),
}

#: `lineType` values that name a product. The rest -- `C` comment, `CB`
#: charge-back, `F` freight, `NA` -- are lines of an order that were never an
#: item, and they carry no SKU, description or quantity by design. Holding them
#: to the product-line requirements reports 65 defects in a corpus that has
#: none, and hides the real ones underneath.
PRODUCT_LINE_TYPES: frozenset[str] = frozenset({"MP", "SP"})

#: Where `lineType` sits on an exploded line. Read directly rather than through
#: the schema because the requirement being filtered is this audit's, not the
#: schema's.
LINE_TYPE_PATH: tuple[str, ...] = ("salesLnsEventData", "lineType")

#: How thin a field may be before it is called out. A field no document carries
#: is a defect; one carried by a tenth of them is usually just optional.
THIN = 0.02

#: `salesInv` holds more than orders. `DALLAS*WE130468*H` is an invoice-shaped
#: record -- `salesInvEventData`/`salesInv`, no header and no lines -- and the
#: schema gates `sales_order` on `docType: headerLines`, so it projects nothing
#: by design. Auditing it as an order reported five defects against a corpus
#: that had none, which is how a clean run stops meaning anything.
ORDER_DOCUMENT_TYPE = "headerLines"


def _is_order(document: Any) -> bool:
    event = document.get("salesHdrEventData") if isinstance(document, dict) else None
    return isinstance(event, dict) and event.get("docType") == ORDER_DOCUMENT_TYPE


def _resolve(node: Any, path: tuple[str, ...]) -> Any:
    for part in path:
        if isinstance(node, list):
            node = node[0] if node else None
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    if isinstance(node, list):
        return node[0] if node else None
    return node


def _records(document: Any, record_path: tuple[str, ...]) -> list[Any]:
    """Every exploded record of one document, or the document itself."""
    if not record_path:
        return [document]
    node: Any = document
    for part in record_path:
        if isinstance(node, list):
            node = node[0] if node else None
        if not isinstance(node, dict):
            return []
        node = node.get(part)
    if isinstance(node, list):
        return [item for item in node if item is not None]
    return [node] if node is not None else []


async def _audit_entity(
    schema: ActiveSchema, entity_id: str, documents: list[dict[str, Any]]
) -> list[str]:
    entity = schema.entities[entity_id]
    record_path = tuple(entity.record_path) if entity.explode else ()
    populated: Counter[str] = Counter()
    total_records = 0
    orphan_documents = 0

    required_records = 0
    required_populated: Counter[str] = Counter()

    for document in documents:
        if entity.source_asset_id == "source_sales" and not _is_order(document):
            continue
        records = _records(document, record_path)
        if not records:
            orphan_documents += 1
            continue
        for record in records:
            total_records += 1
            # A comment line is not a product line, and the required-field check
            # only means anything against the lines a return can name.
            product_line = (
                entity_id != "order_line"
                or str(_resolve(record, LINE_TYPE_PATH) or "").upper() in PRODUCT_LINE_TYPES
            )
            if product_line:
                required_records += 1
            for field_id, field in entity.fields.items():
                if field.physical_path is None:
                    continue
                node = record if field.path_origin is PathOrigin.CURRENT_RECORD else document
                value = _resolve(node, tuple(field.physical_path))
                if value is not None and value != "":
                    populated[field_id] += 1
                    if product_line:
                        required_populated[field_id] += 1

    findings: list[str] = []
    if orphan_documents:
        findings.append(
            f"  RECORD PATH  {orphan_documents}/{len(documents)} documents carry no "
            f"{'/'.join(record_path)} record -- they project as nothing"
        )
    if not total_records:
        findings.append(f"  EMPTY        {entity_id} projects no records at all")
        return findings

    for field_id, field in sorted(entity.fields.items()):
        if field.physical_path is None:
            continue
        count = populated[field_id]
        share = count / total_records
        required = field_id in REQUIRED.get(entity_id, ())
        if count == 0:
            findings.append(
                f"  MISSING      {field_id} at {'.'.join(field.physical_path)} "
                f"resolves on 0/{total_records} records"
            )
        elif required and required_populated[field_id] < required_records:
            findings.append(
                f"  INCOMPLETE   {field_id} absent on "
                f"{required_records - required_populated[field_id]}/{required_records} "
                "records a return can name, and it cannot be raised without it"
            )
        elif share < THIN:
            findings.append(
                f"  THIN         {field_id} on {count}/{total_records} records ({share:.1%})"
            )
    return findings


def _join_report(
    orders: list[dict[str, Any]],
    customers: list[dict[str, Any]],
    products: list[dict[str, Any]],
    shipments: list[dict[str, Any]],
) -> list[str]:
    findings: list[str] = []

    product_ids = {str(document.get("_id")) for document in products}
    referenced: set[str] = set()
    for order in orders:
        for line in order.get("salesLines") or []:
            master = (line.get("lineData") or {}).get("masterProductId")
            if master:
                referenced.add(str(master))
    unresolved = referenced - product_ids
    findings.append(
        f"  order line -> product   {len(referenced) - len(unresolved)}/{len(referenced)} resolve"
        + (f"  MISSING {len(unresolved)}" if unresolved else "")
    )

    # Not order -> master party: the schema sources `customer` from `salesInv`
    # itself ("the order carries custId/custName directly, so this joins to
    # sales_order without the unverifiable hop"), and `customerOutboundCDM`
    # feeds `customer_party`/`customer_account`, which relate only to each
    # other. Auditing a join the release does not declare reports a break that
    # nothing would ever have read.
    parties = sum(1 for document in customers if (document.get("party") or {}))
    findings.append(f"  party -> account        {parties}/{len(customers)} parties carry a record")

    order_numbers = {
        str((order.get("salesHdrEventData") or {}).get("orderId"))
        for order in orders
        if (order.get("salesHdrEventData") or {}).get("orderId")
    }
    shipment_orders = {
        str((document.get("shipmentInfoEventData") or {}).get("trilOrdNum"))
        for document in shipments
        if (document.get("shipmentInfoEventData") or {}).get("trilOrdNum")
    }
    joined = shipment_orders & order_numbers
    findings.append(
        f"  shipment -> order       {len(joined)}/{len(shipment_orders)} resolve"
        + (
            f"  MISSING {len(shipment_orders) - len(joined)} -- these shipments attach to no order"
            if len(joined) < len(shipment_orders)
            else ""
        )
    )
    return findings


def _delivery_report(orders: list[dict[str, Any]]) -> list[str]:
    """How many orders can answer the DELIVERED rule, condition by condition."""
    pickup = {"CPU", "WCL", "BO"}

    def shipping(order: dict[str, Any]) -> dict[str, Any]:
        return ((order.get("salesHdr") or {}).get("salesHdrData") or {}).get("shipping") or {}

    conditions = {
        "orderCode IO/ID": lambda o: (
            (o.get("salesHdrEventData") or {}).get("orderCode") in {"IO", "ID"}
        ),
        "trilogieFile ORDER": lambda o: (
            (o.get("salesHdrEventData") or {}).get("trilogieFile") == "ORDER"
        ),
        "shipVia not pickup": lambda o: shipping(o).get("shipViaCode") not in pickup,
        "fleetwise Completed": lambda o: shipping(o).get("fleetwiseStatus") == "Completed",
        "podSigTd present": lambda o: shipping(o).get("podSigTd") is not None,
    }
    findings = [
        f"  {name:<22} {sum(1 for order in orders if test(order))}/{len(orders)}"
        for name, test in conditions.items()
    ]
    delivered = sum(1 for order in orders if all(test(order) for test in conditions.values()))
    findings.append(f"  {'DELIVERED (all five)':<22} {delivered}/{len(orders)}")
    return findings


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default=None, help="source database (default: configured)")
    arguments = parser.parse_args()

    settings = Settings()
    schema = load_active_schema(settings.dynamic_knowledge_schema_path)
    client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
        settings.source_mongo_dsn.get_secret_value()
        if settings.source_mongo_dsn is not None
        else os.environ["PLATFORM_SOURCE_MONGO_DSN"]
    )
    database = client[arguments.database or settings.source_mongo_database]

    loaded: dict[str, list[dict[str, Any]]] = {}
    for asset, collection in COLLECTIONS.items():
        loaded[asset] = await database[collection].find({}).to_list(length=None)

    print(f"source database: {database.name}")
    for asset, collection in COLLECTIONS.items():
        print(f"  {collection:<22} {len(loaded[asset])} documents")
    non_orders = [
        str(document.get("_id")) for document in loaded["source_sales"] if not _is_order(document)
    ]
    if non_orders:
        print(
            f"  ({len(non_orders)} salesInv document(s) are not order headers and project "
            f"no order: {', '.join(non_orders[:3])})"
        )

    defects = 0
    print("\nDECLARED PATHS")
    for entity_id, entity in sorted(schema.entities.items()):
        documents = loaded.get(entity.source_asset_id)
        if documents is None:
            continue
        findings = await _audit_entity(schema, entity_id, documents)
        if findings:
            defects += sum(1 for line in findings if "THIN" not in line)
            print(f"\n{entity_id}  ({COLLECTIONS[entity.source_asset_id]})")
            for line in findings:
                print(line)

    print("\nJOINS")
    for line in _join_report(
        loaded["source_sales"],
        loaded["source_customers"],
        loaded["source_products"],
        loaded["source_shipments"],
    ):
        print(line)
        defects += 1 if "MISSING" in line else 0

    print("\nDELIVERY RULE")
    for line in _delivery_report([d for d in loaded["source_sales"] if _is_order(d)]):
        print(line)

    print(f"\ndefects: {defects}")
    await client.close()
    return 1 if defects else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
