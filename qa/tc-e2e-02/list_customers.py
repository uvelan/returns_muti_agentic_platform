"""List candidate customers/orders for TC-E2E-02 runs, excluding names already used this cycle."""

from __future__ import annotations

import asyncio
import json

from pymongo import AsyncMongoClient

from return_platform.configuration.settings import Settings

USED = {
    "TAYLOR", "ZORVATH PIPEWORKS", "DUANE", "ALVARO", "ALVARA", "MELGO", "BOYLE",
    "DANE", "FOSTER", "NANCY DOYLE", "CHARLOTTE", "NASH", "GARDEN",
}
USED_ORDERS = {"CW273354", "CA273603", "CQ800002", "CQ363350", "CG800991", "CO803471", "ZZ999999"}


async def main() -> None:
    settings = Settings()
    dsn = (
        settings.source_mongo_dsn.get_secret_value()
        if settings.source_mongo_dsn is not None
        else settings.mongo_dsn.get_secret_value()
    )
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(dsn)
    db = client[settings.source_mongo_database or settings.mongo_database]
    names = await db.list_collection_names()
    print("collections:", sorted(names))
    coll = None
    for candidate in ("salesInv", "sales_inv", "salesinv"):
        if candidate in names:
            coll = db[candidate]
            break
    if coll is None:
        print("no salesInv-like collection found")
        return
    sample = await coll.find_one({})
    print("sample keys:", sorted((sample or {}).keys()))
    print("inner keys:", sorted(((sample or {}).get("salesInv") or {}).keys()))
    cursor = await coll.aggregate(
        [
            {"$group": {
                "_id": {"c": "$salesInv.customer_name", "a": "$salesInv.account_id"},
                "orders": {"$addToSet": "$salesInv.sales_order_number"},
                "n": {"$sum": 1},
            }},
            {"$sort": {"n": -1}},
            {"$limit": 60},
        ]
    )
    rows = await cursor.to_list(length=60)
    fresh = []
    for row in rows:
        name = str((row["_id"] or {}).get("c") or "")
        if not name or any(u in name.upper() for u in USED):
            continue
        orders = [o for o in row.get("orders", []) if o and o not in USED_ORDERS]
        if orders:
            fresh.append({"customer": name, "account": (row["_id"] or {}).get("a"),
                          "orders": sorted(orders)[:5], "lines": row["n"]})
    print(json.dumps(fresh[:20], indent=1, default=str))
    await client.close()


asyncio.run(main())
