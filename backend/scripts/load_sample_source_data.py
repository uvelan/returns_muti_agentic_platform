#!/usr/bin/env python3
"""Load a directory of sample source documents into the source MongoDB.

For working against real Ferguson-shaped documents instead of the synthetic
`seed_e2e_data` manifest. The files are MongoDB extended JSON -- `$date`,
`$numberLong` -- so they are parsed with `bson.json_util` rather than `json`,
which would leave those as nested dicts and make every date field a document.

**The data directory is an argument and nothing is copied into the repository.**
These documents carry real customer names, addresses and contact details; where
they live is the operator's decision, not this script's, and a default that
committed them would make that decision silently. `deidentify_reference_dataset`
is the tool for the other direction.

Each file loads into the collection its own name implies, which is also what the
active schema's `object_ref.name` declares. Real extracts arrive named for a
reader rather than for this script -- `salesInv1.json` is a second batch of
`salesInv`, `shipment.shipmentInfo_limited.json` is a partial extract of
`shipmentInfo` -- so a batch digit, a `<subject>.` prefix and an extract
qualifier after `_` are all stripped, and the candidates are matched against
what the schema actually declares rather than assumed. A file whose collection
no source asset names is skipped and reported rather than loaded, because a
collection nothing reads is indistinguishable from a typo -- and silently
loading `shipment.shipmentInfo_limited` as its own collection would leave the
shipment entity reading an empty `shipmentInfo` with no error anywhere.

Idempotent: documents are replaced by `_id`, so re-running loads the same
dataset rather than a second copy.

Usage, from the repository root so the Vault token path resolves:

    PYTHONPATH=backend/src backend/.venv/Scripts/python.exe \\
        backend/scripts/load_sample_source_data.py <directory>
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path
from typing import Any

from bson import json_util
from pymongo import AsyncMongoClient

from return_platform.configuration.settings import Settings
from return_platform.dynamic_knowledge.config_loader import load_active_schema
from return_platform.secrets.runtime import resolve_runtime_settings_from_vault

_TRAILING_BATCH_DIGITS = re.compile(r"\d+$")


def _collection_candidates(path: Path) -> list[str]:
    """Every collection name a file name could plausibly mean, most literal first.

    `salesInv1` -> `salesInv` (a batch suffix is not a new collection);
    `shipment.shipmentInfo_limited` -> `shipmentInfo` (a subject prefix and an
    extract qualifier are not either). Candidates rather than one answer,
    because the caller matches them against the collections the schema declares
    -- stripping unconditionally would turn a genuine collection whose name
    happens to contain `_` into a shorter name nothing reads.
    """
    stem = path.stem
    candidates = [stem]
    if "." in stem:
        candidates.append(stem.rsplit(".", 1)[1])
    for candidate in list(candidates):
        if "_" in candidate:
            candidates.append(candidate.split("_", 1)[0])
    return [_TRAILING_BATCH_DIGITS.sub("", candidate) for candidate in candidates]


def _collection_for(path: Path, known: set[str]) -> str | None:
    """The declared collection this file belongs to, or None if none does."""
    for candidate in _collection_candidates(path):
        if candidate in known:
            return candidate
    return None


def _documents(path: Path) -> list[dict[str, Any]]:
    parsed = json_util.loads(path.read_text(encoding="utf-8"))
    if isinstance(parsed, dict):
        return [parsed]
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    raise SystemExit(f"{path.name}: expected an object or an array of objects")


async def _run(files: list[Path], directory: Path) -> None:
    settings, _ = await resolve_runtime_settings_from_vault(
        Settings(), resolve_ai_credentials=False
    )
    source_dsn = (
        settings.source_mongo_dsn.get_secret_value()
        if settings.source_mongo_dsn is not None
        else settings.mongo_dsn.get_secret_value()
    )
    client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(source_dsn)
    database = client[settings.source_mongo_database]

    schema = load_active_schema(settings.dynamic_knowledge_schema_path)
    known = {
        name
        for source in schema.sources.values()
        if source.object_ref.get("database") == settings.source_mongo_database
        and isinstance(name := source.object_ref.get("name"), str)
    }

    try:
        for path in files:
            collection_name = _collection_for(path, known)
            if collection_name is None:
                print(
                    f"  skip  {path.name}: no source asset reads any of "
                    f"{_collection_candidates(path)}"
                )
                continue
            documents = _documents(path)
            collection = database[collection_name]
            for document in documents:
                if "_id" not in document:
                    raise SystemExit(f"{path.name}: a document has no _id to replace on")
                await collection.replace_one({"_id": document["_id"]}, document, upsert=True)
            total = await collection.count_documents({})
            print(f"  load  {path.name}: {len(documents)} -> {collection_name} ({total} total)")

        print(f"\n{settings.source_mongo_database} now holds:")
        for name in sorted(known):
            print(f"  {name}: {await database[name].count_documents({})}")
    finally:
        await client.close()


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    directory = Path(sys.argv[1])
    if not directory.is_dir():
        raise SystemExit(f"not a directory: {directory}")
    # Globbed before the async boundary: a blocking filesystem walk inside the
    # event loop stalls every other task sharing it.
    files = sorted(directory.glob("*.json"))
    if not files:
        raise SystemExit(f"no .json files in {directory}")
    asyncio.run(_run(files, directory))


if __name__ == "__main__":
    main()
