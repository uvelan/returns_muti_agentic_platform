"""Build the Neo4j knowledge graph from the loaded source documents.

The step nothing in this repository performed. Loading the source collections
leaves Neo4j empty, so the Order Discovery copilot searches a graph with no
nodes and truthfully reports finding nothing -- which reads as a broken agent
rather than a missing build. Every previous run of this was somebody typing it
into a REPL.

Uses the production `GraphSyncService` with the real resolved settings, so this
is the same code path the platform runs rather than a re-implementation that
can drift from it.

Usage:
    python scripts/build_knowledge_graph.py [MAX_RECORDS_PER_ASSET]
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from neo4j import AsyncGraphDatabase
from pymongo import AsyncMongoClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT / "src"))

# See `load_reference_dataset.py`: `PLATFORM_VAULT_TOKEN_FILE` is relative to the
# repository root, so Vault resolution depends on the working directory.
os.chdir(REPOSITORY_ROOT)

from return_platform.configuration.settings import Settings  # noqa: E402
from return_platform.data_platform.graph.sync_service import (  # noqa: E402
    GraphSyncRequest,
    GraphSyncService,
)
from return_platform.data_platform.schema_registry import load_schema_registry  # noqa: E402
from return_platform.secrets.runtime import resolve_runtime_settings_from_vault  # noqa: E402


async def main() -> int:
    max_records = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
    settings, _ = await resolve_runtime_settings_from_vault(
        Settings(),  # type: ignore[call-arg]
        resolve_ai_credentials=False,
    )

    platform_client: AsyncMongoClient = AsyncMongoClient(settings.mongo_dsn.get_secret_value())
    source_dsn = (
        settings.source_mongo_dsn.get_secret_value()
        if settings.source_mongo_dsn is not None
        else settings.mongo_dsn.get_secret_value()
    )
    source_client: AsyncMongoClient = AsyncMongoClient(source_dsn)
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value()),
    )
    try:
        service = GraphSyncService(
            platform_client=platform_client,
            source_client=source_client,
            driver=driver,
            settings=settings,
            registry=load_schema_registry(settings.schema_registry_path),
        )
        await service.ensure_indexes()
        run = await service.sync(
            GraphSyncRequest(maxRecordsPerAsset=max_records, applySchema=True),
            actor_id="reset-script",
        )
    finally:
        await driver.close()
        await platform_client.close()
        await source_client.close()

    print(f"status               {run.status}")
    print(f"schemaVersion        {run.schemaVersion}")
    print(f"sourceCounts         {run.sourceCounts}")
    print(f"nodeWrites           {run.nodeWrites}")
    print(f"relationshipWrites   {run.relationshipWrites}")
    print(f"constraintsApplied   {len(run.constraintsApplied)}")
    print(f"errorCode            {run.errorCode}")
    # Which generation this run built and cut over to, what it replaced, and how
    # far the cutover got. A full scan is a blue/green rebuild, so "the sync
    # wrote 6,000 nodes" and "a generation activated" are different claims --
    # and the second is the one that decides whether anything can read them.
    print(f"graphGenerationId    {run.graphGenerationId}")
    print(f"activeGenerationId   {run.activeGenerationId}")
    print(f"cutoverStage         {run.cutoverStage}")
    print(f"previousGeneration   {run.previousGenerationStatus}")
    print(f"failureReason        {run.failureReason}")

    # A sync that reports COMPLETED while writing nothing is the failure this
    # whole exercise kept producing: plausible seed data whose joins resolve to
    # nothing. Treat it as an error rather than letting the caller believe the
    # graph is ready.
    if run.status != "COMPLETED" or run.nodeWrites == 0 or run.relationshipWrites == 0:
        print(
            "\nGraph build did not produce a usable graph. "
            "A COMPLETED run with no relationships means the source documents "
            "loaded but their join fields do not match.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
