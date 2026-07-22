"""Deterministic tests for the canonical product contract."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from return_platform.canonical.product import Product

_DIGEST = "b" * 64
_SOURCE_UPDATED_AT = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)


def _provenance_payload(
    *,
    source_system: str = "STEP",
    source_updated_at: datetime | None = _SOURCE_UPDATED_AT,
) -> dict[str, object]:
    return {
        "source_system": source_system,
        "source_database": "product",
        "source_asset": "product",
        "source_record_id": "MP-900",
        "source_updated_at": source_updated_at,
        "source_version": "step-42",
        "source_event_id": "step-event-42",
        "source_hash": _DIGEST,
        "observed_at": datetime(2026, 7, 20, 8, 1, tzinfo=UTC),
        "mapping_version": "canonical-v1",
        "configuration_version": "data-platform-v1",
        "configuration_digest": _DIGEST,
    }


def _product_payload() -> dict[str, object]:
    return {
        "product_key": "STEP:MP-900",
        "master_product_id": "MP-900",
        "product_id": "945184*474",
        "vendor_product_code": "VENDOR-VALVE-42",
        "upc": "012345678905",
        "base_model_number": "VALVE-X",
        "description": "Commercial valve",
        "long_description": "Commercial valve for approved industrial use.",
        "product_status": "ACTIVE",
        "category_id": "VALVES",
        "category_name": "Valves",
        "brand_name": "Example Brand",
        "manufacturer_name": "Example Manufacturing",
        "supplier_name": "Example Supplier",
        "primary_vendor_id": "VENDOR-42",
        "unit_of_measure": "EA",
        "unit_of_measure_description": "Each",
        "serial_number_required": False,
        "obsolete": False,
        "substitute_product_key": "STEP:MP-901",
        "substitution_notes": "Use only when MP-900 is unavailable.",
        "weight": "12.5 lb",
        "dimensions": "10 x 8 x 6 in",
        "source_updated_at": _SOURCE_UPDATED_AT,
        "provenance": _provenance_payload(),
    }


def test_product_accepts_step_identity_and_enrichment() -> None:
    product = Product.model_validate(_product_payload())

    assert product.product_key == "STEP:MP-900"
    assert product.product_id == "945184*474"
    assert product.substitute_product_key == "STEP:MP-901"


def test_product_rejects_key_mismatch() -> None:
    payload = _product_payload()
    payload["product_key"] = "STEP:MP-999"

    with pytest.raises(ValidationError) as exc_info:
        Product.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "product_key_mismatch"


@pytest.mark.parametrize(
    "master_product_id",
    ["MP:900", "MP 900", "MP\t900"],
)
def test_product_rejects_ambiguous_master_product_id(
    master_product_id: str,
) -> None:
    payload = _product_payload()
    payload["master_product_id"] = master_product_id

    with pytest.raises(ValidationError) as exc_info:
        Product.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "string_pattern_mismatch"


def test_product_rejects_non_step_provenance() -> None:
    payload = _product_payload()
    payload["provenance"] = _provenance_payload(source_system="OTHER")

    with pytest.raises(ValidationError) as exc_info:
        Product.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "product_source_system_invalid"


def test_product_rejects_source_timestamp_drift() -> None:
    payload = _product_payload()
    payload["source_updated_at"] = datetime(2026, 7, 21, 8, 0, tzinfo=UTC)

    with pytest.raises(ValidationError) as exc_info:
        Product.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "product_source_updated_at_mismatch"


@pytest.mark.parametrize(
    "substitute_product_key",
    ["OTHER:MP-901", "STEP:", "STEP:MP:901"],
)
def test_product_rejects_invalid_substitute_namespace(
    substitute_product_key: str,
) -> None:
    payload = _product_payload()
    payload["substitute_product_key"] = substitute_product_key

    with pytest.raises(ValidationError) as exc_info:
        Product.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "product_substitute_namespace_invalid"


def test_product_rejects_self_substitution() -> None:
    payload = _product_payload()
    payload["substitute_product_key"] = "STEP:MP-900"

    with pytest.raises(ValidationError) as exc_info:
        Product.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "product_self_substitution_invalid"


@pytest.mark.parametrize(
    "field",
    ["serial_number_required", "obsolete"],
)
def test_product_rejects_integer_boolean_coercion(field: str) -> None:
    payload = _product_payload()
    payload[field] = 1

    with pytest.raises(ValidationError) as exc_info:
        Product.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "bool_type"


def test_product_preserves_measurement_units_as_source_text() -> None:
    payload = _product_payload()
    payload["weight"] = "  12.5 lb  "
    payload["dimensions"] = "  10 x 8 x 6 in  "

    product = Product.model_validate(payload)

    assert product.weight == "12.5 lb"
    assert product.dimensions == "10 x 8 x 6 in"


@pytest.mark.parametrize("field", ["weight", "dimensions"])
def test_product_rejects_unitless_numeric_measurements(field: str) -> None:
    payload = _product_payload()
    payload[field] = 12.5

    with pytest.raises(ValidationError) as exc_info:
        Product.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "string_type"


def test_product_allows_unknown_boolean_state_as_none() -> None:
    payload = _product_payload()
    payload["serial_number_required"] = None
    payload["obsolete"] = None

    product = Product.model_validate(payload)

    assert product.serial_number_required is None
    assert product.obsolete is None


def test_product_rejects_unknown_fields() -> None:
    payload = _product_payload()
    payload["bin_location"] = "A-01"

    with pytest.raises(ValidationError) as exc_info:
        Product.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "extra_forbidden"
