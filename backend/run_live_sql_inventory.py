"""Live validation entry point for SQL Server metadata inventory."""

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

from return_platform.configuration.settings import Settings
from return_platform.data_governance import load_asset_catalog
from return_platform.data_governance.inventory.sqlserver import (
    SQLServerInventoryError,
    get_sqlserver_inventory,
)
from return_platform.resources import RuntimeResources


def _load_repository_environment() -> None:
    """Load the repository-root environment without overriding shell values."""

    repository_root = Path(__file__).resolve().parent.parent
    environment_path = repository_root / ".env"

    if not environment_path.is_file():
        raise RuntimeError("Repository-root .env file was not found.")

    load_dotenv(
        dotenv_path=environment_path,
        override=False,
    )


async def main() -> int:
    """Collect and print safe live SQL Server inventory evidence."""

    _load_repository_environment()

    settings = Settings()
    catalog = load_asset_catalog(settings.catalog_path)
    resources = RuntimeResources(
        settings=settings,
        catalog=catalog,
    )

    try:
        inventory = await get_sqlserver_inventory(
            host=settings.sqlserver_host,
            port=settings.sqlserver_port,
            user=settings.sqlserver_user,
            password=settings.sqlserver_password.get_secret_value(),
            database=settings.sqlserver_database,
            timeout_seconds=settings.probe_timeout_seconds,
            executor=resources.sql_manager.executor,
        )
    except SQLServerInventoryError as error:
        print(
            f"SQL Server inventory failed: {error.code.value}: {error}",
            file=sys.stderr,
        )
        return 1
    finally:
        resources.sql_manager.executor.shutdown(
            wait=True,
            cancel_futures=True,
        )

    print("--- Live SQL Server Inventory ---")
    print(f"Database:       {inventory.database_name}")
    print(f"Observed at:    {inventory.observed_at.isoformat()}")
    print(f"Visible empty:  {inventory.is_empty}")
    print(f"Visible tables: {inventory.table_count}")
    print(f"Visible views:  {inventory.view_count}")
    print(f"Schemas:        {len(inventory.schemas)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
