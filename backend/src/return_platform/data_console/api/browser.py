"""Read-only governed browser for registry-backed MongoDB and SQL Server assets."""

from __future__ import annotations

import asyncio
import copy
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any, cast

import pymssql
from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict

from return_platform.data_console.api.auth import require_read_roles
from return_platform.data_platform.schema_registry import DataAssetSchema, SchemaRegistry
from return_platform.resources import RuntimeResources
from return_platform.shared.contracts import APIResponse, PageMeta, ResponseMeta

router = APIRouter(prefix="/data-console/v1/browser", tags=["Data Browser"])

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SENSITIVE_FIELD = re.compile(
    r"(^|_)(password|secret|token|api_?key|authorization|email|phone)(_|$)",
    re.IGNORECASE,
)


class BrowserModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BrowserAsset(BrowserModel):
    assetId: str
    sourceId: str
    engine: str
    name: str
    ownership: str
    capability: str
    recordCount: int | None
    schemaVersion: str


def _resources(request: Request) -> RuntimeResources:
    resources = getattr(request.app.state, "resources", None)
    if not isinstance(resources, RuntimeResources) or resources.schema_registry is None:
        raise HTTPException(status_code=503, detail="Schema and runtime resources are unavailable.")
    return resources


def _request_id(request: Request) -> str:
    value = getattr(request.state, "correlation_id", None)
    return value if isinstance(value, str) and value else "unknown"


def _response_meta(request: Request) -> ResponseMeta:
    return ResponseMeta(request_id=_request_id(request))


def _identifier(value: str | None) -> str:
    if value is None or not _SAFE_IDENTIFIER.fullmatch(value):
        raise ValueError("Unsafe database identifier in schema registry.")
    return f"[{value}]"


def _frontend_engine(asset: DataAssetSchema) -> str:
    return "SQL_SERVER" if asset.engine == "SQLSERVER" else "MONGODB"


def _ownership(asset: DataAssetSchema) -> str:
    if not asset.ownership:
        return "INTERNAL"
    return {
        "SOURCE_SYSTEM": "AUTHORITATIVE",
        "PLATFORM_OWNED": "INTERNAL",
        "DERIVED_PROJECTION": "DERIVED",
    }[asset.ownership]


def _source_id(resources: RuntimeResources, asset: DataAssetSchema) -> str:
    if asset.engine == "SQLSERVER":
        return "sqlserver"
    if asset.database == resources.settings.source_mongo_database:
        return "source-mongodb"
    return "platform-mongodb"


def _resolve_asset(resources: RuntimeResources, engine: str, asset_id: str) -> DataAssetSchema:
    registry = cast(SchemaRegistry, resources.schema_registry)
    try:
        asset = registry.asset(asset_id)
    except KeyError as error:
        raise HTTPException(
            status_code=404, detail="Asset not found in schema registry."
        ) from error
    normalized = engine.upper()
    if normalized == "SQLSERVER":
        normalized = "SQL_SERVER"
    if normalized != _frontend_engine(asset):
        raise HTTPException(status_code=404, detail="Asset engine does not match the route.")
    return asset


def _nested_get(document: dict[str, Any], path: str) -> Any:
    current: Any = document
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, bytes):
        return f"<binary:{len(value)} bytes>"
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _redact_document(document: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    redacted = copy.deepcopy(document)
    paths: list[str] = []

    def walk(value: Any, prefix: str = "") -> None:
        if isinstance(value, dict):
            for key, item in list(value.items()):
                path = f"{prefix}.{key}" if prefix else str(key)
                if _SENSITIVE_FIELD.search(str(key)):
                    value[key] = "[REDACTED]"
                    paths.append(path)
                else:
                    walk(item, path)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{prefix}[{index}]")

    walk(redacted)
    return cast(dict[str, Any], _json_value(redacted)), paths


def _display_type(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "BOOLEAN"
    if isinstance(value, (int, float, Decimal)):
        return "NUMBER"
    if isinstance(value, (datetime, date)):
        return "DATETIME"
    if isinstance(value, dict):
        return "OBJECT"
    if isinstance(value, (list, tuple)):
        return "ARRAY"
    if isinstance(value, bytes):
        return "BINARY"
    return "STRING"


def _sql_record(asset: DataAssetSchema, row: dict[str, Any]) -> dict[str, Any]:
    key_field = next(field.name for field in asset.fields if field.key)
    identity = str(row.get(key_field, "unknown"))
    data: dict[str, Any] = {}
    fields: dict[str, dict[str, object]] = {}
    for name, value in row.items():
        redacted = bool(_SENSITIVE_FIELD.search(name))
        data[name] = "[REDACTED]" if redacted else _json_value(value)
        fields[name] = {"type": _display_type(value), "redacted": redacted}
    return {
        "kind": "SQL_ROW",
        "identity": {
            "id": identity,
            "assetId": asset.asset_id,
            "engine": "SQL_SERVER",
        },
        "data": data,
        "fields": fields,
    }


def _mongo_record(asset: DataAssetSchema, document: dict[str, Any]) -> dict[str, Any]:
    key_field = next(field.name for field in asset.fields if field.key)
    identity = str(_nested_get(document, key_field) or document.get("_id", "unknown"))
    data, redacted_paths = _redact_document(document)
    return {
        "kind": "MONGO_DOCUMENT",
        "identity": {
            "id": identity,
            "assetId": asset.asset_id,
            "engine": "MONGODB",
        },
        "data": data,
        "redactedPaths": redacted_paths,
    }


@router.get("/assets", response_model=APIResponse[list[BrowserAsset]])
async def get_browser_assets(
    request: Request,
    _actor: str = Depends(require_read_roles),
) -> APIResponse[list[BrowserAsset]]:
    resources = _resources(request)
    registry = cast(SchemaRegistry, resources.schema_registry)
    assets = [
        BrowserAsset(
            assetId=asset.asset_id,
            sourceId=_source_id(resources, asset),
            engine=_frontend_engine(asset),
            name=f"{asset.namespace}.{asset.name}" if asset.namespace else asset.name,
            ownership=_ownership(asset),
            capability="READ_ONLY",
            recordCount=None,
            schemaVersion=registry.schema_version,
        )
        for asset in registry.assets
    ]
    return APIResponse(
        data=assets,
        meta=_response_meta(request),
        page=PageMeta(next_cursor=None, has_more=False, page_size=len(assets)),
    )


@router.get("/{engine}/{asset_id}/records")
async def get_records(
    engine: str,
    asset_id: str,
    request: Request,
    page_index: int = Query(default=0, ge=0, le=10_000),
    page_size: int = Query(default=25, ge=1, le=100),
    _actor: str = Depends(require_read_roles),
) -> APIResponse[list[dict[str, Any]]]:
    resources = _resources(request)
    asset = _resolve_asset(resources, engine, asset_id)
    if asset.engine == "SQLSERVER":
        records = await asyncio.wait_for(
            _get_sql_records(resources, asset, page_index, page_size + 1),
            timeout=resources.settings.probe_timeout_seconds * 5,
        )
    else:
        records = await asyncio.wait_for(
            _get_mongo_records(resources, asset, page_index, page_size + 1),
            timeout=resources.settings.probe_timeout_seconds * 5,
        )
    has_more = len(records) > page_size
    visible = records[:page_size]
    return APIResponse(
        data=visible,
        meta=_response_meta(request),
        page=PageMeta(
            next_cursor=str(page_index + 1) if has_more else None,
            has_more=has_more,
            page_size=page_size,
        ),
    )


async def _get_sql_records(
    resources: RuntimeResources,
    asset: DataAssetSchema,
    page_index: int,
    limit: int,
) -> list[dict[str, Any]]:
    if asset.database != resources.settings.sqlserver_database:
        raise RuntimeError("Cross-database browsing is not allowed.")
    namespace = _identifier(asset.namespace)
    table = _identifier(asset.name)
    key_field = _identifier(next(field.name for field in asset.fields if field.key))
    offset = page_index * max(1, limit - 1)
    query = (
        f"SELECT * FROM {namespace}.{table} ORDER BY {key_field} "
        "OFFSET %s ROWS FETCH NEXT %s ROWS ONLY"
    )

    def fetch() -> list[dict[str, Any]]:
        with pymssql.connect(
            server=resources.settings.sqlserver_host,
            port=str(resources.settings.sqlserver_port),
            user=resources.settings.sqlserver_user,
            password=resources.settings.sqlserver_password.get_secret_value(),
            database=resources.settings.sqlserver_database,
            as_dict=True,
            timeout=5,
            login_timeout=5,
        ) as connection:
            with connection.cursor(as_dict=True) as cursor:
                cursor.execute(query, (offset, limit))
                return [_sql_record(asset, cast(dict[str, Any], row)) for row in cursor.fetchall()]

    return await asyncio.to_thread(fetch)


def _mongo_database(resources: RuntimeResources, asset: DataAssetSchema) -> Any:
    if asset.database == resources.settings.source_mongo_database:
        if resources.source_mongo is None:
            raise RuntimeError("Source MongoDB is not initialized.")
        return resources.source_mongo[asset.database]
    if asset.database == resources.settings.mongo_database:
        if resources.mongo is None:
            raise RuntimeError("Platform MongoDB is not initialized.")
        return resources.mongo[asset.database]
    raise RuntimeError("Unknown MongoDB database in schema registry.")


async def _get_mongo_records(
    resources: RuntimeResources,
    asset: DataAssetSchema,
    page_index: int,
    limit: int,
) -> list[dict[str, Any]]:
    collection = _mongo_database(resources, asset)[asset.name]
    documents = (
        await collection.find({})
        .skip(page_index * max(1, limit - 1))
        .limit(limit)
        .to_list(length=limit)
    )
    return [_mongo_record(asset, cast(dict[str, Any], document)) for document in documents]


async def _get_sql_record(
    resources: RuntimeResources,
    asset: DataAssetSchema,
    record_id: str,
) -> dict[str, Any] | None:
    if asset.database != resources.settings.sqlserver_database:
        raise RuntimeError("Cross-database browsing is not allowed.")
    namespace = _identifier(asset.namespace)
    table = _identifier(asset.name)
    key_name = next(field.name for field in asset.fields if field.key)
    key_field = _identifier(key_name)
    query = f"SELECT TOP 1 * FROM {namespace}.{table} WHERE {key_field}=%s"

    def fetch() -> dict[str, Any] | None:
        with pymssql.connect(
            server=resources.settings.sqlserver_host,
            port=str(resources.settings.sqlserver_port),
            user=resources.settings.sqlserver_user,
            password=resources.settings.sqlserver_password.get_secret_value(),
            database=resources.settings.sqlserver_database,
            as_dict=True,
            timeout=5,
            login_timeout=5,
        ) as connection:
            with connection.cursor(as_dict=True) as cursor:
                cursor.execute(query, (record_id,))
                row = cursor.fetchone()
                return _sql_record(asset, cast(dict[str, Any], row)) if row else None

    return await asyncio.to_thread(fetch)


async def _get_mongo_record(
    resources: RuntimeResources,
    asset: DataAssetSchema,
    record_id: str,
) -> dict[str, Any] | None:
    collection = _mongo_database(resources, asset)[asset.name]
    key_field = next(field.name for field in asset.fields if field.key)
    candidates: list[Any] = [record_id]
    if key_field == "_id":
        try:
            candidates.insert(0, ObjectId(record_id))
        except InvalidId:
            pass
    document = await collection.find_one({key_field: {"$in": candidates}})
    if document is None:
        return None
    return _mongo_record(asset, cast(dict[str, Any], document))


@router.get("/{engine}/{asset_id}/records/{record_id}")
async def get_record(
    engine: str,
    asset_id: str,
    record_id: str,
    request: Request,
    _actor: str = Depends(require_read_roles),
) -> APIResponse[dict[str, Any]]:
    resources = _resources(request)
    asset = _resolve_asset(resources, engine, asset_id)
    if asset.engine == "SQLSERVER":
        record = await _get_sql_record(resources, asset, record_id)
    else:
        record = await _get_mongo_record(resources, asset, record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Record not found.")
    return APIResponse(data=record, meta=_response_meta(request))
