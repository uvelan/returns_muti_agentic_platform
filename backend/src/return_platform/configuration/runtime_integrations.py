"""Apply validated graph-owned AI and data-source metadata to runtime settings."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import SecretStr
from pymongo import AsyncMongoClient

from return_platform.configuration.return_configuration import ReturnPlatformConfiguration
from return_platform.configuration.settings import Settings
from return_platform.secrets.vault import parse_secret_reference

_RECEIPT_COLLECTION = "configuration_validation_receipts"


def _source_map(configuration: ReturnPlatformConfiguration) -> dict[str, Any]:
    return {
        item.source_key: item
        for item in configuration.runtime_integrations.data_sources
        if item.enabled
    }


def apply_graph_runtime_configuration(
    settings: Settings,
    configuration: ReturnPlatformConfiguration,
) -> Settings:
    """Return settings whose non-secret routing metadata comes from the graph release."""

    updates: dict[str, object] = {
        "ai_provider_order": "NONE",
        "google_api_key_references": (),
        "nvidia_api_key_references": (),
        "openai_api_key_references": (),
        "anthropic_api_key_references": (),
        "google_api_keys": (),
        "nvidia_api_keys": (),
        "openai_api_keys": (),
        "anthropic_api_keys": (),
        "google_api_key": None,
        "nvidia_api_key": None,
        "openai_api_key": None,
        "anthropic_api_key": None,
        "google_lightweight_models": (),
        "google_standard_models": (),
        "nvidia_lightweight_models": (),
        "nvidia_standard_models": (),
        "openai_lightweight_models": (),
        "openai_standard_models": (),
        "anthropic_lightweight_models": (),
        "anthropic_standard_models": (),
        "ollama_lightweight_models": (),
        "ollama_standard_models": (),
        "google_model": None,
        "nvidia_model": None,
        "openai_model": None,
        "anthropic_model": None,
        "ollama_model": None,
    }
    sources = _source_map(configuration)

    platform_mongo = sources.get("platform-mongodb")
    if platform_mongo is not None:
        if not platform_mongo.bootstrap_managed and platform_mongo.uri is not None:
            updates["mongo_dsn"] = SecretStr(platform_mongo.uri)
        if not platform_mongo.bootstrap_managed and platform_mongo.database is not None:
            updates["mongo_database"] = platform_mongo.database
        if not platform_mongo.bootstrap_managed and platform_mongo.credential is not None:
            updates["mongo_dsn_secret_reference"] = platform_mongo.credential.vault_reference

    source_mongo = sources.get("source-mongodb")
    if source_mongo is not None:
        if not source_mongo.bootstrap_managed and source_mongo.uri is not None:
            updates["source_mongo_dsn"] = SecretStr(source_mongo.uri)
        if not source_mongo.bootstrap_managed and source_mongo.database is not None:
            updates["source_mongo_database"] = source_mongo.database
        if not source_mongo.bootstrap_managed and source_mongo.credential is not None:
            updates["source_mongo_dsn_secret_reference"] = source_mongo.credential.vault_reference

    neo4j = sources.get("configuration-neo4j") or sources.get("neo4j")
    if neo4j is not None:
        if not neo4j.bootstrap_managed and neo4j.uri is not None:
            updates["neo4j_uri"] = neo4j.uri
        if not neo4j.bootstrap_managed and neo4j.username is not None:
            updates["neo4j_user"] = neo4j.username
        if not neo4j.bootstrap_managed and neo4j.database is not None:
            updates["neo4j_database"] = neo4j.database
        if not neo4j.bootstrap_managed and neo4j.credential is not None:
            updates["neo4j_password_secret_reference"] = neo4j.credential.vault_reference

    sqlserver = sources.get("omc-sqlserver") or sources.get("sqlserver")
    if sqlserver is not None:
        if not sqlserver.bootstrap_managed and sqlserver.host is not None:
            updates["sqlserver_host"] = sqlserver.host
        if not sqlserver.bootstrap_managed and sqlserver.port is not None:
            updates["sqlserver_port"] = sqlserver.port
        if not sqlserver.bootstrap_managed and sqlserver.username is not None:
            updates["sqlserver_user"] = sqlserver.username
        if not sqlserver.bootstrap_managed and sqlserver.database is not None:
            updates["sqlserver_database"] = sqlserver.database
        if not sqlserver.bootstrap_managed and sqlserver.credential is not None:
            updates["sqlserver_password_secret_reference"] = sqlserver.credential.vault_reference

    valkey = sources.get("valkey")
    if valkey is not None:
        if not valkey.bootstrap_managed and valkey.host is not None:
            updates["valkey_host"] = valkey.host
        if not valkey.bootstrap_managed and valkey.port is not None:
            updates["valkey_port"] = valkey.port
        if not valkey.bootstrap_managed and valkey.credential is not None:
            updates["valkey_password_secret_reference"] = valkey.credential.vault_reference

    temporal = sources.get("temporal")
    if (
        temporal is not None
        and not temporal.bootstrap_managed
        and temporal.host is not None
        and temporal.port is not None
    ):
        updates["temporal_target"] = f"{temporal.host}:{temporal.port}"

    enabled_providers = sorted(
        (item for item in configuration.runtime_integrations.ai_providers if item.enabled),
        key=lambda item: item.priority,
    )
    if enabled_providers:
        updates["ai_provider_order"] = ",".join(item.provider_key for item in enabled_providers)
    for provider in enabled_providers:
        key = provider.provider_key.lower()
        updates[f"{key}_base_url"] = provider.base_url
        if provider.provider_key != "OLLAMA":
            updates[f"{key}_api_key_references"] = tuple(
                item.vault_reference for item in provider.credentials
            )
        lightweight = tuple(
            item.model_id
            for item in sorted(provider.models, key=lambda item: item.priority)
            if item.model_class == "LIGHTWEIGHT"
        )
        standard = tuple(
            item.model_id
            for item in sorted(provider.models, key=lambda item: item.priority)
            if item.model_class == "STANDARD"
        )
        updates[f"{key}_lightweight_models"] = lightweight
        updates[f"{key}_standard_models"] = standard

    if not updates:
        return settings
    return Settings.model_validate(settings.model_dump(mode="python") | updates)


def required_validation_receipts(
    configuration: ReturnPlatformConfiguration,
) -> set[str]:
    receipt_ids: set[str] = set()
    for provider in configuration.runtime_integrations.ai_providers:
        if not provider.enabled:
            continue
        receipt_ids.update(item.validation_receipt_id for item in provider.validated_routes)
    for source in configuration.runtime_integrations.data_sources:
        if not source.enabled or source.bootstrap_managed:
            continue
        if source.validation_receipt_id is not None:
            receipt_ids.add(source.validation_receipt_id)
        if source.credential is not None and source.credential.validation_receipt_id is not None:
            receipt_ids.add(source.credential.validation_receipt_id)
    return receipt_ids


def _receipt_matches(
    document: dict[str, Any],
    *,
    subject_type: str,
    subject_key: str,
    target_uri: str,
    configuration_checksum: str,
) -> bool:
    if document.get("status") != "PASSED":
        return False
    if document.get("subject_type") != subject_type:
        return False
    if document.get("subject_key") != subject_key:
        return False
    if document.get("target_uri") != target_uri:
        return False
    if document.get("configuration_checksum") != configuration_checksum:
        return False
    if not document.get("secret_fingerprint"):
        return False
    try:
        reference = parse_secret_reference(target_uri)
    except (TypeError, ValueError):
        return False
    secret_version = document.get("secret_version")
    return (
        isinstance(secret_version, int)
        and secret_version >= 1
        and reference.version == secret_version
    )


async def verify_runtime_validation_receipts(
    client: AsyncMongoClient[dict[str, object]],
    database: str,
    configuration: ReturnPlatformConfiguration,
    *,
    require_unexpired: bool = False,
) -> None:
    """Fail activation when a graph-controlled dependency lacks a valid receipt."""

    required = required_validation_receipts(configuration)
    if not required:
        return
    documents = (
        await client[database][_RECEIPT_COLLECTION]
        .find({"receipt_id": {"$in": sorted(required)}})
        .to_list(length=len(required))
    )
    found: dict[str, dict[str, Any]] = {
        str(document.get("receipt_id")): document for document in documents
    }
    now = datetime.now(UTC)
    invalid: set[str] = set()
    for provider in configuration.runtime_integrations.ai_providers:
        if not provider.enabled:
            continue
        credentials = {item.profile_key: item for item in provider.credentials}
        for route in provider.validated_routes:
            document = found.get(route.validation_receipt_id)
            credential = credentials.get(route.credential_profile_key)
            if (
                document is None
                or credential is None
                or not _receipt_matches(
                    document,
                    subject_type="AI_CREDENTIAL_MODEL_BINDING",
                    subject_key=(f"{provider.provider_key}:{route.model_id}:{route.task_key}"),
                    target_uri=credential.vault_reference,
                    configuration_checksum=route.validation_configuration_checksum,
                )
            ):
                invalid.add(route.validation_receipt_id)

    for source in configuration.runtime_integrations.data_sources:
        if not source.enabled or source.bootstrap_managed:
            continue
        receipt_id = source.validation_receipt_id
        credential = source.credential
        if (
            receipt_id is None
            or credential is None
            or source.validation_configuration_checksum is None
        ):
            if receipt_id is not None:
                invalid.add(receipt_id)
            continue
        document = found.get(receipt_id)
        if document is None or not _receipt_matches(
            document,
            subject_type="DATA_SOURCE_CREDENTIAL_BINDING",
            subject_key=source.source_key,
            target_uri=credential.vault_reference,
            configuration_checksum=source.validation_configuration_checksum,
        ):
            invalid.add(receipt_id)

    if require_unexpired:
        for receipt_id in required:
            document = found.get(receipt_id)
            valid_until = document.get("valid_until") if document else None
            if not isinstance(valid_until, datetime) or valid_until <= now:
                invalid.add(receipt_id)
    if invalid:
        raise RuntimeError(
            "Runtime configuration has missing or expired validation receipts: "
            + ", ".join(sorted(invalid))
        )
