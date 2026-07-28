"""Validation-before-persistence APIs for AI and data-source configuration."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import socket
from typing import Any, Final, Literal, cast
from urllib.parse import parse_qs, urlparse

import httpx
import pymssql
from fastapi import APIRouter, Depends, HTTPException, Request, status
from neo4j import AsyncGraphDatabase
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pymongo import AsyncMongoClient
from pymongo.uri_parser import parse_uri

from return_platform.configuration.settings import Settings
from return_platform.data_console.api.auth import require_read_roles, require_write_roles
from return_platform.resources import RuntimeResources
from return_platform.secrets.vault import SecretResolver, parse_secret_reference
from return_platform.shared.contracts import APIResponse, ResponseMeta
from return_platform.validation.gates import (
    SecretValidationError,
    SecretValidationGate,
    ValidationReceipt,
)

router = APIRouter(
    prefix="/data-console/v1/runtime-validation",
    tags=["Runtime Configuration Validation"],
)
_SOURCE: Final = "RUNTIME_CONFIGURATION_VALIDATION"
_RECEIPT_COLLECTION: Final = "configuration_validation_receipts"


class ValidationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


def _meta(request: Request) -> ResponseMeta:
    request_id = getattr(request.state, "correlation_id", "unknown")
    return ResponseMeta(request_id=request_id if isinstance(request_id, str) else "unknown")


def _settings(request: Request) -> Settings:
    settings = getattr(request.app.state, "settings", None)
    if not isinstance(settings, Settings):
        raise HTTPException(status_code=503, detail="Runtime settings are unavailable")
    return settings


def _secret_resolver(request: Request) -> SecretResolver:
    resolver = getattr(request.app.state, "secret_resolver", None)
    if not isinstance(resolver, SecretResolver):
        raise HTTPException(status_code=503, detail="Vault secret resolver is unavailable")
    return resolver


def _resources(request: Request) -> RuntimeResources:
    resources = getattr(request.app.state, "resources", None)
    if not isinstance(resources, RuntimeResources):
        raise HTTPException(status_code=503, detail="Runtime resources are unavailable")
    return resources


def _checksum(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def ai_binding_checksum(
    *,
    provider: str,
    base_url: str,
    model_id: str,
    model_class: str,
    task_key: str,
    vault_reference: str,
) -> str:
    """Checksum the non-secret fields that bind one AI runtime route."""

    return _checksum(
        {
            "provider": provider,
            "baseUrl": base_url,
            "modelId": model_id,
            "modelClass": model_class,
            "taskKey": task_key,
            "vaultReference": vault_reference.split("?", 1)[0],
        }
    )


def _validate_vault_prefix(reference_uri: str, prefix: str) -> None:
    reference = parse_secret_reference(reference_uri)
    expected = f"production/{prefix.strip('/')}/"
    if reference.mount_path != "secret" or not reference.vault_path.startswith(expected):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Vault reference must be under vault://secret/{expected}",
        )


def _normalized_model_id(value: str) -> str:
    return value.removeprefix("models/").strip()


def _provider_base_url(provider: str, settings: Settings) -> str:
    return {
        "GOOGLE": settings.google_base_url,
        "NVIDIA": settings.nvidia_base_url,
        "OPENAI": settings.openai_base_url,
        "ANTHROPIC": settings.anthropic_base_url,
    }[provider]


def _validate_ai_endpoint(base_url: str, settings: Settings) -> None:
    parsed = urlparse(base_url)
    host = parsed.hostname.lower() if parsed.hostname else ""
    if parsed.scheme != "https" or not host or parsed.username or parsed.password:
        raise HTTPException(
            status_code=422,
            detail="Hosted AI provider endpoints must use HTTPS without embedded credentials",
        )
    if parsed.query or parsed.fragment:
        raise HTTPException(
            status_code=422,
            detail="AI provider endpoints must not contain a query string or fragment",
        )
    if host not in set(settings.ai_allowed_endpoint_hosts):
        raise HTTPException(
            status_code=422,
            detail="AI provider endpoint host is not in the deployment allowlist",
        )


def _assert_status_json(value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Model validation returned no structured text")
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("Model validation did not return valid JSON") from exc
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        raise ValueError("Model validation JSON did not match the required schema")


def _validate_ai_task_binding(
    request: Request,
    *,
    provider: str,
    model_class: str,
    task_key: str,
) -> None:
    loaded = getattr(request.app.state, "ai_gateway_configuration", None)
    configuration = getattr(loaded, "configuration", None)
    tasks = getattr(configuration, "tasks", None)
    if not isinstance(tasks, dict) or task_key not in tasks:
        raise HTTPException(status_code=422, detail="AI task is not registered")
    task = tasks[task_key]
    if provider not in task.allowedProviders:
        raise HTTPException(
            status_code=422,
            detail="AI provider is not permitted for the selected task",
        )
    if str(task.tier.value) != model_class:
        raise HTTPException(
            status_code=422,
            detail="AI model class does not match the selected task tier",
        )


class AIValidateAndStageRequest(ValidationModel):
    provider: Literal["GOOGLE", "NVIDIA", "OPENAI", "ANTHROPIC"]
    modelId: str = Field(min_length=1, max_length=256)
    modelClass: Literal["LIGHTWEIGHT", "STANDARD"]
    taskKey: str = Field(min_length=1, max_length=128)
    apiKey: SecretStr
    vaultReference: str = Field(min_length=16, max_length=768)

    @field_validator("modelId", "taskKey")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Value must not be blank")
        return normalized


async def _validate_google(
    *, base_url: str, api_key: str, model_id: str, timeout_seconds: float
) -> tuple[str, ...]:
    model = _normalized_model_id(model_id)
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        list_response = await client.get(
            f"{base_url}/models",
            params={"key": api_key, "pageSize": "1000"},
        )
        list_response.raise_for_status()
        models = list_response.json().get("models", [])
        matching = next(
            (
                item
                for item in models
                if isinstance(item, dict)
                and _normalized_model_id(str(item.get("name", ""))) == model
            ),
            None,
        )
        if matching is None:
            raise ValueError("Configured model is not accessible to this credential")
        methods = matching.get("supportedGenerationMethods", [])
        if not isinstance(methods, list) or "generateContent" not in methods:
            raise ValueError("Configured model does not support generateContent")
        generation_response = await client.post(
            f"{base_url}/models/{model}:generateContent",
            params={"key": api_key},
            json={
                "contents": [{"parts": [{"text": 'Return only: {"status":"ok"}'}]}],
                "generationConfig": {
                    "temperature": 0,
                    "maxOutputTokens": 32,
                    "responseMimeType": "application/json",
                },
            },
        )
        generation_response.raise_for_status()
        candidates = generation_response.json().get("candidates", [])
        if not isinstance(candidates, list) or not candidates:
            raise ValueError("Model validation returned no candidate")
        first = candidates[0] if isinstance(candidates[0], dict) else {}
        content = first.get("content", {}) if isinstance(first, dict) else {}
        parts = content.get("parts", []) if isinstance(content, dict) else []
        text = parts[0].get("text") if parts and isinstance(parts[0], dict) else None
        _assert_status_json(text)
    return ("AUTHENTICATION", "MODEL_DISCOVERY", "MODEL_ACCESS", "INFERENCE_PROBE")


async def _validate_openai_compatible(
    *, base_url: str, api_key: str, model_id: str, timeout_seconds: float
) -> tuple[str, ...]:
    headers = {"Authorization": f"Bearer {api_key}"}
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        list_response = await client.get(f"{base_url}/models", headers=headers)
        list_response.raise_for_status()
        entries = list_response.json().get("data", [])
        ids = {
            str(entry.get("id")) for entry in entries if isinstance(entry, dict) and entry.get("id")
        }
        if model_id not in ids:
            raise ValueError("Configured model is not accessible to this credential")
        inference_response = await client.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json={
                "model": model_id,
                "messages": [
                    {
                        "role": "user",
                        "content": "Return only a JSON object with status set to ok.",
                    }
                ],
                "temperature": 0,
                "max_tokens": 32,
                "response_format": {"type": "json_object"},
            },
        )
        inference_response.raise_for_status()
        choices = inference_response.json().get("choices", [])
        if not isinstance(choices, list) or not choices:
            raise ValueError("Model validation returned no completion")
        first = choices[0] if isinstance(choices[0], dict) else {}
        message = first.get("message", {}) if isinstance(first, dict) else {}
        _assert_status_json(message.get("content") if isinstance(message, dict) else None)
    return ("AUTHENTICATION", "MODEL_DISCOVERY", "MODEL_ACCESS", "INFERENCE_PROBE")


async def _validate_anthropic(
    *, base_url: str, api_key: str, model_id: str, timeout_seconds: float, version: str
) -> tuple[str, ...]:
    headers = {
        "x-api-key": api_key,
        "anthropic-version": version,
        "content-type": "application/json",
    }
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        list_response = await client.get(f"{base_url}/models", headers=headers)
        list_response.raise_for_status()
        entries = list_response.json().get("data", [])
        ids = {
            str(entry.get("id")) for entry in entries if isinstance(entry, dict) and entry.get("id")
        }
        if model_id not in ids:
            raise ValueError("Configured model is not accessible to this credential")
        inference_response = await client.post(
            f"{base_url}/messages",
            headers=headers,
            json={
                "model": model_id,
                "max_tokens": 32,
                "temperature": 0,
                "messages": [
                    {
                        "role": "user",
                        "content": "Return only a JSON object with status set to ok.",
                    }
                ],
            },
        )
        inference_response.raise_for_status()
        content = inference_response.json().get("content", [])
        if not isinstance(content, list) or not content:
            raise ValueError("Model validation returned no content")
        first = content[0] if isinstance(content[0], dict) else {}
        _assert_status_json(first.get("text") if isinstance(first, dict) else None)
    return ("AUTHENTICATION", "MODEL_DISCOVERY", "MODEL_ACCESS", "INFERENCE_PROBE")


async def probe_ai_provider_model(
    *,
    provider: str,
    base_url: str,
    api_key: str,
    model_id: str,
    settings: Settings,
) -> tuple[str, ...]:
    """Probe one hosted provider credential/model pair without persisting its secret."""

    if provider == "GOOGLE":
        return await _validate_google(
            base_url=base_url,
            api_key=api_key,
            model_id=model_id,
            timeout_seconds=settings.ai_timeout_seconds,
        )
    if provider in {"NVIDIA", "OPENAI"}:
        return await _validate_openai_compatible(
            base_url=base_url,
            api_key=api_key,
            model_id=model_id,
            timeout_seconds=settings.ai_timeout_seconds,
        )
    if provider == "ANTHROPIC":
        return await _validate_anthropic(
            base_url=base_url,
            api_key=api_key,
            model_id=model_id,
            timeout_seconds=settings.ai_timeout_seconds,
            version=settings.anthropic_version,
        )
    raise ValueError(f"Unsupported hosted AI provider: {provider}")


async def _persist_receipt(request: Request, receipt: ValidationReceipt) -> None:
    resources = _resources(request)
    if resources.mongo is None:
        raise HTTPException(status_code=503, detail="Receipt repository is unavailable")
    collection = resources.mongo[resources.settings.mongo_database][_RECEIPT_COLLECTION]
    await collection.create_index("receipt_id", unique=True)
    await collection.create_index("subject_key")
    await collection.create_index("valid_until")
    payload = receipt.model_dump(mode="python")
    payload["receipt_id"] = str(receipt.receipt_id)
    await collection.update_one(
        {"receipt_id": payload["receipt_id"]},
        {"$setOnInsert": payload},
        upsert=True,
    )


@router.post(
    "/ai/validate-and-stage",
    response_model=APIResponse[ValidationReceipt],
    status_code=status.HTTP_201_CREATED,
)
async def validate_and_stage_ai_configuration(
    body: AIValidateAndStageRequest,
    request: Request,
    actor_id: str = Depends(require_write_roles),
) -> APIResponse[ValidationReceipt]:
    """Validate a provider key/model/task binding before writing the key to Vault."""

    settings = _settings(request)
    resolver = _secret_resolver(request)
    provider = body.provider
    _validate_vault_prefix(body.vaultReference, f"ai/{provider.lower()}")
    reference = parse_secret_reference(body.vaultReference)
    base_url = _provider_base_url(provider, settings)
    _validate_ai_endpoint(base_url, settings)
    _validate_ai_task_binding(
        request,
        provider=provider,
        model_class=body.modelClass,
        task_key=body.taskKey,
    )
    configuration_checksum = ai_binding_checksum(
        provider=provider,
        base_url=base_url,
        model_id=body.modelId,
        model_class=body.modelClass,
        task_key=body.taskKey,
        vault_reference=reference.to_uri(),
    )
    previous_secret = await resolver.get_secret(reference.model_copy(update={"version": None}))

    async def probe(api_key: str) -> tuple[str, ...]:
        return await probe_ai_provider_model(
            provider=provider,
            base_url=base_url,
            api_key=api_key,
            model_id=body.modelId,
            settings=settings,
        )

    try:
        receipt = await SecretValidationGate.validate_and_stage(
            reference=reference,
            transient_value=body.apiKey.get_secret_value(),
            resolver=resolver,
            probe=probe,
            subject_type="AI_CREDENTIAL_MODEL_BINDING",
            subject_key=f"{provider}:{body.modelId}:{body.taskKey}",
            configuration_checksum=configuration_checksum,
            fingerprint_key=settings.validation_fingerprint_key.get_secret_value().encode("utf-8"),
            actor_id=actor_id,
        )
    except SecretValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.reason) from exc

    try:
        await _persist_receipt(request, receipt)
    except Exception:
        if receipt.secret_version is not None:
            await resolver.rollback_secret_write(
                reference,
                receipt.secret_version,
                previous_secret,
            )
        raise
    return APIResponse(data=receipt, meta=_meta(request))


class DataSourceValidateAndStageRequest(ValidationModel):
    sourceKey: str = Field(min_length=2, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]+$")
    sourceType: Literal["MONGODB", "NEO4J", "SQLSERVER"]
    accessMode: Literal["READ_ONLY", "READ_WRITE"] = "READ_ONLY"
    host: str | None = Field(default=None, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65_535)
    uri: str | None = Field(default=None, max_length=1024)
    username: str | None = Field(default=None, max_length=256)
    database: str = Field(min_length=1, max_length=128)
    requiredDatasets: tuple[str, ...] = ()
    credential: SecretStr
    credentialKind: Literal["DSN", "PASSWORD"]
    vaultReference: str = Field(min_length=16, max_length=768)

    @field_validator("requiredDatasets", mode="before")
    @classmethod
    def normalize_datasets(cls, value: object) -> object:
        if value in (None, ""):
            return ()
        if isinstance(value, list | tuple):
            normalized = tuple(str(item).strip() for item in value if str(item).strip())
            if len(normalized) != len(set(normalized)):
                raise ValueError("requiredDatasets must not contain duplicates")
            return normalized
        raise ValueError("requiredDatasets must be a list")

    @model_validator(mode="after")
    def validate_source_shape(self) -> DataSourceValidateAndStageRequest:
        if self.sourceType == "MONGODB":
            if self.credentialKind != "DSN":
                raise ValueError("MongoDB validation requires credentialKind=DSN")
            if not self.credential.get_secret_value().startswith(("mongodb://", "mongodb+srv://")):
                raise ValueError("MongoDB credential must be a MongoDB DSN")
        elif self.sourceType == "NEO4J":
            if self.credentialKind != "PASSWORD" or not self.uri or not self.username:
                raise ValueError("Neo4j validation requires URI, username, and password")
        elif self.sourceType == "SQLSERVER":
            if (
                self.credentialKind != "PASSWORD"
                or not self.host
                or not self.port
                or not self.username
            ):
                raise ValueError(
                    "SQL Server validation requires host, port, username, and password"
                )
        return self


def _extract_hosts(body: DataSourceValidateAndStageRequest) -> tuple[str, ...]:
    if body.host:
        return (body.host.strip().lower(),)
    if body.uri:
        parsed = urlparse(body.uri)
        return (parsed.hostname.lower(),) if parsed.hostname else ()
    if body.sourceType == "MONGODB":
        dsn = body.credential.get_secret_value()
        if dsn.startswith("mongodb+srv://"):
            raise ValueError(
                "mongodb+srv is not supported for data-source validation; "
                "configure explicit allowlisted seed hosts"
            )
        mongo_parsed = parse_uri(dsn, validate=True, warn=False)
        nodes = mongo_parsed.get("nodelist", [])
        return tuple(str(host).strip().lower() for host, _port in nodes if host)
    return ()


def _validate_mongodb_dsn_security(dsn: str) -> None:
    parsed = urlparse(dsn)
    query = {
        key.lower(): tuple(item.lower() for item in values)
        for key, values in parse_qs(parsed.query, keep_blank_values=True).items()
    }
    unsafe_options = {
        "tlsallowinvalidcertificates",
        "tlsallowinvalidhostnames",
        "tlsinsecure",
        "sslallowinvalidcertificates",
        "sslallowinvalidhostnames",
    }
    enabled_unsafe = sorted(
        option
        for option in unsafe_options
        if any(value in {"1", "true", "yes", "on"} for value in query.get(option, ()))
    )
    if enabled_unsafe:
        raise ValueError("MongoDB DSN disables TLS verification: " + ", ".join(enabled_unsafe))


def _validate_data_source_host(host: str | None, settings: Settings) -> None:
    if not host:
        raise ValueError("Data-source host is required")
    normalized_host = host.strip().lower().rstrip(".")
    try:
        addresses = {ipaddress.ip_address(normalized_host)}
    except ValueError:
        try:
            addresses = {
                ipaddress.ip_address(item[4][0])
                for item in socket.getaddrinfo(
                    normalized_host,
                    None,
                    type=socket.SOCK_STREAM,
                )
            }
        except socket.gaierror as exc:
            raise ValueError("Data-source host cannot be resolved") from exc
    prohibited = {
        ipaddress.ip_address("169.254.169.254"),
        ipaddress.ip_address("100.100.100.200"),
    }
    if addresses & prohibited:
        raise ValueError("Cloud metadata endpoints are prohibited")

    allowed_hosts: set[str] = set()
    allowed_networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for entry in settings.data_source_allowed_hosts:
        normalized_entry = entry.strip().lower().rstrip(".")
        try:
            allowed_networks.append(ipaddress.ip_network(normalized_entry, strict=False))
        except ValueError:
            allowed_hosts.add(normalized_entry)

    if normalized_host in allowed_hosts:
        return
    if allowed_networks and all(
        any(address in network for network in allowed_networks) for address in addresses
    ):
        return
    raise ValueError("Data-source host is not in the deployment allowlist")


def _validate_data_source_hosts(hosts: tuple[str, ...], settings: Settings) -> None:
    if not hosts:
        raise ValueError("Data-source host is required")
    for host in hosts:
        _validate_data_source_host(host, settings)


async def _probe_mongodb(
    body: DataSourceValidateAndStageRequest,
    credential: str,
) -> tuple[str, ...]:
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        credential,
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=5000,
    )
    try:
        await client.admin.command("ping")
        collection_names = set(await client[body.database].list_collection_names())
        missing = sorted(set(body.requiredDatasets) - collection_names)
        if missing:
            raise ValueError("Missing MongoDB collections: " + ", ".join(missing))
    finally:
        await client.close()
    return ("DNS", "TRANSPORT_CONNECTIVITY", "AUTHENTICATION", "PING", "DATASET_DISCOVERY")


async def _probe_neo4j(body: DataSourceValidateAndStageRequest, credential: str) -> tuple[str, ...]:
    assert body.uri is not None and body.username is not None
    driver = AsyncGraphDatabase.driver(body.uri, auth=(body.username, credential))
    try:
        await driver.verify_connectivity()
        records, _, _ = await driver.execute_query("RETURN 1 AS ok", database_=body.database)
        if not records or int(records[0]["ok"]) != 1:
            raise ValueError("Neo4j health query failed")
        if body.requiredDatasets:
            records, _, _ = await driver.execute_query(
                "SHOW INDEXES YIELD name RETURN collect(name) AS names",
                database_=body.database,
            )
            indexes = set(cast(list[str], records[0]["names"])) if records else set()
            missing = sorted(set(body.requiredDatasets) - indexes)
            if missing:
                raise ValueError("Missing Neo4j indexes: " + ", ".join(missing))
    finally:
        await driver.close()
    return (
        "DNS",
        "TRANSPORT_CONNECTIVITY",
        "AUTHENTICATION",
        "HEALTH_QUERY",
        "SCHEMA_DISCOVERY",
    )


def _probe_sqlserver_sync(
    body: DataSourceValidateAndStageRequest,
    credential: str,
) -> tuple[str, ...]:
    assert body.host is not None and body.port is not None and body.username is not None
    connection = pymssql.connect(
        server=body.host,
        port=str(body.port),
        user=body.username,
        password=credential,
        database=body.database,
        login_timeout=5,
        timeout=5,
        autocommit=False,
    )
    try:
        cursor = connection.cursor(as_dict=True)
        cursor.execute("SELECT 1 AS ok")
        row = cursor.fetchone()
        if not row or int(str(row["ok"])) != 1:
            raise ValueError("SQL Server health query failed")
        if body.requiredDatasets:
            cursor.execute("SELECT TABLE_SCHEMA, TABLE_NAME FROM INFORMATION_SCHEMA.TABLES")
            available = {
                f"{row['TABLE_SCHEMA']!s}.{row['TABLE_NAME']!s}" for row in cursor.fetchall()
            }
            missing = sorted(set(body.requiredDatasets) - available)
            if missing:
                raise ValueError("Missing SQL Server datasets: " + ", ".join(missing))
        connection.rollback()
    finally:
        connection.close()
    return (
        "DNS",
        "TRANSPORT_CONNECTIVITY",
        "AUTHENTICATION",
        "HEALTH_QUERY",
        "DATASET_DISCOVERY",
    )


@router.post(
    "/data-sources/validate-and-stage",
    response_model=APIResponse[ValidationReceipt],
    status_code=status.HTTP_201_CREATED,
)
async def validate_and_stage_data_source(
    body: DataSourceValidateAndStageRequest,
    request: Request,
    actor_id: str = Depends(require_write_roles),
) -> APIResponse[ValidationReceipt]:
    """Validate connectivity and declared datasets before storing a source credential."""

    fixed_source_types = {
        "platform-mongodb": "MONGODB",
        "source-mongodb": "MONGODB",
        "configuration-neo4j": "NEO4J",
        "omc-sqlserver": "SQLSERVER",
    }
    required_type = fixed_source_types.get(body.sourceKey)
    if required_type is not None and body.sourceType != required_type:
        raise HTTPException(
            status_code=422,
            detail=f"{body.sourceKey} must use sourceType={required_type}",
        )
    if body.sourceKey in {"source-mongodb", "omc-sqlserver"} and body.accessMode != "READ_ONLY":
        raise HTTPException(
            status_code=422,
            detail=(
                f"{body.sourceKey} is an authoritative external source and must remain READ_ONLY"
            ),
        )

    _validate_vault_prefix(body.vaultReference, "data-sources")
    try:
        if body.sourceType == "MONGODB":
            _validate_mongodb_dsn_security(body.credential.get_secret_value())
        _validate_data_source_hosts(_extract_hosts(body), _settings(request))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    settings = _settings(request)
    resolver = _secret_resolver(request)
    reference = parse_secret_reference(body.vaultReference)
    non_secret_configuration = body.model_dump(
        mode="json",
        exclude={"credential"},
    )
    configuration_checksum = _checksum(non_secret_configuration)
    previous_secret = await resolver.get_secret(reference.model_copy(update={"version": None}))

    async def probe(credential: str) -> tuple[str, ...]:
        if body.sourceType == "MONGODB":
            return await _probe_mongodb(body, credential)
        if body.sourceType == "NEO4J":
            return await _probe_neo4j(body, credential)
        return await asyncio.to_thread(_probe_sqlserver_sync, body, credential)

    try:
        receipt = await SecretValidationGate.validate_and_stage(
            reference=reference,
            transient_value=body.credential.get_secret_value(),
            resolver=resolver,
            probe=probe,
            subject_type="DATA_SOURCE_CREDENTIAL_BINDING",
            subject_key=body.sourceKey,
            configuration_checksum=configuration_checksum,
            fingerprint_key=settings.validation_fingerprint_key.get_secret_value().encode("utf-8"),
            actor_id=actor_id,
        )
    except SecretValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.reason) from exc

    try:
        await _persist_receipt(request, receipt)
    except Exception:
        if receipt.secret_version is not None:
            await resolver.rollback_secret_write(
                reference,
                receipt.secret_version,
                previous_secret,
            )
        raise
    return APIResponse(data=receipt, meta=_meta(request))


@router.get(
    "/receipts",
    response_model=APIResponse[list[ValidationReceipt]],
)
async def list_validation_receipts(
    request: Request,
    _actor_id: str = Depends(require_read_roles),
) -> APIResponse[list[ValidationReceipt]]:
    resources = _resources(request)
    if resources.mongo is None:
        raise HTTPException(status_code=503, detail="Receipt repository is unavailable")
    cursor = (
        resources.mongo[resources.settings.mongo_database][_RECEIPT_COLLECTION]
        .find({})
        .sort("verified_at", -1)
        .limit(100)
    )
    receipts = [
        ValidationReceipt.model_validate(
            {key: value for key, value in document.items() if key != "_id"}
        )
        async for document in cursor
    ]
    return APIResponse(data=receipts, meta=_meta(request))
