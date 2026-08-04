"""Resolve Vault-backed process credentials before dependency clients are created."""

from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr

from return_platform.configuration.settings import Settings
from return_platform.secrets.vault import (
    VaultClientConfiguration,
    VaultHTTPSecretResolver,
    parse_secret_reference,
)


class RuntimeConfigurationError(RuntimeError):
    pass



async def _resolve_required(
    resolver: VaultHTTPSecretResolver,
    reference_text: str,
) -> str:
    reference = parse_secret_reference(reference_text)
    try:
        resolved = await resolver.get_secret(reference)
    except Exception as exc:
        raise RuntimeConfigurationError(f"Required Vault secret is unavailable: {reference.to_uri()}") from exc
    if resolved is None:
        raise RuntimeConfigurationError(f"Required Vault secret is unavailable: {reference.to_uri()}")
    return resolved.value


async def _resolve_many(
    resolver: VaultHTTPSecretResolver,
    references: tuple[str, ...],
) -> tuple[SecretStr, ...]:
    values: list[SecretStr] = []
    for reference in references:
        values.append(SecretStr(await _resolve_required(resolver, reference)))
    return tuple(values)


async def resolve_runtime_settings_from_vault(
    settings: Settings,
    *,
    resolve_ai_credentials: bool = True,
) -> tuple[Settings, VaultHTTPSecretResolver | None]:
    """Return settings whose credential fields were resolved from Vault in memory."""

    if not settings.vault_enabled:
        return settings, None

    resolver = VaultHTTPSecretResolver(
        VaultClientConfiguration(
            address=settings.vault_address,
            token=(settings.vault_token.get_secret_value() if settings.vault_token else None),
            token_file=(Path(settings.vault_token_file) if settings.vault_token_file else None),
            namespace=settings.vault_namespace,
            timeout_seconds=settings.vault_timeout_seconds,
            verify_tls=settings.vault_verify_tls,
        )
    )
    updates: dict[str, object] = {}

    scalar_references = {
        "mongo_dsn": settings.mongo_dsn_secret_reference,
        "source_mongo_dsn": settings.source_mongo_dsn_secret_reference,
        "neo4j_password": settings.neo4j_password_secret_reference,
        "valkey_password": settings.valkey_password_secret_reference,
        "sqlserver_password": settings.sqlserver_password_secret_reference,
        "validation_fingerprint_key": (settings.validation_fingerprint_key_secret_reference),
        "contact_lookup_hmac_key": settings.contact_lookup_hmac_key_secret_reference,
    }
    for field_name, reference in scalar_references.items():
        if reference:
            updates[field_name] = SecretStr(await _resolve_required(resolver, reference))

    key_reference_fields = {
        "google_api_keys": settings.google_api_key_references,
        "nvidia_api_keys": settings.nvidia_api_key_references,
        "openai_api_keys": settings.openai_api_key_references,
        "anthropic_api_keys": settings.anthropic_api_key_references,
    }
    if resolve_ai_credentials:
        for field_name, references in key_reference_fields.items():
            if references:
                updates[field_name] = await _resolve_many(resolver, references)

    updates["vault_secrets_resolved"] = True

    resolved_settings = Settings.model_validate(settings.model_dump(mode="python") | updates)
    return resolved_settings, resolver
