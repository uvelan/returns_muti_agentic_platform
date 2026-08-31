"""Put the right *kind* of value in each name field of the committed fixture.

`deidentify_reference_dataset.py` chose its replacement pool from the shape of
the original value -- multi-word and upper case meant a trade name, anything
else a person. That reads the data instead of the schema, and it was wrong in
both directions at once:

  * "JOHN SMITH" is two upper-case words, so every `contactFirstName` in the
    fixture became a company. `CLEARBROOK SUPPLY` as a first name,
    `HARBOR POINT SERVICES` as a last name, and neither of them the customer on
    the order.
  * "US Dollar" is not upper case, so `transactionCurrencyName` became
    `JORDAN REYES` -- a person in a currency field.
  * Product fields went the same way: `webDisplayName` and `brandNames` hold
    people, so the copilot can offer an associate a line item called
    `RILEY CHEN`.

The generator is fixed, but it reads a source extract that is not in this
repository -- only the derived JSON is committed -- so the fixture cannot simply
be regenerated. This repairs it in place, applying the same corrected rules.

**Business meaning is untouched.** Order numbers, dates, quantities, prices,
SKUs, statuses, branch codes: none of them names anybody and all of them are
what makes the copilot's answers worth reading.

**Identity stays consistent.** A customer keeps one contact person across every
order, because the choice is a stable hash of the customer id rather than a
counter -- the same property the generator relies on for its joins to survive.

    python backend/scripts/repair_reference_dataset.py --check
    python backend/scripts/repair_reference_dataset.py --apply
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DATASET = BACKEND_ROOT / "fixtures" / "reference_dataset"

#: Given names and surnames kept apart, because `contactFirstName` and
#: `contactLastName` are separate fields and the old pool held only full names.
#: Wide enough that 487 address rows do not resolve to eight people.
GIVEN_NAMES = (
    "ALEX",
    "JORDAN",
    "SAM",
    "RILEY",
    "CASEY",
    "AVERY",
    "TAYLOR",
    "QUINN",
    "MORGAN",
    "DREW",
    "HAYDEN",
    "ROWAN",
    "SKYLER",
    "EMERSON",
    "PARKER",
    "REESE",
    "DAKOTA",
    "LOGAN",
    "CAMERON",
    "ELLIS",
    "FINLEY",
    "HARPER",
    "JAMIE",
    "KENDALL",
)
SURNAMES = (
    "MORGAN",
    "REYES",
    "OKONKWO",
    "CHEN",
    "NDIAYE",
    "LINDQVIST",
    "MBEKI",
    "DELACROIX",
    "HALVORSEN",
    "ADEYEMI",
    "KOWALSKI",
    "NAKAMURA",
    "OSEI",
    "PETROV",
    "RAMIREZ",
    "SOLBERG",
    "TANAKA",
    "VARGAS",
    "WHITFIELD",
    "ZHAO",
)

#: Trade names. The extract is B2B (`b2bCustFlag` is true), so a company in
#: `custName` is correct and is what the copilot disambiguates on.
BUSINESS_NAMES = (
    "MERIDIAN HEATING & COOLING",
    "NORTHGATE PLUMBING SUPPLY",
    "BRIGHTWATER MECHANICAL",
    "CEDAR RIDGE HVAC",
    "STONEBRIDGE PIPEWORKS",
    "HARBOR POINT SERVICES",
    "IRONGATE CONTRACTORS",
    "SILVERLAKE AIR SYSTEMS",
    "WESTFIELD PLUMBING CO",
    "GRANITE PEAK MECHANICAL",
    "BLUEFIN UTILITIES",
    "OAKMONT CLIMATE CONTROL",
    "REDSTONE INDUSTRIAL",
    "CLEARBROOK SUPPLY",
    "FAIRVIEW HEATING",
)

PRODUCT_NAMES = (
    "3/4 IN COPPER 90 ELBOW",
    "1/2 IN PEX BALL VALVE",
    "40 GAL GAS WATER HEATER",
    "3 TON CONDENSING UNIT",
    "20X25X1 PLEATED AIR FILTER",
    "1 IN BRASS GATE VALVE",
    "4 IN PVC SEWER PIPE",
    "3/4 HP SUMP PUMP",
    "24 IN FLEXIBLE DUCT",
    "1/2 IN COPPER TUBE 10FT",
)

GIVEN_KEYS = ("contactfirstname", "personfirstname")
FAMILY_KEYS = ("contactlastname", "personlastname")
PERSON_FULL_KEYS = ("empname", "placedbyname", "salesmanname", "attnname", "buyername")
ORGANISATION_KEYS = (
    "custname",
    "customername",
    "shiptoname",
    "billtoname",
    "vendorname",
    "companyname",
    "accountname",
    "maincustsname",
    "partyname",
    "organizationname",
    "brandname",
)
PRODUCT_KEYS = ("webdisplayname", "displayname", "machinename", "productname", "itemname")
FIXED_VALUES = {
    "transactioncurrencyname": "US Dollar",
    "currencyname": "US Dollar",
    "countryname": "United States",
}


def _hash(token: str) -> int:
    return int.from_bytes(hashlib.sha256(token.encode("utf-8")).digest()[:8], "big")


def _pick(values: tuple[str, ...], token: str) -> str:
    return values[_hash(token) % len(values)]


def _looks_like_company(value: str) -> bool:
    return isinstance(value, str) and any(name == value for name in BUSINESS_NAMES)


def _looks_like_person(value: str) -> bool:
    if not isinstance(value, str):
        return False
    parts = value.split()
    return len(parts) == 2 and parts[0] in GIVEN_NAMES and parts[1] in SURNAMES


class Repair:
    """Walks a document, fixing name fields and the two structural defects."""

    def __init__(self, anchor: str) -> None:
        #: Everything about one document's identity hangs off this, so a customer
        #: keeps the same contact across all of their orders.
        self.anchor = anchor
        self.counts: dict[str, int] = {}

    def _count(self, what: str) -> None:
        self.counts[what] = self.counts.get(what, 0) + 1

    def value_for(self, key: str, value: Any) -> Any:
        if not isinstance(value, str) or not value:
            return value
        lowered = key.lower()

        for marker, fixed in FIXED_VALUES.items():
            if marker in lowered:
                if value != fixed:
                    self._count(f"{key}: person/company -> {fixed}")
                return fixed

        if lowered == "country" and value == "REDACTED":
            self._count("country: REDACTED -> US")
            return "US"

        if any(k in lowered for k in GIVEN_KEYS):
            if not _looks_like_company(value) and value in GIVEN_NAMES:
                return value
            self._count(f"{key}: company -> given name")
            return _pick(GIVEN_NAMES, f"given:{self.anchor}")

        if any(k in lowered for k in FAMILY_KEYS):
            if not _looks_like_company(value) and value in SURNAMES:
                return value
            self._count(f"{key}: company -> surname")
            return _pick(SURNAMES, f"family:{self.anchor}")

        if any(k in lowered for k in PERSON_FULL_KEYS):
            if _looks_like_person(value):
                return value
            self._count(f"{key}: company -> person")
            given = _pick(GIVEN_NAMES, f"{key}:{self.anchor}")
            family = _pick(SURNAMES, f"{key}:{self.anchor}:s")
            return f"{given} {family}"

        if any(k in lowered for k in PRODUCT_KEYS):
            if _looks_like_person(value) or _looks_like_company(value):
                self._count(f"{key}: identity -> product")
                return _pick(PRODUCT_NAMES, f"{key}:{value}")
            return value

        if any(k in lowered for k in ORGANISATION_KEYS):
            if _looks_like_person(value):
                self._count(f"{key}: person -> company")
                return _pick(BUSINESS_NAMES, f"{key}:{value}")
            return value

        return value

    def walk(self, node: Any, key: str = "") -> Any:
        if isinstance(node, dict):
            return {inner: self.walk(value, inner) for inner, value in node.items()}
        if isinstance(node, list):
            return [self.walk(item, key) for item in node]
        return self.value_for(key, node)


def _fix_addresses(document: dict[str, Any], repair: Repair) -> None:
    """Two structural defects the name pools cannot reach.

    `address2` repeated `address1` verbatim on some rows -- a second line that
    adds nothing and reads as a copy/paste error -- and identical address rows
    were emitted more than once per customer.
    """
    rows = (document.get("customer") or {}).get("address")
    if not isinstance(rows, list):
        return
    seen: set[str] = set()
    kept: list[Any] = []
    for row in rows:
        if not isinstance(row, dict):
            kept.append(row)
            continue
        if row.get("address2") and row.get("address2") == row.get("address1"):
            row.pop("address2")
            repair._count("address2: identical to address1 -> removed")  # noqa: SLF001
        fingerprint = json.dumps(row, sort_keys=True)
        if fingerprint in seen:
            repair._count("address row: exact duplicate -> removed")  # noqa: SLF001
            continue
        seen.add(fingerprint)
        kept.append(row)
    document["customer"]["address"] = kept


def _anchor_for(document: dict[str, Any], index: int) -> str:
    header = ((document.get("salesHdr") or {}).get("salesHdrData")) or {}
    return str(header.get("custId") or header.get("custName") or index)


def repair_file(path: Path) -> tuple[Any, dict[str, int]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    documents = data if isinstance(data, list) else [data]
    totals: dict[str, int] = {}
    repaired: list[Any] = []
    for index, document in enumerate(documents):
        repair = Repair(_anchor_for(document if isinstance(document, dict) else {}, index))
        fixed = repair.walk(document)
        if isinstance(fixed, dict) and isinstance(fixed.get("customer"), dict):
            _fix_addresses(fixed, repair)
        repaired.append(fixed)
        for what, count in repair.counts.items():
            totals[what] = totals.get(what, 0) + count
    return (repaired if isinstance(data, list) else repaired[0]), totals


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the repaired files")
    parser.add_argument("--check", action="store_true", help="report without writing")
    arguments = parser.parse_args()
    if not (arguments.apply or arguments.check):
        parser.error("pass --check or --apply")

    grand: dict[str, int] = {}
    for path in sorted(DATASET.glob("*.json")):
        payload, counts = repair_file(path)
        if counts:
            print(f"\n{path.name}")
            for what, count in sorted(counts.items(), key=lambda item: -item[1]):
                print(f"  {count:>5}  {what}")
        for what, count in counts.items():
            grand[what] = grand.get(what, 0) + count
        if arguments.apply:
            path.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")

    total = sum(grand.values())
    if arguments.apply:
        print(f"\nApplied {total} corrections.")
    else:
        print(f"\n{total} corrections would be applied. Re-run with --apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
