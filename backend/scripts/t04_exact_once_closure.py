"""T04 closure: one Support answer writes exactly one return record and N items.

UIAUDIT-010's other half. ADR-001 resolved as option B, so the *case workflow*
owns `dbo.return_record` and `dbo.return_record_item`; this proves that path
writes them exactly once, against identifiers that did not exist before the run.

**Three ways it could be wrong, and all three are exercised:**

  1. *Never* -- the answer is accepted and nothing durable appears. That is the
     P0 as the audit found it.
  2. *Twice* -- a retry with the same `supportEventId` creates a second record.
     The workflow takes the first notice and ignores later ones, so a duplicate
     here is a persistence bug rather than a workflow one.
  3. *Concurrently* -- two answers in flight at once race to the same insert.

**Fresh identifiers only.** The repair rules sequence historical repair *after*
this passes, and explicitly against identifiers isolated from the audit's own
records -- so nothing here reuses a case, ticket or reference that existed
before the run. Pass `--case-id` from a case this session created.

    python scripts/t04_exact_once_closure.py --case-id <fresh-case-id>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx
import pymssql

from return_platform.configuration.settings import Settings


def _sql_counts(settings: Settings, case_id: str) -> dict[str, int]:
    """What the authoritative store holds for this case, right now."""
    connection = pymssql.connect(
        server=settings.sqlserver_host,
        port=str(settings.sqlserver_port),
        user=settings.sqlserver_user,
        password=settings.sqlserver_password.get_secret_value(),
        database=getattr(settings, "sqlserver_database", "return_platform"),
    )
    try:
        with connection.cursor(as_dict=True) as cursor:
            cursor.execute(
                "SELECT COUNT(*) AS n FROM dbo.return_record WHERE case_id = %s", (case_id,)
            )
            records = int(cursor.fetchone()["n"])
            cursor.execute(
                "SELECT COUNT(*) AS n FROM dbo.return_record_item WHERE case_id = %s", (case_id,)
            )
            items = int(cursor.fetchone()["n"])
            cursor.execute("SELECT COUNT(*) AS n FROM dbo.return_tracking")
            tracking = int(cursor.fetchone()["n"])
        return {"records": records, "items": items, "trackingTotal": tracking}
    finally:
        connection.close()


async def _work_item_for(client: httpx.AsyncClient, case_id: str) -> str | None:
    response = await client.get("/api/v1/return-support/work-items")
    if response.status_code != 200:
        return None
    for item in response.json().get("data") or []:
        if item.get("caseId") == case_id:
            return str(item.get("workItemId") or item.get("id"))
    return None


async def _answer(
    client: httpx.AsyncClient,
    work_item_id: str,
    *,
    event_id: str,
    reference: str,
    lines: list[str],
) -> tuple[int, str]:
    response = await client.post(
        f"/api/v1/return-support/work-items/{work_item_id}/return-outcome",
        json={
            "supportEventId": event_id,
            "rejected": False,
            "records": [
                {
                    "returnReference": reference,
                    # Required: an RMA covering no lines is a return that cannot
                    # be received, credited or reconciled.
                    "orderLineReferences": lines,
                }
            ],
        },
    )
    return response.status_code, response.text[:300]


def _write_evidence(record: dict[str, Any]) -> Path:
    directory = Path(__file__).resolve().parents[2] / ".runtime" / "t04-closure"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-t04.json"
    path.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
    return path


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-id", required=True, help="a case created by this session")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--settle-seconds", type=float, default=20.0)
    arguments = parser.parse_args()

    settings = Settings()
    case_id = arguments.case_id
    # Fresh by construction: nothing derived from an existing reference.
    reference = f"RMA-T04-{uuid.uuid4().hex[:10].upper()}"
    event_id = f"t04-{uuid.uuid4()}"
    #: The lines the RMA covers. N items are expected for N lines -- that is the
    #: "one record, N items" half of the invariant, so it is compared against
    #: what was actually sent rather than against a literal.
    lines = ["LINE-1"]

    record: dict[str, Any] = {
        "startedAt": datetime.now(UTC).isoformat(),
        "caseId": case_id,
        "returnReference": reference,
        "supportEventId": event_id,
        "before": _sql_counts(settings, case_id),
    }

    async with httpx.AsyncClient(base_url=arguments.base_url, timeout=60.0) as client:
        work_item_id = await _work_item_for(client, case_id)
        record["workItemId"] = work_item_id
        if work_item_id is None:
            record["outcome"] = "NO_WORK_ITEM"
            print(json.dumps(record, indent=2, default=str))
            return 2

        # 1. the answer
        status, body = await _answer(
            client, work_item_id, event_id=event_id, reference=reference, lines=lines
        )
        record["firstAnswer"] = {"status": status, "body": body}

        # 2. the same answer again -- a retry, not a second notice
        status2, body2 = await _answer(
            client, work_item_id, event_id=event_id, reference=reference, lines=lines
        )
        record["retrySameEventId"] = {"status": status2, "body": body2}

        # 3. two at once
        concurrent = await asyncio.gather(
            _answer(client, work_item_id, event_id=event_id, reference=reference, lines=lines),
            _answer(client, work_item_id, event_id=event_id, reference=reference, lines=lines),
            return_exceptions=True,
        )
        record["concurrent"] = [str(result)[:200] for result in concurrent]

    # The workflow writes asynchronously, so give it a bounded moment.
    await asyncio.sleep(arguments.settle_seconds)
    record["after"] = _sql_counts(settings, case_id)
    record["completedAt"] = datetime.now(UTC).isoformat()

    created_records = record["after"]["records"] - record["before"]["records"]
    created_items = record["after"]["items"] - record["before"]["items"]
    record["recordsCreated"] = created_records
    record["itemsCreated"] = created_items
    record["trackingFabricated"] = (
        record["after"]["trackingTotal"] - record["before"]["trackingTotal"]
    )

    record["closed"] = (
        created_records == 1
        and created_items == len(lines)
        # Issuance must never invent a tracking observation.
        and record["trackingFabricated"] == 0
    )

    # Off the event loop: the linter is right that blocking filesystem calls do
    # not belong in an async frame, even for one small write.
    path = await asyncio.to_thread(_write_evidence, record)

    print(json.dumps(record, indent=2, default=str))
    print(f"\nevidence: {path}")
    return 0 if record["closed"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
