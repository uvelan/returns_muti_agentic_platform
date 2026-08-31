#!/usr/bin/env python3
"""Generate a large, realistic source corpus in the Ferguson idiom.

**Derived from the real extract, never scraped.** The shapes reproduced here
come from the data already in `return_source`: the real `salesInv` documents
(572 order lines carrying 482 distinct `masterProductId`, with genuine SKUs and
descriptions such as `Q1685` / "16X25 SILV FLEX AIR DUCT R8.0"), the one real
`lkpSearchProduct` document, and the one real `customerOutboundCDM` document.
`scripts/seed_ferguson_idiom.py` holds what was mined and states what each
observation was taken from. Nothing was fetched from any website.

What this run does, in order:

1. **Backs up every real source document first, and never overwrites a backup.**
   `backend/fixtures/real_ferguson_source/` receives the real `salesInv`,
   `customerOutboundCDM`, `lkpSearchProduct` and `shipmentInfo` documents as
   MongoDB Extended JSON. They are the only genuine Ferguson data in the system.
   Once written, that directory -- not the database -- is the authority on what
   the originals said, which is what makes step 2 idempotent.

2. **Renames every customer to an individual person, real records included.**
   An operator decision: `ATLAS MECHANICAL SERVICES` and `TAMILLO PLBG` become
   people, with emails derived from the name. Applied to the *backed-up*
   originals rather than to whatever is currently in Mongo, so running this
   twice produces the same names rather than renaming the renames.
   **Everything except the name and the email is preserved byte for byte** --
   `_id`, order numbers, line structure, SKUs, dates, statuses, account ids.

3. **Replaces the synthetic corpus** -- every document whose `_id` is not in the
   backup -- at the volumes `config/seed/generation.yaml` asks for.

4. **Verifies every path the active schema declares** against a generated
   document of each kind, and refuses to load a corpus the schema cannot read.

**Why the documents are built to a real template rather than from schema paths
alone.** The previous generator wrote a value at each declared `physical_path`
and nothing else, which satisfied the schema and produced documents no ERP would
emit: `upc_code-38342`, `brand_type-403988`, `SKU0000001`. It could not be used
to judge whether a screen reads well, which is what a manual-testing corpus is
for. The builders below write the enclosing structure the real documents carry
and put real-idiom values at the declared paths. The guarantee the old approach
gave -- that a schema change is picked up without editing this file -- is
replaced by `_verify`, which fails loudly and names the field, rather than by
silence.

Realism rules that are not decoration:

* **Emails are derived from the name they belong to** (`richard.reynolds@...`,
  `r.reynolds@...`), so searching an email and searching a name resolve the same
  customer -- which is what the copilot's clarification policy assumes when it
  ranks email above every narrowing signal.
* **Phone numbers are unique by construction**, allocated from a shuffled index
  over the 555 fiction range rather than generated and retried. At 1,000
  customers a birthday collision is otherwise near-certain, and a duplicate
  phone makes phone search ambiguous in a way no test would catch.
* **Addresses are drawn as a unit** from a city/state/ZIP table, so no customer
  is ever in "Dallas, VT 90210".
* **A product has a finish only where a finish is real.** A lavatory faucet
  does; a flex duct does not. See `seed_ferguson_idiom.Category.finishes`.

**This corpus is synthetic and must never be mistaken for real customer data.**
Domains are the RFC 2606 reserved ones, phone numbers sit in the 555 exchange
reserved for fiction, and every name is assembled from pools in
`seed_ferguson_idiom.py`.

## The warehouse master is still provisional

The active schema has no warehouse *source*; warehouses appear only as
identifiers on other records. What this writes to `warehouseMaster` is invented
for this project's needs and nothing reads it -- but the ids are now the real
inventory warehouse ids the order lines carry, so at least they are the same
identifiers. See `docs/SEED_DATA_GENERATION.md`.

Usage, from the repository root so the token and `.env` paths resolve:

    PYTHONPATH=backend/src backend/.venv/Scripts/python.exe \\
        backend/scripts/generate_seed_data.py [config.yaml]
"""

from __future__ import annotations

import asyncio
import random
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml
from bson import json_util
from pymongo import AsyncMongoClient

# `sys.path[0]` is this script's own directory when it is run as a file, so the
# idiom module beside it imports without any path manipulation.
from seed_ferguson_idiom import (
    ASSOCIATES,
    BRANCH_WEIGHTS,
    CATEGORIES,
    DELIVERY_SHIP_VIA_WEIGHTS,
    GIVEN_NAMES,
    INVOICED_STATUS_WEIGHTS,
    LINE_TYPE_WEIGHTS,
    LINES_PER_ORDER,
    ORDER_PREFIXES,
    ORDER_STATUS_WEIGHTS,
    PLACES,
    PO_NUMBERS,
    SALES_TYPE_WEIGHTS,
    SHIP_VIA,
    SKU_SHAPES,
    STREET_NAMES,
    STREET_TYPES,
    SURNAMES,
    UOM_DESCRIPTIONS,
    WAREHOUSE_WEIGHTS,
    Category,
    GeneratedProduct,
    expand,
)

from return_platform.configuration.settings import BACKEND_ROOT, Settings
from return_platform.dynamic_knowledge.config_loader import load_active_schema
from return_platform.dynamic_knowledge.schema import ActiveSchema, PathOrigin

DEFAULT_CONFIG = BACKEND_ROOT / "config" / "seed" / "generation.yaml"
BACKUP_DIRECTORY = BACKEND_ROOT / "fixtures" / "real_ferguson_source"


def _moment(moment: datetime) -> datetime:
    """A naive BSON datetime, which is what the real documents hold.

    **Load-bearing, and it cost a whole graph build to learn.** Every source
    carries `incremental_cursor_field: source_updated_at`, and
    `MongoDBSourceScanConnector` bounds its scan with
    `{<cursor field>: {"$lte": <Date>}}` after reading the collection's high
    watermark. MongoDB compares within a BSON type bracket, and String sorts
    below Date, so **a timestamp written as a string matches no date bound at
    all**. A corpus that wrote `"2026-03-26 14:24:00.000000"` there scanned as
    zero records, the run reported COMPLETED, and the new generation activated
    holding only the 101 real documents -- silently, because an unscanned
    document and an absent one are indistinguishable downstream.

    Naive rather than UTC-aware because that is what the extract holds: the
    real `CQ363350` carries `datetime(2025, 10, 14, 21, 38, 3, 408000)` with no
    tzinfo, and a mix of aware and naive values in one field is not comparable
    in Python at all.
    """
    return moment.replace(tzinfo=None, microsecond=moment.microsecond)


def _day(moment: datetime) -> datetime:
    """Midnight on the given day, as every real `orderDate` is written."""
    return moment.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)


#: Ship-via codes that are collected rather than delivered: counter pick-up,
#: will-call and backorder. An order on one of these is never DELIVERED, however
#: many signatures it carries -- the signature is the customer signing at the
#: counter.
PICKUP_SHIP_VIA: frozenset[str] = frozenset({"CPU", "WCL", "BO"})

_POD_MONTHS = (
    "JAN",
    "FEB",
    "MAR",
    "APR",
    "MAY",
    "JUN",
    "JUL",
    "AUG",
    "SEP",
    "OCT",
    "NOV",
    "DEC",
)


def _pod_signature(moment: datetime) -> str:
    """A proof-of-delivery timestamp in `salesInv`'s own spelling.

    `15:11:19 OCT 15 2025` -- a string, and deliberately not a BSON date. The
    real extract writes it this way, and a generated corpus that wrote a proper
    date would let a reader parse `podSigTd` with code that fails against
    production.
    """
    return (
        f"{moment.hour:02d}:{moment.minute:02d}:{moment.second:02d} "
        f"{_POD_MONTHS[moment.month - 1]} {moment.day} {moment.year}"
    )


# ---------------------------------------------------------------------------
# People and the identities derived from them
# ---------------------------------------------------------------------------


class Person:
    """A customer identity: one name, and the email derived from it.

    The ERP holds customer names in upper case -- every real `custName` does --
    so `display` is upper case and the email is not. Two derivation styles are
    used, `first.last` and `f.last`, chosen deterministically from the person's
    own index so that the same person always produces the same address.
    """

    __slots__ = ("email", "family", "given", "phone")

    def __init__(self, given: str, family: str, email: str, phone: str) -> None:
        self.given = given
        self.family = family
        self.email = email
        self.phone = phone

    @property
    def display(self) -> str:
        return f"{self.given} {self.family}".upper()

    @property
    def digits(self) -> str:
        return "".join(character for character in self.phone if character.isdigit())


class PersonDirectory:
    """Distinct people, drawn without replacement so variety is guaranteed.

    Sampling with replacement from 128 given names and 208 surnames would give
    roughly 30 duplicate full names at a thousand customers and, worse, a
    visibly lumpy surname distribution -- the "hundred variations of one
    surname" failure. Drawing pairs without replacement from the full cross
    product cannot repeat, and shuffling the product means the surnames arrive
    in no order at all.
    """

    def __init__(self, rng: random.Random, domains: Sequence[str]) -> None:
        pairs = [(given, family) for family in SURNAMES for given in GIVEN_NAMES]
        rng.shuffle(pairs)
        self._pairs = pairs
        self._domains = tuple(domains)
        self._next = 0
        self._emails: set[str] = set()
        self._phones = _Phones(rng, capacity=len(pairs))

    def take(self) -> Person:
        if self._next >= len(self._pairs):
            raise RuntimeError("person pool exhausted; widen GIVEN_NAMES or SURNAMES")
        given, family = self._pairs[self._next]
        index = self._next
        self._next += 1
        stem = (
            (f"{given}.{family}" if index % 3 else f"{given[0]}.{family}")
            .lower()
            .replace(" ", "")
            .replace("'", "")
        )
        domain = self._domains[index % len(self._domains)]
        candidate = f"{stem}@{domain}"
        attempt = 2
        # A numeric suffix appears only on a genuine collision -- `r.reynolds`
        # and `richard.reynolds` can both be taken -- which is what happens in a
        # real directory too.
        while candidate in self._emails:
            candidate = f"{stem}{attempt}@{domain}"
            attempt += 1
        self._emails.add(candidate)
        return Person(given, family, candidate, self._phones.take())


class _Phones:
    """Unique phone numbers, by construction rather than by retry."""

    def __init__(self, rng: random.Random, capacity: int) -> None:
        area_codes = (205, 212, 303, 312, 404, 469, 512, 602, 704, 720, 813, 919)
        pool: list[str] = []
        for area in area_codes:
            for subscriber in range(10_000):
                pool.append(f"{area}-555-{subscriber:04d}")
                if len(pool) >= capacity:
                    break
            if len(pool) >= capacity:
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


def _address(rng: random.Random) -> dict[str, str]:
    city, state, zip_prefix = rng.choice(PLACES)
    return {
        "address1": f"{rng.randint(100, 9899)} {rng.choice(STREET_NAMES)} "
        f"{rng.choice(STREET_TYPES)}",
        "city": city,
        "state": state,
        "zipCode": f"{zip_prefix}{rng.randint(10, 99)}",
        # Upper case, as every real `county` is -- "COLUMBIA", not "Columbia".
        "county": city,
    }


def _weighted(rng: random.Random, weights: Sequence[tuple[Any, int]]) -> Any:
    population = [value for value, _ in weights]
    return rng.choices(population, weights=[count for _, count in weights], k=1)[0]


# ---------------------------------------------------------------------------
# The real corpus: backup, and the identities it needs
# ---------------------------------------------------------------------------

#: A synthetic `_id` from any generator this repository has ever run. Used only
#: on the first backup, to tell the real documents from the generated ones
#: before a backup exists to answer the question. It covers `salesInv` and
#: `shipmentInfo`, whose generated documents carry no marker; generated
#: `lkpSearchProduct` and `customerOutboundCDM` documents are recognised by the
#: `__context` key the previous generator stamped on them.
#:
#: Deliberately no `MASTER:...` clause. The real party is `MASTER:900781` and
#: the previous generator minted `MASTER:900001` upward -- any pattern wide
#: enough to catch the second catches the first, which would classify the one
#: real CDM document as synthetic and delete it.
_SYNTHETIC_ID = re.compile(r"^(ACCT\d+\*SO\d+|PRD\d+|SKU\d+)")

_COLLECTION_FILES = {
    "orders": "salesInv.real.json",
    "customers": "customerOutboundCDM.real.json",
    "products": "lkpSearchProduct.real.json",
    "shipments": "shipmentInfo.real.json",
}


class RealCorpus:
    """The genuine Ferguson documents, loaded from the on-disk backup.

    Read from `backend/fixtures/real_ferguson_source/` rather than from Mongo,
    because this class is also the input to the rename: taking the originals
    from the database would mean a second run renamed the already-renamed
    documents, and the third run would rename those.
    """

    def __init__(self, documents: Mapping[str, list[dict[str, Any]]]) -> None:
        self.orders = documents["orders"]
        self.customers = documents["customers"]
        self.products = documents["products"]
        self.shipments = documents["shipments"]

    def identifiers(self, kind: str) -> set[Any]:
        return {document["_id"] for document in getattr(self, kind)}


async def _ensure_backup(database: Any, names: Mapping[str, str]) -> RealCorpus:
    """Write the real documents to disk once, then always read them from there.

    Refusing to overwrite is the safety property. The alternative -- refresh the
    backup on every run -- means one run after a mistake replaces the only copy
    of the only genuine data in the system with the mistake.
    """
    if BACKUP_DIRECTORY.is_dir() and all(
        (BACKUP_DIRECTORY / file_name).is_file() for file_name in _COLLECTION_FILES.values()
    ):
        restored = {
            kind: json_util.loads((BACKUP_DIRECTORY / file_name).read_text(encoding="utf-8"))
            for kind, file_name in _COLLECTION_FILES.items()
        }
        print(f"real corpus loaded from {BACKUP_DIRECTORY}")
        return RealCorpus(restored)

    BACKUP_DIRECTORY.mkdir(parents=True, exist_ok=True)
    documents: dict[str, list[dict[str, Any]]] = {}
    for kind, file_name in _COLLECTION_FILES.items():
        collection = database[names[kind]]
        found: list[dict[str, Any]] = []
        async for document in collection.find({}):
            identifier = str(document.get("_id"))
            if "__context" in document or "__seed" in document:
                continue
            if _SYNTHETIC_ID.match(identifier):
                continue
            found.append(document)
        found.sort(key=lambda document: str(document["_id"]))
        (BACKUP_DIRECTORY / file_name).write_text(
            json_util.dumps(found, indent=1), encoding="utf-8"
        )
        documents[kind] = found
    (BACKUP_DIRECTORY / "README.md").write_text(_BACKUP_README, encoding="utf-8")
    print(f"real corpus backed up to {BACKUP_DIRECTORY}")
    return RealCorpus(documents)


_BACKUP_README = """# Real Ferguson source documents

**The only genuine Ferguson data in this system.** Everything else in
`return_source` is generated by `backend/scripts/generate_seed_data.py`.

Written once, by that script, before it renamed any customer. It is never
overwritten: the script reads this directory when it exists and only writes it
when it does not, so the originals survive a mistake in a later run.

| File | Contents |
|---|---|
| `salesInv.real.json` | the real orders, `_id` shaped `BRANCH*ORDERNO` |
| `customerOutboundCDM.real.json` | the real master party |
| `lkpSearchProduct.real.json` | the real product master row |
| `shipmentInfo.real.json` | the real shipment rows |

MongoDB Extended JSON (`bson.json_util`), so the `{"$date": ...}` wrappers
survive a round trip. Read them with `json_util.loads`, never `json.loads`.

**These documents still carry the original customer identities.** The corpus in
Mongo does not: an operator decision replaced every customer name with an
individual person and derived the email from that name. To restore the
originals, load these documents back over the collections.
"""


# ---------------------------------------------------------------------------
# Renaming the real records
# ---------------------------------------------------------------------------


def _state_delivery(document: dict[str, Any]) -> None:
    """Give a real order the delivery fields the extract does not carry.

    `orderCode`, `trilogieFile` and `fleetwiseStatus` are absent from every real
    `salesInv` document in `fixtures/real_ferguson_source/` -- checked, not
    assumed -- so a corpus that mixed real and generated orders had a hundred
    documents the DELIVERED rule could not be evaluated against at all. They
    read as "not delivered" either way, but for the wrong reason: the field was
    missing rather than the condition unmet, and the two are different answers
    to "why is this order not delivered".

    **The real fixture is not edited.** It is the untouched original, and its
    README says so. The fields are stated here, as the document is inserted, so
    the corpus is uniform and the originals stay original.

    No order is made delivered here. The delivered cohort is `delivered_orders`
    from the config, drawn from generated orders, and a real order quietly
    joining it would make that count a lie. A real order that ships gets a route
    still in progress; a pick-up gets no route at all, and keeps whatever
    counter signature it came with.
    """
    event = document.get("salesHdrEventData") or {}
    shipping = ((document.get("salesHdr") or {}).get("salesHdrData") or {}).get("shipping") or {}
    invoiced = bool(event.get("invoiced")) or str(event.get("orderStatus", "")).startswith(
        "INVOICE"
    )
    event["orderCode"] = "IO" if invoiced else "OO"
    event["trilogieFile"] = "ORDER"
    if shipping.get("shipViaCode") not in PICKUP_SHIP_VIA:
        shipping["fleetwiseStatus"] = "InRoute"
    else:
        shipping.pop("fleetwiseStatus", None)


def _rename_real_orders(
    orders: Sequence[dict[str, Any]], directory: PersonDirectory
) -> tuple[list[dict[str, Any]], dict[tuple[str, str], Person], int]:
    """Give every real order's customer a person's name and a derived email.

    Keyed on `(accountId, custId)`, which is `customer`'s natural key: the same
    ERP customer number in two branches is two customers, and one person spread
    across both would merge them in the graph.

    Everything else is preserved. The name is replaced where the name is stated
    -- header, ship-to, the embedded contact rows, the placed-by initials and
    the cardholder -- and nowhere else, so `shipToKey` and every id keep the
    original string.
    """
    people: dict[tuple[str, str], Person] = {}
    renamed: list[dict[str, Any]] = []
    emails_added = 0
    for original in orders:
        document = json_util.loads(json_util.dumps(original))
        _state_delivery(document)
        header = (document.get("salesHdr") or {}).get("salesHdrData") or {}
        account = str((document.get("salesHdrEventData") or {}).get("accountId") or "")
        customer_id = str(header.get("custId") or "")
        key = (account, customer_id)
        person = people.setdefault(key, directory.take())
        previous = {
            value
            for value in (header.get("custName"), _ship_to(document).get("shipToName"))
            if isinstance(value, str) and value
        }

        if "custName" in header:
            header["custName"] = person.display
        ship_to = _ship_to(document)
        if "shipToName" in ship_to:
            ship_to["shipToName"] = person.display
        sales_person = header.get("salesPerson")
        if isinstance(sales_person, dict) and "placedByName" in sales_person:
            sales_person["placedByName"] = person.family.upper()

        for row in (document.get("customer") or {}).get("address") or []:
            if not isinstance(row, dict):
                continue
            if isinstance(row.get("contactFirstName"), str):
                row["contactFirstName"] = person.given.upper()
            if isinstance(row.get("contactLastName"), str):
                row["contactLastName"] = person.family.upper()
            if isinstance(row.get("email"), str):
                row["email"] = person.email

        # 97 of the 101 real orders already carry an email on a contact row and
        # the rewrite above has just replaced it. The remaining four carry none
        # at all, and the operator's decision is that every customer has one --
        # so an email is written onto a row that had no contact on it. Only for
        # those four: an order that already stated an email needs no addition,
        # and adding a second one would invent a contact the source never had.
        if not _states_an_email(document) and _add_email_row(document, person):
            emails_added += 1

        payments = header.get("payments")
        if isinstance(payments, dict):
            _rename_cardholder(payments, person)
        _replace_emails(document, person.email)
        for name in previous:
            _replace_exact(document, name, person.display)
        renamed.append(document)
    return renamed, people, emails_added


def _ship_to(document: Mapping[str, Any]) -> dict[str, Any]:
    node: Any = document
    for part in ("salesHdr", "salesHdrData", "shipping", "shipTo", "address"):
        node = node.get(part) if isinstance(node, dict) else None
    return node if isinstance(node, dict) else {}


def _states_an_email(document: Mapping[str, Any]) -> bool:
    rows = (document.get("customer") or {}).get("address")
    if not isinstance(rows, list):
        return False
    return any(isinstance(row, dict) and row.get("email") for row in rows)


def _add_email_row(document: dict[str, Any], person: Person) -> bool:
    """Write the email onto a row that carries no contact of its own.

    `contact_value` is `COALESCE(email, phone_number)`, so a row with neither
    projects on a null natural key and is lost. Choosing such a row means the
    email is gained without shadowing the phone contact on a sibling row. If
    every row already carries a phone, none is chosen and the order simply has
    no email contact -- which is what its source says.
    """
    rows = (document.get("customer") or {}).get("address")
    if not isinstance(rows, list):
        return False
    for row in rows:
        if not isinstance(row, dict):
            continue
        if not row.get("email") and not row.get("phoneNumber"):
            row["email"] = person.email
            return True
    return False


def _rename_cardholder(payments: dict[str, Any], person: Person) -> None:
    """The cardholder is a person's real name; it is a name, so it is replaced."""
    names = payments.get("ccName")
    if isinstance(names, list):
        payments["ccName"] = [
            [person.given.upper(), person.family.upper()] if isinstance(entry, list) else entry
            for entry in names
        ]


_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _replace_emails(node: Any, email: str) -> None:
    """Every address-shaped string becomes the derived one, wherever it hides.

    Real orders carry an email inside the card-on-file tuple as well as on the
    contact rows. Leaving one behind would leave a real business address in a
    corpus whose whole point is that it holds none.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, str) and _EMAIL.match(value):
                node[key] = email.upper() if value.isupper() else email
            else:
                _replace_emails(value, email)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            if isinstance(value, str) and _EMAIL.match(value):
                node[index] = email.upper() if value.isupper() else email
            else:
                _replace_emails(value, email)


def _replace_exact(node: Any, previous: str, replacement: str) -> None:
    """Replace the customer's name where it appears verbatim, and only there.

    Exact whole-string matching, never substring: `shipToKey` is
    `CHARLOTTE*CQ363350-0000` and an id must survive a rename untouched.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if value == previous:
                node[key] = replacement
            else:
                _replace_exact(value, previous, replacement)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            if value == previous:
                node[index] = replacement
            else:
                _replace_exact(value, previous, replacement)


def _rename_real_customers(
    customers: Sequence[dict[str, Any]],
    people: Mapping[tuple[str, str], Person],
    directory: PersonDirectory,
) -> list[dict[str, Any]]:
    """Rename the real CDM party, reusing the person its accounts already have.

    `party[].partyMainCusts[].mainCusts` is `BRANCH*CUSTID`, and `CUSTID` is
    what the order writers copy into `salesInv.custId`. If one of those accounts
    appears on a real order, the party is that order's customer and must carry
    the same person -- otherwise a name search and an account lookup disagree
    about who this is.
    """
    renamed: list[dict[str, Any]] = []
    for original in customers:
        document = json_util.loads(json_util.dumps(original))
        parties = document.get("party")
        bridges: list[str] = []
        if isinstance(parties, list):
            for party in parties:
                for bridge in (party or {}).get("partyMainCusts") or []:
                    value = (bridge or {}).get("mainCusts")
                    if isinstance(value, str):
                        bridges.append(value)
        person: Person | None = None
        for bridge in bridges:
            branch, _, customer_id = bridge.partition("*")
            person = people.get((branch, customer_id))
            if person is not None:
                break
        if person is None:
            person = directory.take()

        previous = {value for value in _party_names(document) if isinstance(value, str) and value}
        _rename_party(document, person)
        for name in previous:
            _replace_exact(document, name, person.display)
        _replace_emails(document, person.email)
        renamed.append(document)
    return renamed


def _party_names(document: Mapping[str, Any]) -> list[Any]:
    names: list[Any] = []
    for party in document.get("customerParties") or []:
        names.append((party or {}).get("partyName"))
    for party in document.get("party") or []:
        names.append((party or {}).get("partyName"))
        names.append((party or {}).get("organizationName"))
    return names


def _rename_party(document: dict[str, Any], person: Person) -> None:
    for party in document.get("customerParties") or []:
        if isinstance(party, dict) and "partyName" in party:
            party["partyName"] = person.display
    for party in document.get("party") or []:
        if not isinstance(party, dict):
            continue
        if "partyName" in party:
            party["partyName"] = person.display
        if "organizationName" in party:
            party["organizationName"] = person.display
        for bridge in party.get("partyMainCusts") or []:
            if isinstance(bridge, dict) and "mainCustsName" in bridge:
                bridge["mainCustsName"] = person.display
        for contact in party.get("customerContactPoints") or []:
            if not isinstance(contact, dict):
                continue
            if "personFirstName" in contact:
                contact["personFirstName"] = person.given.upper()
            if "personLastName" in contact:
                contact["personLastName"] = person.family.upper()
            # The field exists and is null on every real contact point. Filling
            # it is the email half of the operator's decision; adding a contact
            # point would be a structural change the brief does not ask for.
            if "emailAddress" in contact and not contact["emailAddress"]:
                contact["emailAddress"] = person.email


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------


def _mine_real_products(orders: Iterable[Mapping[str, Any]]) -> list[GeneratedProduct]:
    """One catalogue entry per `masterProductId` any real order line names.

    The id, the SKU and the description are **the values the order line already
    carries** -- `3180140` / `Q1685` / `16X25 SILV FLEX AIR DUCT R8.0` -- not
    invented ones. `line_references_product` matches `master_product_id` against
    `product.product_id`, so building these is what lets the real orders resolve
    a product at all; without them the graph answers "no product" for 482 real
    lines, which is finding D5.

    Attributes the line does not state -- vendor, department, finish -- are
    **left absent**, because guessing a vendor for a real catalogue number would
    put a claim in the corpus that no source made. `webDisplayName` is derived
    from the description by expansion, which restates it rather than adding to
    it.
    """
    products: dict[str, GeneratedProduct] = {}
    for order in orders:
        for line in order.get("salesLines") or []:
            data = (line or {}).get("lineData") or {}
            master = data.get("masterProductId")
            if master is None:
                continue
            key = str(master)
            if key in products:
                continue
            description = str(data.get("productDesc") or "").strip()
            if not description:
                continue
            sku = str(data.get("altCode1") or key)
            products[key] = GeneratedProduct(
                product_id=key,
                sku=sku,
                description=description,
                long_description=expand(description),
                web_display_name=expand(description),
                vendor="",
                department="",
                unit_of_measure="EA",
                unit_of_measure_description="EACH",
                brand_type="",
                upc_code="",
                list_price=float(data.get("listPrice") or 0.0),
                colour_finish=None,
                category_key="from_order_line",
                real=True,
            )
    return list(products.values())


def _clip(description: str, limit: int) -> str:
    """Trim on a word boundary. No real `productDesc` ends mid-token.

    The longest real description is 34 characters, and the ERP abbreviates
    rather than truncates -- `16X25 SILV FLEX AIR DUCT R8.0`, not
    `16X25 SILVER FLEXIBLE AIR DU`. Cutting at `limit` characters would produce
    the second, which is a tell that no ERP wrote it.
    """
    if len(description) <= limit:
        return description
    clipped = description[:limit]
    head, separator, _ = clipped.rpartition(" ")
    return head if separator else clipped


def _sku(rng: random.Random, category: Category, ordinal: int) -> str:
    """A vendor product code in one of the four shapes the real 482 exhibit."""
    letters = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    prefix = "".join(rng.choice(letters) for _ in range(2))
    shape = SKU_SHAPES[ordinal % len(SKU_SHAPES)]
    if shape == "letter_digits":
        return f"{rng.choice(letters)}{rng.randint(1000, 999999)}"
    if shape == "alpha_block":
        return prefix + "".join(rng.choice(letters) for _ in range(rng.randint(3, 9)))
    if shape == "prefix_digits":
        return f"{prefix}{rng.choice(letters)}{rng.randint(1000, 9999)}"
    return f"{rng.choice(letters)}{rng.randint(10**8, 10**10 - 1)}"


def _generate_products(
    count: int,
    taken: set[str],
    rng: random.Random,
) -> list[GeneratedProduct]:
    """Catalogue entries in the mined idiom: size, spec, abbreviated noun.

    A finish is appended to the description and written to `eco.colorFinish`
    only for the categories that have one. `_id` is drawn from a numeric band
    disjoint from every real `masterProductId`, and asserted against `taken`, so
    a generated product can never shadow a real one.
    """
    # Categories that carry a finish appear twice in the cycle. **A deliberate
    # over-representation, and the only one in this file.** Colour/finish is on
    # Ferguson's return-setup list and had exactly one product behind it; the
    # 482 real catalogue numbers cannot be given one, because their order lines
    # do not state it. Weighting trim and fixtures up puts roughly half the
    # generated catalogue behind the attribute instead of a third, at the cost
    # of a catalogue mix that leans further towards trim than a real branch's.
    cycle = [category for category in CATEGORIES for _ in range(2 if category.finishes else 1)]
    products: list[GeneratedProduct] = []
    used_skus: set[str] = set()
    used_descriptions: set[str] = set()
    ordinal = 0
    next_id = 4_000_000
    while len(products) < count:
        category = cycle[ordinal % len(cycle)]
        ordinal += 1
        while str(next_id) in taken:
            next_id += 1
        product_id = str(next_id)
        next_id += 1

        sku = _sku(rng, category, ordinal)
        while sku in used_skus or sku in taken:
            sku = _sku(rng, category, ordinal + len(used_skus))
        used_skus.add(sku)

        # Redrawn until the description is one no other generated product
        # already carries. Two catalogue numbers reading `2 IPS DEEP ESC CP`
        # from the same vendor make a product search return two rows an
        # associate cannot tell apart, which is a defect in the corpus rather
        # than in the search. Bounded: a category whose cross product is
        # genuinely exhausted accepts the repeat rather than looping.
        size = spec = ""
        finish_name: str | None = None
        finish_token = ""
        description = ""
        for _ in range(24):
            size = rng.choice(category.sizes)
            spec = rng.choice(category.specs)
            if category.finishes:
                finish_name, finish_token = rng.choice(category.finishes)
            description = " ".join(
                token for token in (size, spec, category.noun, finish_token) if token
            )
            if description not in used_descriptions:
                break
        used_descriptions.add(description)
        long_description = " ".join(
            token
            for token in (
                size,
                expand(spec),
                category.long_noun if category.noun else "",
                finish_name or "",
            )
            if token
        )
        vendor = rng.choice(category.vendors)
        low, high = category.price_range
        products.append(
            GeneratedProduct(
                product_id=product_id,
                sku=sku,
                description=_clip(description, 40),
                long_description=long_description,
                web_display_name=f"{long_description} by {vendor.title()}",
                vendor=vendor,
                department=category.department,
                unit_of_measure=category.unit_of_measure,
                unit_of_measure_description=UOM_DESCRIPTIONS[category.unit_of_measure],
                brand_type="Own Brand" if vendor == "PROSELECT" else "National Brand",
                upc_code=f"{rng.randint(10**11, 10**12 - 1)}",
                list_price=round(rng.uniform(low, high), 2),
                colour_finish=finish_name,
                category_key=category.key,
            )
        )
    return products


def _product_document(product: GeneratedProduct, generated_at: datetime) -> dict[str, Any]:
    """A `lkpSearchProduct` document shaped like the real one.

    `eco.colorFinish` is the path the real document carries its finish on, and
    it is written only where a finish exists. **The active schema declares no
    colour field on `product`**, so the value reaches Mongo but not the graph;
    it is searchable today only through the description and the display name,
    both of which state it. Declaring a `color_finish` field is a schema change
    and out of this script's scope.
    """
    document: dict[str, Any] = {
        "_id": product.product_id,
        "eventMeta": {
            "insertMongoTs": _moment(generated_at),
            "collectionVersion": "1",
            "type": "ADD",
            "machineName": "STEP",
            "insertTS": _moment(generated_at),
            "updateMongoTs": _moment(generated_at),
            "lastUpdateTS": _moment(generated_at),
            "deleteMongoTs": None,
            "expireDate": None,
            "id": int(product.product_id) if product.product_id.isdigit() else 0,
            "account": "MASTER",
        },
        "mpData": {
            "integLocProductId": int(product.product_id) if product.product_id.isdigit() else 0,
            "ecatAnsiUom": product.unit_of_measure,
            "webDisplayName": product.web_display_name,
            "ecatQtyMultiple": 1,
        },
        "masterProduct": {
            "uom": product.unit_of_measure,
            "uomDesc": product.unit_of_measure_description,
            "vendorProdCode": product.sku,
            "productDesc": product.description,
            "prodLongDesc": product.long_description,
            "altCodes": {"alt1Code1": product.sku},
            "perQty": 1,
            "sellMultiplier": 1,
        },
        "pricingData": {"listPrice": product.list_price, "discPct": 0, "localPriceFlag": "N"},
        "eco": {},
        "fld": {"baseModelNumber": product.sku, "shippingClassification": "Standard"},
        "fopt": {},
        "mpNotes": None,
        "ecomm": None,
    }
    master = document["masterProduct"]
    if product.upc_code:
        master["upcCode"] = product.upc_code
    if product.vendor:
        master["vendorName"] = product.vendor
    if product.department:
        master["deptCodeDesc"] = product.department
    if product.brand_type:
        master["brandType"] = product.brand_type
    if product.colour_finish:
        document["eco"]["colorFinish"] = [product.colour_finish]
    document["fopt"]["mstrProdId"] = f"{product.product_id}*{19635}"
    if not product.real:
        document["__seed"] = True
    return document


def _hydrate_real_product(product: GeneratedProduct, generated_at: datetime) -> dict[str, Any]:
    """Fill only the paths the schema declares and the order line can answer.

    A real catalogue number gets its own SKU and description, an expanded
    display name, and nothing else. No vendor, no department, no finish: the
    order line does not state them and a plausible guess against a real
    identifier is the fabrication this whole exercise is about.
    """
    hydrated = _product_document(product, generated_at)
    hydrated["masterProduct"].pop("upcCode", None)
    # Written by this script, so it keeps the marker: only the *values* are
    # real, and a document with no marker is one `_ensure_backup` would take
    # for a genuine source row if the backup directory were ever lost.
    hydrated["__seed"] = "DERIVED_FROM_ORDER_LINE"
    hydrated["provenance"] = {
        "derivedFrom": "salesInv order line",
        "statedFields": ["_id", "vendorProdCode", "productDesc", "listPrice"],
    }
    if not product.list_price:
        hydrated["pricingData"].pop("listPrice", None)
    return hydrated


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------


class Account:
    """One `BRANCH*CUSTID` bridge, and the party it belongs to."""

    __slots__ = ("address", "b2b", "branch", "customer_id", "party_id", "person")

    def __init__(
        self,
        branch: str,
        customer_id: str,
        party_id: str,
        person: Person,
        address: dict[str, str],
        b2b: str,
    ) -> None:
        self.branch = branch
        self.customer_id = customer_id
        self.party_id = party_id
        self.person = person
        self.address = address
        self.b2b = b2b

    @property
    def bridge(self) -> str:
        return f"{self.branch}*{self.customer_id}"


def _generate_customers(
    count: int,
    directory: PersonDirectory,
    reserved_customer_ids: set[str],
    reserved_party_ids: set[str],
    rng: random.Random,
    generated_at: datetime,
) -> tuple[list[dict[str, Any]], list[Account]]:
    """`customerOutboundCDM` documents shaped like the real master party.

    One party, one to three `partyMainCusts` bridges. **The bridge is the real
    one** -- `party[].partyMainCusts[].mainCusts` holding `BRANCH*CUSTID` -- not
    the `party[].custAccts[].additionalCustomerInfo[]` path the previous
    generator built. No real CDM document has a `custAccts` array at any level,
    and manufacturing one cleared a validation error dishonestly: it produced
    exactly the shape the declaration asserted and the source lacks. See D41 and
    D48 in the execution state.

    Orders are drawn from the accounts returned here, so `salesInv.custId` and
    the CDM bridge agree by construction rather than by coincidence.
    """
    documents: list[dict[str, Any]] = []
    accounts: list[Account] = []
    next_customer = 600_001
    next_party = 700_001
    for _ in range(count):
        person = directory.take()
        while str(next_party) in reserved_party_ids:
            next_party += 1
        party_id = str(next_party)
        next_party += 1
        address = _address(rng)
        b2b = "Y" if rng.random() < 0.72 else "N"

        bridges: list[Account] = []
        for _ in range(rng.choices((1, 2, 3), weights=(68, 24, 8), k=1)[0]):
            while str(next_customer) in reserved_customer_ids:
                next_customer += 1
            customer_id = str(next_customer)
            next_customer += 1
            branch = _weighted(rng, BRANCH_WEIGHTS)
            bridges.append(Account(branch, customer_id, party_id, person, address, b2b))
        accounts.extend(bridges)

        area, _, subscriber = person.phone.partition("-")
        documents.append(
            {
                "_id": f"MASTER:{party_id}",
                "partyId": party_id,
                "lastUpdateDate": _moment(generated_at),
                "num": None,
                "type": "PARTY",
                "expireDate": None,
                "sourceSystem": "Trilogie",
                "notSearchable": "N",
                "status": None,
                "customerParties": [
                    {
                        "partyName": person.display,
                        "partyNumber": party_id,
                        "personFirstName": person.given.upper(),
                        "personLastName": person.family.upper(),
                        "lastUpdateDate": _moment(generated_at),
                    }
                ],
                "party": [
                    {
                        "partyNumber": party_id,
                        "partyName": person.display,
                        "organizationName": person.display,
                        "nationalAccountIndicator": "N",
                        "b2bCustomer": b2b,
                        "partySites": [
                            {
                                "partySiteLocations": [
                                    {
                                        "country": "US",
                                        "address1": address["address1"],
                                        "city": address["city"],
                                        "state": address["state"],
                                        "postalCode": address["zipCode"],
                                        "county": address["county"],
                                        "lastUpdateDate": _moment(generated_at),
                                    }
                                ],
                                "partySiteUses": [{"siteUseType": "BILL_TO"}],
                                "lastUpdateDate": _moment(generated_at),
                            }
                        ],
                        "customerContactPoints": [
                            {
                                "contactPointId": f"MASTER*{party_id}-0000*0",
                                "contactPointType": "PHONE",
                                "emailAddress": None,
                                "phoneAreaCode": area,
                                "phoneNumber": subscriber.replace("-", ""),
                                "searchPhoneNumber": f"1{person.digits}",
                                "phoneLineType": "GEN",
                                "personFirstName": person.given.upper(),
                                "personLastName": person.family.upper(),
                                "lastUpdateDate": _moment(generated_at),
                            },
                            {
                                "contactPointId": f"MASTER*{party_id}-0000*1",
                                "contactPointType": "EMAIL",
                                "emailAddress": person.email,
                                "personFirstName": person.given.upper(),
                                "personLastName": person.family.upper(),
                                "lastUpdateDate": _moment(generated_at),
                            },
                        ],
                        "partyMainCusts": [
                            {
                                "mainCusts": account.bridge,
                                "mainCustsName": person.display,
                                "mainCustJobs": [],
                            }
                            for account in bridges
                        ],
                        "additionalMcustomerInfo": {
                            "mcustId": party_id,
                            "mcustAlpha": "**B2B**" if b2b == "Y" else "**RES**",
                            "mcustAddrType": "DOM",
                            "mcustPhone": person.digits,
                            "mcustJobFlag": "N",
                            "searchMcustPhone": f"1{person.digits}",
                            "lastUpdateDate": _moment(generated_at),
                        },
                        "lastUpdateDate": _moment(generated_at),
                    }
                ],
                "__seed": True,
            }
        )
    return documents, accounts


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------


class OrderNumbers:
    """Globally unique order numbers, in the real two-letter series.

    An order number is unique within an account and not globally in the real
    ERP -- `sales_order.sales_order_number` says so -- but the copilot's search
    takes a bare number, and a synthetic order sharing `CQ363350` would turn the
    one order this environment is manually tested against into two candidates.
    So the generated band is disjoint from every real number by construction and
    asserted against the real set.
    """

    def __init__(self, reserved: Iterable[str]) -> None:
        self._reserved = set(reserved)
        self._next = 800_000

    def take(self, rng: random.Random) -> str:
        while True:
            candidate = f"{rng.choice(ORDER_PREFIXES)}{self._next}"
            self._next += 1
            if candidate not in self._reserved:
                self._reserved.add(candidate)
                return candidate


def _delivered_indices(count: int, delivered: int, rng: random.Random) -> frozenset[int]:
    """Which of the generated orders end up DELIVERED.

    Drawn from the same seeded `rng` as everything else, so the corpus stays
    reproducible from `generation.yaml` alone. A target above the corpus size
    is refused rather than clamped: silently generating 900 of the 1,000
    delivered orders a test asked for is the kind of shortfall that surfaces
    three failures later as "the window rule is flaky".
    """
    if delivered > count:
        raise SystemExit(
            f"delivered_orders ({delivered}) exceeds the {count} orders being generated"
        )
    if delivered <= 0:
        return frozenset()
    return frozenset(rng.sample(range(count), delivered))


def _generate_orders(
    count: int,
    accounts: Sequence[Account],
    products: Sequence[GeneratedProduct],
    numbers: OrderNumbers,
    earliest: datetime,
    span_days: int,
    shipped_fraction: float,
    rng: random.Random,
    delivered_orders: int = 0,
) -> list[dict[str, Any]]:
    """`salesInv` documents in the real header/lines shape."""
    documents: list[dict[str, Any]] = []
    delivered_set = _delivered_indices(count, delivered_orders, rng)
    for index in range(count):
        account = rng.choice(accounts)
        person = account.person
        branch = account.branch
        order_number = numbers.take(rng)
        ordered_at = earliest + timedelta(
            days=rng.randint(0, span_days), hours=rng.randint(7, 18), minutes=rng.randint(0, 59)
        )
        warehouse = _weighted(rng, WAREHOUSE_WEIGHTS)
        ship_via_code, ship_via_desc = _weighted(
            rng, tuple(((code, description), weight) for code, description, weight in SHIP_VIA)
        )
        line_count = rng.choice(LINES_PER_ORDER)
        lines, subtotal, cost = _order_lines(
            line_count, branch, order_number, warehouse, products, shipped_fraction, ordered_at, rng
        )
        tax = round(subtotal * rng.choice((0.0, 0.06, 0.0725, 0.0825)), 2)
        address = account.address
        status = _weighted(rng, ORDER_STATUS_WEIGHTS)
        sales_type = _weighted(rng, SALES_TYPE_WEIGHTS)
        associate = rng.choice(ASSOCIATES)
        # Delivery, by the five conditions `salesInv` actually decides it on:
        # an invoice-file order, an invoice order code, a ship-via that is
        # driven rather than collected, a completed FleetWise route and a POD
        # signature. Generating the last two without the first three is how a
        # corpus ends up claiming a counter pick-up was delivered.
        if index in delivered_set:
            # Forced, and forced completely: an order in this set must satisfy
            # every one of the five conditions, so its status and its ship-via
            # are overwritten rather than hoped for. Both are still drawn from
            # the real vocabulary -- the corpus gains no code or status it did
            # not already have, only a different mix of them.
            status = _weighted(rng, INVOICED_STATUS_WEIGHTS)
            ship_via_code, ship_via_desc = _weighted(rng, DELIVERY_SHIP_VIA_WEIGHTS)
        invoiced = status.startswith("INVOICE")
        deliverable = ship_via_code not in PICKUP_SHIP_VIA
        # Exact, in both directions. An order outside the set that happened to
        # draw an invoice status and a driven ship-via would satisfy the rule
        # too, and `delivered_orders: 1000` would quietly mean "about 1,600".
        # It gets a route that has not completed instead, which is an ordinary
        # state and not a contrivance.
        delivered_at = (
            ordered_at + timedelta(days=rng.randint(1, 4), hours=rng.randint(0, 9))
            if index in delivered_set
            else None
        )
        documents.append(
            {
                "_id": f"{branch}*{order_number}",
                "salesHdrEventMeta": {
                    "srcSyncId": int(ordered_at.timestamp() * 1000),
                    "srcSyncTs": _moment(ordered_at),
                    "rcvdTs": _moment(ordered_at),
                    "insertTs": _moment(ordered_at),
                    "lastUpdateTs": _moment(ordered_at + timedelta(minutes=7)),
                    "updatedBy": "order-cashsale-writer-v4",
                },
                "salesHdrEventData": {
                    "accountId": branch,
                    "orderId": order_number,
                    "sellWhseId": warehouse,
                    "shipFromWhseId": warehouse,
                    # The `sales_order` entity is gated on this discriminator. A
                    # corpus that omits it produces zero orders in the graph and
                    # no error anywhere.
                    "docType": "headerLines",
                    "salesType": sales_type,
                    "salesCode": "CS",
                    "numOfLines": str(len(lines)),
                    "custType": "MAIN",
                    "srcSysCode": "SOE",
                    "srcErp": "Trilogie",
                    "openFlag": status == "CALLCSR",
                    "invoiced": status.startswith("INVOICE"),
                    "orderStatus": status,
                    "shipViaCode": ship_via_code,
                    # `IO` is an open invoice, `OO` an order still working. The
                    # delivery rule tests for an invoice code, so an order that
                    # was never invoiced cannot read as delivered whatever its
                    # route says.
                    "orderCode": "IO" if invoiced else "OO",
                    #: The Trilogie file this document came out of.
                    "trilogieFile": "ORDER",
                    "totalPages": "1",
                    "pageNumber": "1",
                },
                "salesHdr": {
                    "salesHdrMeta": {
                        "insertTs": _moment(ordered_at),
                        "updateTs": _moment(ordered_at + timedelta(minutes=7)),
                        "lastUpdatedBy": "order-cashsale-writer-v4",
                    },
                    "salesHdrData": {
                        "orderCust": account.customer_id,
                        "custId": account.customer_id,
                        "custName": person.display,
                        "custPONumber": rng.choice(PO_NUMBERS),
                        "mcustId": account.party_id,
                        "jobName": rng.choice(PO_NUMBERS),
                        "b2bCustFlag": account.b2b == "Y",
                        "transactionCurrencyCode": "USD",
                        "transactionCurrencyName": "US Dollars",
                        "transactionType": "Cash Sale" if sales_type == "CASH" else "Invoice",
                        "entryDate": _day(ordered_at),
                        "orderDate": _day(ordered_at),
                        "invoiceDate": _day(ordered_at),
                        "arAgingDate": _day(ordered_at),
                        "priceSubtltAmt": subtotal,
                        "costSubtltAmt": cost,
                        "freightAmt": 0,
                        "handlingAmt": 0,
                        "orderTotalAmt": round(subtotal + tax, 2),
                        "refundAmt": 0,
                        "creditHoldFlag": False,
                        "submittedFlag": True,
                        "deleteFlag": False,
                        "shipping": {
                            "reqrdShipDate": _day(ordered_at),
                            "shipViaCode": ship_via_code,
                            "shipViaDesc": ship_via_desc,
                            "shipCompleteFlag": False,
                            # DispatchTrack's verdict on the route, and the
                            # signature it captured. Absent on a pick-up: there
                            # is no route to complete.
                            **(
                                {
                                    "fleetwiseStatus": (
                                        "Completed" if delivered_at is not None else "InRoute"
                                    )
                                }
                                if deliverable
                                else {}
                            ),
                            **(
                                {"podSigTd": _pod_signature(delivered_at)}
                                if delivered_at is not None
                                else {}
                            ),
                            "commitDate": _day(ordered_at),
                            "shipDate": _day(ordered_at + timedelta(days=rng.randint(0, 3))),
                            "shipTo": {
                                "shipToSuffix": "0000",
                                "address": {
                                    "shipToKey": f"{branch}*{order_number}-0000",
                                    "shipToName": person.display,
                                    "address1": address["address1"],
                                    "address2": f"{address['city']}, {address['state']} "
                                    f"{address['zipCode']}",
                                    "city": address["city"],
                                    "state": address["state"],
                                    "zipCode": address["zipCode"],
                                    "county": address["county"],
                                    "countryCode": "US",
                                    "shipToPhone": person.phone,
                                },
                            },
                        },
                        "terms": {
                            "termsCode": "COD" if sales_type == "CASH" else "N30",
                            "termsDesc": "CASH ON DEMAND" if sales_type == "CASH" else "NET 30",
                            "termsDiscPct": 0,
                            "termsDate": _day(ordered_at),
                        },
                        "tax": {
                            "taxAmt": tax,
                            "taxableAmt": subtotal,
                            "exemptAmt": 0,
                            "combinedTaxRate": 0,
                        },
                        "salesPerson": {
                            "empName": associate.title(),
                            "salesmanName": associate,
                            "placedByName": person.family.upper(),
                            "writerInitials": "".join(word[0] for word in associate.split()),
                        },
                        "linesInfo": {
                            "rLineNumber": [str(index + 1) for index in range(len(lines))]
                        },
                    },
                },
                # The real `customer.address` array repeats one postal address
                # per contact. Two rows here: the first carries the email and
                # the phone, the second the phone alone. `contact_value` is
                # COALESCE(email, phone_number), so that is one contact point
                # keyed on the address and one keyed on the number -- both of
                # the identifiers the clarification policy ranks highest, and
                # both resolving to the same customer.
                "customer": {
                    "address": [
                        {
                            "contactFirstName": person.given.upper(),
                            "contactLastName": person.family.upper(),
                            "address1": address["address1"],
                            "city": address["city"],
                            "state": address["state"],
                            "postalCode": address["zipCode"],
                            "county": address["county"],
                            "country": "US",
                            "phoneNumber": person.digits,
                            "email": person.email,
                        },
                        {
                            "contactFirstName": person.given.upper(),
                            "contactLastName": person.family.upper(),
                            "address1": address["address1"],
                            "city": address["city"],
                            "state": address["state"],
                            "postalCode": address["zipCode"],
                            "county": address["county"],
                            "country": "US",
                            "phoneNumber": person.digits,
                        },
                    ]
                },
                "salesInvHierarchy": {},
                "salesLines": lines,
                "__seed": True,
            }
        )
    return documents


def _order_lines(
    line_count: int,
    branch: str,
    order_number: str,
    warehouse: str,
    products: Sequence[GeneratedProduct],
    shipped_fraction: float,
    ordered_at: datetime,
    rng: random.Random,
) -> tuple[list[dict[str, Any]], float, float]:
    lines: list[dict[str, Any]] = []
    subtotal = 0.0
    cost = 0.0
    for index in range(line_count):
        product = rng.choice(products)
        quantity = rng.choices(
            (1, 2, 3, 4, 5, 6, 10, 12, 24), weights=(38, 18, 11, 7, 6, 4, 8, 4, 4)
        )[0]
        unit_price = round(
            (product.list_price or rng.uniform(4.5, 240.0)) * rng.uniform(0.42, 0.96), 3
        )
        unit_cost = round(unit_price * rng.uniform(0.55, 0.86), 2)
        shipped = quantity if rng.random() < shipped_fraction else 0
        line_net = round(unit_price * quantity, 2)
        subtotal = round(subtotal + line_net, 2)
        cost = round(cost + unit_cost * quantity, 2)
        lines.append(
            {
                "salesLnsEventData": {
                    "account": branch,
                    "orderId": order_number,
                    "lineNumber": str(index + 1),
                    "lineType": _weighted(rng, LINE_TYPE_WEIGHTS),
                },
                "lineMeta": {
                    "insertTs": _moment(ordered_at),
                    "updateTs": _moment(ordered_at + timedelta(minutes=7)),
                    "lastUpdatedBy": "order-cashsale-writer-v4",
                },
                "lineData": {
                    "productId": f"{product.product_id}*{warehouse}",
                    "altCode1": product.sku,
                    "masterProductId": product.product_id,
                    "productDesc": product.description,
                    "perQty": 1,
                    "orderQty": quantity,
                    "boQty": max(0, quantity - shipped),
                    "shipQty": shipped,
                    "prodAvailQty": rng.randint(0, 240),
                    "netPrice": unit_price,
                    "listPrice": product.list_price or unit_price,
                    "unitCostAmt": unit_cost,
                    "invenWhse": warehouse,
                    "lineNetAmt": line_net,
                    "lineTaxAmt": 0,
                    "commitQty": shipped,
                    "affectInvenFlag": False,
                },
            }
        )
    return lines, subtotal, cost


# ---------------------------------------------------------------------------
# Shipments and warehouses
# ---------------------------------------------------------------------------


def _generate_shipments(
    orders: Sequence[Mapping[str, Any]], rng: random.Random
) -> list[dict[str, Any]]:
    """One `shipmentInfo` document per generated order that shipped something.

    An order with no shipped line gets no shipment, deliberately: a shipment for
    every order would make "the graph holds no shipment for this number" a state
    the corpus can never produce.

    `trilOrdNum` carries the **bare order number**, because
    `order_shipped_as` joins `shipment.sales_order_number` to
    `sales_order.sales_order_number`. The 100 real `shipmentInfo` rows put
    `BRANCH*ORDER` there instead and can therefore never form that edge -- a
    real-data finding, recorded rather than papered over by changing the join.
    """
    documents: list[dict[str, Any]] = []
    for ordinal, order in enumerate(orders, start=1):
        lines = order.get("salesLines") or []
        if not any(int((line.get("lineData") or {}).get("shipQty") or 0) > 0 for line in lines):
            continue
        header = order["salesHdrEventData"]
        account = str(header["accountId"])
        order_number = str(header["orderId"])
        # Convey brokers parcel carriers and carries their tracking numbers;
        # DispatchTrack is own-fleet last mile and issues its own route
        # references. One shape for both would make a tracking-number search
        # look uniform in a way the real source is not.
        convey = rng.random() < 0.34
        tracking = (
            f"1Z{rng.choice('ABCDEFGHJKLMNPQRSTUVWXYZ')}{rng.randint(10**11, 10**12 - 1)}"
            if convey
            else f"{3520000000 + ordinal}_474"
        )
        # The order already holds a real datetime here, so the carrier event is
        # placed a few hours into that day rather than re-parsed out of a
        # string.
        shipped_at = _moment(
            order["salesHdr"]["salesHdrData"]["shipping"]["shipDate"]
            + timedelta(hours=rng.randint(8, 19), minutes=rng.randint(0, 59))
        )
        documents.append(
            {
                "_id": f"{account}*{order_number}*{tracking}",
                "shipmentInfoEventMeta": {
                    "srcSyncTs": shipped_at,
                    "rcvdTs": shipped_at,
                    "insertTs": shipped_at,
                    "lastUpdateTs": shipped_at,
                    "docType": "convey" if convey else "dispatchtrack",
                    "updatedBy": "shipping-writer-v1",
                },
                "shipmentInfoEventData": {
                    "acctId": account,
                    "shipmentId": (
                        f"shipid{ordinal:08d}" if convey else f"{account}:{order_number}:4"
                    ),
                    "trkNum": tracking,
                    "trilOrdNum": order_number,
                    # The order's own answer, not a second independent draw.
                    # Drawn separately, a shipment reported `delivered` on an
                    # order whose FleetWise route was still in progress -- and
                    # the shipment is the entity the release calls the
                    # authoritative delivery signal, so the two disagreeing is
                    # the corpus arguing with itself.
                    "currentStatus": (
                        "delivered"
                        if order["salesHdr"]["salesHdrData"]["shipping"].get("fleetwiseStatus")
                        == "Completed"
                        else rng.choices(("intransit", "outfordelivery"), weights=(80, 20))[0]
                    ),
                    "srcSystem": "Convey" if convey else "DispatchTrack",
                },
                "shipmentInfo": [
                    {
                        "shipmentInfoDetail": {
                            "createdDateTime": shipped_at,
                            "carrierScac": rng.choice(("UPS", "FDEG", "FDE", "USPS", "SAIA")),
                            "carrierName": rng.choice(
                                (
                                    "UNITED PARCEL SERVICE",
                                    "FEDEX GROUND",
                                    "FEDEX EXPRESS",
                                    "US POSTAL SERVICE",
                                    "SAIA MOTOR FREIGHT",
                                )
                            )
                            if convey
                            else "FERGUSON DELIVERY",
                            "eventType": "tracking",
                            "billOfLadingNum": f"{account}_{order_number}",
                            "origOrdNum": f"{account}_{order_number}",
                            "insertTs": shipped_at,
                        },
                        "shipmentInfo": {
                            "carrierShipDate": shipped_at,
                            "direction": "outbound",
                            "carrierServLevel": "Ground" if convey else "Local Delivery",
                        },
                    }
                ],
                "__seed": True,
            }
        )
    return documents


def _generate_warehouses(
    orders: Sequence[Mapping[str, Any]], rng: random.Random, generated_at: datetime
) -> list[dict[str, Any]]:
    """Provisional -- nothing reads this. See the module docstring.

    The ids are at least the real inventory warehouse ids the order lines carry,
    so a warehouse id on an order resolves to a document with the same
    identifier rather than to a `WH001` that appears nowhere else.
    """
    identifiers: set[str] = set()
    for order in orders:
        header = order.get("salesHdrEventData") or {}
        for key in ("sellWhseId", "shipFromWhseId"):
            value = header.get(key)
            if isinstance(value, str) and value:
                identifiers.add(value)
        for line in order.get("salesLines") or []:
            value = (line.get("lineData") or {}).get("invenWhse")
            if isinstance(value, str) and value:
                identifiers.add(value)
    documents: list[dict[str, Any]] = []
    for identifier in sorted(identifiers):
        city, state, zip_prefix = rng.choice(PLACES)
        documents.append(
            {
                "_id": identifier,
                "warehouseId": identifier,
                "name": f"{city.title()} Distribution Center",
                "address": {
                    "line1": f"{rng.randint(100, 9899)} {rng.choice(STREET_NAMES)} "
                    f"{rng.choice(STREET_TYPES)}",
                    "city": city,
                    "state": state,
                    "postal_code": f"{zip_prefix}{rng.randint(10, 99)}",
                },
                "bays": [f"BAY-{bay:02d}" for bay in range(1, rng.randint(6, 24))],
                "capacityUnits": rng.randint(500, 5000),
                "acceptsHazmat": rng.random() < 0.3,
                "acceptsOversize": rng.random() < 0.5,
                "sourceUpdatedAt": generated_at,
                "__seed": True,
            }
        )
    return documents


# ---------------------------------------------------------------------------
# Verification and loading
# ---------------------------------------------------------------------------


def _verify(
    schema: ActiveSchema,
    order: Mapping[str, Any],
    customer: Mapping[str, Any],
    product: Mapping[str, Any],
    shipment: Mapping[str, Any],
) -> None:
    """Every declared path must resolve, or the entity silently vanishes.

    Checked here rather than left to a graph build: a missing path produces an
    empty projection, and an empty projection is indistinguishable from a source
    that had no data. Each path is checked against the document extraction reads
    it from -- the exploded record for a `CURRENT_RECORD` path, the root for a
    `ROOT_DOCUMENT` one -- because checking every path against one of them
    passes a corpus the other half of the schema cannot read.

    Fields with a `derive` block have no physical path and are skipped: they are
    computed from a sibling that is checked.
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
        record_path = entity.record_path if entity.explode else ()
        base: Any = document
        for part in record_path:
            base = base.get(part) if isinstance(base, dict) else None
            if isinstance(base, list):
                base = base[0] if base else None
        if base is None:
            missing.append(f"{entity_id}: record_path {tuple(record_path)} absent")
            continue
        for field_id, field in entity.fields.items():
            if field.physical_path is None:
                continue
            if field.path_origin is PathOrigin.CURRENT_RECORD:
                node: Any = base
            elif field.path_origin is PathOrigin.ROOT_DOCUMENT:
                node = document
            else:
                continue
            for part in field.physical_path:
                node = node.get(part) if isinstance(node, dict) else None
            if node is None:
                missing.append(f"{entity_id}.{field_id} at {'.'.join(field.physical_path)}")
    if missing:
        raise SystemExit(
            "generated documents do not satisfy the active schema. The builders in this "
            "script write a real-shape document, so a schema change needs a change here:\n  "
            + "\n  ".join(missing[:30])
        )


async def _load(
    collection: Any,
    documents: Sequence[Mapping[str, Any]],
    real_ids: set[Any],
    label: str,
) -> None:
    """Replace everything that is not a real document, and nothing that is.

    Deletion is `_id $nin <the backed-up ids>` rather than a marker match: a
    marker only removes what a previous run of *this* script wrote, and the
    collections already hold documents from two earlier generators.
    """
    removed = await collection.delete_many({"_id": {"$nin": sorted(real_ids, key=str)}})
    for start in range(0, len(documents), 1000):
        batch = documents[start : start + 1000]
        if batch:
            await collection.insert_many(list(batch))
    total = await collection.count_documents({})
    print(
        f"  {label:22s} removed {removed.deleted_count:6d}  inserted {len(documents):6d}"
        f"  total {total:6d}"
    )


async def _replace_real(collection: Any, documents: Sequence[Mapping[str, Any]]) -> None:
    for document in documents:
        await collection.replace_one({"_id": document["_id"]}, dict(document), upsert=True)


async def _run(config: Mapping[str, Any], config_name: str) -> None:
    counts = config["counts"]
    rng = random.Random(config["seed"])
    domains = tuple(config["email_domains"])
    names = config["collections"]
    # The one value in the corpus that is not a function of `seed`. It stamps
    # `lkpSearchProduct.eventMeta.lastUpdateTS` and `customerOutboundCDM
    # .lastUpdateDate` -- the two source-change timestamps a product master and
    # a party carry, which have no business date to derive from the way an
    # order's do. Every identifier, SKU, description, name, email and address
    # is seed-determined, so two runs produce the same corpus in every respect
    # any consumer reads; only the "when did the source last change" stamp on
    # those two collections moves.
    generated_at = datetime.now(UTC).replace(tzinfo=None)

    settings = Settings()
    schema: ActiveSchema = load_active_schema(settings.dynamic_knowledge_schema_path)
    client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
        settings.source_mongo_dsn.get_secret_value()
        if settings.source_mongo_dsn is not None
        else settings.mongo_dsn.get_secret_value()
    )
    database = client[settings.source_mongo_database]

    earliest = datetime.fromisoformat(config["order_dates"]["earliest"])
    latest = datetime.fromisoformat(config["order_dates"]["latest"])
    span_days = max(1, (latest - earliest).days)
    shipped_fraction = float(config.get("shipped_fraction", 0.7))

    try:
        print(f"generating from {config_name} (seed {config['seed']})\n")
        real = await _ensure_backup(database, names)
        print(
            f"  real documents: orders {len(real.orders)}  customers {len(real.customers)}"
            f"  products {len(real.products)}  shipments {len(real.shipments)}"
        )

        directory = PersonDirectory(rng, domains)
        renamed_orders, people, emails_added = _rename_real_orders(real.orders, directory)
        renamed_customers = _rename_real_customers(real.customers, people, directory)
        print(
            f"\nrenamed {len(people)} real customer identities to individual people "
            f"({emails_added} contact rows given a derived email)"
        )

        # A catalogue number that already has a real `lkpSearchProduct`
        # document is dropped from the mined set. `2175168` / `PSRGW1212` is
        # both: it is the one real product row *and* it appears on a real order
        # line. The real document is the richer and the truer of the two, and
        # `_load` preserves it, so deriving a second one from the order line
        # would be a duplicate `_id` -- which is exactly how it announced
        # itself.
        already_real = {str(document["_id"]) for document in real.products}
        real_products = [
            product
            for product in _mine_real_products(renamed_orders)
            if product.product_id not in already_real
        ]
        reserved_products = {product.product_id for product in real_products} | already_real
        reserved_skus = {product.sku for product in real_products}
        generated_products = _generate_products(
            max(0, counts["products"] - len(reserved_products)),
            reserved_products | reserved_skus,
            rng,
        )
        product_documents = [
            _hydrate_real_product(product, generated_at) for product in real_products
        ] + [_product_document(product, generated_at) for product in generated_products]
        catalogue = [*real_products, *generated_products]
        if not generated_products:
            raise SystemExit(
                "counts.products is at or below the number of real catalogue numbers "
                f"({len(reserved_products)}); raise it in config/seed/generation.yaml"
            )

        reserved_customer_ids = {
            str(((order.get("salesHdr") or {}).get("salesHdrData") or {}).get("custId"))
            for order in renamed_orders
        }
        reserved_party_ids = {str(document.get("partyId")) for document in renamed_customers}
        customer_documents, accounts = _generate_customers(
            max(0, counts["customers"] - len(renamed_customers)),
            directory,
            reserved_customer_ids,
            reserved_party_ids,
            rng,
            generated_at,
        )

        numbers = OrderNumbers(
            str((order.get("salesHdrEventData") or {}).get("orderId") or "")
            for order in renamed_orders
        )
        order_documents = _generate_orders(
            max(0, counts["orders"] - len(renamed_orders)),
            accounts,
            catalogue,
            numbers,
            earliest,
            span_days,
            shipped_fraction,
            rng,
            int(config.get("delivered_orders", 0)),
        )
        shipment_documents = _generate_shipments(order_documents, rng)
        warehouse_documents = _generate_warehouses(order_documents, rng, generated_at)
        if not shipment_documents:
            # A corpus with orders but no shipments is silent otherwise --
            # fulfilment simply reports every return as awaiting handoff.
            raise SystemExit("no shipments generated; check shipped_fraction and counts.orders")

        print("\nverifying every declared path resolves against a generated document...")
        _verify(
            schema,
            order_documents[0],
            customer_documents[0],
            # `is True`, not truthiness. A product derived from a real order
            # line carries `__seed: "DERIVED_FROM_ORDER_LINE"`, which is also
            # truthy -- so this picked one of those, and then failed the whole
            # run because a derived product deliberately states no vendor, no
            # department and no UPC. The verification wants a fully generated
            # document, which is the one marked exactly `True`.
            next(document for document in product_documents if document.get("__seed") is True),
            shipment_documents[0],
        )
        print("all declared paths resolve.\n")

        await _load(
            database[names["products"]],
            product_documents,
            real.identifiers("products"),
            "lkpSearchProduct",
        )
        await _load(
            database[names["customers"]],
            customer_documents,
            real.identifiers("customers"),
            "customerOutboundCDM",
        )
        await _load(
            database[names["orders"]], order_documents, real.identifiers("orders"), "salesInv"
        )
        await _load(
            database[names["shipments"]],
            shipment_documents,
            real.identifiers("shipments"),
            "shipmentInfo",
        )
        await _load(database[names["warehouses"]], warehouse_documents, set(), "warehouseMaster")

        await _replace_real(database[names["orders"]], renamed_orders)
        await _replace_real(database[names["customers"]], renamed_customers)
        print("\nreal documents rewritten with person names, everything else preserved.")

        lines = sum(len(document["salesLines"]) for document in order_documents)
        finishes = {
            product.colour_finish for product in catalogue if product.colour_finish is not None
        }
        print(
            f"\norder lines generated  {lines}\n"
            f"distinct SKUs          {len({product.sku for product in catalogue})}\n"
            f"distinct finishes      {len(finishes)} {sorted(finishes)}\n"
            f"products with a finish "
            f"{sum(1 for product in catalogue if product.colour_finish)}\n"
            f"customer accounts      {len(accounts)}"
        )
        print("\nNext: python backend/scripts/build_knowledge_graph.py 20000")
    finally:
        await client.close()


def main() -> None:
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CONFIG
    if not config_path.is_file():
        raise SystemExit(f"no such config: {config_path}")
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    asyncio.run(_run(config, config_path.name))


if __name__ == "__main__":
    main()
