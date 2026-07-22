"""Deterministic tests for canonical return contracts."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from return_platform.canonical.return_models import (
    FreightShipment,
    Return,
    ReturnItem,
    ReturnVersion,
)

_DIGEST = "d" * 64
_CREATED_AT = datetime(2026, 7, 20, 10, 0, tzinfo=UTC)
_UPDATED_AT = datetime(2026, 7, 20, 11, 0, tzinfo=UTC)


def _provenance_payload(
    *,
    source_system: str = "OMC",
    source_record_id: str = "RET-100",
) -> dict[str, object]:
    return {
        "source_system": source_system,
        "source_database": "omc",
        "source_asset": "returns.Returns",
        "source_record_id": source_record_id,
        "source_updated_at": _UPDATED_AT,
        "source_version": "omc-8",
        "source_event_id": "event-8",
        "source_hash": _DIGEST,
        "observed_at": datetime(2026, 7, 20, 11, 1, tzinfo=UTC),
        "mapping_version": "canonical-v1",
        "configuration_version": "data-platform-v1",
        "configuration_digest": _DIGEST,
    }


def _return_payload() -> dict[str, object]:
    return {
        "return_key": "OMC:V2:RET-100",
        "return_version": ReturnVersion.V2,
        "source_return_id": "RET-100",
        "sales_order_key": "TDS:202:SO-77:2026-07-20",
        "source_order_id": "SO-77",
        "return_status": "OPEN",
        "shipping_path": "FREIGHT",
        "created_at": _CREATED_AT,
        "updated_at": _UPDATED_AT,
        "provenance": _provenance_payload(),
    }


def _return_item_payload() -> dict[str, object]:
    return {
        "return_item_key": "OMC:V2:ITEM:CART-1",
        "return_key": "OMC:V2:RET-100",
        "source_cart_item_id": "CART-1",
        "order_line_key": "TDS:202:SO-77:2026-07-20:LINE:10",
        "product_key": "STEP:MP-900",
        "omc_unique_id": "UNIQUE-900",
        "returned_quantity": Decimal("1.000000"),
        "return_reason": "DAMAGED",
        "item_condition": "OPEN_BOX",
        "return_item_status": "ACCEPTED",
        "provenance": _provenance_payload(source_record_id="CART-1"),
    }


def _freight_payload() -> dict[str, object]:
    return {
        "freight_shipment_key": "OMC:FREIGHT:FS-1",
        "freight_shipment_id": "FS-1",
        "return_key": "OMC:V2:RET-100",
        "bol_number": "BOL-100",
        "carrier": "Example Freight",
        "scac": "EXFR",
        "freight_status": "BOOKED",
        "quote_reference": "QUOTE-1",
        "created_at": _CREATED_AT,
        "updated_at": _UPDATED_AT,
        "provenance": _provenance_payload(source_record_id="FS-1"),
    }


def test_return_accepts_v2_identity_and_conditional_order_join() -> None:
    canonical_return = Return.model_validate(_return_payload())

    assert canonical_return.return_key == "OMC:V2:RET-100"
    assert canonical_return.sales_order_key == "TDS:202:SO-77:2026-07-20"


def test_return_allows_unresolved_sales_order_join() -> None:
    payload = _return_payload()
    payload["sales_order_key"] = None

    canonical_return = Return.model_validate(payload)

    assert canonical_return.sales_order_key is None


@pytest.mark.parametrize(
    ("field", "value", "error_type"),
    [
        ("return_key", "OMC:V1:RET-100", "return_key_mismatch"),
        ("source_return_id", "RET:100", "string_pattern_mismatch"),
        ("source_order_id", "SO*77", "string_pattern_mismatch"),
        (
            "sales_order_key",
            "TDS:202:OTHER:2026-07-20",
            "return_sales_order_id_mismatch",
        ),
        (
            "sales_order_key",
            "TDS:202:SO-77",
            "return_sales_order_key_invalid",
        ),
    ],
)
def test_return_rejects_invalid_identity_or_order_reference(
    field: str,
    value: str,
    error_type: str,
) -> None:
    payload = _return_payload()
    payload[field] = value

    with pytest.raises(ValidationError) as exc_info:
        Return.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == error_type


def test_return_rejects_non_omc_provenance() -> None:
    payload = _return_payload()
    payload["provenance"] = _provenance_payload(source_system="OTHER")

    with pytest.raises(ValidationError) as exc_info:
        Return.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "return_source_system_invalid"


def test_return_rejects_source_record_drift() -> None:
    payload = _return_payload()
    payload["provenance"] = _provenance_payload(source_record_id="OTHER")

    with pytest.raises(ValidationError) as exc_info:
        Return.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "return_source_record_id_mismatch"


def test_return_rejects_reverse_timeline() -> None:
    payload = _return_payload()
    payload["updated_at"] = datetime(2026, 7, 20, 9, 59, tzinfo=UTC)

    with pytest.raises(ValidationError) as exc_info:
        Return.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "return_timestamp_order_invalid"


def test_return_rejects_string_datetime_coercion() -> None:
    payload = _return_payload()
    payload["created_at"] = "2026-07-20T10:00:00Z"

    with pytest.raises(ValidationError) as exc_info:
        Return.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "datetime_type"


def test_return_item_accepts_resolved_references_with_bridge_evidence() -> None:
    return_item = ReturnItem.model_validate(_return_item_payload())

    assert return_item.product_key == "STEP:MP-900"
    assert return_item.omc_unique_id == "UNIQUE-900"


def test_return_item_allows_all_conditional_references_to_remain_unresolved() -> None:
    payload = _return_item_payload()
    payload["order_line_key"] = None
    payload["product_key"] = None
    payload["omc_unique_id"] = None

    return_item = ReturnItem.model_validate(payload)

    assert return_item.order_line_key is None
    assert return_item.product_key is None


@pytest.mark.parametrize(
    ("field", "value", "error_type"),
    [
        ("return_item_key", "OMC:V1:ITEM:CART-1", "return_item_key_mismatch"),
        ("return_key", "OMC:V3:RET-100", "return_key_invalid"),
        (
            "order_line_key",
            "TDS:202:SO-77:LINE:10",
            "return_item_order_line_key_invalid",
        ),
        (
            "product_key",
            "OTHER:MP-900",
            "return_item_product_key_invalid",
        ),
        (
            "product_key",
            "STEP:MP:900",
            "return_item_product_key_invalid",
        ),
    ],
)
def test_return_item_rejects_invalid_identity_or_reference(
    field: str,
    value: str,
    error_type: str,
) -> None:
    payload = _return_item_payload()
    payload[field] = value

    with pytest.raises(ValidationError) as exc_info:
        ReturnItem.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == error_type


def test_return_item_requires_unique_id_for_resolved_product() -> None:
    payload = _return_item_payload()
    payload["omc_unique_id"] = None

    with pytest.raises(ValidationError) as exc_info:
        ReturnItem.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "return_item_product_bridge_evidence_required"


def test_return_item_rejects_negative_quantity() -> None:
    payload = _return_item_payload()
    payload["returned_quantity"] = Decimal("-0.000001")

    with pytest.raises(ValidationError) as exc_info:
        ReturnItem.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "greater_than_equal"


def test_return_item_rejects_float_quantity() -> None:
    payload = _return_item_payload()
    payload["returned_quantity"] = 1.0

    with pytest.raises(ValidationError) as exc_info:
        ReturnItem.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "is_instance_of"


def test_return_item_rejects_source_record_drift() -> None:
    payload = _return_item_payload()
    payload["provenance"] = _provenance_payload(source_record_id="OTHER")

    with pytest.raises(ValidationError) as exc_info:
        ReturnItem.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "return_item_source_record_id_mismatch"


def test_freight_shipment_accepts_finalized_identity() -> None:
    freight = FreightShipment.model_validate(_freight_payload())

    assert freight.freight_shipment_key == "OMC:FREIGHT:FS-1"


@pytest.mark.parametrize(
    ("field", "value", "error_type"),
    [
        (
            "freight_shipment_key",
            "OMC:FREIGHT:OTHER",
            "freight_shipment_key_mismatch",
        ),
        ("return_key", "OMC:V3:RET-100", "return_key_invalid"),
    ],
)
def test_freight_shipment_rejects_invalid_identity_or_return(
    field: str,
    value: str,
    error_type: str,
) -> None:
    payload = _freight_payload()
    payload[field] = value

    with pytest.raises(ValidationError) as exc_info:
        FreightShipment.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == error_type


def test_freight_shipment_rejects_reverse_timeline() -> None:
    payload = _freight_payload()
    payload["updated_at"] = datetime(2026, 7, 20, 9, 0, tzinfo=UTC)

    with pytest.raises(ValidationError) as exc_info:
        FreightShipment.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "freight_shipment_timestamp_order_invalid"


def test_return_models_reject_unknown_package_or_ppl_fields() -> None:
    payload = _return_payload()
    payload["ppl_tracking"] = "PPL-1"

    with pytest.raises(ValidationError) as exc_info:
        Return.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "extra_forbidden"
