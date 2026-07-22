"""Live validation entry point for MongoDB metadata inventory."""

import asyncio
import math
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pymongo import AsyncMongoClient

from return_platform.configuration.settings import Settings
from return_platform.data_governance.inventory.mongodb import (
    MongoDBInventoryError,
    get_mongodb_inventory,
)

MongoDocument = dict[str, Any]


def _load_repository_environment() -> None:
    """Load the repository-root environment without overriding shell values."""

    repository_root = Path(__file__).resolve().parent.parent
    environment_path = repository_root / ".env"

    if not environment_path.is_file():
        raise RuntimeError(
            "Repository-root .env file was not found.",
        )

    load_dotenv(
        dotenv_path=environment_path,
        override=False,
    )


async def main() -> int:
    """Collect and print safe live MongoDB inventory evidence."""

    _load_repository_environment()
    settings = Settings()

    connection_timeout_ms = math.ceil(
        settings.dependency_connect_timeout_seconds * 1_000,
    )

    client: AsyncMongoClient[MongoDocument] = AsyncMongoClient(
        settings.mongo_dsn.get_secret_value(),
        serverSelectionTimeoutMS=connection_timeout_ms,
        connectTimeoutMS=connection_timeout_ms,
        uuidRepresentation="standard",
        appname="return-platform-data-governance-live-validation",
    )

    try:
        inventory = await get_mongodb_inventory(
            client=client,
            timeout_seconds=settings.probe_timeout_seconds,
        )
    except MongoDBInventoryError as error:
        print(
            f"MongoDB inventory failed: {error.code.value}: {error}",
            file=sys.stderr,
        )
        return 1
    finally:
        await client.close()

    print("--- Live MongoDB Inventory ---")
    print(f"Database:            {inventory.database_name}")
    print(f"Observed at:         {inventory.observed_at.isoformat()}")
    print(f"Visible empty:       {inventory.is_empty}")
    print(f"Visible collections: {inventory.collection_count}")
    print(f"Visible indexes:     {inventory.index_count}")

    for collection in inventory.collections:
        print(
            "Collection: "
            f"{collection.name}; "
            f"approximate_documents="
            f"{collection.approximate_document_count}; "
            f"indexes={len(collection.indexes)}",
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
