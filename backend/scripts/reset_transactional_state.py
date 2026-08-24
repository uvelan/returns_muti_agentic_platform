"""Clear the two stores the dataset loader cannot reach, without touching containers.

`load_reference_dataset.py` already drops every Mongo database it owns and every
node, constraint and index in Neo4j. So a full data reset does not actually need
`docker compose down --volumes` -- it needs those two stores cleared plus the two
the loader has no business knowing about:

  * **SQL Server**, which holds the authoritative return records. Dropping the
    database is enough; `sqlserver-init` is a default compose service and
    recreates it empty on the next `up`, then the host start applies the
    migrations.
  * **Temporal**, whose running workflows outlive any database. A case workflow
    left running against dropped Mongo state is worse than no workflow at all:
    it wakes up, cannot find its case, and retries against a world that no longer
    contains the thing it is about.

Destroying the volumes clears both as a side effect, at the cost of SQL Server
re-initialising its system databases from scratch on every reset. This is the
same outcome without that wait.

    python backend/scripts/reset_transactional_state.py --apply
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pymssql
from temporalio.client import Client

from return_platform.configuration.settings import Settings


def _drop_sql_database(settings: Settings, apply: bool) -> str:
    """Drop the platform database. `sqlserver-init` recreates it empty."""
    database = getattr(settings, "sqlserver_database", "return_platform")
    connection = pymssql.connect(
        server=settings.sqlserver_host,
        port=str(settings.sqlserver_port),
        user=settings.sqlserver_user,
        password=settings.sqlserver_password.get_secret_value(),
        database="master",
        autocommit=True,
    )
    try:
        with connection.cursor(as_dict=True) as cursor:
            cursor.execute(
                "SELECT COUNT(*) AS n FROM sys.databases WHERE name = %s", (database,)
            )
            row = cursor.fetchone()
            if not row or not row["n"]:
                return f"{database}: already absent"
            if not apply:
                return f"{database}: would be dropped and recreated empty"
            # Single-user first: an open session from a worker that has not quite
            # died yet is enough to make DROP hang indefinitely.
            cursor.execute(
                f"ALTER DATABASE [{database}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE"
            )
            cursor.execute(f"DROP DATABASE [{database}]")
            # Recreated here rather than left to `sqlserver-init`. That container
            # is a one-shot with `restart: "no"`, so whether a later `up` re-runs
            # it depends on whether Compose considers it out of date -- and the
            # migrations connect straight to this database rather than creating
            # it, so "probably" is not good enough. Empty is all it needs to be.
            cursor.execute(f"CREATE DATABASE [{database}]")
            return f"{database}: dropped and recreated empty"
    finally:
        connection.close()


async def _terminate_workflows(settings: Settings, apply: bool) -> str:
    client = await Client.connect(
        str(settings.temporal_target),
        namespace=getattr(settings, "temporal_namespace", "default"),
    )
    running = [
        description
        async for description in client.list_workflows('ExecutionStatus="Running"')
    ]
    if not running:
        return "temporal: nothing running"
    if not apply:
        return f"temporal: {len(running)} running workflow(s) would be terminated"
    terminated = 0
    for description in running:
        handle = client.get_workflow_handle(description.id, run_id=description.run_id)
        try:
            await handle.terminate(reason="data reset: the state this workflow is about is gone")
            terminated += 1
        except Exception:  # noqa: BLE001, PERF203 - one refusal must not stop the rest
            continue
    return f"temporal: terminated {terminated} of {len(running)} running workflow(s)"


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    if not (arguments.apply or arguments.check):
        parser.error("pass --check or --apply")

    settings = Settings()  # type: ignore[call-arg]
    failures = 0

    for label, task in (
        ("sqlserver", lambda: asyncio.to_thread(_drop_sql_database, settings, arguments.apply)),
        ("temporal", lambda: _terminate_workflows(settings, arguments.apply)),
    ):
        try:
            print(f"[reset-state] {await task()}")
        except Exception as exc:  # noqa: BLE001 - report each store independently
            print(f"[reset-state] {label}: FAILED {type(exc).__name__}: {exc}", file=sys.stderr)
            failures += 1

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
