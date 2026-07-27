"""HashiCorp Vault KV v2 client, secret contracts, and redaction utilities."""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator


class SecretReference(BaseModel):
    """Reference to one field inside a Vault KV v2 secret."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    vault_path: str = Field(min_length=1, max_length=512)
    secret_key: str = Field(min_length=1, max_length=128)
    version: int | None = Field(default=None, ge=1)
    mount_path: str = Field(default="secret", min_length=1, max_length=128)

    @field_validator("vault_path", "mount_path", "secret_key")
    @classmethod
    def reject_unsafe_segments(cls, value: str) -> str:
        normalized = value.strip().strip("/")
        if not normalized or ".." in normalized.split("/"):
            raise ValueError("Vault secret references must use normalized non-parent paths")
        return normalized

    def to_uri(self) -> str:
        version = f"?version={self.version}" if self.version is not None else ""
        return f"vault://{self.mount_path}/{self.vault_path}#{self.secret_key}{version}"


class ResolvedSecret(BaseModel):
    """Secret value held only in process memory."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reference: SecretReference
    value: str = Field(repr=False)
    secret_version: int | None = None
    resolved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def __str__(self) -> str:
        return "<REDACTED_SECRET>"


@runtime_checkable
class SecretResolver(Protocol):
    async def get_secret(self, reference: SecretReference) -> ResolvedSecret | None: ...

    async def put_secret(self, reference: SecretReference, value: str) -> ResolvedSecret: ...

    async def delete_secret_version(self, reference: SecretReference, version: int) -> None: ...

    async def rollback_secret_write(
        self,
        reference: SecretReference,
        staged_version: int,
        previous: ResolvedSecret | None,
    ) -> None: ...


class VaultClientConfiguration(BaseModel):
    """Connection settings for Vault or a loopback Vault Proxy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    address: str = Field(min_length=8)
    token: str | None = Field(default=None, repr=False)
    token_file: Path | None = None
    namespace: str | None = None
    timeout_seconds: float = Field(default=5.0, gt=0.0, le=30.0)
    verify_tls: bool = True

    @field_validator("address")
    @classmethod
    def validate_address(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        if not normalized.startswith(("http://", "https://")):
            raise ValueError("Vault address must use HTTP or HTTPS")
        return normalized


class VaultHTTPSecretResolver:
    """Minimal async Vault KV v2 resolver with no secret persistence or logging."""

    def __init__(self, configuration: VaultClientConfiguration) -> None:
        self._configuration = configuration

    def _token(self) -> str | None:
        if self._configuration.token:
            return self._configuration.token
        if self._configuration.token_file is None:
            return None
        token = self._configuration.token_file.expanduser().read_text(encoding="utf-8").strip()
        return token or None

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        token = self._token()
        if token:
            headers["X-Vault-Token"] = token
        if self._configuration.namespace:
            headers["X-Vault-Namespace"] = self._configuration.namespace
        return headers

    def _url(self, reference: SecretReference) -> str:
        return (
            f"{self._configuration.address}/v1/{reference.mount_path}/data/{reference.vault_path}"
        )

    async def _read_document(
        self,
        reference: SecretReference,
        *,
        version: int | None = None,
    ) -> tuple[dict[str, Any], int] | None:
        params = {"version": str(version)} if version is not None else None
        async with httpx.AsyncClient(
            timeout=self._configuration.timeout_seconds,
            verify=self._configuration.verify_tls,
        ) as client:
            response = await client.get(
                self._url(reference),
                headers=self._headers(),
                params=params,
            )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data", {}).get("data", {})
        metadata = payload.get("data", {}).get("metadata", {})
        current_version = metadata.get("version")
        if not isinstance(data, dict) or not isinstance(current_version, int):
            raise RuntimeError("Vault returned an invalid KV v2 secret document")
        return dict(data), current_version

    async def get_secret(self, reference: SecretReference) -> ResolvedSecret | None:
        document = await self._read_document(reference, version=reference.version)
        if document is None:
            return None
        data, version = document
        if reference.secret_key not in data:
            return None
        value = data[reference.secret_key]
        if value is None or not str(value).strip():
            return None
        return ResolvedSecret(
            reference=reference.model_copy(update={"version": version}),
            value=str(value),
            secret_version=version,
        )

    async def put_secret(self, reference: SecretReference, value: str) -> ResolvedSecret:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Cannot store a blank secret")
        document = await self._read_document(reference, version=None)
        if document is None:
            if reference.version is not None:
                raise ValueError("Cannot create a Vault secret with an expected version")
            current_data: dict[str, Any] = {}
            expected_version = 0
        else:
            current_data, current_version = document
            if reference.version is None:
                raise ValueError("Existing Vault secrets require an expected version for rotation")
            if reference.version != current_version:
                raise ValueError(
                    f"Vault secret rotation version conflict: expected {reference.version}, "
                    f"current {current_version}"
                )
            expected_version = current_version
        current_data[reference.secret_key] = normalized
        async with httpx.AsyncClient(
            timeout=self._configuration.timeout_seconds,
            verify=self._configuration.verify_tls,
        ) as client:
            response = await client.post(
                self._url(reference),
                headers=self._headers(),
                json={
                    "options": {"cas": expected_version},
                    "data": current_data,
                },
            )
        response.raise_for_status()
        payload = response.json()
        version_raw = payload.get("data", {}).get("version")
        if not isinstance(version_raw, int):
            raise RuntimeError("Vault did not return the staged secret version")
        stored_reference = reference.model_copy(update={"version": version_raw})
        return ResolvedSecret(
            reference=stored_reference,
            value=normalized,
            secret_version=version_raw,
        )

    async def rollback_secret_write(
        self,
        reference: SecretReference,
        staged_version: int,
        previous: ResolvedSecret | None,
    ) -> None:
        document = await self._read_document(reference, version=None)
        if document is None:
            return
        current_data, current_version = document
        if current_version != staged_version:
            raise ValueError("Vault secret changed after staging; automatic rollback was refused")
        if previous is None:
            current_data.pop(reference.secret_key, None)
        else:
            current_data[reference.secret_key] = previous.value
        if not current_data:
            url = (
                f"{self._configuration.address}/v1/{reference.mount_path}/metadata/"
                f"{reference.vault_path}"
            )
            method = "DELETE"
            payload: dict[str, Any] | None = None
        else:
            url = self._url(reference)
            method = "POST"
            payload = {
                "options": {"cas": staged_version},
                "data": current_data,
            }
        async with httpx.AsyncClient(
            timeout=self._configuration.timeout_seconds,
            verify=self._configuration.verify_tls,
        ) as client:
            response = await client.request(
                method,
                url,
                headers=self._headers(),
                json=payload,
            )
        response.raise_for_status()

    async def delete_secret_version(self, reference: SecretReference, version: int) -> None:
        if version < 1:
            raise ValueError("Vault secret version must be positive")
        url = (
            f"{self._configuration.address}/v1/{reference.mount_path}/delete/{reference.vault_path}"
        )
        async with httpx.AsyncClient(
            timeout=self._configuration.timeout_seconds,
            verify=self._configuration.verify_tls,
        ) as client:
            response = await client.post(
                url,
                headers=self._headers(),
                json={"versions": [version]},
            )
        response.raise_for_status()


class LocalProxySecretResolver:
    """Deterministic in-memory resolver reserved for automated tests."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[str, int]] = {}
        self._known_secrets: set[str] = set()

    @staticmethod
    def _storage_key(reference: SecretReference) -> str:
        return f"{reference.mount_path}/{reference.vault_path}#{reference.secret_key}"

    async def get_secret(self, reference: SecretReference) -> ResolvedSecret | None:
        key = self._storage_key(reference)
        stored = self._store.get(key)
        if stored is not None:
            value, version = stored
            if reference.version is not None and reference.version != version:
                return None
            self._known_secrets.add(value)
            return ResolvedSecret(
                reference=reference.model_copy(update={"version": version}),
                value=value,
                secret_version=version,
            )

        env_key = f"{reference.vault_path.replace('/', '_').upper()}_{reference.secret_key.upper()}"
        env_value = os.environ.get(env_key)
        if env_value:
            self._known_secrets.add(env_value)
            return ResolvedSecret(reference=reference, value=env_value)
        return None

    async def put_secret(self, reference: SecretReference, value: str) -> ResolvedSecret:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Cannot store a blank secret")
        key = self._storage_key(reference)
        previous = self._store.get(key)
        if previous is not None:
            if reference.version is None:
                raise ValueError("Existing Vault secrets require an expected version for rotation")
            if reference.version != previous[1]:
                raise ValueError("Vault secret rotation version conflict")
        elif reference.version not in (None, 0):
            raise ValueError("Cannot create a new Vault secret with a non-zero version")
        version = 1 if previous is None else previous[1] + 1
        self._store[key] = (value, version)
        self._known_secrets.add(value)
        stored_reference = reference.model_copy(update={"version": version})
        return ResolvedSecret(
            reference=stored_reference,
            value=value,
            secret_version=version,
        )

    async def delete_secret_version(self, reference: SecretReference, version: int) -> None:
        key = self._storage_key(reference)
        stored = self._store.get(key)
        if stored is not None and stored[1] == version:
            del self._store[key]

    async def rollback_secret_write(
        self,
        reference: SecretReference,
        staged_version: int,
        previous: ResolvedSecret | None,
    ) -> None:
        key = self._storage_key(reference)
        stored = self._store.get(key)
        if stored is None:
            return
        if stored[1] != staged_version:
            raise ValueError("Vault secret changed after staging; automatic rollback was refused")
        if previous is None:
            del self._store[key]
            return
        self._store[key] = (previous.value, staged_version + 1)

    def get_known_secrets(self) -> set[str]:
        return set(self._known_secrets)


class SecretRedactor:
    """Redact known values, Vault URIs, bearer tokens, passwords, and API keys."""

    def __init__(self, resolver: LocalProxySecretResolver | None = None) -> None:
        self._resolver = resolver
        self._uri_pattern = re.compile(r"vault://[a-zA-Z0-9_/?.=&-]+#[a-zA-Z0-9_]+")
        self._token_pattern = re.compile(
            r"(?i)(bearer|password|passwd|secret|api[_-]?key|token)\s*[:=]\s*([^\s,;]+)"
        )

    def redact(self, text: str) -> str:
        if not text:
            return text
        if self._resolver:
            for secret in self._resolver.get_known_secrets():
                if secret and len(secret) >= 4:
                    text = text.replace(secret, "[REDACTED_SECRET]")
        text = self._uri_pattern.sub("[REDACTED_VAULT_URI]", text)
        return self._token_pattern.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)


def parse_secret_reference(value: str) -> SecretReference:
    """Parse `vault://mount/path#key` into a validated reference."""

    normalized = value.strip()
    if not normalized.startswith("vault://") or "#" not in normalized:
        raise ValueError("Secret reference must use vault://mount/path#key")
    path_part, key_part = normalized.removeprefix("vault://").split("#", maxsplit=1)
    mount, separator, secret_path = path_part.partition("/")
    if not separator:
        raise ValueError("Vault reference must include a mount and secret path")
    secret_key, _, query = key_part.partition("?")
    version: int | None = None
    if query:
        values: dict[str, str] = {}
        for item in query.split("&"):
            key, separator, item_value = item.partition("=")
            if separator:
                values[key] = item_value
        if "version" in values:
            version = int(values["version"])
    return SecretReference(
        mount_path=mount,
        vault_path=secret_path,
        secret_key=secret_key,
        version=version,
    )
