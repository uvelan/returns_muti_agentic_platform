"""Deterministic tests for platform-owned Bay contracts."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from return_platform.canonical.bay import Bay, BayAssignment

_ASSIGNMENT_ID = UUID("f7832d8e-73c5-4dd9-9f56-69b52c979b87")
_CREATED_AT = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
_CONFIRMED_AT = datetime(2026, 7, 20, 12, 5, tzinfo=UTC)
_RELEASED_AT = datetime(2026, 7, 20, 13, 0, tzinfo=UTC)


def _bay_payload() -> dict[str, object]:
    return {
        "bay_key": "PLATFORM:FERGUSON:202:BAY:A",
        "bay_id": "A",
        "warehouse_key": "FERGUSON:202",
        "branch_id": "202",
        "bay_name": "Primary Returns Bay",
        "bay_type": "STANDARD",
        "active": True,
        "priority": 10,
        "supported_shipping_paths": ("PARCEL", "FREIGHT"),
        "supported_product_types": ("STANDARD", "SERIALIZED"),
        "maximum_package_count": 50,
        "overflow_bay_key": "PLATFORM:FERGUSON:202:BAY:B",
        "configuration_version": "bay-config-v1",
    }


def _assignment_payload() -> dict[str, object]:
    return {
        "assignment_id": _ASSIGNMENT_ID,
        "return_key": "OMC:V2:RET-100",
        "return_item_key": "OMC:V2:ITEM:CART-1",
        "sales_order_key": "TDS:202:SO-77:2026-07-20",
        "order_line_key": "TDS:202:SO-77:2026-07-20:LINE:10",
        "warehouse_key": "FERGUSON:202",
        "bay_key": "PLATFORM:FERGUSON:202:BAY:A",
        "package_count": 2,
        "status": "CONFIRMED",
        "assigned_by": "bay-assignment-agent",
        "confirmed_by": "associate-42",
        "created_at": _CREATED_AT,
        "confirmed_at": _CONFIRMED_AT,
        "released_at": _RELEASED_AT,
        "evidence": (
            {
                "evidence_type": "POLICY_DECISION",
                "evidence_reference": "decision-100",
            },
        ),
    }


def test_bay_accepts_platform_identity_and_overflow() -> None:
    bay = Bay.model_validate(_bay_payload())

    assert bay.bay_key == "PLATFORM:FERGUSON:202:BAY:A"
    assert bay.overflow_bay_key == "PLATFORM:FERGUSON:202:BAY:B"


def test_bay_allows_no_overflow_and_empty_capability_lists() -> None:
    payload = _bay_payload()
    payload["overflow_bay_key"] = None
    payload["supported_shipping_paths"] = ()
    payload["supported_product_types"] = ()

    bay = Bay.model_validate(payload)

    assert bay.overflow_bay_key is None


@pytest.mark.parametrize(
    ("field", "value", "error_type"),
    [
        ("bay_key", "PLATFORM:FERGUSON:202:BAY:X", "bay_key_mismatch"),
        ("warehouse_key", "OTHER:202", "bay_warehouse_key_invalid"),
        ("warehouse_key", "FERGUSON:20:2", "bay_warehouse_key_invalid"),
        ("bay_id", "A:B", "string_pattern_mismatch"),
        (
            "overflow_bay_key",
            "PLATFORM:FERGUSON:999:BAY:B",
            "bay_overflow_warehouse_mismatch",
        ),
        (
            "overflow_bay_key",
            "PLATFORM:FERGUSON:202:BAY:A",
            "bay_overflow_self_reference",
        ),
        (
            "overflow_bay_key",
            "FERGUSON:202:BAY:B",
            "bay_overflow_key_invalid",
        ),
    ],
)
def test_bay_rejects_invalid_identity_or_overflow(
    field: str,
    value: str,
    error_type: str,
) -> None:
    payload = _bay_payload()
    payload[field] = value

    with pytest.raises(ValidationError) as exc_info:
        Bay.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == error_type


def test_bay_rejects_duplicate_shipping_paths() -> None:
    payload = _bay_payload()
    payload["supported_shipping_paths"] = ("PARCEL", "PARCEL")

    with pytest.raises(ValidationError) as exc_info:
        Bay.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "bay_duplicate_shipping_path"


def test_bay_rejects_duplicate_product_types() -> None:
    payload = _bay_payload()
    payload["supported_product_types"] = ("STANDARD", "STANDARD")

    with pytest.raises(ValidationError) as exc_info:
        Bay.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "bay_duplicate_product_type"


@pytest.mark.parametrize(
    ("field", "value", "error_type"),
    [
        ("priority", -1, "greater_than_equal"),
        ("maximum_package_count", 0, "greater_than_equal"),
        ("active", 1, "bool_type"),
        ("priority", True, "int_type"),
    ],
)
def test_bay_rejects_invalid_scalar(
    field: str,
    value: object,
    error_type: str,
) -> None:
    payload = _bay_payload()
    payload[field] = value

    with pytest.raises(ValidationError) as exc_info:
        Bay.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == error_type


def test_bay_assignment_accepts_consistent_references_and_timeline() -> None:
    assignment = BayAssignment.model_validate(_assignment_payload())

    assert assignment.assignment_id == _ASSIGNMENT_ID
    assert assignment.package_count == 2


@pytest.mark.parametrize(
    ("field", "value", "error_type"),
    [
        ("return_key", "OMC:V3:RET-100", "bay_assignment_return_key_invalid"),
        (
            "return_item_key",
            "OMC:V1:ITEM:CART-1",
            "bay_assignment_return_version_mismatch",
        ),
        (
            "return_item_key",
            "OMC:V2:CART-1",
            "bay_assignment_return_item_key_invalid",
        ),
        (
            "sales_order_key",
            "TDS:202:SO-77",
            "bay_assignment_sales_order_key_invalid",
        ),
        (
            "order_line_key",
            "TDS:202:OTHER:2026-07-20:LINE:10",
            "bay_assignment_order_line_parent_mismatch",
        ),
        (
            "order_line_key",
            "TDS:202:SO-77:LINE:10",
            "bay_assignment_order_line_key_invalid",
        ),
        (
            "warehouse_key",
            "OTHER:202",
            "bay_assignment_warehouse_key_invalid",
        ),
        (
            "bay_key",
            "PLATFORM:FERGUSON:999:BAY:A",
            "bay_assignment_bay_warehouse_mismatch",
        ),
    ],
)
def test_bay_assignment_rejects_inconsistent_reference(
    field: str,
    value: str,
    error_type: str,
) -> None:
    payload = _assignment_payload()
    payload[field] = value

    with pytest.raises(ValidationError) as exc_info:
        BayAssignment.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == error_type


@pytest.mark.parametrize(
    ("confirmed_by", "confirmed_at"),
    [(None, _CONFIRMED_AT), ("associate-42", None)],
)
def test_bay_assignment_rejects_partial_confirmation(
    confirmed_by: str | None,
    confirmed_at: datetime | None,
) -> None:
    payload = _assignment_payload()
    payload["confirmed_by"] = confirmed_by
    payload["confirmed_at"] = confirmed_at

    with pytest.raises(ValidationError) as exc_info:
        BayAssignment.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "bay_assignment_confirmation_pair_invalid"


def test_bay_assignment_rejects_confirmation_before_creation() -> None:
    payload = _assignment_payload()
    payload["confirmed_at"] = datetime(2026, 7, 20, 11, 59, tzinfo=UTC)

    with pytest.raises(ValidationError) as exc_info:
        BayAssignment.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "bay_assignment_confirmation_time_invalid"


def test_bay_assignment_rejects_release_before_confirmation() -> None:
    payload = _assignment_payload()
    payload["released_at"] = datetime(2026, 7, 20, 12, 4, tzinfo=UTC)

    with pytest.raises(ValidationError) as exc_info:
        BayAssignment.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "bay_assignment_release_time_invalid"


def test_bay_assignment_rejects_duplicate_evidence() -> None:
    evidence = {
        "evidence_type": "POLICY_DECISION",
        "evidence_reference": "decision-100",
    }
    payload = _assignment_payload()
    payload["evidence"] = (evidence, evidence)

    with pytest.raises(ValidationError) as exc_info:
        BayAssignment.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "bay_assignment_duplicate_evidence"


def test_bay_assignment_rejects_string_uuid_coercion() -> None:
    payload = _assignment_payload()
    payload["assignment_id"] = str(_ASSIGNMENT_ID)

    with pytest.raises(ValidationError) as exc_info:
        BayAssignment.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "is_instance_of"


def test_bay_assignment_rejects_zero_package_count() -> None:
    payload = _assignment_payload()
    payload["package_count"] = 0

    with pytest.raises(ValidationError) as exc_info:
        BayAssignment.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "greater_than_equal"
