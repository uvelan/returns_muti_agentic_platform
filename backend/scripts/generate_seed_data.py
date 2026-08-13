#!/usr/bin/env python3
"""Generate a realistic source corpus from the shapes the active schema declares.

**Schema-driven, not shape-guessed.** The generator does not hard-code what a
`salesInv` document looks like. It reads the active schema, and for every entity
bound to a source it writes a value at each field's declared `physical_path`,
honouring `record_path`, `explode`, `path_origin`, and the `where` selectors
that decide whether a document projects at all. A document built that way
satisfies the schema by construction, and a schema change is picked up without
editing this file. The alternative -- transcribing the sample documents by hand
-- produces a corpus that looks right and silently omits whatever path was
mistyped, which is exactly how an entity goes missing from the graph with no
error anywhere.

**One document is built per source asset, not per entity.** Four entities share
`salesInv` -- `sales_order`, `customer`, `order_line`, `contact_point` -- and
extraction runs all four over whatever document it is handed, so a document that
carries only the entity someone had in mind leaves the rest with nothing to
project. `_build_source_document` therefore takes a source asset id and builds
every entity bound to it, nesting each exploded entity's records at its
`record_path` inside the enclosing entity's records. Which collection an entity
belongs in is then the schema's answer, not the caller's: `contact_point` is
embedded on the *order* under `customer.address`, and only `customer_party` and
the `customer_account` bridge nested inside it live on `customerOutboundCDM`.

**Curated name pools, no `faker`.** One fewer dependency, fully offline, and
deterministic: the same `seed` in `config/seed/generation.yaml` reproduces the
same corpus on any machine.

Realism rules that are not decoration:

* **Emails are derived from the name they belong to** (`marcus.feldman@...`), so
  searching an email and searching a name find the same customer -- which is
  what the copilot's clarification policy assumes when it ranks email above
  every narrowing signal.
* **Phone numbers are unique by construction**, allocated from a shuffled index
  over a valid NANP range rather than generated and retried. At 1,000 customers
  a birthday collision is otherwise near-certain, and a duplicate phone makes
  the phone search ambiguous in a way no test would catch.
* **Addresses are drawn as a unit** from a city/state/ZIP table, so no customer
  is ever in "Dallas, VT 90210". A geographically impossible address makes a
  location search look broken when it is the data that is wrong.

**This corpus is synthetic and must never be mistaken for real customer data.**
Domains are `example.com` and friends, phone numbers sit in the 555 exchange
reserved for fiction, and every name is assembled from pools in this file.

## The warehouse structure is provisional

The active schema has no warehouse entity and no warehouse source. Warehouses
appear only as *identifiers* on other records -- `sell_warehouse_id`,
`ship_from_warehouse_id`, `inventory_warehouse_id` on an order, and bay and
warehouse ids on a return handling unit -- so there is no declared shape to
generate against, and nothing reads a warehouse document today.

What this writes to `warehouseMaster` is therefore **invented for this project's
needs, not derived from a real Ferguson structure**: an id, a name, an address
drawn the same way a customer's is, a bay list, and capacity. It exists so that
the warehouse ids scattered across orders resolve to something with a name and a
location, and so the return-side work has a master to point at when a real
structure arrives. See `docs/SEED_DATA_GENERATION.md`.

Usage, from the repository root so the token and `.env` paths resolve:

    PYTHONPATH=backend/src backend/.venv/Scripts/python.exe \\
        backend/scripts/generate_seed_data.py [config.yaml]
"""

from __future__ import annotations

import asyncio
import random
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml
from pymongo import AsyncMongoClient

from return_platform.configuration.settings import BACKEND_ROOT, Settings
from return_platform.dynamic_knowledge.config_loader import load_active_schema
from return_platform.dynamic_knowledge.schema import (
    ActiveSchema,
    EntityDefinition,
    FieldType,
    PathOrigin,
)

DEFAULT_CONFIG = BACKEND_ROOT / "config" / "seed" / "generation.yaml"

# ---------------------------------------------------------------------------
# Curated pools. Ordinary names that read as real without belonging to anyone:
# common given names and surnames combined freely, and trade-company tokens in
# the style the sample data uses ("HIGHVIEW PLUMBING INC").
# ---------------------------------------------------------------------------

GIVEN_NAMES = (
    "Marcus",
    "Danielle",
    "Priya",
    "Andre",
    "Colleen",
    "Hector",
    "Nadia",
    "Trevor",
    "Imani",
    "Grant",
    "Rosa",
    "Dmitri",
    "Fiona",
    "Malik",
    "Camille",
    "Sean",
    "Yuki",
    "Alejandro",
    "Bridget",
    "Omar",
    "Lena",
    "Curtis",
    "Anita",
    "Rafael",
    "Simone",
    "Douglas",
    "Farah",
    "Nathan",
    "Beatriz",
    "Kyle",
    "Ingrid",
    "Terrence",
)

SURNAMES = (
    "Feldman",
    "Okonkwo",
    "Vasquez",
    "Lindqvist",
    "Barrett",
    "Nakamura",
    "Duffy",
    "Castellanos",
    "Whitfield",
    "Rahman",
    "Kowalski",
    "Mbeki",
    "Sorensen",
    "Pruitt",
    "Delacroix",
    "Ferraro",
    "Adeyemi",
    "Novak",
    "Guerrero",
    "Hollingsworth",
    "Bhatt",
    "Sandoval",
    "Kirkland",
    "Petrov",
    "Ngata",
    "Ellison",
    "Moreau",
    "Vance",
)

COMPANY_HEADS = (
    "Highview",
    "Ironbridge",
    "Cedar Ridge",
    "Northgate",
    "Blue Harbor",
    "Stonecrest",
    "Fairmont",
    "Lakeside",
    "Redwood",
    "Summit Line",
    "Bayard",
    "Kingsford",
    "Cobalt",
    "Meridian",
    "Ashford",
    "Granite Peak",
    "Silverton",
    "Westbrook",
)

COMPANY_TRADES = (
    "PLUMBING",
    "MECHANICAL",
    "HVAC",
    "SUPPLY",
    "CONTRACTING",
    "PIPE & FITTING",
    "WATERWORKS",
    "BUILDERS",
    "FACILITIES",
    "UTILITY",
)

COMPANY_SUFFIXES = ("INC", "LLC", "CO", "GROUP", "SERVICES")

STREET_NAMES = (
    "Beacon",
    "Whitcomb",
    "Larkspur",
    "Foundry",
    "Kestrel",
    "Alderman",
    "Corbin",
    "Mulberry",
    "Pinewood",
    "Halstead",
    "Chandler",
    "Ravenswood",
    "Dunmore",
    "Sycamore",
    "Winslow",
    "Trenholm",
    "Beaufort",
    "Calloway",
)

STREET_TYPES = ("St", "Ave", "Rd", "Blvd", "Dr", "Way", "Ln")

# City / state / ZIP prefix drawn together, so an address is never
# geographically impossible.
PLACES = (
    ("CHARLOTTE", "NC", "282"),
    ("DALLAS", "TX", "752"),
    ("CHICAGO", "IL", "606"),
    ("PHOENIX", "AZ", "850"),
    ("TEMPE", "AZ", "852"),
    ("LEWISTON", "ME", "042"),
    ("ATLANTA", "GA", "303"),
    ("DENVER", "CO", "802"),
    ("SEATTLE", "WA", "981"),
    ("NASHVILLE", "TN", "372"),
    ("COLUMBUS", "OH", "432"),
    ("TAMPA", "FL", "336"),
    ("RICHMOND", "VA", "232"),
    ("OMAHA", "NE", "681"),
    ("BOISE", "ID", "837"),
    ("SPOKANE", "WA", "992"),
    ("MOBILE", "AL", "366"),
    ("FRESNO", "CA", "937"),
)

PRODUCT_ADJECTIVES = ("Brushed", "Polished", "Matte", "Compact", "Heavy-Duty", "Low-Flow")
PRODUCT_MATERIALS = ("Brass", "Chrome", "Stainless", "PVC", "Copper", "Cast Iron")
PRODUCT_NOUNS = (
    "Kitchen Faucet",
    "Ball Valve",
    "Elbow Fitting",
    "Water Heater",
    "Shower Trim",
    "Sump Pump",
    "Pipe Coupling",
    "Toilet Flange",
    "Pressure Regulator",
    "Sink Basin",
)
PRODUCT_BRANDS = ("Moen", "Kohler", "Delta", "Zurn", "Rheem", "Watts", "Nibco")

ORDER_STATUSES = ("CALLCSR", "OPEN", "SHIPPED", "INVOICED", "CLOSED")
UNITS = ("EA", "BX", "FT", "CS")

# The two shipment families the real `shipmentInfo` collection holds, paired
# with the tracking-number shape each one issues: Convey brokers parcel carriers
# and carries their tracking numbers, DispatchTrack is own-fleet last-mile and
# issues its own route references. Generating one shape for both would make a
# tracking-number search look uniform in a way the real source is not.
#
# `currentStatus` is generated normalised. The real source is not -- it holds
# `intransit`, `in_transit`, `INTRANSIT` and `DELIVEREDD` for two states -- but
# reproducing that here would put dirt in the corpus that no consumer has been
# built to handle yet, and a generator is the wrong place to decide that.
SHIPMENT_STATUSES = ("intransit", "delivered")


class Phones:
    """Unique phone numbers, by construction rather than by retry.

    A shuffled index over the 555-01xx fiction range extended with a subscriber
    counter: pull the n-th value and it cannot repeat, so 1,000 customers get
    1,000 distinct numbers without a collision set to consult.
    """

    def __init__(self, rng: random.Random, capacity: int) -> None:
        area_codes = (205, 212, 312, 404, 469, 512, 602, 704, 720, 813, 919)
        pool: list[str] = []
        for area in area_codes:
            for subscriber in range(10_000):
                pool.append(f"({area}) 555-{subscriber % 10_000:04d}")
                if len(pool) >= capacity * 3:
                    break
            if len(pool) >= capacity * 3:
                break
        rng.shuffle(pool)
        self._pool = pool
        self._next = 0

    def take(self) -> str:
        if self._next >= len(self._pool):
            raise RuntimeError("phone pool exhausted; raise the capacity estimate")
        value = self._pool[self._next]
        self._next += 1
        return value


class People:
    """Names, and the emails derived from them."""

    def __init__(self, rng: random.Random) -> None:
        self._rng = rng
        self._seen_emails: set[str] = set()

    def person(self) -> tuple[str, str]:
        return self._rng.choice(GIVEN_NAMES), self._rng.choice(SURNAMES)

    def company(self) -> str:
        head = self._rng.choice(COMPANY_HEADS).upper()
        trade = self._rng.choice(COMPANY_TRADES)
        suffix = self._rng.choice(COMPANY_SUFFIXES)
        return f"{head} {trade} {suffix}"

    def email(self, given: str, family: str, domains: tuple[str, ...]) -> str:
        """Derived from the name, so a name search and an email search agree.

        A numeric suffix appears only on a genuine collision -- two people
        called the same thing -- which is what happens in a real directory too.
        """
        stem = f"{given}.{family}".lower().replace(" ", "")
        domain = self._rng.choice(domains)
        candidate = f"{stem}@{domain}"
        attempt = 2
        while candidate in self._seen_emails:
            candidate = f"{stem}{attempt}@{domain}"
            attempt += 1
        self._seen_emails.add(candidate)
        return candidate


class Places:
    def __init__(self, rng: random.Random) -> None:
        self._rng = rng

    def address(self) -> dict[str, str]:
        city, state, zip_prefix = self._rng.choice(PLACES)
        return {
            "line1": f"{self._rng.randint(100, 9899)} {self._rng.choice(STREET_NAMES)} "
            f"{self._rng.choice(STREET_TYPES)}",
            "city": city,
            "state": state,
            "postal_code": f"{zip_prefix}{self._rng.randint(10, 99)}",
        }


def _assign(document: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    """Write `value` at a declared physical path, creating the nesting."""
    node = document
    for part in path[:-1]:
        nested = node.get(part)
        if not isinstance(nested, dict):
            nested = {}
            node[part] = nested
        node = nested
    node[path[-1]] = value


def _digits(phone: str) -> str:
    """The bare-digit form the order and CDM ship-to fields carry."""
    return "".join(character for character in phone if character.isdigit())


def _sample_value(
    field_type: FieldType,
    field_id: str,
    context: dict[str, Any],
    rng: random.Random,
) -> Any:
    """A value that fits the declared type and reads like the field it is for.

    Context supplies anything already decided for this record -- the order
    number, the customer's city -- so related fields agree instead of each
    inventing its own answer.
    """
    if field_id in context:
        return context[field_id]
    if field_type is FieldType.INTEGER:
        return rng.randint(1, 40)
    if field_type is FieldType.NUMBER:
        return round(rng.uniform(4.5, 2400.0), 2)
    if field_type is FieldType.BOOLEAN:
        return rng.random() < 0.5
    if field_type in (FieldType.DATE, FieldType.DATETIME):
        return context.get("__timestamp__", datetime.now(UTC))
    return f"{field_id}-{rng.randint(1000, 999999)}"


def _entity_fields(
    entity: EntityDefinition,
    document: dict[str, Any],
    context: dict[str, Any],
    rng: random.Random,
    *,
    origins: tuple[PathOrigin, ...],
) -> None:
    """Write the declared fields of `entity` whose `path_origin` is in `origins`.

    Fields whose value the caller already decided arrive through `context`
    keyed by `field_id`; everything else is invented to the declared type.

    **`physical_path` is relative to the field's origin, not to the document**,
    so the origin decides which document a field belongs in: a `CURRENT_RECORD`
    path exists inside the entity's own exploded record and everything else
    exists in a document enclosing it. Writing them all at one level is not a
    tidier approximation -- it is how an exploded entity ends up with no record
    at all, every path resolving somewhere except where the schema reads it.
    """
    for field_id, field in entity.fields.items():
        if field.physical_path is None or field.path_origin not in origins:
            continue
        value = _sample_value(field.data_type, field_id, context, rng)
        _assign(document, tuple(field.physical_path), value)


def _apply_selectors(entity: EntityDefinition, record: dict[str, Any]) -> None:
    """Satisfy the `where` selectors, or the record never projects.

    Selector paths are current-record relative, the same as a `CURRENT_RECORD`
    field's -- `_passes_where` tests the exploded record, not the root.

    `sales_order` is gated on a document-type discriminator; a corpus that
    omits it produces zero orders in the graph and no error anywhere.
    """
    for selector in entity.where:
        if selector.equals is not None:
            _assign(record, tuple(selector.physical_path), selector.equals)


def _source_entities(schema: ActiveSchema, source_asset_id: str) -> list[EntityDefinition]:
    """Every entity bound to this source, shallowest `record_path` first.

    Shallowest first so an entity nested inside another finds its enclosing
    records already built: `customer_account` lives at
    `party.custAccts.additionalCustomerInfo`, inside the `party` records
    `customer_party` creates.
    """
    return sorted(
        (
            entity
            for entity in schema.entities.values()
            if entity.source_asset_id == source_asset_id
        ),
        key=lambda entity: len(entity.record_path),
    )


def _build_source_document(
    schema: ActiveSchema,
    source_asset_id: str,
    document: dict[str, Any],
    context: dict[str, Any],
    rng: random.Random,
    *,
    records: Mapping[str, Sequence[dict[str, Any]]] | None = None,
) -> None:
    """Build one document carrying every entity bound to `source_asset_id`.

    **A document belongs to a source asset, not to an entity.** `salesInv`
    carries `sales_order`, `customer`, `order_line` *and* `contact_point` at
    once, and extraction runs every entity bound to the source over whatever
    document it is handed. Building only the entities someone remembered to name
    is how three of them came to have no record anywhere in the corpus -- an
    entity whose `record_path` was never created projects nothing, and nothing
    projected is indistinguishable from a source that had no data.

    An entity with no `record_path` is written at the document root. An exploded
    entity becomes a list of records at its `record_path`, resolved relative to
    the innermost enclosing entity's records rather than to the root, because
    that is where the path exists: `customer_account` declares
    `party.custAccts.additionalCustomerInfo`, and `party` is the array
    `customer_party` has already built.

    Intermediate segments the record path crosses become plain objects. Nothing
    in the schema says which of them are arrays -- `explode` marks the level the
    records sit at, not every level above it -- and extraction descends through
    a list and a map alike.

    `records` supplies one context per record for an exploded entity, which is
    how an order's lines each carry their own product. An entity absent from it
    gets a single record built from `context`.
    """
    per_entity = records or {}
    built: dict[tuple[str, ...], list[dict[str, Any]]] = {(): [document]}
    for entity in _source_entities(schema, source_asset_id):
        # Extraction ignores the record path of an entity it does not explode
        # and reads every one of that entity's fields from the root.
        record_path = entity.record_path if entity.explode else ()
        if not record_path:
            _entity_fields(entity, document, context, rng, origins=tuple(PathOrigin))
            _apply_selectors(entity, document)
            continue
        enclosing = max((known for known in built if record_path[: len(known)] == known), key=len)
        relative = record_path[len(enclosing) :]
        _entity_fields(entity, document, context, rng, origins=(PathOrigin.ROOT_DOCUMENT,))
        created: list[dict[str, Any]] = []
        for parent in built[enclosing]:
            # The parent record is the map one list level above the exploded
            # record, which is exactly the enclosing entity's record.
            _entity_fields(entity, parent, context, rng, origins=(PathOrigin.PARENT_RECORD,))
            siblings: list[dict[str, Any]] = []
            for record_context in per_entity.get(entity.entity_id) or (context,):
                record: dict[str, Any] = {}
                _entity_fields(
                    entity, record, record_context, rng, origins=(PathOrigin.CURRENT_RECORD,)
                )
                _apply_selectors(entity, record)
                siblings.append(record)
            _assign(parent, relative, siblings)
            created.extend(siblings)
        built[record_path] = created


async def _write(
    collection: Any, documents: list[dict[str, Any]], *, replace: bool, label: str
) -> None:
    if replace:
        await collection.drop()
    for start in range(0, len(documents), 1000):
        batch = documents[start : start + 1000]
        if batch:
            await collection.insert_many(batch)
    print(f"  {label}: {len(documents)}")


async def _run(config: dict[str, Any], config_name: str) -> None:
    counts = config["counts"]
    rng = random.Random(config["seed"])
    people, places = People(rng), Places(rng)
    phones = Phones(rng, counts["customers"] + counts["warehouses"] + 100)
    domains = tuple(config["email_domains"])
    names = config["collections"]

    settings = Settings()
    schema: ActiveSchema = load_active_schema(settings.dynamic_knowledge_schema_path)
    client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
        settings.source_mongo_dsn.get_secret_value()
        if settings.source_mongo_dsn is not None
        else settings.mongo_dsn.get_secret_value()
    )
    database = client[settings.source_mongo_database]
    replace = bool(config.get("replace", True))

    earliest = datetime.fromisoformat(config["order_dates"]["earliest"]).replace(tzinfo=UTC)
    latest = datetime.fromisoformat(config["order_dates"]["latest"]).replace(tzinfo=UTC)
    span_days = max(1, (latest - earliest).days)

    try:
        print(f"generating from {config_name} (seed {config['seed']})")

        warehouses = _warehouses(counts["warehouses"], places, phones, rng)
        await _write(database[names["warehouses"]], warehouses, replace=replace, label="warehouses")

        customers = _customers(counts["customers"], schema, people, places, phones, domains, rng)
        await _write(database[names["customers"]], customers, replace=replace, label="customers")

        products = _products(counts["products"], schema, rng)
        await _write(database[names["products"]], products, replace=replace, label="products")

        orders, order_contexts = _orders(
            counts["orders"],
            schema,
            customers,
            products,
            warehouses,
            config,
            earliest,
            span_days,
            rng,
        )
        await _write(database[names["orders"]], orders, replace=replace, label="orders")

        shipments = _shipments(schema, orders, order_contexts, rng)
        await _write(database[names["shipments"]], shipments, replace=replace, label="shipments")
        if not shipments:
            # A corpus with orders but no shipments is the state this run exists
            # to end, and it is silent otherwise -- fulfillment simply reports
            # every return as awaiting handoff.
            raise SystemExit("no shipments generated; check shipped_fraction and lines_per_order")

        print("\nverifying every declared path resolves against a generated document...")
        _verify(schema, orders[0], customers[0], products[0], shipments[0])
        print("all declared paths resolve.")
    finally:
        await client.close()


def _warehouses(
    count: int, places: Places, phones: Phones, rng: random.Random
) -> list[dict[str, Any]]:
    """Provisional. No warehouse source exists in the schema -- see the docstring."""
    documents = []
    for index in range(count):
        address = places.address()
        code = f"WH{index + 1:03d}"
        documents.append(
            {
                "_id": code,
                "warehouseId": code,
                "name": f"{address['city'].title()} Distribution Center",
                "address": address,
                "phone": phones.take(),
                "bays": [f"BAY-{bay:02d}" for bay in range(1, rng.randint(6, 24))],
                "capacityUnits": rng.randint(500, 5000),
                "acceptsHazmat": rng.random() < 0.3,
                "acceptsOversize": rng.random() < 0.5,
                "sourceUpdatedAt": datetime.now(UTC),
            }
        )
    return documents


def _customers(
    count: int,
    schema: ActiveSchema,
    people: People,
    places: Places,
    phones: Phones,
    domains: tuple[str, ...],
    rng: random.Random,
) -> list[dict[str, Any]]:
    """The `customerOutboundCDM` documents, one master party each.

    Only the entities bound to that source are built here -- `customer_party`
    and the `customer_account` bridge nested inside it. `customer` and
    `contact_point` read from `salesInv`: the order carries custId/custName
    directly and embeds the contact rows under `customer.address`, so both are
    built with the order.

    The bridge is what makes these documents reachable. `customer_account`'s
    `customer_id` is the value `salesInv` copies into custId, and `_orders`
    draws its customer from the contexts returned here, so the two agree by
    construction rather than by coincidence.
    """
    documents = []
    for index in range(count):
        given, family = people.person()
        address = places.address()
        company = people.company()
        account_id = f"ACCT{index + 1:06d}"
        phone = phones.take()
        party_id = f"{900_000 + index + 1}"
        context = {
            "party_id": party_id,
            "account_id": account_id,
            "customer_account": account_id,
            "customer_branch_id": account_id,
            "customer_id": f"CUST{index + 1:06d}",
            "customer_name": company,
            "organization_name": company,
            "party_name": f"{given} {family}",
            "b2b_customer": "Y" if rng.random() < 0.75 else "N",
            "email": people.email(given, family, domains),
            "phone_number": phone,
            "ship_to_phone": _digits(phone),
            "address_line1": address["line1"],
            "city": address["city"],
            "state": address["state"],
            "postal_code": address["postal_code"],
            "county": f"{address['city'].title()} County",
            "__timestamp__": datetime.now(UTC),
        }
        document: dict[str, Any] = {"_id": f"MASTER:{party_id}"}
        _build_source_document(schema, "source_customers", document, context, rng)
        document["__context"] = context
        documents.append(document)
    return documents


def _products(count: int, schema: ActiveSchema, rng: random.Random) -> list[dict[str, Any]]:
    documents = []
    for index in range(count):
        description = (
            f"{rng.choice(PRODUCT_ADJECTIVES)} {rng.choice(PRODUCT_MATERIALS)} "
            f"{rng.choice(PRODUCT_NOUNS)}"
        )
        sku = f"SKU{index + 1:07d}"
        context = {
            "sku": sku,
            "product_id": f"PRD{index + 1:07d}",
            "product_description": description,
            "product_long_description": f"{rng.choice(PRODUCT_BRANDS)} {description}",
            "web_display_name": description,
            "vendor_name": rng.choice(PRODUCT_BRANDS),
            "unit_of_measure": rng.choice(UNITS),
            "__timestamp__": datetime.now(UTC),
        }
        document: dict[str, Any] = {"_id": sku, "__context": context}
        _build_source_document(schema, "source_products", document, context, rng)
        documents.append(document)
    return documents


def _orders(
    count: int,
    schema: ActiveSchema,
    customers: list[dict[str, Any]],
    products: list[dict[str, Any]],
    warehouses: list[dict[str, Any]],
    config: dict[str, Any],
    earliest: datetime,
    span_days: int,
    rng: random.Random,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The order documents, and the contexts they were built from, aligned by index.

    The contexts are returned rather than stashed on the documents because
    shipments have to agree with the order they belong to -- same account, same
    order number -- or `SHIPPED_AS` matches nothing and every shipment lands in
    the graph orphaned.
    """
    line_bounds = config["lines_per_order"]
    shipped_fraction = float(config.get("shipped_fraction", 0.7))

    documents: list[dict[str, Any]] = []
    contexts: list[dict[str, Any]] = []
    for index in range(count):
        customer = rng.choice(customers)["__context"]
        warehouse = rng.choice(warehouses)
        ordered_at = earliest + timedelta(days=rng.randint(0, span_days))
        order_number = f"SO{index + 1:07d}"
        context = {
            "sales_order_number": order_number,
            "order_key": f"{customer['account_id']}*{order_number}",
            "account_id": customer["account_id"],
            "customer_id": customer["customer_id"],
            "customer_name": customer["customer_name"],
            "customer_account": customer["account_id"],
            "ship_to_name": customer["customer_name"],
            "ship_to_city": customer["city"],
            "ship_to_state": customer["state"],
            "ship_to_postal_code": customer["postal_code"],
            "ship_to_phone": customer["ship_to_phone"],
            "order_status": rng.choice(ORDER_STATUSES),
            "order_date": ordered_at,
            "sell_warehouse_id": warehouse["warehouseId"],
            "ship_from_warehouse_id": warehouse["warehouseId"],
            "inventory_warehouse_id": warehouse["warehouseId"],
            "__timestamp__": ordered_at,
        }
        line_contexts: list[dict[str, Any]] = []
        for line_number in range(
            1, rng.randint(line_bounds["minimum"], line_bounds["maximum"]) + 1
        ):
            product = rng.choice(products)["__context"]
            line_context = {
                **context,
                "line_number": line_number,
                "order_line_id": f"{order_number}-{line_number}",
                "sku": product["sku"],
                "product_id": product["product_id"],
                "product_description": product["product_description"],
                "ordered_quantity": rng.randint(1, 25),
                "unit_of_measure": product["unit_of_measure"],
                "shipped_quantity": 0,
            }
            if rng.random() < shipped_fraction:
                line_context["shipped_quantity"] = line_context["ordered_quantity"]
            line_contexts.append(line_context)

        document: dict[str, Any] = {"_id": context["order_key"]}
        _build_source_document(
            schema,
            "source_sales",
            document,
            context,
            rng,
            records={
                "order_line": line_contexts,
                # The contact rows carry the customer's own contact details and
                # postal address. The order context holds neither under those
                # field ids -- it keeps a ship-to copy under `ship_to_*` -- so
                # the customer's own context supplies them, which is also what
                # makes a search by email and a search by name agree.
                "contact_point": ({**customer, **context},),
            },
        )
        documents.append(document)
        contexts.append(context)
    return documents, contexts


def _shipments(
    schema: ActiveSchema,
    orders: list[dict[str, Any]],
    order_contexts: list[dict[str, Any]],
    rng: random.Random,
) -> list[dict[str, Any]]:
    """One `shipmentInfo` document per order that actually shipped something.

    `config/seed/generation.yaml` has named `shipmentInfo` since the generator
    was written and nothing was ever emitted for it, so the fulfillment path had
    no corpus to read and every tracking number in a generated deployment looked
    like a label printed and never collected.

    An order with no shipped line gets no shipment, deliberately: a shipment for
    every order would make "the graph holds no shipment for this number" a state
    the corpus can never produce, and that is precisely the reading W2.6 exists
    to make possible.
    """
    if not _source_entities(schema, "source_shipments"):
        return []
    line_entity = schema.entities.get("order_line")
    shipped_quantity_path = (
        line_entity.fields["shipped_quantity"].physical_path
        if line_entity is not None and "shipped_quantity" in line_entity.fields
        else None
    )
    record_path = tuple(line_entity.record_path) if line_entity is not None else ()

    documents: list[dict[str, Any]] = []
    for index, (order, context) in enumerate(zip(orders, order_contexts, strict=True)):
        if not _has_shipped_line(order, record_path, shipped_quantity_path):
            continue
        ordinal = index + 1
        # Convey brokers parcel carriers, DispatchTrack runs own fleet; the
        # tracking numbers they issue do not look alike, and the source system
        # is the field that says which one a number came from.
        convey = rng.random() < 0.3
        tracking = f"1Z{ordinal:09d}" if convey else f"{3520000000 + ordinal}_474"
        shipment_context = {
            "tracking_number": tracking,
            "sales_order_number": context["sales_order_number"],
            "account_id": context["account_id"],
            "shipment_id": (
                f"shipid{ordinal:06d}"
                if convey
                else f"{context['account_id']}:{context['sales_order_number']}:4"
            ),
            "current_status": rng.choice(SHIPMENT_STATUSES),
            "source_system": "Convey" if convey else "DispatchTrack",
            "__timestamp__": context["__timestamp__"],
        }
        # `_id` mirrors the real collection's composite: account, order and
        # tracking number together, which is the only thing unique about a
        # `shipmentInfo` document -- the tracking number alone is not.
        document: dict[str, Any] = {
            "_id": f"{context['account_id']}*{context['sales_order_number']}*{tracking}"
        }
        _build_source_document(schema, "source_shipments", document, shipment_context, rng)
        documents.append(document)
    return documents


def _has_shipped_line(
    order: dict[str, Any],
    record_path: tuple[str, ...],
    shipped_quantity_path: tuple[str, ...] | None,
) -> bool:
    """Whether any of this order's lines carries a shipped quantity."""
    if shipped_quantity_path is None:
        return True
    node: Any = order
    for part in record_path:
        node = node.get(part) if isinstance(node, dict) else None
    if not isinstance(node, list):
        return False
    for line in node:
        value: Any = line
        for part in shipped_quantity_path:
            value = value.get(part) if isinstance(value, dict) else None
        if isinstance(value, int) and value > 0:
            return True
    return False


def _verify(
    schema: ActiveSchema,
    order: dict[str, Any],
    customer: dict[str, Any],
    product: dict[str, Any],
    shipment: dict[str, Any],
) -> None:
    """Every declared path must resolve, or the entity silently vanishes.

    Checked here rather than left to a graph build: a missing path produces an
    empty projection, and an empty projection is indistinguishable from a source
    that had no data.

    Each path is checked against the document extraction reads it from -- the
    exploded record for a `CURRENT_RECORD` path, the root for a
    `ROOT_DOCUMENT` one. Checking every path against one of them passes a corpus
    the other half of the schema cannot read.
    """
    by_source = {
        "source_sales": order,
        "source_customers": customer,
        "source_products": product,
        "source_shipments": shipment,
    }
    missing: list[str] = []
    for entity_id, entity in schema.entities.items():
        document = by_source.get(entity.source_asset_id)
        if document is None:
            continue
        # Extraction walks the record path only for an entity it explodes; for
        # any other, every field is read from the root.
        record_path = entity.record_path if entity.explode else ()
        base: Any = document
        for part in record_path:
            base = base.get(part) if isinstance(base, dict) else None
            if isinstance(base, list):
                base = base[0] if base else None
        if base is None:
            missing.append(f"{entity_id}: record_path {record_path} absent")
            continue
        for field_id, field in entity.fields.items():
            if field.physical_path is None:
                continue
            if field.path_origin is PathOrigin.CURRENT_RECORD:
                node: Any = base
            elif field.path_origin is PathOrigin.ROOT_DOCUMENT:
                node = document
            else:
                # PARENT_RECORD resolves to one list level above the record,
                # which is the root only when the record path crossed a single
                # list. No entity declares one, so there is nothing to check
                # and no reason to guess which level it would have been.
                continue
            for part in field.physical_path:
                node = node.get(part) if isinstance(node, dict) else None
            if node is None:
                missing.append(f"{entity_id}.{field_id}")
    if missing:
        raise SystemExit(
            "generated documents do not satisfy the schema:\n  " + "\n  ".join(missing[:20])
        )


def main() -> None:
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CONFIG
    if not config_path.is_file():
        raise SystemExit(f"no such config: {config_path}")
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    asyncio.run(_run(config, config_path.name))


if __name__ == "__main__":
    main()
