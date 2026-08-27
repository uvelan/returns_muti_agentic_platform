"""Wipe every datastore and load the committed reference dataset.

The 100 salesInv documents are real orders with the identities removed (see
`deidentify_reference_dataset.py`). The product and customer collections are
**derived from them** rather than generated independently, which is the whole
point: every order line's `masterProductId` gets a product, and every order's
`custId` gets a customer party, so the graph's joins resolve. Generating the
three collections separately is how you get a plausible-looking dataset in
which `REFERENCES_PRODUCT` has no edges.

This is destructive by design and refuses to run outside development or test.

Usage:
    python scripts/load_reference_dataset.py [DATASET_DIR]
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
from collections import OrderedDict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bson import json_util
from neo4j import GraphDatabase
from pymongo import MongoClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT / "src"))

# `.env` sets `PLATFORM_VAULT_TOKEN_FILE` to a path relative to the repository
# root, so every Vault lookup depends on the working directory. Run from
# `backend/` the token is simply not found and the failure surfaces as
# "Required Vault secret is unavailable" -- which points at Vault rather than at
# the caller's `cd`. Anchoring here means the script works from anywhere.
os.chdir(REPOSITORY_ROOT)

from return_platform.configuration.settings import Settings  # noqa: E402
from return_platform.data_platform.operational_generation.deterministic_values import (  # noqa: E402  # noqa: E402
    build_synthetic_email,
    get_synthetic_name,
)
from return_platform.secrets.runtime import resolve_runtime_settings_from_vault  # noqa: E402

DEFAULT_DATASET = BACKEND_ROOT / "fixtures" / "reference_dataset"
SOURCE_DATABASE = "return_source"

#: Dropped wholesale. `platform` carries the system store and every operational
#: collection; the bootstrap recreates its structures on the next backend start,
#: which is why this runs before the backend does.
DROP_DATABASES = ("platform", "return_platform", SOURCE_DATABASE)

#: Expanded forms for the ERP's abbreviations. `productDesc` reads
#: `16X25 SILV FLEX AIR DUCT R8.0`; nobody searches for that. `webDisplayName`
#: is what the copilot matches free text against, so it has to read like
#: language an associate would actually type.
_WORDS = {
    "RTN": "Return",
    "GRL": "Grille",
    "FLTR": "Filter",
    "AIR": "Air",
    "DUCT": "Duct",
    "FLEX": "Flexible",
    "SILV": "Silver",
    "WHIT": "White",
    "FIN": "Fin",
    "RG": "Roll Groove",
    "FG": "Filter Grille",
    "REG": "Register",
    "GALV": "Galvanised",
    "ELL": "Elbow",
    "PIPE": "Pipe",
    "VLV": "Valve",
    "MTR": "Motor",
    "ASSY": "Assembly",
    "DRFT": "Draft",
    "IND": "Induced",
    "LAV": "Lavatory",
    "CART": "Cartridge",
    "FAUCET": "Faucet",
}


def _human_name(abbreviated: str) -> str:
    return " ".join(_WORDS.get(token, token.title()) for token in abbreviated.split())


def _load(dataset: Path, name: str) -> Any:
    """Parse as MongoDB Extended JSON, not plain JSON.

    The fixtures keep the export's `{"$date": "..."}` wrappers. Read with
    `json.loads` those stay ordinary dicts, Mongo stores them as nested maps,
    and the graph write then fails on `Property values can only be of primitive
    types` -- after the load has already reported success. `json_util` turns
    them back into the BSON datetimes the extract actually held.
    """
    path = dataset / name
    if not path.is_file():
        raise SystemExit(
            f"Reference dataset document is missing: {path}\n"
            "Regenerate it with scripts/deidentify_reference_dataset.py"
        )
    return json_util.loads(path.read_text(encoding="utf-8"))


def _upc_a(key: str) -> str:
    """A valid 12-digit UPC-A for a product id, check digit included.

    `f"7818{key}"[:12]` produced ELEVEN digits, not twelve: the ids here are at
    most eight characters, so `7818` + the id never reached the truncation and
    the check digit was simply absent. Every `upcCode` in the seed was a barcode
    no scanner or validator would accept -- and `upcCode` is one of the six
    fields the active schema actually reads.

    Two id shapes have to reach eleven digits without colliding. Most are numeric
    and at most seven digits, and those are used as they are so the barcode still
    reads back to the product. Eight of the 482 are special-order ids like
    `SP1845832`, and the numeric ones run to eight digits -- both would be
    mangled by a plain slice, so they are hashed to seven digits instead. The
    caller checks the result set for collisions rather than trusting this.
    """
    digits = key if key.isdigit() and len(key) <= 7 else None
    if digits is None:
        digest = hashlib.sha256(key.encode("utf-8")).digest()
        digits = f"{int.from_bytes(digest[:4], 'big') % 10**7:07d}"
    body = f"7818{digits.zfill(7)}"
    odd = sum(int(body[index]) for index in range(0, 11, 2))
    even = sum(int(body[index]) for index in range(1, 11, 2))
    return f"{body}{(10 - (odd * 3 + even) % 10) % 10}"


def _product_facts(orders: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per-product business values, read from the order lines that reference it.

    `lkpSearchProduct.json` is a SINGLE document -- one real product -- so every
    field this loader does not set explicitly is that one product's value
    repeated once per derived product: 482 products all priced 9.99, all weighing
    1.63, all stocked in bin XE1A03 of the same warehouse. A catalogue in which
    nothing distinguishes one item from another cannot teach the copilot anything
    about the item an associate is returning.

    Nothing is invented to fix that. The order lines carry the real numbers --
    `listPrice`, `matrixPrice`, `unitCostAmt`, `invenWhse` -- and
    `deidentify_reference_dataset.py` deliberately kept them: "order numbers,
    dates, statuses, warehouse and branch codes, product descriptions, SKUs,
    quantities and prices are real". So they are read from there.

    First writer wins per field, so a product keeps one price across the corpus
    rather than the price of whichever order happened to be loaded last.
    """
    facts: dict[str, dict[str, Any]] = {}
    for order in orders:
        for line in order.get("salesLines", []):
            data = line.get("lineData") or {}
            master = data.get("masterProductId")
            if master is None:
                continue
            entry = facts.setdefault(str(master), {"warehouses": OrderedDict()})
            for field, source in (
                ("listPrice", "listPrice"),
                ("previousPrice", "matrixPrice"),
            ):
                value = data.get(source)
                # `> 0` and not merely present: these columns are zero-filled on
                # lines where they do not apply, and a zero list price reads as a
                # free product rather than as a missing one.
                if field not in entry and isinstance(value, int | float) and value > 0:
                    entry[field] = round(float(value), 2)
            warehouse = data.get("invenWhse")
            if warehouse not in (None, ""):
                entry["warehouses"][str(warehouse)] = None
    return facts


def _products(orders: list[dict[str, Any]], template: dict[str, Any]) -> list[dict[str, Any]]:
    """One lkpSearchProduct per distinct masterProductId on any order line.

    Keyed on `_id`, because `line_references_product` matches
    order_line.master_product_id against product.product_id, whose physical path
    is `_id`. Anything else here and the relationship cannot form.
    """
    facts = _product_facts(orders)
    products: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for order in orders:
        for line in order.get("salesLines", []):
            data = line.get("lineData") or {}
            master = data.get("masterProductId")
            if master is None or str(master) in products:
                continue
            key = str(master)
            description = str(data.get("productDesc") or "PRODUCT")
            vendor_code = str(data.get("altCode1") or key)
            display_name = _human_name(description)
            known = facts.get(key, {})
            document = json_util.loads(json_util.dumps(template))
            document["_id"] = key
            document["mpData"]["integLocProductId"] = int(key) if key.isdigit() else 0
            document["mpData"]["webDisplayName"] = display_name
            # The template's value is the ONE real product's name, so leaving it
            # put "1/2 IN PEX BALL VALVE" on all 482 -- including on the eBay
            # display name an associate reads next to the one derived above.
            document["mpData"]["ebayWebDisplayName"] = display_name
            document["masterProduct"]["productDesc"] = description
            document["masterProduct"]["prodLongDesc"] = display_name
            document["masterProduct"]["vendorProdCode"] = vendor_code
            document["masterProduct"]["upcCode"] = _upc_a(key)
            document["fld"]["baseModelNumber"] = vendor_code
            document["fopt"]["mfgPartNum"] = vendor_code
            document["fopt"]["mstrProdId"] = f"{key}*19635"
            document["integ"]["locProduct"] = key

            pricing = document["pricingData"]
            for field in ("listPrice", "previousPrice"):
                if field in known:
                    pricing[field] = known[field]

            # Warehouse rows: the template carries 28 of them, all for the one
            # real product. Replaced with the warehouses that actually stocked
            # THIS product, so `prodAvailQty` questions have somewhere true to
            # land. Falls back to the template rows when a product was never
            # shipped from stock, rather than leaving a product with none.
            warehouses = list(known.get("warehouses") or ())
            if warehouses:
                row_template = json_util.loads(json_util.dumps(document["whseProducts"][0]))
                rows = []
                for warehouse in warehouses:
                    row = json_util.loads(json_util.dumps(row_template))
                    row["whseId"] = warehouse
                    rows.append(row)
                document["whseProducts"] = rows
                pricing["productWhseId"] = warehouses[0]
            products[key] = document

    # A duplicate barcode across two products is worse than the malformed one
    # this replaced: it is well-formed, so nothing downstream questions it, and
    # a scan resolves to whichever row is found first. Cheap to check, and the
    # only thing standing behind the hashed ids above.
    codes = [document["masterProduct"]["upcCode"] for document in products.values()]
    if len(set(codes)) != len(codes):
        duplicated = sorted({code for code in codes if codes.count(code) > 1})
        raise SystemExit(f"upcCode collision across products: {duplicated[:5]}")
    return list(products.values())


#: The address row on a salesInv order, mapped to the CDM location it belongs in.
_ADDRESS_FIELDS = (
    ("address1", "address1"),
    ("address2", "address2"),
    ("address3", "address3"),
    ("city", "city"),
    ("state", "state"),
    ("zipCode", "postalCode"),
    ("country", "country"),
)


def _customer_addresses(orders: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Each customer's own address, taken from an order they actually placed.

    `customerOutboundCDM.json` is one template document, so without this every
    derived customer inherited the same location -- all 97 of them at 512 QUARRY
    AVENUE, RIVERTON TX 75001. The orders carry six distinct de-identified cities
    already harmonised with their state and postcode by
    `deidentify_reference_dataset._harmonise_locations`, so the address a
    customer is given here is one that customer is genuinely associated with, and
    a search by city returns the accounts that are really in it.
    """
    found: dict[str, dict[str, Any]] = {}
    for order in orders:
        customer_id = str(order["salesHdr"]["salesHdrData"]["custId"])
        if customer_id in found:
            continue
        for row in (order.get("customer") or {}).get("address") or []:
            if isinstance(row, dict) and row.get("city"):
                found[customer_id] = row
                break
    return found


def _apply_address(party: dict[str, Any], address: dict[str, Any] | None) -> None:
    """Write one order address into every party site location on the template."""
    if not address:
        return
    for site in party.get("partySites") or []:
        for location in site.get("partySiteLocations") or []:
            for source, destination in _ADDRESS_FIELDS:
                value = address.get(source)
                if value not in (None, ""):
                    location[destination] = value


def _customers(orders: list[dict[str, Any]], template: dict[str, Any]) -> list[dict[str, Any]]:
    """One customerOutboundCDM per distinct custId.

    Carries the bridge back to salesInv at the path the **real** documents use:
    `party[].partyMainCusts[].mainCusts` holds `BRANCH*CUSTID`, and the CUSTID
    half is what the order writers copy into `custId`. `customer_account`
    derives `customer_id` and `customer_branch_id` from it with a `SPLIT_PART`
    on the `*`.

    This used to build `party[].custAccts[].additionalCustomerInfo[]`, taken
    from the field specification rather than from data. **No real CDM document
    has a `custAccts` array at any level** -- verified against `MASTER:900781`,
    the only non-synthetic one in the extract -- so that path extracted nothing
    and the fixture cleared the entity's validation error by manufacturing
    exactly the shape the declaration asserted and the source lacks. See D41 and
    D48 in `docs/RETURN_COPILOT_EXECUTION_STATE.md`.
    """
    addresses = _customer_addresses(orders)
    customers: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for index, order in enumerate(orders):
        account_id = order["salesHdrEventData"]["accountId"]
        header = order["salesHdr"]["salesHdrData"]
        customer_id = str(header["custId"])
        if customer_id in customers:
            continue
        name = str(header["custName"])
        # A *person*, not the company again. Both of these used to be set to
        # `name`, so every contact in the seed read "BLUEFIN UTILITIES BLUEFIN
        # UTILITIES" -- a company in a person field, twice. `get_synthetic_name`
        # is the repository's answer to this and is what
        # `test_synthetic_names_are_realistic_safe_and_deterministic` guards:
        # stable, two words, and free of the "Synthetic"/"Sandbox" tells.
        contact = get_synthetic_name(int(customer_id) if customer_id.isdigit() else index)
        contact_first, _, contact_last = contact.partition(" ")
        party_id = f"9{customer_id.zfill(6)}"[:7]
        document = json_util.loads(json_util.dumps(template))
        document["_id"] = f"MASTER:{party_id}"
        document["partyId"] = party_id
        document["customerParties"] = [{"partyName": name, "partyNumber": party_id}]
        party = document["party"][0]
        party["partyNumber"] = party_id
        party["partyName"] = name
        party["organizationName"] = name
        party["customerContactPoints"] = [
            {
                "contactPointId": f"MASTER*{party_id}*0",
                "contactPointType": "PHONE",
                "phoneAreaCode": "555",
                "phoneNumber": f"555-01{index % 100:02d}",
                "searchPhoneNumber": f"55501{index % 100:02d}",
                # Readable, and still undeliverable: `.example` is reserved by
                # RFC 2606 and can never be registered, so this is as unroutable
                # as the `generated+000000@example.invalid` it replaced while
                # reading as the contact it belongs to. Same treatment the
                # de-identifier now gives every address in salesInv.
                "emailAddress": build_synthetic_email(contact, name),
                "personFirstName": contact_first,
                "personLastName": contact_last or contact_first,
            }
        ]
        party["partyMainCusts"] = [
            {
                "mainCusts": f"{account_id}*{customer_id}",
                "mainCustsName": name,
                "mainCustJobs": [],
            }
        ]
        party["additionalMcustomerInfo"] = {
            "mcustId": party_id,
            "mcustPhone": f"55501{index % 100:02d}",
            "searchMcustPhone": f"155501{index % 100:02d}",
        }
        _apply_address(party, addresses.get(customer_id))
        party.pop("custAccts", None)
        customers[customer_id] = document
    return list(customers.values())


def _wipe_neo4j(settings: Settings) -> None:
    driver = GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value()),
    )
    try:
        with driver.session() as session:

            def total(cypher: str) -> int:
                """`.single()` is `Record | None`, and a wipe that silently did
                nothing is worse than one that stops. An aggregate always
                returns a row, so no row means the query did not run."""
                record = session.run(cypher).single()
                if record is None:
                    raise SystemExit(f"Neo4j returned no row for: {cypher}")
                return int(record["total"])

            before = total("MATCH (n) RETURN count(n) AS total")
            # Batched: one DETACH DELETE over a large graph exhausts the heap.
            while True:
                deleted = total(
                    "MATCH (n) WITH n LIMIT 10000 DETACH DELETE n RETURN count(n) AS total"
                )
                if deleted == 0:
                    break
            for record in session.run("SHOW CONSTRAINTS YIELD name RETURN name"):
                session.run(f"DROP CONSTRAINT `{record['name']}` IF EXISTS")
            for record in session.run("SHOW INDEXES YIELD name, type RETURN name, type"):
                if record["type"] != "LOOKUP":
                    session.run(f"DROP INDEX `{record['name']}` IF EXISTS")
            print(f"neo4j                nodes {before} -> 0, constraints and indexes dropped")
    finally:
        driver.close()


async def main() -> int:
    dataset = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DATASET
    settings, _ = await resolve_runtime_settings_from_vault(
        Settings(),  # type: ignore[call-arg]
        resolve_ai_credentials=False,
    )
    if settings.environment not in {"development", "test"}:
        raise SystemExit(
            f"Refusing to wipe datastores in environment {settings.environment!r}. "
            "This script is destructive and is for development and test only."
        )

    orders = _load(dataset, "salesInv1.json")
    products = _products(orders, _load(dataset, "lkpSearchProduct.json"))
    customers = _customers(orders, _load(dataset, "customerOutboundCDM.json"))

    client: MongoClient = MongoClient(settings.mongo_dsn.get_secret_value())
    for name in client.list_database_names():
        if name in DROP_DATABASES or name.startswith("probe_"):
            client.drop_database(name)
            print(f"dropped mongo database {name}")
    _wipe_neo4j(settings)

    source_dsn = (
        settings.source_mongo_dsn.get_secret_value()
        if settings.source_mongo_dsn is not None
        else settings.mongo_dsn.get_secret_value()
    )
    source_client: MongoClient = (
        client if source_dsn == settings.mongo_dsn.get_secret_value() else MongoClient(source_dsn)
    )
    source = source_client[SOURCE_DATABASE]
    now = datetime.now(UTC)
    for order in orders:
        order.setdefault("salesHdrEventMeta", {})["lastUpdateTs"] = now
    for product in products:
        product.setdefault("eventMeta", {})["lastUpdateTS"] = now
    for customer in customers:
        customer["lastUpdateDate"] = now

    source["salesInv"].insert_many(orders)
    source["lkpSearchProduct"].insert_many(products)
    source["customerOutboundCDM"].insert_many(customers)

    lines = sum(len(order.get("salesLines", [])) for order in orders)
    print(f"salesInv             {source['salesInv'].count_documents({})}")
    print(f"lkpSearchProduct     {source['lkpSearchProduct'].count_documents({})}")
    print(f"customerOutboundCDM  {source['customerOutboundCDM'].count_documents({})}")
    print(f"order lines          {lines}")
    print("\nNext: python scripts/build_knowledge_graph.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
