"""Give every warehouse an order names a `warehouseMaster` document.

`generate_seed_data.py::_generate_warehouses` mints one master document per
warehouse id its *own* generated orders carry. The sales source this platform
actually syncs (`salesInv`) names more warehouses than that run produced --
90 against the master's 24 -- so a return raised on an order from warehouse
`628` asked the bay agent about a warehouse no master, no bay table and no
graph node described, and the placement answered `WAREHOUSE_NOT_IN_GRAPH`.

This reads the distinct sell, ship-from and line inventory warehouse ids off
the sales source and writes a master document for each one the master lacks,
in the generator's own shape and carrying its `__seed` marker, with a random
generator seeded on the warehouse id so a re-run mints the same document.
Existing documents are never touched. Run
`seed_warehouse_bay_configuration.py` afterwards to project the bays into
`platform.bay_configuration`, which is what the platform reads.

Usage, from `backend/`: poetry run python scripts/backfill_warehouse_master.py [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import random
from datetime import UTC, datetime
from typing import Any

from pymongo import AsyncMongoClient
from seed_ferguson_idiom import PLACES, STREET_NAMES, STREET_TYPES

from return_platform.configuration.settings import Settings

SALES_COLLECTION = "salesInv"
MASTER_COLLECTION = "warehouseMaster"


def _document(identifier: str, generated_at: datetime) -> dict[str, Any]:
    rng = random.Random(f"warehouse-master:{identifier}")
    city, state, zip_prefix = rng.choice(PLACES)
    return {
        "_id": identifier,
        "warehouseId": identifier,
        "name": f"{city.title()} Distribution Center",
        "address": {
            "line1": f"{rng.randint(100, 9899)} {rng.choice(STREET_NAMES)} {rng.choice(STREET_TYPES)}",
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


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    arguments = parser.parse_args()
    settings = Settings()
    dsn = settings.mongo_dsn
    dsn = dsn.get_secret_value() if hasattr(dsn, "get_secret_value") else dsn
    client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(dsn, tz_aware=True)
    database = client[settings.source_mongo_database or "return_source"]

    referenced: set[str] = set()
    async for order in database[SALES_COLLECTION].find(
        {},
        {
            "salesHdrEventData.sellWhseId": 1,
            "salesHdrEventData.shipFromWhseId": 1,
            "salesLines.lineData.invenWhse": 1,
        },
    ):
        header = order.get("salesHdrEventData") or {}
        for key in ("sellWhseId", "shipFromWhseId"):
            value = header.get(key)
            if isinstance(value, str) and value:
                referenced.add(value)
        for line in order.get("salesLines") or []:
            value = (line.get("lineData") or {}).get("invenWhse")
            if isinstance(value, str) and value:
                referenced.add(value)

    existing = {
        str(document["warehouseId"])
        async for document in database[MASTER_COLLECTION].find({}, {"warehouseId": 1})
    }
    missing = sorted(referenced - existing)
    print(f"referenced={len(referenced)} existing={len(existing)} missing={len(missing)}")
    if arguments.dry_run:
        print("warehouse_master_backfill=DRY_RUN", missing)
        return 0
    generated_at = datetime.now(UTC)
    documents = [_document(identifier, generated_at) for identifier in missing]
    if documents:
        await database[MASTER_COLLECTION].insert_many(documents)
    print(f"warehouse_master_backfill=WRITTEN inserted={len(documents)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
