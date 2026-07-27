"""Unit tests for Vault secret handling and validation-before-persistence gates."""

from __future__ import annotations

import pytest

from return_platform.secrets.vault import (
    LocalProxySecretResolver,
    SecretRedactor,
    SecretReference,
)
from return_platform.validation.gates import (
    ConfigurationValidationError,
    ConfigurationValidationGate,
    SecretValidationError,
    SecretValidationGate,
)


@pytest.mark.asyncio
async def test_local_secret_resolver_rotation_and_redaction() -> None:
    resolver = LocalProxySecretResolver()
    reference = SecretReference(
        mount_path="secret",
        vault_path="production/data-sources/neo4j",
        secret_key="password",
    )

    stored = await resolver.put_secret(reference, "SuperSecretGraphPass123!")
    resolved = await resolver.get_secret(reference)

    assert stored.secret_version == 1
    assert resolved is not None
    assert resolved.value == "SuperSecretGraphPass123!"
    assert str(resolved) == "<REDACTED_SECRET>"

    with pytest.raises(ValueError, match="expected version"):
        await resolver.put_secret(reference, "replacement")

    rotated = await resolver.put_secret(
        reference.model_copy(update={"version": 1}),
        "ReplacementGraphPass456!",
    )
    assert rotated.secret_version == 2

    redactor = SecretRedactor(resolver=resolver)
    message = "password=ReplacementGraphPass456! bearer: token-value"
    redacted = redactor.redact(message)
    assert "ReplacementGraphPass456!" not in redacted
    assert "token-value" not in redacted


@pytest.mark.asyncio
async def test_validation_probe_runs_before_secret_is_stored() -> None:
    resolver = LocalProxySecretResolver()
    reference = SecretReference(
        mount_path="secret",
        vault_path="production/ai/google/key-01",
        secret_key="api_key",
    )

    async def rejected_probe(_value: str) -> tuple[str, ...]:
        raise RuntimeError("credential rejected")

    with pytest.raises(SecretValidationError, match="Target validation failed"):
        await SecretValidationGate.validate_and_stage(
            reference=reference,
            transient_value="invalid-key",
            resolver=resolver,
            probe=rejected_probe,
            subject_type="AI_CREDENTIAL_MODEL_BINDING",
            subject_key="GOOGLE:model:task",
            configuration_checksum="a" * 64,
            fingerprint_key=b"validation-key",
            actor_id="admin-1",
        )
    assert await resolver.get_secret(reference) is None

    async def accepted_probe(value: str) -> tuple[str, ...]:
        assert value == "valid-key"
        return ("AUTHENTICATION", "MODEL_ACCESS", "INFERENCE_PROBE")

    receipt = await SecretValidationGate.validate_and_stage(
        reference=reference,
        transient_value="valid-key",
        resolver=resolver,
        probe=accepted_probe,
        subject_type="AI_CREDENTIAL_MODEL_BINDING",
        subject_key="GOOGLE:model:task",
        configuration_checksum="b" * 64,
        fingerprint_key=b"validation-key",
        actor_id="admin-1",
    )
    assert receipt.status == "PASSED"
    assert receipt.secret_version == 1
    assert receipt.secret_fingerprint
    assert receipt.configuration_checksum == "b" * 64
    assert (await resolver.get_secret(reference)) is not None


@pytest.mark.asyncio
async def test_secret_reference_validation_returns_redacted_receipt() -> None:
    resolver = LocalProxySecretResolver()
    reference = SecretReference(
        mount_path="secret",
        vault_path="production/data-sources/omc-sqlserver",
        secret_key="password",
    )
    await resolver.put_secret(reference, "valid-password")

    receipt = await SecretValidationGate.validate_secret_reference(
        reference,
        resolver,
        actor_id="admin-1",
    )
    assert receipt.status == "PASSED"
    assert receipt.target_uri.startswith("vault://secret/")
    assert "valid-password" not in receipt.model_dump_json()

    missing_reference = reference.model_copy(update={"secret_key": "missing"})
    with pytest.raises(SecretValidationError, match="Secret could not be resolved"):
        await SecretValidationGate.validate_secret_reference(
            missing_reference,
            resolver,
        )


def test_configuration_validation_gate_rejects_empty_payload() -> None:
    receipt = ConfigurationValidationGate.validate_domain_payload(
        "RETURN_PLATFORM",
        {"schema_version": "1"},
        "admin-1",
    )
    assert receipt.status == "PASSED"
    assert receipt.target_uri == "domain://RETURN_PLATFORM"

    with pytest.raises(ConfigurationValidationError, match="Payload cannot be empty"):
        ConfigurationValidationGate.validate_domain_payload("RETURN_PLATFORM", {})


@pytest.mark.asyncio
async def test_secret_rollback_restores_previous_generation() -> None:
    resolver = LocalProxySecretResolver()
    reference = SecretReference(
        mount_path="secret",
        vault_path="production/ai/google/key-01",
        secret_key="api_key",
    )

    first = await resolver.put_secret(reference, "first-generation")
    previous = await resolver.get_secret(reference)
    assert previous is not None

    second = await resolver.put_secret(
        reference.model_copy(update={"version": first.secret_version}),
        "second-generation",
    )
    assert second.secret_version is not None
    await resolver.rollback_secret_write(reference, second.secret_version, previous)

    restored = await resolver.get_secret(reference)
    assert restored is not None
    assert restored.value == "first-generation"
    assert restored.secret_version == second.secret_version + 1


@pytest.mark.asyncio
async def test_secret_rollback_removes_new_field() -> None:
    resolver = LocalProxySecretResolver()
    reference = SecretReference(
        mount_path="secret",
        vault_path="production/data-sources/new-source",
        secret_key="password",
    )

    staged = await resolver.put_secret(reference, "temporary-value")
    assert staged.secret_version is not None
    await resolver.rollback_secret_write(reference, staged.secret_version, None)

    assert await resolver.get_secret(reference) is None
