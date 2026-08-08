"""Standalone v2 data-source configuration, validation, schema, and preview APIs."""

from __future__ import annotations

import asyncio
import re
import uuid
from datetime import UTC, datetime
from typing import Any, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pymongo.asynchronous.collection import AsyncCollection

from return_platform.data_console.api.auth import require_admin_roles, require_read_roles
from return_platform.data_console.api.browser import get_mongo_records, get_sql_records
from return_platform.data_console.api.runtime_validation import (
    DataSourceValidateAndStageRequest,
    validate_and_stage_data_source,
)
from return_platform.data_console.infrastructure.probes import (
    probe_mongodb,
    probe_neo4j,
    probe_source_mongodb,
    probe_sqlserver,
)
from return_platform.data_platform.schema_registry import DataAssetSchema, SchemaRegistry
from return_platform.resources import RuntimeResources
from return_platform.secrets.vault import SecretResolver, parse_secret_reference
from return_platform.shared.contracts import (
    APIResponse,
    DependencyProbeResult,
    DependencyStatus,
    PageMeta,
    ResponseMeta,
)

router = APIRouter(prefix="/api/v2/config/data-sources", tags=["Data Source Configuration v2"])

_COLLECTION = "data_source_configurations_v2"
_SOURCE_ID = re.compile(r"^[a-z][a-z0-9-]{1,63}$")


class DataSourceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DataSourceWrite(DataSourceModel):
    name: str = Field(min_length=2, max_length=128)
    description: str = Field(default="", max_length=500)
    sourceType: Literal["MONGODB", "SQLSERVER", "NEO4J"]
    accessMode: Literal["READ_ONLY", "READ_WRITE"] = "READ_ONLY"
    host: str | None = Field(default=None, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65_535)
    uri: str | None = Field(default=None, max_length=1024)
    database: str = Field(min_length=1, max_length=128)
    username: str | None = Field(default=None, max_length=256)
    credentialVaultReference: str = Field(min_length=16, max_length=768)
    credentialKind: Literal["DSN", "PASSWORD"]
    credential: SecretStr | None = Field(default=None, repr=False, exclude=True)
    sslEnabled: bool = True

    @field_validator("credentialVaultReference")
    @classmethod
    def validate_vault_reference(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized.startswith("vault://secret/production/data-sources/") or "#" not in normalized:
            raise ValueError(
                "credentialVaultReference must use the production data-sources Vault path"
            )
        return normalized

    @model_validator(mode="after")
    def validate_connection_shape(self) -> DataSourceWrite:
        if self.sourceType == "MONGODB":
            if self.credentialKind != "DSN":
                raise ValueError("MongoDB sources require credentialKind=DSN")
        elif self.sourceType == "SQLSERVER":
            if not self.host or not self.port or not self.username:
                raise ValueError("SQL Server sources require host, port, and username")
            if self.credentialKind != "PASSWORD":
                raise ValueError("SQL Server sources require credentialKind=PASSWORD")
        elif not self.uri or not self.username or self.credentialKind != "PASSWORD":
            raise ValueError("Neo4j sources require URI, username, and password")
        return self


class DataSourceView(DataSourceWrite):
    id: str
    status: Literal["VALID", "INVALID", "NOT_VALIDATED", "VALIDATING"]
    managed: bool
    createdAt: datetime
    updatedAt: datetime
    lastValidatedAt: datetime | None = None
    validationMessage: str | None = None


class ValidationCheck(DataSourceModel):
    name: str
    status: Literal["PASSED", "FAILED"]
    message: str


class ValidationResult(DataSourceModel):
    sourceId: str
    status: Literal["VALID", "INVALID"]
    checkedAt: datetime
    latencyMs: int | None
    checks: list[ValidationCheck]


class ValidateDataSourceRequest(DataSourceModel):
    credential: SecretStr | None = Field(default=None, repr=False)


class CredentialRevealView(DataSourceModel):
    credential: str


class SchemaFieldView(DataSourceModel):
    name: str
    type: str
    required: bool
    key: bool
    description: str


class SchemaDatasetView(DataSourceModel):
    id: str
    name: str
    namespace: str | None
    kind: Literal["TABLE", "COLLECTION", "NODE"]
    description: str
    fields: list[SchemaFieldView]


class SchemaExplorerView(DataSourceModel):
    sourceId: str
    schemaVersion: str
    datasets: list[SchemaDatasetView]


class DataPreviewView(DataSourceModel):
    sourceId: str
    datasetId: str
    columns: list[str]
    rows: list[dict[str, Any]]
    redacted: bool = True


def _resources(request: Request) -> RuntimeResources:
    resources = getattr(request.app.state, "resources", None)
    if not isinstance(resources, RuntimeResources) or resources.schema_registry is None:
        raise HTTPException(status_code=503, detail="Data-source resources are unavailable.")
    return resources


def _meta(request: Request) -> ResponseMeta:
    request_id = getattr(request.state, "correlation_id", "unknown")
    return ResponseMeta(request_id=request_id if isinstance(request_id, str) else "unknown")


def _now() -> datetime:
    return datetime.now(UTC)


def _builtins(resources: RuntimeResources) -> dict[str, DataSourceView]:
    now = _now()
    settings = resources.settings
    common = {
        "description": "Platform-managed connection",
        "sslEnabled": False,
        "status": "NOT_VALIDATED",
        "managed": True,
        "createdAt": now,
        "updatedAt": now,
        "lastValidatedAt": None,
        "validationMessage": None,
    }
    return {
        "source-mongodb": DataSourceView(
            **common,
            id="source-mongodb",
            name="Source MongoDB",
            sourceType="MONGODB",
            accessMode="READ_ONLY",
            database=settings.source_mongo_database,
            credentialVaultReference="vault://secret/production/data-sources/mongodb#dsn",
            credentialKind="DSN",
        ),
        "platform-mongodb": DataSourceView(
            **common,
            id="platform-mongodb",
            name="Platform MongoDB",
            sourceType="MONGODB",
            accessMode="READ_WRITE",
            database=settings.mongo_database,
            credentialVaultReference="vault://secret/production/data-sources/mongodb#dsn",
            credentialKind="DSN",
        ),
        "sqlserver": DataSourceView(
            **common,
            id="sqlserver",
            name="Return Business State SQL Server",
            sourceType="SQLSERVER",
            accessMode="READ_ONLY",
            host=settings.sqlserver_host,
            port=settings.sqlserver_port,
            database=settings.sqlserver_database,
            username=settings.sqlserver_user,
            credentialVaultReference="vault://secret/production/data-sources/sqlserver#password",
            credentialKind="PASSWORD",
        ),
        "neo4j": DataSourceView(
            **common,
            id="neo4j",
            name="Neo4j Graph Projection",
            sourceType="NEO4J",
            accessMode="READ_ONLY",
            uri=settings.neo4j_uri,
            database=settings.neo4j_database,
            username=settings.neo4j_user,
            credentialVaultReference="vault://secret/production/data-sources/neo4j#password",
            credentialKind="PASSWORD",
        ),
    }


def _collection(resources: RuntimeResources) -> AsyncCollection[dict[str, Any]]:
    if resources.mongo is None:
        raise HTTPException(status_code=503, detail="Configuration storage is unavailable.")
    return resources.mongo[resources.settings.mongo_database][_COLLECTION]


def _secret_resolver(request: Request) -> SecretResolver:
    resolver = getattr(request.app.state, "secret_resolver", None)
    if not isinstance(resolver, SecretResolver):
        raise HTTPException(status_code=503, detail="Vault secret storage is unavailable.")
    return resolver


def _generated_vault_reference(source_id: str, credential_kind: str) -> str:
    secret_key = "dsn" if credential_kind == "DSN" else "password"
    return f"vault://secret/production/data-sources/{source_id}#{secret_key}"


async def _store_credential(
    request: Request,
    vault_reference: str,
    credential: SecretStr,
) -> None:
    resolver = _secret_resolver(request)
    reference = parse_secret_reference(vault_reference)
    current = await resolver.get_secret(reference)
    expected_version = current.secret_version if current is not None else None
    await resolver.put_secret(
        reference.model_copy(update={"version": expected_version}),
        credential.get_secret_value(),
    )


async def _load_credential(request: Request, vault_reference: str) -> SecretStr:
    resolved = await _secret_resolver(request).get_secret(
        parse_secret_reference(vault_reference)
    )
    if resolved is None:
        raise HTTPException(status_code=422, detail="A credential has not been saved yet.")
    return SecretStr(resolved.value)


def _document_to_view(document: dict[str, Any]) -> DataSourceView:
    payload = {
        key: value
        for key, value in document.items()
        if key not in {"_id", "_deleted"}
    }
    return DataSourceView.model_validate({"id": str(document["_id"]), **payload})


def _view_document(source: DataSourceView) -> dict[str, Any]:
    return {
        "_id": source.id,
        **source.model_dump(mode="python", exclude={"id"}),
        "_deleted": False,
    }


async def _stored_source_documents(resources: RuntimeResources) -> list[dict[str, Any]]:
    cursor = _collection(resources).find({}).sort("name", 1)
    return [item async for item in cursor]


async def _get_source(resources: RuntimeResources, source_id: str) -> DataSourceView:
    document = await _collection(resources).find_one({"_id": source_id})
    if document is not None:
        if document.get("_deleted") is True:
            raise HTTPException(status_code=404, detail="Data source not found.")
        return _document_to_view(document)
    builtin = _builtins(resources).get(source_id)
    if builtin is not None:
        return builtin
    raise HTTPException(status_code=404, detail="Data source not found.")


def _source_assets(resources: RuntimeResources, source: DataSourceView) -> list[DataAssetSchema]:
    registry = cast(SchemaRegistry, resources.schema_registry)
    if source.id == "neo4j" or source.sourceType == "NEO4J":
        return []
    expected_engine = "SQLSERVER" if source.sourceType == "SQLSERVER" else "MONGODB"
    return [
        asset
        for asset in registry.assets
        if asset.engine == expected_engine and asset.database == source.database
    ]


def _schema_dataset(asset: DataAssetSchema) -> SchemaDatasetView:
    return SchemaDatasetView(
        id=asset.asset_id,
        name=asset.name,
        namespace=asset.namespace,
        kind="TABLE" if asset.engine == "SQLSERVER" else "COLLECTION",
        description=asset.description,
        fields=[
            SchemaFieldView(
                name=field.name,
                type=field.type,
                required=field.required,
                key=field.key,
                description=field.description,
            )
            for field in asset.fields
        ],
    )


def _slug(name: str) -> str:
    stem = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:48] or "source"
    candidate = f"{stem}-{uuid.uuid4().hex[:8]}"
    if not _SOURCE_ID.fullmatch(candidate):
        raise ValueError("Unable to create a valid source identifier")
    return candidate


@router.get("", response_model=APIResponse[list[DataSourceView]])
async def list_data_sources(
    request: Request,
    _actor: str = Depends(require_read_roles),
) -> APIResponse[list[DataSourceView]]:
    resources = _resources(request)
    stored = {
        str(document["_id"]): document
        for document in await _stored_source_documents(resources)
    }
    sources: list[DataSourceView] = []
    for source_id, builtin in _builtins(resources).items():
        override = stored.pop(source_id, None)
        if override is None:
            sources.append(builtin)
        elif override.get("_deleted") is not True:
            sources.append(_document_to_view(override))
    sources.extend(
        _document_to_view(document)
        for document in stored.values()
        if document.get("_deleted") is not True
    )
    return APIResponse(
        data=sources,
        meta=_meta(request),
        page=PageMeta(next_cursor=None, has_more=False, page_size=len(sources)),
    )


@router.post("", response_model=APIResponse[DataSourceView], status_code=status.HTTP_201_CREATED)
async def create_data_source(
    body: DataSourceWrite,
    request: Request,
    _actor: str = Depends(require_admin_roles),
) -> APIResponse[DataSourceView]:
    resources = _resources(request)
    now = _now()
    source_id = _slug(body.name)
    if body.credential is None:
        raise HTTPException(status_code=422, detail="Enter a connection credential.")
    vault_reference = _generated_vault_reference(source_id, body.credentialKind)
    await _store_credential(request, vault_reference, body.credential)
    document = {
        "_id": source_id,
        **body.model_dump(
            mode="python",
            exclude={"credential", "credentialVaultReference"},
        ),
        "credentialVaultReference": vault_reference,
        "status": "NOT_VALIDATED",
        "managed": False,
        "createdAt": now,
        "updatedAt": now,
        "lastValidatedAt": None,
        "validationMessage": None,
    }
    await _collection(resources).insert_one(document)
    return APIResponse(data=_document_to_view(document), meta=_meta(request))


@router.get("/{source_id}", response_model=APIResponse[DataSourceView])
async def get_data_source(
    source_id: str,
    request: Request,
    _actor: str = Depends(require_read_roles),
) -> APIResponse[DataSourceView]:
    return APIResponse(data=await _get_source(_resources(request), source_id), meta=_meta(request))


@router.get("/{source_id}/credential", response_model=APIResponse[CredentialRevealView])
async def reveal_data_source_credential(
    source_id: str,
    request: Request,
    response: Response,
    _actor: str = Depends(require_admin_roles),
) -> APIResponse[CredentialRevealView]:
    resources = _resources(request)
    if not resources.settings.data_source_credential_reveal_enabled:
        raise HTTPException(status_code=403, detail="Saved credential reveal is disabled.")
    source = await _get_source(resources, source_id)
    credential = await _load_credential(request, source.credentialVaultReference)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return APIResponse(
        data=CredentialRevealView(credential=credential.get_secret_value()),
        meta=_meta(request),
    )


@router.put("/{source_id}", response_model=APIResponse[DataSourceView])
async def update_data_source(
    source_id: str,
    body: DataSourceWrite,
    request: Request,
    _actor: str = Depends(require_admin_roles),
) -> APIResponse[DataSourceView]:
    resources = _resources(request)
    current = await _get_source(resources, source_id)
    if body.credential is not None:
        await _store_credential(
            request,
            current.credentialVaultReference,
            body.credential,
        )
    updated = current.model_copy(
        update={
            **body.model_dump(
                mode="python",
                exclude={"credential", "credentialVaultReference"},
            ),
            "credentialVaultReference": current.credentialVaultReference,
            "status": "NOT_VALIDATED",
            "managed": False,
            "updatedAt": _now(),
            "lastValidatedAt": None,
            "validationMessage": None,
        },
    )
    await _collection(resources).replace_one(
        {"_id": source_id},
        _view_document(updated),
        upsert=True,
    )
    return APIResponse(data=updated, meta=_meta(request))


@router.delete("/{source_id}", response_model=APIResponse[dict[str, bool]])
async def delete_data_source(
    source_id: str,
    request: Request,
    _actor: str = Depends(require_admin_roles),
) -> APIResponse[dict[str, bool]]:
    resources = _resources(request)
    await _get_source(resources, source_id)
    if source_id in _builtins(resources):
        await _collection(resources).replace_one(
            {"_id": source_id},
            {"_id": source_id, "_deleted": True, "updatedAt": _now()},
            upsert=True,
        )
        return APIResponse(data={"deleted": True}, meta=_meta(request))
    result = await _collection(resources).delete_one({"_id": source_id})
    if result.deleted_count != 1:
        raise HTTPException(status_code=404, detail="Data source not found.")
    return APIResponse(data={"deleted": True}, meta=_meta(request))


async def _builtin_validation(
    source_id: str,
    request: Request,
) -> DependencyProbeResult | None:
    probes = {
        "source-mongodb": probe_source_mongodb,
        "platform-mongodb": probe_mongodb,
        "sqlserver": probe_sqlserver,
        "neo4j": probe_neo4j,
    }
    probe = probes.get(source_id)
    if probe is None:
        return None
    return await probe(request)


@router.post("/{source_id}/validate", response_model=APIResponse[ValidationResult])
async def validate_data_source(
    source_id: str,
    body: ValidateDataSourceRequest,
    request: Request,
    actor_id: str = Depends(require_admin_roles),
) -> APIResponse[ValidationResult]:
    resources = _resources(request)
    source = await _get_source(resources, source_id)
    checked_at = _now()
    probe = await _builtin_validation(source_id, request) if source.managed else None
    if probe is None:
        credential = body.credential or await _load_credential(
            request,
            source.credentialVaultReference,
        )
        receipt_response = await validate_and_stage_data_source(
            DataSourceValidateAndStageRequest(
                sourceKey=source.id,
                sourceType=source.sourceType,
                accessMode=source.accessMode,
                host=source.host,
                port=source.port,
                uri=source.uri,
                username=source.username,
                database=source.database,
                credential=credential,
                credentialKind=source.credentialKind,
                vaultReference=source.credentialVaultReference,
            ),
            request,
            actor_id,
        )
        receipt = receipt_response.data
        if receipt is None:
            raise HTTPException(status_code=502, detail="Validation returned no receipt.")
        result = ValidationResult(
            sourceId=source.id,
            status="VALID",
            checkedAt=receipt.verified_at,
            latencyMs=None,
            checks=[
                ValidationCheck(
                    name=test.replace("_", " ").title(),
                    status="PASSED",
                    message="Check passed.",
                )
                for test in (receipt.tests or ("CONNECTION",))
            ],
        )
    else:
        healthy = probe.status is DependencyStatus.HEALTHY
        result = ValidationResult(
            sourceId=source.id,
            status="VALID" if healthy else "INVALID",
            checkedAt=checked_at,
            latencyMs=probe.latency_ms,
            checks=[
                ValidationCheck(
                    name="Connection",
                    status="PASSED" if healthy else "FAILED",
                    message=(
                        "Connection succeeded."
                        if healthy
                        else probe.safe_message or "Connection could not be validated."
                    ),
                ),
                ValidationCheck(
                    name="Schema access",
                    status="PASSED" if healthy else "FAILED",
                    message=(
                        "Schema metadata is available."
                        if healthy
                        else "Schema metadata is unavailable."
                    ),
                ),
            ],
        )
    persisted = source.model_copy(
        update={
            "status": result.status,
            "lastValidatedAt": result.checkedAt,
            "validationMessage": result.checks[0].message,
            "updatedAt": _now(),
        }
    )
    await _collection(resources).replace_one(
        {"_id": source.id},
        _view_document(persisted),
        upsert=True,
    )
    return APIResponse(data=result, meta=_meta(request))


@router.get("/{source_id}/schema", response_model=APIResponse[SchemaExplorerView])
async def get_data_source_schema(
    source_id: str,
    request: Request,
    _actor: str = Depends(require_read_roles),
) -> APIResponse[SchemaExplorerView]:
    resources = _resources(request)
    source = await _get_source(resources, source_id)
    registry = cast(SchemaRegistry, resources.schema_registry)
    if source.sourceType == "NEO4J":
        datasets = [
            SchemaDatasetView(
                id=f"neo4j-node-{node.label.lower()}",
                name=node.label,
                namespace=None,
                kind="NODE",
                description=f"{node.label} graph node",
                fields=[
                    SchemaFieldView(
                        name=property_name,
                        type="property",
                        required=property_name == node.key_property,
                        key=property_name == node.key_property,
                        description=f"{node.label} property",
                    )
                    for property_name in node.properties
                ],
            )
            for node in registry.graph.nodes
        ]
    else:
        datasets = [_schema_dataset(asset) for asset in _source_assets(resources, source)]
    return APIResponse(
        data=SchemaExplorerView(
            sourceId=source.id,
            schemaVersion=registry.schema_version,
            datasets=datasets,
        ),
        meta=_meta(request),
    )


@router.get("/{source_id}/data", response_model=APIResponse[DataPreviewView])
async def get_data_source_preview(
    source_id: str,
    request: Request,
    dataset_id: str = Query(alias="datasetId", min_length=1, max_length=255),
    limit: int = Query(default=25, ge=1, le=25),
    _actor: str = Depends(require_read_roles),
) -> APIResponse[DataPreviewView]:
    resources = _resources(request)
    source = await _get_source(resources, source_id)
    if source.sourceType == "NEO4J":
        raise HTTPException(status_code=422, detail="Graph preview is not available in this view.")
    assets = {asset.asset_id: asset for asset in _source_assets(resources, source)}
    asset = assets.get(dataset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Dataset not found for this source.")
    if asset.engine == "SQLSERVER":
        records = await asyncio.wait_for(
            get_sql_records(resources, asset, 0, limit),
            timeout=resources.settings.probe_timeout_seconds * 5,
        )
    else:
        records = await asyncio.wait_for(
            get_mongo_records(resources, asset, 0, limit),
            timeout=resources.settings.probe_timeout_seconds * 5,
        )
    rows = [cast(dict[str, Any], record.get("data", {})) for record in records]
    columns = sorted({str(key) for row in rows for key in row})
    return APIResponse(
        data=DataPreviewView(
            sourceId=source.id,
            datasetId=asset.asset_id,
            columns=columns,
            rows=rows,
        ),
        meta=_meta(request),
        page=PageMeta(next_cursor=None, has_more=False, page_size=limit),
    )
