"""Security boundaries for configuration-time source validation."""

from __future__ import annotations

import pytest

from return_platform.configuration.runtime_validation import (
    DataSourceValidateAndStageRequest,
    _extract_hosts,
    _validate_mongodb_dsn_security,
)


def _mongo_request(dsn: str) -> DataSourceValidateAndStageRequest:
    return DataSourceValidateAndStageRequest(
        sourceKey="source-mongodb",
        sourceType="MONGODB",
        accessMode="READ_ONLY",
        database="source",
        credential=dsn,
        credentialKind="DSN",
        vaultReference="vault://secret/production/data-sources/source-mongodb#dsn",
    )


def test_mongodb_validation_checks_every_explicit_seed_host() -> None:
    request = _mongo_request(
        "mongodb://user:password@mongo-a:27017,mongo-b:27018/source?replicaSet=rs0&tls=true"
    )

    assert _extract_hosts(request) == ("mongo-a", "mongo-b")


def test_mongodb_validation_rejects_srv_resolution() -> None:
    request = _mongo_request("mongodb+srv://user:password@cluster.example/source")

    with pytest.raises(ValueError, match="mongodb\\+srv is not supported"):
        _extract_hosts(request)


@pytest.mark.parametrize(
    "option",
    (
        "tlsAllowInvalidCertificates=true",
        "tlsAllowInvalidHostnames=1",
        "tlsInsecure=yes",
        "sslAllowInvalidCertificates=on",
    ),
)
def test_mongodb_validation_rejects_disabled_tls_verification(option: str) -> None:
    with pytest.raises(ValueError, match="disables TLS verification"):
        _validate_mongodb_dsn_security(
            f"mongodb://user:password@mongo-a:27017/source?tls=true&{option}"
        )


def test_mongodb_validation_accepts_verified_tls() -> None:
    _validate_mongodb_dsn_security("mongodb://user:password@mongo-a:27017/source?tls=true")
