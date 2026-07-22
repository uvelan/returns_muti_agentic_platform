"""Deterministic tests for warehouse canonical contracts."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from return_platform.canonical.warehouse import Warehouse, WarehouseProduct

_DIGEST = "c" * 64
_SOURCE_UPDATED_AT = datetime(2026, 7, 20, 9, 0, tzinfo=UTC)


def _provenance_payload(
    *,
    source_system: str = "STEP",
    source_record_id: str = "MP-900",
    source_updated_at: datetime | None = _SOURCE_UPDATED_AT,
) -> dict[str, object]:
    return {
        "source_system": source_system,
        "source_database": "product",
        "source_asset": "product",
        "source_record_id": source_record_id,
        "source_updated_at": source_updated_at,
        "source_version": "step-42",
        "source_event_id": "step-event-42",
        "source_hash": _DIGEST,
        "observed_at": datetime(2026, 7, 20, 9, 1, tzinfo=UTC),
        "mapping_version": "canonical-v1",
        "configuration_version": "data-platform-v1",
        "configuration_digest": _DIGEST,
    }


def _warehouse_payload() -> dict[str, object]:
    return {
        "warehouse_key": "FERGUSON:202",
        "warehouse_id": "202",
        "warehouse_name": "Newport News Distribution Center",
        "branch_id": "202",
        "active": True,
        "provenance": _provenance_payload(
            source_system="FERGUSON",
            source_record_id="202",
            source_updated_at=None,
        ),
    }


def _warehouse_product_payload() -> dict[str, object]:
    return {
        "warehouse_product_key": "STEP:MP-900:FERGUSON:202",
        "product_key": "STEP:MP-900",
        "warehouse_key": "FERGUSON:202",
        "product_warehouse_id": "945184*474*202",
        "bin_location": "A-01-02",
        "rank_code": "A",
        "quantity_on_hand": Decimal("12.500000"),
        "quantity_available": Decimal("10.250000"),
        "reorder_point": Decimal("2.000000"),
        "status": "ACTIVE",
        "source_updated_at": _SOURCE_UPDATED_AT,
        "provenance": _provenance_payload(),
    }


def test_warehouse_accepts_finalized_identity() -> None:
    warehouse = Warehouse.model_validate(_warehouse_payload())

    assert warehouse.warehouse_key == "FERGUSON:202"
    assert warehouse.active is True


def test_warehouse_allows_missing_name_and_branch() -> None:
    payload = _warehouse_payload()
    payload["warehouse_name"] = None
    payload["branch_id"] = None

    warehouse = Warehouse.model_validate(payload)

    assert warehouse.warehouse_name is None
    assert warehouse.branch_id is None


def test_warehouse_rejects_key_mismatch() -> None:
    payload = _warehouse_payload()
    payload["warehouse_key"] = "FERGUSON:999"

    with pytest.raises(ValidationError) as exc_info:
        Warehouse.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "warehouse_key_mismatch"


@pytest.mark.parametrize("warehouse_id", ["20:2", "20*2", "20 2", "20\t2"])
def test_warehouse_rejects_ambiguous_identifier(warehouse_id: str) -> None:
    payload = _warehouse_payload()
    payload["warehouse_id"] = warehouse_id

    with pytest.raises(ValidationError) as exc_info:
        Warehouse.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "string_pattern_mismatch"


def test_warehouse_rejects_integer_boolean_coercion() -> None:
    payload = _warehouse_payload()
    payload["active"] = 1

    with pytest.raises(ValidationError) as exc_info:
        Warehouse.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "bool_type"


def test_warehouse_rejects_unknown_fields() -> None:
    payload = _warehouse_payload()
    payload["timezone"] = "America/New_York"

    with pytest.raises(ValidationError) as exc_info:
        Warehouse.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "extra_forbidden"


def test_warehouse_product_accepts_inventory_facts() -> None:
    warehouse_product = WarehouseProduct.model_validate(
        _warehouse_product_payload(),
    )

    assert warehouse_product.product_key == "STEP:MP-900"
    assert warehouse_product.warehouse_key == "FERGUSON:202"
    assert warehouse_product.bin_location == "A-01-02"


def test_warehouse_product_preserves_negative_inventory_facts() -> None:
    payload = _warehouse_product_payload()
    payload["quantity_on_hand"] = Decimal("-2.000000")
    payload["quantity_available"] = Decimal("-5.000000")

    warehouse_product = WarehouseProduct.model_validate(payload)

    assert warehouse_product.quantity_on_hand == Decimal("-2.000000")
    assert warehouse_product.quantity_available == Decimal("-5.000000")


def test_warehouse_product_rejects_key_mismatch() -> None:
    payload = _warehouse_product_payload()
    payload["warehouse_product_key"] = "STEP:MP-900:FERGUSON:999"

    with pytest.raises(ValidationError) as exc_info:
        WarehouseProduct.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "warehouse_product_key_mismatch"


@pytest.mark.parametrize(
    ("field", "value", "error_type"),
    [
        (
            "product_key",
            "OTHER:MP-900",
            "warehouse_product_product_key_invalid",
        ),
        (
            "product_key",
            "STEP:MP:900",
            "warehouse_product_product_key_invalid",
        ),
        (
            "warehouse_key",
            "OTHER:202",
            "warehouse_product_warehouse_key_invalid",
        ),
        (
            "warehouse_key",
            "FERGUSON:20:2",
            "warehouse_product_warehouse_key_invalid",
        ),
    ],
)
def test_warehouse_product_rejects_invalid_namespaced_reference(
    field: str,
    value: str,
    error_type: str,
) -> None:
    payload = _warehouse_product_payload()
    payload[field] = value

    with pytest.raises(ValidationError) as exc_info:
        WarehouseProduct.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == error_type


def test_warehouse_product_rejects_non_step_provenance() -> None:
    payload = _warehouse_product_payload()
    payload["provenance"] = _provenance_payload(source_system="OTHER")

    with pytest.raises(ValidationError) as exc_info:
        WarehouseProduct.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "warehouse_product_source_system_invalid"


def test_warehouse_product_rejects_source_timestamp_drift() -> None:
    payload = _warehouse_product_payload()
    payload["source_updated_at"] = datetime(2026, 7, 21, 9, 0, tzinfo=UTC)

    with pytest.raises(ValidationError) as exc_info:
        WarehouseProduct.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "warehouse_product_source_updated_at_mismatch"


def test_warehouse_product_rejects_negative_reorder_point() -> None:
    payload = _warehouse_product_payload()
    payload["reorder_point"] = Decimal("-0.000001")

    with pytest.raises(ValidationError) as exc_info:
        WarehouseProduct.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "greater_than_equal"


def test_warehouse_product_rejects_float_quantity() -> None:
    payload = _warehouse_product_payload()
    payload["quantity_on_hand"] = 12.5

    with pytest.raises(ValidationError) as exc_info:
        WarehouseProduct.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "is_instance_of"


def test_bin_location_is_not_a_return_bay() -> None:
    payload = _warehouse_product_payload()
    payload["return_bay_key"] = "PLATFORM:FERGUSON:202:BAY:A"

    with pytest.raises(ValidationError) as exc_info:
        WarehouseProduct.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "extra_forbidden"
