"""API routes for data browser."""

import asyncio
import re
from typing import Final

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from return_platform.data_console.api.auth import require_read_roles
from return_platform.resources import RuntimeResources
from return_platform.shared.contracts import APIResponse, ResponseMeta, WarningMeta
from return_platform.shared.governance import AssetCatalogEntry, DataStoreType

router = APIRouter(prefix="/data-console/v1/browser", tags=["Data Browser"])

_SOURCE: Final = "BROWSER"


class BrowserAsset(BaseModel):
    assetId: str
    sourceId: str
    engine: str
    name: str
    ownership: str
    capability: str
    recordCount: int | None
    schemaVersion: str


def _request_id(request: Request) -> str:
    value = getattr(request.state, "correlation_id", None)
    return value if isinstance(value, str) and value else "unknown"


def _response_meta(request: Request, warnings: list[WarningMeta] | None = None) -> ResponseMeta:
    return ResponseMeta(
        request_id=_request_id(request),
        partial=bool(warnings),
        warnings=tuple(warnings) if warnings else (),
    )


def _validate_sql_identifier(identifier: str | None) -> str:
    if not identifier:
        return ""
    if "]" in identifier or "[" in identifier or ";" in identifier or "--" in identifier:
        raise ValueError(f"Invalid characters in SQL identifier: {identifier}")
    if not re.match(r"^[a-zA-Z0-9_\-]+$", identifier):
        raise ValueError(f"Invalid characters in SQL identifier: {identifier}")
    return f"[{identifier}]"


@router.get("/assets", response_model=APIResponse[list[BrowserAsset]])
async def get_browser_assets(
    request: Request, user_id: str = Depends(require_read_roles)
) -> APIResponse[list[BrowserAsset]]:
    resources_value: object = getattr(request.app.state, "resources", None)
    if not isinstance(resources_value, RuntimeResources):
        raise HTTPException(status_code=500, detail="Resources unavailable")

    assets = []
    for entry in resources_value.catalog.catalog.assets:
        # Determine sourceId based on store type for demo purposes
        if entry.store == DataStoreType.SQLSERVER:
            source_id = "src-sql-omc"
        elif entry.store == DataStoreType.MONGODB:
            source_id = "src-mongo-returns"
        else:
            source_id = "src-unknown"

        # Determine name
        name = entry.object_name
        if entry.namespace:
            name = f"{entry.namespace}.{name}"

        assets.append(
            BrowserAsset(
                assetId=entry.asset_id,
                sourceId=source_id,
                engine=entry.store.value,
                name=name,
                ownership=entry.ownership.value,
                capability="READ_ONLY",
                recordCount=None,  # Expensive to count in a list view
                schemaVersion="1.0",
            )
        )

    return APIResponse(
        data=assets,
        meta=_response_meta(request),
        page={"next_cursor": None, "has_more": False, "page_size": len(assets) or 10},
    )


@router.get("/{engine}/{asset_id}/records")
async def get_records(
    request: Request, engine: str, asset_id: str, user_id: str = Depends(require_read_roles)
) -> APIResponse[list[dict]]:
    resources_value: object = getattr(request.app.state, "resources", None)
    if not isinstance(resources_value, RuntimeResources):
        raise HTTPException(status_code=500, detail="Resources unavailable")

    allowed_engines = {"SQLSERVER", "MONGODB", "NEO4J"}
    engine_upper = engine.upper()
    if engine_upper not in allowed_engines:
        raise HTTPException(status_code=400, detail="Unsupported engine")

    catalog_entry = next(
        (
            a
            for a in resources_value.catalog.catalog.assets
            if a.asset_id == asset_id and a.store.value == engine_upper
        ),
        None,
    )
    if not catalog_entry:
        raise HTTPException(status_code=404, detail="Asset not found in catalog")

    records = []
    warnings: list[WarningMeta] = []

    try:
        if engine_upper == "SQLSERVER":
            records = await asyncio.wait_for(
                _get_sql_records(resources_value, catalog_entry), timeout=10.0
            )
        elif engine_upper == "MONGODB":
            records = await asyncio.wait_for(
                _get_mongo_records(resources_value, catalog_entry), timeout=10.0
            )
        elif engine_upper == "NEO4J":
            records = await asyncio.wait_for(
                _get_neo4j_records(resources_value, catalog_entry), timeout=10.0
            )
    except TimeoutError:
        warnings.append(
            WarningMeta(source=_SOURCE, code="TIMEOUT", message="Query execution timed out")
        )
    except Exception:
        warnings.append(
            WarningMeta(
                source=_SOURCE,
                code="ENGINE_ERROR",
                message="Failed to fetch records from underlying engine",
            )
        )

    return APIResponse(
        data=records,
        meta=_response_meta(request, warnings=warnings),
        page={"next_cursor": None, "has_more": False, "page_size": len(records) or 10},
    )


async def _get_sql_records(resources: RuntimeResources, entry: AssetCatalogEntry) -> list[dict]:
    try:
        db = _validate_sql_identifier(entry.database)
        ns = _validate_sql_identifier(entry.namespace)
        obj = _validate_sql_identifier(entry.object_name)
    except ValueError as e:
        raise RuntimeError(f"Invalid identifiers: {e}") from e

    # Build safe query with TOP 50 (Bounded Pagination)
    query = f"SELECT TOP 50 * FROM {db}.{ns}.{obj}"

    def fetch():
        import pymssql

        with pymssql.connect(
            server=resources.settings.sqlserver_host,
            user=resources.settings.sqlserver_user,
            password=resources.settings.sqlserver_password.get_secret_value(),
            database=resources.settings.sqlserver_database,
            timeout=5,
            login_timeout=5,
        ) as conn:
            with conn.cursor() as cursor:
                cursor.execute(query)
                columns = [column[0] for column in cursor.description]
                rows = cursor.fetchall()
                result = []
                for idx, row in enumerate(rows):
                    data = dict(zip(columns, row, strict=False))
                    result.append(
                        {
                            "kind": "SQL_ROW",
                            "identity": {
                                "id": f"row-{idx}",
                                "assetId": entry.asset_id,
                                "engine": "SQL_SERVER",
                            },
                            "data": data,
                            "fields": {
                                col: {"type": "STRING", "redacted": False} for col in columns
                            },
                        }
                    )
                return result

    return await asyncio.to_thread(fetch)


async def _get_mongo_records(resources: RuntimeResources, entry: AssetCatalogEntry) -> list[dict]:
    if not resources.mongo:
        raise RuntimeError("MongoDB not configured")
    db = resources.mongo[entry.database]
    collection = db[entry.object_name]

    # Bounded fetch limit
    docs = await collection.find().limit(50).to_list(length=50)
    result = []
    for doc in docs:
        doc_id = str(doc.get("_id", "unknown"))
        if "_id" in doc:
            doc["_id"] = str(doc["_id"])

        result.append(
            {
                "kind": "MONGO_DOCUMENT",
                "identity": {"id": doc_id, "assetId": entry.asset_id, "engine": "MONGODB"},
                "data": doc,
                "redactedPaths": [],
            }
        )
    return result


async def _get_neo4j_records(resources: RuntimeResources, entry: AssetCatalogEntry) -> list[dict]:
    # Placeholder for Neo4j. Graph primarily defines via direct queries in graph_evidence
    return []


@router.get("/{engine}/{asset_id}/records/{record_id}")
async def get_record(
    request: Request,
    engine: str,
    asset_id: str,
    record_id: str,
    user_id: str = Depends(require_read_roles),
) -> APIResponse[dict]:
    response = await get_records(request, engine, asset_id, user_id)
    record = next((r for r in (response.data or []) if r["identity"]["id"] == record_id), None)
    if not record:
        raise HTTPException(status_code=404, detail="Record not found")

    return APIResponse(data=record, meta=response.meta, page=response.page)
