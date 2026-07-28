"""Validation-before-persistence gates and immutable validation receipts."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from return_platform.secrets.vault import ResolvedSecret, SecretReference, SecretResolver

SecretProbe = Callable[[str], Awaitable[tuple[str, ...]]]


class SecretValidationError(Exception):
    def __init__(self, uri: str, reason: str) -> None:
        super().__init__(f"Secret validation failed for {uri}: {reason}")
        self.uri = uri
        self.reason = reason


class ConfigurationValidationError(Exception):
    def __init__(self, domain_key: str, reason: str) -> None:
        super().__init__(f"Configuration validation failed for domain {domain_key}: {reason}")
        self.domain_key = domain_key
        self.reason = reason


class ValidationReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    receipt_id: UUID = Field(default_factory=uuid4)
    target_uri: str
    subject_type: str = "SECRET_REFERENCE"
    subject_key: str = "unknown"
    verified_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    valid_until: datetime = Field(default_factory=lambda: datetime.now(UTC) + timedelta(minutes=15))
    verified_by: str = "system"
    status: str = "PASSED"
    checksum_sha256: str
    configuration_checksum: str | None = None
    secret_fingerprint: str | None = None
    secret_version: int | None = None
    tests: tuple[str, ...] = ()


def _hmac_fingerprint(secret: str, fingerprint_key: bytes) -> str:
    return hmac.new(fingerprint_key, secret.encode("utf-8"), hashlib.sha256).hexdigest()


class SecretValidationGate:
    """Validate a credential against its target before Vault persistence."""

    @staticmethod
    async def validate_secret_reference(
        reference: SecretReference,
        resolver: SecretResolver,
        actor_id: str = "system",
        *,
        fingerprint_key: bytes = b"test-only-validation-fingerprint-key",
    ) -> ValidationReceipt:
        resolved = await resolver.get_secret(reference)
        if resolved is None or not resolved.value.strip():
            raise SecretValidationError(reference.to_uri(), "Secret could not be resolved")
        return SecretValidationGate._receipt(
            reference=resolved.reference,
            value=resolved.value,
            secret_version=resolved.secret_version,
            actor_id=actor_id,
            fingerprint_key=fingerprint_key,
            tests=("VAULT_RESOLUTION", "NON_EMPTY_VALUE"),
        )

    @staticmethod
    async def validate_and_stage(
        *,
        reference: SecretReference,
        transient_value: str,
        resolver: SecretResolver,
        probe: SecretProbe,
        subject_type: str,
        subject_key: str,
        configuration_checksum: str,
        fingerprint_key: bytes,
        actor_id: str,
    ) -> ValidationReceipt:
        value = transient_value.strip()
        if not value:
            raise SecretValidationError(reference.to_uri(), "Submitted secret is blank")
        try:
            completed_tests = await probe(value)
        except Exception as exc:
            raise SecretValidationError(
                reference.to_uri(),
                f"Target validation failed: {type(exc).__name__}",
            ) from exc
        if not completed_tests:
            raise SecretValidationError(reference.to_uri(), "Validator returned no evidence")

        stored: ResolvedSecret = await resolver.put_secret(reference, value)
        return SecretValidationGate._receipt(
            reference=stored.reference,
            value=value,
            secret_version=stored.secret_version,
            actor_id=actor_id,
            fingerprint_key=fingerprint_key,
            tests=completed_tests,
            subject_type=subject_type,
            subject_key=subject_key,
            configuration_checksum=configuration_checksum,
        )

    @staticmethod
    def receipt_for_validated_secret(
        *,
        resolved: ResolvedSecret,
        tests: tuple[str, ...],
        subject_type: str,
        subject_key: str,
        configuration_checksum: str,
        fingerprint_key: bytes,
        actor_id: str,
    ) -> ValidationReceipt:
        """Build a receipt after an external probe validated an existing Vault secret."""

        if not tests:
            raise ValueError("Validated secret receipt requires completed tests")
        return SecretValidationGate._receipt(
            reference=resolved.reference,
            value=resolved.value,
            secret_version=resolved.secret_version,
            actor_id=actor_id,
            fingerprint_key=fingerprint_key,
            tests=tests,
            subject_type=subject_type,
            subject_key=subject_key,
            configuration_checksum=configuration_checksum,
        )

    @staticmethod
    def _receipt(
        *,
        reference: SecretReference,
        value: str,
        secret_version: int | None,
        actor_id: str,
        fingerprint_key: bytes,
        tests: tuple[str, ...],
        subject_type: str = "SECRET_REFERENCE",
        subject_key: str = "unknown",
        configuration_checksum: str | None = None,
    ) -> ValidationReceipt:
        uri = reference.to_uri()
        checksum = hashlib.sha256(
            json.dumps(
                {
                    "target": uri,
                    "subjectType": subject_type,
                    "subjectKey": subject_key,
                    "configurationChecksum": configuration_checksum,
                    "tests": tests,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return ValidationReceipt(
            target_uri=uri,
            subject_type=subject_type,
            subject_key=subject_key,
            verified_by=actor_id,
            checksum_sha256=checksum,
            configuration_checksum=configuration_checksum,
            secret_fingerprint=_hmac_fingerprint(value, fingerprint_key),
            secret_version=secret_version,
            tests=tests,
        )


class ConfigurationValidationGate:
    @staticmethod
    def validate_domain_payload(
        domain_key: str,
        payload: dict[str, Any],
        actor_id: str = "system",
    ) -> ValidationReceipt:
        if not payload:
            raise ConfigurationValidationError(domain_key, "Payload cannot be empty")
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return ValidationReceipt(
            target_uri=f"domain://{domain_key}",
            subject_type="CONFIGURATION_DOMAIN",
            subject_key=domain_key,
            verified_by=actor_id,
            checksum_sha256=hashlib.sha256(encoded).hexdigest(),
            tests=("JSON_OBJECT", "NON_EMPTY_PAYLOAD"),
        )
