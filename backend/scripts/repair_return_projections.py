"""T19b: reclassify return projections that claim ISSUED over an empty store.

Dry run by default. An apply run must quote the digest of the plan it is
applying, which is printed by the dry run -- so approving one plan and applying
a different one is not possible without noticing.

    python scripts/repair_return_projections.py                    # dry run
    python scripts/repair_return_projections.py --apply <digest>   # apply
    python scripts/repair_return_projections.py --rollback <file>  # undo

The reasoning behind the reclassification is in
`return_platform.operations.repair.return_projections`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pymssql
from pymongo import AsyncMongoClient

from return_platform.configuration.settings import Settings
from return_platform.operations.repair.return_projections import (
    RepairPlan,
    apply_repair,
    plan_repair,
    rollback_manifest,
)

_RETURN_RECORDS = "return_records"


class MongoProjections:
    def __init__(self, database: Any) -> None:
        self._collection = database[_RETURN_RECORDS]

    async def find_issued_without_items(self) -> list[dict[str, Any]]:
        query = {
            "status": "ISSUED",
            "$or": [
                {"approvedItems": {"$size": 0}},
                {"approvedItems": {"$exists": False}},
                {"approvedItems": None},
            ],
        }
        return [document async for document in self._collection.find(query)]

    async def reclassify(
        self, return_record_id: str, *, status: str, marker: dict[str, Any]
    ) -> bool:
        # Filtered on the status the plan recorded, so a record that moved on
        # between the dry run and the apply is left alone.
        result = await self._collection.update_one(
            {"returnRecordId": return_record_id, "status": "ISSUED"},
            {"$set": {"status": status, **marker}},
        )
        return bool(result.modified_count)

    async def restore(self, return_record_id: str, status: str, unset: list[str]) -> bool:
        result = await self._collection.update_one(
            {"returnRecordId": return_record_id},
            {"$set": {"status": status}, "$unset": {field: "" for field in unset}},
        )
        return bool(result.modified_count)


class SqlAuthoritative:
    """`dbo.return_record` and `dbo.return_record_item`, counted."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _connect(self) -> Any:
        return pymssql.connect(
            server=self._settings.sqlserver_host,
            port=str(self._settings.sqlserver_port),
            user=self._settings.sqlserver_user,
            password=self._settings.sqlserver_password.get_secret_value(),
            database=getattr(self._settings, "sqlserver_database", "return_platform"),
        )

    def _count(self, table: str) -> int:
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(f"SELECT COUNT(*) FROM dbo.{table}")  # noqa: S608 - fixed names
                return int(cursor.fetchone()[0])
        finally:
            connection.close()

    async def count_records(self) -> int:
        return await asyncio.to_thread(self._count, "return_record")

    async def count_items(self) -> int:
        return await asyncio.to_thread(self._count, "return_record_item")


def _evidence_directory() -> Path:
    directory = Path(__file__).resolve().parents[2] / ".runtime" / "repair" / "T19b"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _write(name: str, payload: dict[str, Any]) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = _evidence_directory() / f"{stamp}-{name}.json"
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


async def _open() -> tuple[AsyncMongoClient, Any, Settings]:
    settings = Settings()
    dsn = settings.mongo_dsn
    client: AsyncMongoClient = AsyncMongoClient(
        dsn.get_secret_value() if hasattr(dsn, "get_secret_value") else str(dsn)
    )
    return client, client[settings.mongo_database], settings


async def _dry_run() -> int:
    client, database, settings = await _open()
    try:
        plan = await plan_repair(MongoProjections(database), SqlAuthoritative(settings))
        manifest = {**plan.manifest, "digest": plan.digest, "applicable": plan.applicable}
        path = _write("dry-run", manifest)
        rollback = _write("rollback", rollback_manifest(plan))

        print(json.dumps(manifest, indent=2, default=str))
        print(f"\nplan digest : {plan.digest}")
        print(f"manifest    : {path}")
        print(f"rollback    : {rollback}")
        if plan.refusal:
            print(f"\nREFUSED: {plan.refusal}")
            return 2
        if not plan.targets:
            print("\nNothing to repair.")
            return 0
        print(
            f"\nTo apply: python scripts/repair_return_projections.py --apply {plan.digest}"
        )
        return 0
    finally:
        await client.close()


async def _apply(digest: str) -> int:
    client, database, settings = await _open()
    try:
        projections = MongoProjections(database)
        plan: RepairPlan = await plan_repair(projections, SqlAuthoritative(settings))
        if plan.digest != digest:
            print(
                "The current plan does not match the digest given. The data has "
                "changed since the dry run; re-run it and review the new plan.\n"
                f"  approved : {digest}\n"
                f"  current  : {plan.digest}",
                file=sys.stderr,
            )
            return 2

        outcome = await apply_repair(plan, projections, approved_digest=digest)
        report = {
            "repairId": plan.manifest["repairId"],
            "manifestDigest": outcome.manifest_digest,
            "attempted": outcome.attempted,
            "reclassified": outcome.reclassified,
            "skipped": list(outcome.skipped),
            "complete": outcome.complete,
        }
        path = _write("applied", report)
        print(json.dumps(report, indent=2))
        print(f"\nreport: {path}")
        return 0 if outcome.complete else 1
    finally:
        await client.close()


async def _rollback(manifest_path: str) -> int:
    client, database, _ = await _open()
    try:
        manifest = json.loads(
            await asyncio.to_thread(Path(manifest_path).read_text, encoding="utf-8")
        )
        projections = MongoProjections(database)
        restored = 0
        for entry in manifest["restore"]:
            if await projections.restore(
                entry["returnRecordId"], entry["status"], list(manifest["unset"])
            ):
                restored += 1
        report = {
            "repairId": manifest["repairId"],
            "manifestDigest": manifest["manifestDigest"],
            "restored": restored,
            "of": len(manifest["restore"]),
        }
        print(json.dumps(report, indent=2))
        _write("rolled-back", report)
        return 0
    finally:
        await client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--apply", metavar="DIGEST", help="apply the plan with this digest")
    group.add_argument("--rollback", metavar="MANIFEST", help="undo using a rollback manifest")
    arguments = parser.parse_args()

    if arguments.apply:
        return asyncio.run(_apply(arguments.apply))
    if arguments.rollback:
        return asyncio.run(_rollback(arguments.rollback))
    return asyncio.run(_dry_run())


if __name__ == "__main__":
    raise SystemExit(main())
