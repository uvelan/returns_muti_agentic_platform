"""Project the seeded warehouse master into `platform.bay_configuration`.

## The gap this closes

`platform.bay_configuration` is the platform's only bay authority. The `warehouse`
and `bay` entities in the active schema are both bound to it
(`scripts/add_warehouse_bay_entities.py`), and `GraphWarehouseBayObservations`
answers "which bays does this warehouse have" by running a targeted read anchored
on `warehouse.warehouse_id` against that table and projecting the rows it returns
into Neo4j.

The table's only writer is the `MERGE` in
`configuration/sql_migrations/002_domain_models.sql`, which seeds six bays for a
single warehouse, `WH-CHENNAI-01`. Every generated order, meanwhile, carries a
numeric inventory warehouse id -- `686`, `1969`, `1305` and so on -- and
`generate_seed_data.py` mints a `warehouseMaster` document per distinct one, with
bays, capacity and handling flags.

Those two sets are disjoint, so on the seeded corpus `observe_eligible_bays` finds
no candidate for any real order and the Bay Assignment Agent can only ever answer
with an empty `eligibleBayIds`. Bay placement is not flaky there; it is
structurally impossible. `add_warehouse_bay_entities.py` says as much in its own
docstring -- *"There is no warehouse master anywhere this platform can reach"* --
which was true when it was written and is not true now.

This script is the missing half: it reads the warehouse master the generator
already writes and materialises its bays in the table the platform actually reads.
The on-demand sync carries them into the graph on the next warehouse observation,
so nothing here writes to Neo4j.

## What is copied and what is decided

Everything is taken from the source document or derived arithmetically from it:

| Column | Source |
|---|---|
| `bay_id` | `"<warehouseId>-<bay>"` -- the table's primary key is global, the source's bay names are not |
| `bay_name` | the bay entry verbatim |
| `warehouse_id` | `warehouseId` |
| `branch_id` | `warehouseId` -- the master states no separate branch, so warehouse and branch are the same reference here rather than an invented one |
| `active` | `1` -- the master lists only bays that exist |
| `priority` | the bay's ordinal in `bays`, so ranking is stable across runs |
| `supported_shipping_paths` | `[]` -- **the source states no restriction**, and `_permits` reads an empty list as exactly that. Inventing a path list here would silently exclude return methods the warehouse never refused |
| `supported_product_types` | `[]` -- same reasoning, read by `_supports_product` |
| `max_package_count`, `max_handling_unit_count`, `max_pallet_count` | `capacityUnits` divided evenly across the warehouse's bays, floored at 1 (`CK_bay_capacity` requires > 0) |
| `hazardous_allowed` | `acceptsHazmat` |
| `oversized_allowed` | `acceptsOversize` |

One field is a judgement rather than a copy: **`bay_type` is `HOLD` for every
projected bay.** The column is `NOT NULL` under `CK_bay_type` and the warehouse
master says nothing about bay purpose. `HOLD` is the honest answer for a return
staged into a warehouse pending disposition -- it is what `BAY-HOLD-01` is for --
and it is the one value that does not assert a shipping path the source never
mentioned.

## Safety

- **Additive and idempotent.** A `MERGE` on `bay_id`; re-running updates the same
  rows. Nothing is deleted.
- **The bootstrap rows are untouched.** `WH-CHENNAI-01` is not in the warehouse
  master, so its six bays are never matched.
- **Synthetic data stays labelled.** Only documents carrying the generator's
  `__seed` marker are read.

Usage, from the repository root:

    PYTHONPATH=backend/src backend/.venv/Scripts/python.exe \\
        backend/scripts/seed_warehouse_bay_configuration.py [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

import pymssql
from pymongo import AsyncMongoClient

from return_platform.configuration.settings import Settings

#: See the module docstring. The column is NOT NULL under `CK_bay_type` and the
#: warehouse master declares no bay purpose.
PROJECTED_BAY_TYPE = "HOLD"

#: An empty JSON array, which `_permits` and `_supports_product` in
#: `operations/warehouse/service.py` both read as "this bay states no
#: restriction". Not `NULL`: the columns are NOT NULL, and the distinction the
#: reader makes is between an empty list and a populated one.
NO_STATED_RESTRICTION = json.dumps([])


def _rows_for(warehouse: dict[str, Any]) -> list[dict[str, Any]]:
    """One `bay_configuration` row per bay the master lists for this warehouse."""
    warehouse_id = str(warehouse.get("warehouseId") or "").strip()
    bays = [str(bay).strip() for bay in (warehouse.get("bays") or []) if str(bay).strip()]
    if not warehouse_id or not bays:
        return []

    # `CK_bay_capacity` requires a positive figure, and a warehouse with more
    # bays than capacity units would otherwise produce zero.
    capacity_units = int(warehouse.get("capacityUnits") or 0)
    per_bay = max(1, capacity_units // len(bays))

    hazardous = bool(warehouse.get("acceptsHazmat"))
    oversized = bool(warehouse.get("acceptsOversize"))

    return [
        {
            "bay_id": f"{warehouse_id}-{bay}",
            "bay_name": bay,
            "warehouse_id": warehouse_id,
            "branch_id": warehouse_id,
            "bay_type": PROJECTED_BAY_TYPE,
            "active": 1,
            # Ordinal, not the loop index of a shuffled list: the master's order
            # is the only ranking the source expresses.
            "priority": (index + 1) * 10,
            "supported_shipping_paths": NO_STATED_RESTRICTION,
            "supported_product_types": NO_STATED_RESTRICTION,
            "max_package_count": per_bay,
            "max_handling_unit_count": per_bay,
            "max_pallet_count": per_bay,
            "hazardous_allowed": 1 if hazardous else 0,
            "oversized_allowed": 1 if oversized else 0,
        }
        for index, bay in enumerate(bays)
    ]


MERGE_STATEMENT = """
MERGE platform.bay_configuration AS target
USING (
    SELECT %(bay_id)s AS bay_id, %(bay_name)s AS bay_name, %(warehouse_id)s AS warehouse_id,
           %(branch_id)s AS branch_id, %(bay_type)s AS bay_type, %(active)s AS active,
           %(priority)s AS priority,
           %(supported_shipping_paths)s AS supported_shipping_paths,
           %(supported_product_types)s AS supported_product_types,
           %(max_package_count)s AS max_package_count,
           %(max_handling_unit_count)s AS max_handling_unit_count,
           %(max_pallet_count)s AS max_pallet_count,
           %(hazardous_allowed)s AS hazardous_allowed,
           %(oversized_allowed)s AS oversized_allowed
) AS source
ON target.bay_id = source.bay_id
WHEN MATCHED THEN UPDATE SET
    bay_name = source.bay_name,
    warehouse_id = source.warehouse_id,
    branch_id = source.branch_id,
    bay_type = source.bay_type,
    active = source.active,
    priority = source.priority,
    supported_shipping_paths = source.supported_shipping_paths,
    supported_product_types = source.supported_product_types,
    max_package_count = source.max_package_count,
    max_handling_unit_count = source.max_handling_unit_count,
    max_pallet_count = source.max_pallet_count,
    hazardous_allowed = source.hazardous_allowed,
    oversized_allowed = source.oversized_allowed,
    row_version_v2 = target.row_version_v2 + 1,
    updated_at = SYSUTCDATETIME()
WHEN NOT MATCHED THEN INSERT (
    bay_id, bay_name, warehouse_id, branch_id, bay_type, active, priority,
    supported_shipping_paths, supported_product_types, max_package_count,
    max_handling_unit_count, max_pallet_count, hazardous_allowed, oversized_allowed
) VALUES (
    source.bay_id, source.bay_name, source.warehouse_id, source.branch_id, source.bay_type,
    source.active, source.priority, source.supported_shipping_paths,
    source.supported_product_types, source.max_package_count,
    source.max_handling_unit_count, source.max_pallet_count,
    source.hazardous_allowed, source.oversized_allowed
);
"""


async def _read_warehouse_master(settings: Settings) -> list[dict[str, Any]]:
    dsn = settings.source_mongo_dsn
    if dsn is None:
        raise RuntimeError("PLATFORM_SOURCE_MONGO_DSN is not configured.")
    client: AsyncMongoClient = AsyncMongoClient(dsn.get_secret_value())
    try:
        collection = client[settings.source_mongo_database]["warehouseMaster"]
        return [document async for document in collection.find({"__seed": True})]
    finally:
        await client.close()


def _write(settings: Settings, rows: list[dict[str, Any]]) -> None:
    connection = pymssql.connect(
        server=settings.sqlserver_host,
        port=str(settings.sqlserver_port),
        user=settings.sqlserver_user,
        password=settings.sqlserver_password.get_secret_value(),
        database=settings.sqlserver_database,
        login_timeout=max(1, int(settings.dependency_connect_timeout_seconds)),
        timeout=max(1, int(settings.operation_timeout_seconds)),
        autocommit=False,
    )
    try:
        cursor = connection.cursor()
        for row in rows:
            cursor.execute(MERGE_STATEMENT, row)
        connection.commit()
    finally:
        connection.close()


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be written without touching SQL Server.",
    )
    arguments = parser.parse_args()

    settings = Settings()
    warehouses = await _read_warehouse_master(settings)
    if not warehouses:
        print("warehouse_bay_projection=SKIPPED reason=no-seeded-warehouse-master")
        return 0

    rows = [row for warehouse in warehouses for row in _rows_for(warehouse)]
    if not rows:
        print("warehouse_bay_projection=SKIPPED reason=no-bays-declared")
        return 0

    print(f"warehouses={len(warehouses)} bays={len(rows)}")
    if arguments.dry_run:
        for row in rows[:5]:
            print(f"  would merge {row['bay_id']} capacity={row['max_package_count']}")
        print("warehouse_bay_projection=DRY_RUN")
        return 0

    _write(settings, rows)
    print("warehouse_bay_projection=READY")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
