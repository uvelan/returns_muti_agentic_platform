"""Deterministic tests for shared canonical contracts."""

from datetime import UTC, datetime, timedelta, timezone
from typing import Protocol, cast

import pytest
from pydantic import ValidationError

from return_platform.canonical.base import (
    CanonicalBaseModel,
    CanonicalIdentifier,
    IdentityQuality,
    SourceProvenance,
)

_DIGEST = "a" * 64


class _IdentityFixture(CanonicalBaseModel):
    canonical_key: CanonicalIdentifier


class _MutableSourceProvenance(Protocol):
    source_system: str


def _valid_provenance_payload() -> dict[str, object]:
    return {
        "source_system": "TDS",
        "source_database": "TDS",
        "source_asset": "salesInv",
        "source_record_id": "100*200",
        "source_updated_at": datetime(2026, 7, 20, 6, 0, tzinfo=UTC),
        "source_version": "42",
        "source_event_id": "evt-42",
        "source_hash": _DIGEST,
        "observed_at": datetime(2026, 7, 20, 6, 1, tzinfo=UTC),
        "mapping_version": "canonical-v1",
        "configuration_version": "data-platform-v1",
        "configuration_digest": _DIGEST,
    }


def test_identity_quality_has_only_approved_states() -> None:
    assert tuple(IdentityQuality) == (
        IdentityQuality.VERIFIED,
        IdentityQuality.CONDITIONAL,
        IdentityQuality.FALLBACK,
    )


def test_canonical_identifier_is_trimmed_and_bounded() -> None:
    model = _IdentityFixture(canonical_key="  TDS:100:200:2026-07-20  ")

    assert model.canonical_key == "TDS:100:200:2026-07-20"


@pytest.mark.parametrize(
    "invalid_key",
    ["", "   ", "TDS:100 200", "TDS:100\n200", "x" * 513],
)
def test_canonical_identifier_rejects_ambiguous_values(invalid_key: str) -> None:
    with pytest.raises(ValidationError):
        _IdentityFixture(canonical_key=invalid_key)


def test_source_provenance_is_strict_and_immutable() -> None:
    provenance = SourceProvenance.model_validate(_valid_provenance_payload())
    mutable_provenance = cast(_MutableSourceProvenance, provenance)

    with pytest.raises(ValidationError) as exc_info:
        mutable_provenance.source_system = "OTHER"

    assert exc_info.value.errors()[0]["type"] == "frozen_instance"


def test_source_provenance_forbids_unknown_fields() -> None:
    payload = _valid_provenance_payload()
    payload["unexpected"] = "value"

    with pytest.raises(ValidationError) as exc_info:
        SourceProvenance.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "extra_forbidden"


def test_source_provenance_rejects_type_coercion() -> None:
    payload = _valid_provenance_payload()
    payload["source_system"] = 123

    with pytest.raises(ValidationError) as exc_info:
        SourceProvenance.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "string_type"


def test_timestamps_are_normalized_to_utc() -> None:
    payload = _valid_provenance_payload()
    india_time = timezone(timedelta(hours=5, minutes=30))
    payload["observed_at"] = datetime(2026, 7, 20, 11, 31, tzinfo=india_time)

    provenance = SourceProvenance.model_validate(payload)

    assert provenance.observed_at == datetime(2026, 7, 20, 6, 1, tzinfo=UTC)
    assert provenance.observed_at.tzinfo is UTC


def test_naive_timestamps_are_rejected() -> None:
    payload = _valid_provenance_payload()
    payload["observed_at"] = datetime(2026, 7, 20, 6, 1)

    with pytest.raises(ValidationError) as exc_info:
        SourceProvenance.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "timezone_aware"


@pytest.mark.parametrize(
    "field,value",
    [
        ("mapping_version", "mapping version"),
        ("configuration_version", ""),
        ("configuration_digest", "A" * 64),
        ("source_hash", "not-a-sha256"),
    ],
)
def test_version_and_digest_references_fail_closed(field: str, value: str) -> None:
    payload = _valid_provenance_payload()
    payload[field] = value

    with pytest.raises(ValidationError):
        SourceProvenance.model_validate(payload)


def test_validation_errors_hide_input_values() -> None:
    payload = _valid_provenance_payload()
    payload["configuration_digest"] = "sensitive-invalid-digest"

    with pytest.raises(ValidationError) as exc_info:
        SourceProvenance.model_validate(payload)

    assert "sensitive-invalid-digest" not in str(exc_info.value)
