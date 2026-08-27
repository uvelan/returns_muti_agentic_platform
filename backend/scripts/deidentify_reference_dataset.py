"""Turn the real Ferguson extract into a committable reference dataset.

The source documents are production records: customer and contact names, street
addresses, phone numbers, and -- further down -- cheque account numbers, driver
licence numbers, card-holder addresses and payment tokens. None of that can
enter git history, but the *shape* has to survive exactly, because the active
schema reads through it by physical path and a flatter approximation extracts
nothing.

**Denylist plus proof, not denylist alone.** Keys are matched by pattern, which
on its own is how a scrubber misses one of four hundred field names. So after
rewriting, `_assert_nothing_leaked` collects every sensitive value that appeared
in the source and searches the serialized output for it. A survivor fails the
run. The proof is the part that makes the pattern list safe to rely on; without
it this script would only be a good intention.

Business meaning is deliberately preserved: order numbers, dates, statuses,
warehouse and branch codes, product descriptions, SKUs, quantities and prices
are real. They are what makes the copilot's answers worth looking at, and none
of them identifies a person.

Usage:
    python scripts/deidentify_reference_dataset.py [SOURCE_DIR] [OUTPUT_DIR]
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT / "src"))

# What a synthetic contact address looks like is defined once, in the library the
# record generator also uses. Re-exported here because this module is the one the
# reference-dataset tests read the contract from.
from return_platform.data_platform.operational_generation import (  # noqa: E402
    deterministic_values as _values,
)

RESERVED_EMAIL_TLDS = _values.RESERVED_EMAIL_TLDS
_synthetic_email = _values.build_synthetic_email

__all__ = ["RESERVED_EMAIL_TLDS"]

DEFAULT_SOURCE = Path("K:/Projects/FEG/Ret/full/return_discovery_order_analysis_package/files")
DEFAULT_OUTPUT = BACKEND_ROOT / "fixtures" / "reference_dataset"

DOCUMENTS = ("salesInv1.json", "lkpSearchProduct.json", "customerOutboundCDM.json")

#: Keys whose values name or locate a person or business, or authenticate a
#: payment. Substring match, case-insensitive, deliberately wide: a false
#: positive costs a realistic-looking value, a false negative leaks a record.
SENSITIVE_KEY_PATTERN = re.compile(
    r"name|addr|phone|mobile|email|fax|zip|postal|city|state|county|country"
    r"|acctnum|licen|authcode|voiceauth|token|cof|exempt|drawer|transit"
    r"|chknum|custcode|exprym|geocode|attn|usertonotify|initials|empnum"
    # Operator free text. These hold whatever somebody typed, and in this
    # extract `custPONumber` and `jobName` both hold the customer's site
    # address -- which is how an address survived a scrub that covered every
    # field actually named after one. The proof below is what found it.
    r"|ponumber|jobname|comment|shipinstr|servmsg|refcmt"
    # Staff identity. `pickedEmpId` holds "AUSTIN WILSON" despite the `Id`
    # suffix, and `relManualHold` is an audit trail with the employee's name
    # embedded mid-string ("...*Austin Miller*SOE.COMPLETION*..."). Employees
    # are people too; a scrub aimed only at customers leaves half the record.
    r"|empid|associd|slsmid|updatedby|manualhold|writerinit|splitempinit"
    r"|licnum|drvlic|taginfo",
    re.IGNORECASE,
)

#: Contact details recognised by *shape*, applied to every surviving string.
#:
#: The key list above is a denylist, and a denylist over four hundred field
#: names loses. It lost three times here: `custPONumber` held a site address,
#: `pickedEmpId` held a person, `drvLicNum` and `tagInfo` each held a phone
#: number -- and `drvLicNum` was missed by a pattern that already contained
#: `licen`. Matching the value as well as the key means a field nobody
#: enumerated still cannot carry a routable number or address out of here.
CONTACT_SHAPES = (
    re.compile(r"\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b"),
    re.compile(r"\b\(\d{3}\)\s*\d{3}[-.\s]?\d{4}\b"),
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
)

#: Keys that survive the pattern above but must not: each is a real business
#: value the platform reads, and none of them identifies anybody.
KEEP = frozenset(
    {
        "countryCode",
        "salesCode",
        "holdCode",
        "srcCode",
        "shipViaCode",
        "frtReason",
        "shipInstrCode",
        "priceCode",
        "origPriceCode",
        "avgCostCode",
        "ctryOriginCode",
        "servErrorCode",
        "servFuncCode",
        "loadSourceCode",
        "srcSysCode",
        "taxJurCode",
        "termsCode",
        "expediteFeeCodes",
        "transactionCurrencyCode",
        "reasonCode",
    }
)

#: Synthetic replacements. Small closed sets rather than random strings: the
#: copilot is searched by these names, so they have to read like trade names an
#: associate would type, and repeat across orders the way real customers do.
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
PERSON_NAMES = (
    "ALEX MORGAN",
    "JORDAN REYES",
    "SAM OKONKWO",
    "RILEY CHEN",
    "CASEY NDIAYE",
    "AVERY LINDQVIST",
    "TAYLOR MBEKI",
    "QUINN DELACROIX",
)
STREETS = (
    "118 FOUNDRY LANE",
    "27 KESTREL WAY",
    "940 ALDER STREET",
    "6 MILLRACE ROAD",
    "512 QUARRY AVENUE",
    "83 HARROW CLOSE",
    "1204 SEVERN DRIVE",
    "45 TANNERY ROW",
)
CITIES = (
    ("SPRINGFIELD", "OH", "45501"),
    ("FAIRHAVEN", "NC", "28202"),
    ("RIVERTON", "TX", "75001"),
    ("MAPLETON", "GA", "30907"),
    ("EASTBROOK", "PA", "19019"),
    ("WESTHAVEN", "IL", "60601"),
)


def _pick(values: tuple[Any, ...], token: str) -> Any:
    """Deterministic choice, so a customer keeps one identity across the file.

    Hashing the original value rather than a counter is what makes the mapping
    consistent: the same real customer, wherever they appear and in whichever
    document, resolves to the same synthetic one -- which is the only reason the
    joins still hold after scrubbing.
    """
    return values[_stable_hash(token) % len(values)]


def _stable_hash(token: str) -> int:
    """A digest that is the same in every process.

    Python's built-in `hash()` is salted per interpreter run, so using it here
    would give the committed fixture a different set of emails and phone numbers
    on every regeneration -- an enormous diff that says nothing, and a file
    nobody could verify by re-running the script that produced it.
    """
    return int.from_bytes(hashlib.sha256(token.encode("utf-8")).digest()[:8], "big")


def _synthetic(key: str, value: Any) -> Any:
    if value is None or value == "":
        return value
    if not isinstance(value, str):
        return value
    token = f"{key}:{value}"
    lowered = key.lower()

    if "email" in lowered:
        return _synthetic_email(_pick(PERSON_NAMES, token), _pick(BUSINESS_NAMES, token))
    if "phone" in lowered or "fax" in lowered or "mobile" in lowered:
        return f"555-01{_stable_hash(token) % 100:02d}"
    if "zip" in lowered or "postal" in lowered:
        return _pick(CITIES, token)[2]
    if lowered.endswith("city") or lowered == "city":
        return _pick(CITIES, token)[0]
    if lowered.endswith("state") or lowered == "state":
        return _pick(CITIES, token)[1]
    if "county" in lowered:
        return f"{_pick(CITIES, token)[0].title()} County"
    if "addr" in lowered:
        return _pick(STREETS, token)
    if "jobname" in lowered:
        return f"SITE {_pick(STREETS, token)}"
    if "ponumber" in lowered:
        return f"PO-{_stable_hash(token) % 10**6:06d}"
    if "comment" in lowered or "shipinstr" in lowered or "refcmt" in lowered or "msg" in lowered:
        # Emptied, not synthesized. Free text has no shape worth preserving and
        # anything invented here would only be noise in a prompt.
        return ""
    for marker, fixed in _NON_IDENTITY_NAME_VALUES.items():
        if marker in lowered:
            return fixed
    if "name" in lowered:
        return _pick(_name_pool_for(lowered), token)
    # Account numbers, licences, tokens, auth codes: replaced with an opaque
    # marker rather than a plausible-looking value, because nothing should be
    # able to mistake one of these for real.
    return "REDACTED"


#: Fields that name a *person*, and fields that name an *organisation*.
#:
#: The pool used to be chosen from the shape of the original value --
#: multi-word and upper case meant a trade name, anything else a person -- which
#: reads the data instead of the schema and gets it wrong in both directions.
#: "JOHN SMITH" is two upper-case words, so every `contactFirstName` in the
#: committed fixture became a company: `CLEARBROOK SUPPLY` as a first name,
#: `HARBOR POINT SERVICES` as a last name, and neither of them the customer on
#: the order. "US Dollar" is not upper case, so `transactionCurrencyName` became
#: `JORDAN REYES` -- a person's name in a currency field.
#:
#: A field's meaning is fixed by the schema and known here, so it decides.
_PERSON_NAME_KEYS = (
    "contactfirstname",
    "contactlastname",
    "empname",
    "placedbyname",
    "salesmanname",
    "shiptoattnname",
    "attnname",
    "buyername",
    "orderedbyname",
    "receivedbyname",
    "personfirstname",
    "personlastname",
    "personname",
)

#: Names of the trading party. This extract is B2B (`b2bCustFlag` is true), so a
#: company here is correct and is what the copilot disambiguates on.
_ORGANISATION_NAME_KEYS = (
    "custname",
    "customername",
    "shiptoname",
    "billtoname",
    "vendorname",
    "companyname",
    "accountname",
    "vendorname",
    "mainCustsName".lower(),
    "partyname",
    "organizationname",
    "brandname",
    "manufacturername",
)

#: Names that identify neither, and must not be replaced with either. A currency
#: is not a person; a warehouse is not a company. Held as fixed values so the
#: field keeps a meaning the platform can read.
#: What a product is called. Neither pool fits: `webDisplayName` is what an
#: associate reads on the line they are returning, and a person's name there --
#: `RILEY CHEN` as a product -- is the most visible form of this defect.
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

#: `machinename` is deliberately NOT here. It reads like a product field and is
#: not one: in this extract `eventMeta.machineName` is the ETL host that wrote
#: the document, so classifying it as a product name stamped
#: "1/2 IN PEX BALL VALVE" into a hostname field -- the same class of error as
#: the currency and warehouse cases this table exists to prevent. It carries no
#: identity, so it needs no replacement at all.
_PRODUCT_NAME_KEYS = (
    "webdisplayname",
    "displayname",
    "productname",
    "itemname",
)

_NON_IDENTITY_NAME_VALUES = {
    "transactioncurrencyname": "US Dollar",
    "currencyname": "US Dollar",
    "countryname": "United States",
    "statename": "Ohio",
    "unitofmeasurename": "EACH",
    # The ETL host that wrote the document. It has to be listed here rather than
    # merely left out of the product pool: `machineName` still matches the `name`
    # branch of SENSITIVE_KEY_PATTERN, so anything not classified falls through
    # to the person pool -- which would put "RILEY CHEN" in a hostname field
    # instead of the product name that was there before. A hostname identifies a
    # server, not a person, so a fixed value is both safe and correct.
    "machinename": "etl-loader-01",
}


def _name_pool_for(lowered: str) -> tuple[str, ...]:
    """The pool a `*name*` field should draw from, decided by the field."""
    if any(key in lowered for key in _PRODUCT_NAME_KEYS):
        return PRODUCT_NAMES
    if any(key in lowered for key in _PERSON_NAME_KEYS):
        return PERSON_NAMES
    if any(key in lowered for key in _ORGANISATION_NAME_KEYS):
        return BUSINESS_NAMES
    # Unclassified. A person's name is the conservative default: it is the shape
    # that carries identity, so treating an unknown name field as one keeps the
    # scrub safe, and `_assert_nothing_leaked` still proves the original is gone.
    return PERSON_NAMES


def _scrub(node: Any, key: str = "") -> Any:
    if isinstance(node, dict):
        return {inner: _scrub(value, inner) for inner, value in node.items()}
    if isinstance(node, list):
        return [_scrub(item, key) for item in node]
    if key and key not in KEEP and SENSITIVE_KEY_PATTERN.search(key):
        return _synthetic(key, node)
    # Whatever the key list missed. Applied to values it decided to keep, so a
    # phone number in a field nobody classified as a contact field still does
    # not survive.
    if isinstance(node, str):
        for shape in CONTACT_SHAPES:
            node = shape.sub("REDACTED", node)
    return node


_CITY_KEYS = ("city", "ccCity")
_STATE_KEYS = ("state", "ccState")
_ZIP_KEYS = ("zipCode", "postalCode", "ccZip")
_COUNTY_KEYS = ("county", "countyName")


def _harmonise_locations(node: Any) -> Any:
    """Make each address's city, state and postcode agree with each other.

    Scrubbing is per field, so the three are chosen independently and land on
    "RIVERTON TX 60601" -- a Texas city with an Illinois postcode. Nothing in
    the platform breaks, but an associate can search by city, and seed data that
    contradicts itself teaches the wrong thing about what a match means. One
    tuple per address object, anchored on whichever city was already chosen.
    """
    if isinstance(node, list):
        return [_harmonise_locations(item) for item in node]
    if not isinstance(node, dict):
        return node

    harmonised = {key: _harmonise_locations(value) for key, value in node.items()}
    for city_key in _CITY_KEYS:
        city = harmonised.get(city_key)
        if not isinstance(city, str) or not city:
            continue
        chosen = next((entry for entry in CITIES if entry[0] == city), None) or _pick(CITIES, city)
        harmonised[city_key] = chosen[0]
        for state_key in _STATE_KEYS:
            if isinstance(harmonised.get(state_key), str) and harmonised[state_key]:
                harmonised[state_key] = chosen[1]
        for zip_key in _ZIP_KEYS:
            if isinstance(harmonised.get(zip_key), str) and harmonised[zip_key]:
                harmonised[zip_key] = chosen[2]
        # `county` was scrubbed independently of `city`, so a row read
        # "WESTHAVEN ... Mapleton County" -- two different towns in one address.
        # Same defect the city/state/postcode triple above exists to prevent.
        for county_key in _COUNTY_KEYS:
            if isinstance(harmonised.get(county_key), str) and harmonised[county_key]:
                harmonised[county_key] = f"{chosen[0].title()} County"
    return harmonised


#: Where an object names the person the address belongs to.
_CONTACT_FIRST_KEYS = ("contactFirstName", "personFirstName", "firstName")
_CONTACT_LAST_KEYS = ("contactLastName", "personLastName", "lastName")


def _harmonise_contacts(node: Any, business_name: str, key: str = "") -> Any:
    """Make each email address agree with the contact and company on its record.

    Scrubbing is per field, so the email was drawn from a hash of its own old
    value and had nothing to do with the person standing next to it: an address
    row for TAYLOR SOLBERG of BLUEFIN UTILITIES carried an address belonging to
    neither. Anchored here on the row's own contact name and the order's
    `custName`, it becomes `taylor.solberg@bluefinutilities.example` -- still
    undeliverable, and now consistent with every other field on the row.

    Rows without a contact name keep the address `_synthetic` chose. That one is
    already safe; it simply has nobody to agree with.
    """
    if isinstance(node, list):
        return [_harmonise_contacts(item, business_name, key) for item in node]
    if not isinstance(node, dict):
        return node

    harmonised = {
        inner: _harmonise_contacts(value, business_name, inner) for inner, value in node.items()
    }
    first = next((harmonised.get(name) for name in _CONTACT_FIRST_KEYS if harmonised.get(name)), "")
    last = next((harmonised.get(name) for name in _CONTACT_LAST_KEYS if harmonised.get(name)), "")
    if not isinstance(first, str) or not isinstance(last, str) or not (first or last):
        return harmonised
    for inner, value in harmonised.items():
        if "email" in inner.lower() and isinstance(value, str) and value:
            harmonised[inner] = _synthetic_email(f"{first} {last}".strip(), business_name)
    return harmonised


def _find_business_name(node: Any, key: str = "") -> str:
    """The trading party this record belongs to, wherever the document keeps it.

    `custName` on a salesInv order, `partyName` on a CDM customer. Rather than
    hard-code both paths, take the first organisation-named field holding a value
    the scrubber itself could have produced -- which is exactly the set of trade
    names the fixture is allowed to contain.
    """
    if isinstance(node, dict):
        for inner, value in node.items():
            if (
                isinstance(value, str)
                and value in BUSINESS_NAMES
                and any(marker in inner.lower() for marker in _ORGANISATION_NAME_KEYS)
            ):
                return value
        for inner, value in node.items():
            found = _find_business_name(value, inner)
            if found:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _find_business_name(item, key)
            if found:
                return found
    return ""


def _harmonise_per_record(node: Any) -> Any:
    """Run the contact pass once per top-level record, anchored on its own party."""
    if isinstance(node, list):
        return [_harmonise_per_record(item) for item in node]
    business = _find_business_name(node)
    if not business:
        return node
    return _harmonise_contacts(node, business)


def _sensitive_values(node: Any, key: str = "", found: set[str] | None = None) -> set[str]:
    """Every value the source held under a sensitive key, for the proof below."""
    found = set() if found is None else found
    if isinstance(node, dict):
        for inner, value in node.items():
            _sensitive_values(value, inner, found)
    elif isinstance(node, list):
        for item in node:
            _sensitive_values(item, key, found)
    elif key and key not in KEEP and SENSITIVE_KEY_PATTERN.search(key):
        if isinstance(node, str) and _is_distinctive(node.strip()):
            found.add(node.strip())
    return found


def _is_distinctive(value: str) -> bool:
    """Is this value specific enough that finding it again means a real leak?

    A substring search over the whole document is the right net -- it catches a
    value that moved, or that also sits under a key the pattern misses -- but a
    short numeric code like `0071` or `00000` occurs all over an ERP record for
    reasons that have nothing to do with the field it came from, and reporting
    those as survivors buries the one that matters. So the search is restricted
    to values that could not plausibly collide: anything containing a letter,
    and long digit strings such as an account number.
    """
    if len(value) < 5:
        return False
    # A field carrying the string "false" is a flag, not a person, and it will
    # match every boolean in the serialized output.
    if value.strip().lower() in {"true", "false", "null", "none", "unknown"}:
        return False
    if any(character.isalpha() for character in value):
        return True
    return len(value.replace("-", "").replace(" ", "")) >= 7


def _business_values(node: Any, key: str = "", found: set[str] | None = None) -> set[str]:
    """Values the source held under keys that are *not* sensitive.

    A product called "GATE VLV" appears both in `productDesc`, which is business
    vocabulary worth keeping, and under a name-shaped key that the denylist
    catches. Seeing it in the output is therefore not evidence that anything
    leaked, and treating it as such buries a real finding under noise.
    """
    found = set() if found is None else found
    if isinstance(node, dict):
        for inner, value in node.items():
            _business_values(value, inner, found)
    elif isinstance(node, list):
        for item in node:
            _business_values(item, key, found)
    elif isinstance(node, str) and key and not SENSITIVE_KEY_PATTERN.search(key):
        found.add(node.strip())
    return found


#: Everything the scrubber can emit, so a source value that happens to be a
#: fragment of a replacement -- "JORDAN" inside "JORDAN REYES" -- is not
#: mistaken for a survivor of the value it replaced.
_SYNTHETIC_CORPUS = " | ".join(
    BUSINESS_NAMES + PERSON_NAMES + STREETS + tuple(part for city in CITIES for part in city)
)


def _assert_nothing_leaked(original: Any, scrubbed: Any, label: str) -> int:
    """Fail the run if a value that only ever appeared under a sensitive key
    survives anywhere in the output.

    Two checks, because either alone is weak. First, per path: every sensitive
    field must differ from its source value -- exact, and blind to coincidence.
    Then a substring sweep of the whole serialized document, which is what
    catches a value that moved, or that also sits under a key the pattern does
    not match. That sweep is restricted to values the source used *only* in
    sensitive positions, and never counts a fragment of the scrubber's own
    vocabulary. Between them, the pattern list becomes safe to rely on.
    """
    mismatches = _unchanged_sensitive_paths(original, scrubbed)
    if mismatches:
        preview = ", ".join(mismatches[:8])
        raise SystemExit(
            f"{label}: {len(mismatches)} sensitive field(s) were not replaced: {preview}"
        )

    haystack = json.dumps(scrubbed, ensure_ascii=False)
    # Containment, not equality. "GATE VLV" reaches a name-shaped key in this
    # extract while also being part of the product description "2 GATE VLV BRZ"
    # that the dataset deliberately keeps -- so finding it in the output says
    # nothing about the sensitive field it also came from.
    business_corpus = " | ".join(sorted(_business_values(original)))
    survivors = sorted(
        value
        for value in _sensitive_values(original)
        if value in haystack and value not in business_corpus and value not in _SYNTHETIC_CORPUS
    )
    if survivors:
        preview = ", ".join(repr(value) for value in survivors[:10])
        raise SystemExit(
            f"{label}: {len(survivors)} sensitive value(s) survived de-identification: {preview}"
        )
    return len(_sensitive_values(original))


def _unchanged_sensitive_paths(
    original: Any, scrubbed: Any, key: str = "", path: str = ""
) -> list[str]:
    """Sensitive paths whose value came through untouched."""
    if isinstance(original, dict) and isinstance(scrubbed, dict):
        return [
            problem
            for inner, value in original.items()
            for problem in _unchanged_sensitive_paths(
                value, scrubbed.get(inner), inner, f"{path}.{inner}"
            )
        ]
    if isinstance(original, list) and isinstance(scrubbed, list):
        return [
            problem
            for index, item in enumerate(original)
            if index < len(scrubbed)
            for problem in _unchanged_sensitive_paths(item, scrubbed[index], key, f"{path}[]")
        ]
    if (
        key
        and key not in KEEP
        and SENSITIVE_KEY_PATTERN.search(key)
        and isinstance(original, str)
        and original.strip()
        and original == scrubbed
        # A value drawn from a closed vocabulary can land on itself: a
        # two-letter state code has one-in-six odds against this pool, and the
        # collision means the scrubber ran, not that it skipped the field. What
        # would be a real finding is an unchanged value the scrubber could
        # never have produced.
        and original.strip() not in _SYNTHETIC_CORPUS
    ):
        return [path]
    return []


def main() -> int:
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SOURCE
    output = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_OUTPUT
    if not source.is_dir():
        raise SystemExit(
            f"Source directory not found: {source}\n"
            "Pass it explicitly: python scripts/deidentify_reference_dataset.py <SOURCE_DIR>"
        )
    output.mkdir(parents=True, exist_ok=True)

    for name in DOCUMENTS:
        path = source / name
        if not path.is_file():
            raise SystemExit(f"Expected source document is missing: {path}")
        original = json.loads(path.read_text(encoding="utf-8"))
        scrubbed = _harmonise_per_record(_harmonise_locations(_scrub(original)))
        checked = _assert_nothing_leaked(original, scrubbed, name)
        destination = output / name
        destination.write_text(
            json.dumps(scrubbed, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        count = len(scrubbed) if isinstance(scrubbed, list) else 1
        print(
            f"{name:28} {count:4} document(s)  {checked:5} sensitive values replaced and verified"
        )

    print(f"\nWrote de-identified dataset to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
