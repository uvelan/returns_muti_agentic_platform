"""Live declared-versus-observed governance drift validation."""

import asyncio
import math
import sys
from pathlib import Path
from typing import Any, Final

from dotenv import load_dotenv
from pymongo import AsyncMongoClient

from return_platform.configuration.settings import Settings
from return_platform.data_governance import load_asset_catalog
from return_platform.data_governance.drift import (
    DriftReport,
    analyze_drift,
)
from return_platform.data_governance.inventory.contracts import (
    MongoDBInventory,
    SQLServerInventory,
)
from return_platform.data_governance.inventory.mongodb import (
    MongoDBInventoryError,
    get_mongodb_inventory,
)
from return_platform.data_governance.inventory.sqlserver import (
    SQLServerInventoryError,
    get_sqlserver_inventory,
)
from return_platform.resources import RuntimeResources

MongoDocument = dict[str, Any]

_EXIT_SUCCESS: Final = 0
_EXIT_OPERATIONAL_FAILURE: Final = 1
_EXIT_INCOMPLETE_EVIDENCE: Final = 2
_EXIT_DRIFT_DETECTED: Final = 3


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


def _create_mongodb_client(
    settings: Settings,
) -> AsyncMongoClient[MongoDocument]:
    """Create the temporary MongoDB client owned by this live runner."""

    connection_timeout_ms = math.ceil(
        settings.dependency_connect_timeout_seconds * 1_000,
    )

    return AsyncMongoClient(
        settings.mongo_dsn.get_secret_value(),
        serverSelectionTimeoutMS=connection_timeout_ms,
        connectTimeoutMS=connection_timeout_ms,
        uuidRepresentation="standard",
        appname="return-platform-live-drift-validation",
    )


async def _collect_sqlserver_inventory(
    *,
    settings: Settings,
    resources: RuntimeResources,
) -> SQLServerInventory | None:
    """Collect SQL Server evidence while preserving partial execution."""

    try:
        return await get_sqlserver_inventory(
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
        return None


async def _collect_mongodb_inventory(
    *,
    settings: Settings,
    client: AsyncMongoClient[MongoDocument],
) -> MongoDBInventory | None:
    """Collect MongoDB evidence while preserving partial execution."""

    try:
        return await get_mongodb_inventory(
            client=client,
            timeout_seconds=settings.probe_timeout_seconds,
        )
    except MongoDBInventoryError as error:
        print(
            f"MongoDB inventory failed: {error.code.value}: {error}",
            file=sys.stderr,
        )
        return None


def _print_report(
    report: DriftReport,
) -> None:
    """Print only safe governance and physical-identity evidence."""

    print("--- Live Governance Drift Report ---")
    print(f"Catalog version:        {report.catalog_version}")
    print(f"Analyzed at:            {report.analyzed_at.isoformat()}")
    print(f"Complete evidence:      {report.is_complete}")
    print(f"Drift free:             {report.is_drift_free}")
    print(f"Confirmed drift count:  {report.drift_count}")
    print(f"Not evaluated count:    {report.not_evaluated_count}")
    print(f"Total records:          {len(report.records)}")

    print(
        "SQL Server observed:    "
        + (
            report.sqlserver_observed_at.isoformat()
            if report.sqlserver_observed_at is not None
            else "NOT_EVALUATED"
        ),
    )
    print(
        "MongoDB observed:       "
        + (
            report.mongodb_observed_at.isoformat()
            if report.mongodb_observed_at is not None
            else "NOT_EVALUATED"
        ),
    )

    for record in report.records:
        namespace = record.namespace if record.namespace is not None else "-"
        asset_id = record.asset_id if record.asset_id is not None else "-"

        print(
            "Record: "
            f"store={record.store.value}; "
            f"database={record.database}; "
            f"namespace={namespace}; "
            f"object={record.object_name}; "
            f"kind={record.object_kind.value}; "
            f"state={record.drift_state.value}; "
            f"asset_id={asset_id}",
        )


def _resolve_exit_code(
    report: DriftReport,
) -> int:
    """Return a stable process status for automation and evidence capture."""

    if not report.is_complete:
        return _EXIT_INCOMPLETE_EVIDENCE

    if not report.is_drift_free:
        return _EXIT_DRIFT_DETECTED

    return _EXIT_SUCCESS


async def main() -> int:
    """Collect live inventories and compare them with the approved catalog."""

    _load_repository_environment()

    settings = Settings()
    loaded_catalog = load_asset_catalog(
        settings.catalog_path,
    )
    resources = RuntimeResources(
        settings=settings,
        catalog=loaded_catalog,
    )
    mongodb_client = _create_mongodb_client(
        settings,
    )

    try:
        sqlserver_inventory = await _collect_sqlserver_inventory(
            settings=settings,
            resources=resources,
        )
        mongodb_inventory = await _collect_mongodb_inventory(
            settings=settings,
            client=mongodb_client,
        )

        report = analyze_drift(
            loaded_catalog.catalog,
            sqlserver_inventory=sqlserver_inventory,
            mongodb_inventory=mongodb_inventory,
        )

        _print_report(
            report,
        )

        return _resolve_exit_code(
            report,
        )
    finally:
        await mongodb_client.close()
        resources.sql_manager.executor.shutdown(
            wait=True,
            cancel_futures=True,
        )


def run() -> int:
    """Execute the live validator without exposing internal exceptions."""

    try:
        return asyncio.run(
            main(),
        )
    except KeyboardInterrupt:
        print(
            "Live drift validation was cancelled.",
            file=sys.stderr,
        )
        return _EXIT_OPERATIONAL_FAILURE
    except Exception:
        print(
            "Live drift validation failed during initialization.",
            file=sys.stderr,
        )
        return _EXIT_OPERATIONAL_FAILURE


if __name__ == "__main__":
    raise SystemExit(run())
