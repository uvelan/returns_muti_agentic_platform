"""Deterministic tests for canonical sales-order and order-line contracts."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from return_platform.canonical.base import IdentityQuality
from return_platform.canonical.order import OrderLine, SalesOrder

_DIGEST = "a" * 64
_SOURCE_UPDATED_AT = datetime(2026, 7, 20, 7, 0, tzinfo=UTC)


def _provenance_payload(
    *,
    source_system: str = "TDS",
    source_record_id: str = "101*W123456",
    source_updated_at: datetime | None = _SOURCE_UPDATED_AT,
    source_version: str | None = "42",
) -> dict[str, object]:
    return {
        "source_system": source_system,
        "source_database": "TDS",
        "source_asset": "salesInv",
        "source_record_id": source_record_id,
        "source_updated_at": source_updated_at,
        "source_version": source_version,
        "source_event_id": "evt-42",
        "source_hash": _DIGEST,
        "observed_at": datetime(2026, 7, 20, 7, 1, tzinfo=UTC),
        "mapping_version": "canonical-v1",
        "configuration_version": "data-platform-v1",
        "configuration_digest": _DIGEST,
    }


def _sales_order_payload() -> dict[str, object]:
    return {
        "sales_order_key": "TDS:101:W123456:evt-created-42",
        "source_document_id": "101*W123456",
        "source_system": "TDS",
        "account_id": "101",
        "order_id": "W123456",
        "order_instance_key": "evt-created-42",
        "customer_account_key": "CUSTOMER_CDM:101*customer-200",
        "customer_id": "customer-200",
        "customer_name_snapshot": "Acme Plumbing",
        "customer_po_number": "PO-900",
        "job_name": "Hospital Expansion",
        "sales_type": "COUNTER",
        "order_status": "INVOICED",
        "order_date": date(2026, 7, 1),
        "invoice_date": date(2026, 7, 2),
        "request_date": date(2026, 7, 1),
        "order_total_amount": Decimal("1250.2500"),
        "recorded_refund_amount": Decimal("0.0000"),
        "payment_authorization_code": "AUTH-42",
        "selling_warehouse_key": "FERGUSON:101",
        "ship_from_warehouse_key": "FERGUSON:202",
        "ship_to": {
            "address_id": "ship-to-1",
            "address_type": "SHIPPING",
            "line1": "100 Main Street",
            "city": "Newport News",
            "state": "VA",
            "postal_code": "23601",
            "country": "US",
        },
        "source_updated_at": _SOURCE_UPDATED_AT,
        "source_version": "42",
        "identity_quality": IdentityQuality.VERIFIED,
        "provenance": _provenance_payload(),
    }


def _order_line_payload() -> dict[str, object]:
    return {
        "order_line_key": "TDS:101:W123456:evt-created-42:LINE:10",
        "sales_order_key": "TDS:101:W123456:evt-created-42",
        "source_line_number": "10",
        "product_key": "STEP:MP-900",
        "product_id_snapshot": "945184*474",
        "master_product_id": "MP-900",
        "product_description_snapshot": "Commercial valve",
        "ordered_quantity": Decimal("2.000000"),
        "shipped_quantity": Decimal("2.000000"),
        "unit_price": Decimal("625.1250"),
        "line_net_amount": Decimal("1250.2500"),
        "unit_of_measure": "EA",
        "inventory_warehouse_key": "FERGUSON:202",
        "line_status": "SHIPPED",
        "source_updated_at": _SOURCE_UPDATED_AT,
        "identity_quality": IdentityQuality.CONDITIONAL,
        "provenance": _provenance_payload(
            source_record_id="101*W123456:LINE:10",
        ),
    }


def test_sales_order_accepts_finalized_identity_and_source_evidence() -> None:
    order = SalesOrder.model_validate(_sales_order_payload())

    assert order.sales_order_key == "TDS:101:W123456:evt-created-42"
    assert order.ship_to is not None
    assert order.ship_to.city == "Newport News"
    assert order.order_total_amount == Decimal("1250.2500")


def test_sales_order_rejects_non_tds_source_system() -> None:
    payload = _sales_order_payload()
    payload["source_system"] = "OTHER"

    with pytest.raises(ValidationError) as exc_info:
        SalesOrder.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "sales_order_source_system_invalid"


def test_sales_order_rejects_source_document_identity_mismatch() -> None:
    payload = _sales_order_payload()
    payload["source_document_id"] = "101*W999999"

    with pytest.raises(ValidationError) as exc_info:
        SalesOrder.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "sales_order_source_document_id_mismatch"


def test_sales_order_rejects_canonical_key_mismatch() -> None:
    payload = _sales_order_payload()
    payload["sales_order_key"] = "TDS:101:W123456:other-instance"

    with pytest.raises(ValidationError) as exc_info:
        SalesOrder.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "sales_order_key_mismatch"


def test_sales_order_rejects_customer_account_join_mismatch() -> None:
    payload = _sales_order_payload()
    payload["customer_account_key"] = "CUSTOMER_CDM:999*customer-200"

    with pytest.raises(ValidationError) as exc_info:
        SalesOrder.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "sales_order_customer_account_key_mismatch"


@pytest.mark.parametrize(
    "field",
    ["account_id", "order_id", "order_instance_key", "customer_id"],
)
def test_sales_order_rejects_ambiguous_identity_delimiters(field: str) -> None:
    payload = _sales_order_payload()
    payload[field] = "unsafe:value"

    with pytest.raises(ValidationError) as exc_info:
        SalesOrder.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "string_pattern_mismatch"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("selling_warehouse_key", "OTHER:101"),
        ("ship_from_warehouse_key", "FERGUSON:"),
        ("ship_from_warehouse_key", "FERGUSON:202:duplicate"),
    ],
)
def test_sales_order_rejects_invalid_warehouse_key(
    field: str,
    value: str,
) -> None:
    payload = _sales_order_payload()
    payload[field] = value

    with pytest.raises(ValidationError) as exc_info:
        SalesOrder.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "sales_order_warehouse_key_invalid"


@pytest.mark.parametrize(
    ("provenance_field", "value", "error_type"),
    [
        (
            "source_system",
            "OTHER",
            "sales_order_source_system_mismatch",
        ),
        (
            "source_record_id",
            "101*W999999",
            "sales_order_source_record_id_mismatch",
        ),
        (
            "source_updated_at",
            datetime(2026, 7, 21, 7, 0, tzinfo=UTC),
            "sales_order_source_updated_at_mismatch",
        ),
        (
            "source_version",
            "43",
            "sales_order_source_version_mismatch",
        ),
    ],
)
def test_sales_order_rejects_provenance_drift(
    provenance_field: str,
    value: object,
    error_type: str,
) -> None:
    payload = _sales_order_payload()
    provenance = dict(_provenance_payload())
    provenance[provenance_field] = value
    payload["provenance"] = provenance

    with pytest.raises(ValidationError) as exc_info:
        SalesOrder.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == error_type


def test_sales_order_rejects_negative_total() -> None:
    payload = _sales_order_payload()
    payload["order_total_amount"] = Decimal("-0.0001")

    with pytest.raises(ValidationError) as exc_info:
        SalesOrder.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "greater_than_equal"


def test_sales_order_rejects_float_money_input() -> None:
    payload = _sales_order_payload()
    payload["order_total_amount"] = 1250.25

    with pytest.raises(ValidationError) as exc_info:
        SalesOrder.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "is_instance_of"


def test_sales_order_rejects_date_string_coercion() -> None:
    payload = _sales_order_payload()
    payload["order_date"] = "2026-07-01"

    with pytest.raises(ValidationError) as exc_info:
        SalesOrder.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "date_type"


def test_order_line_accepts_conditional_identity_and_source_facts() -> None:
    line = OrderLine.model_validate(_order_line_payload())

    assert line.identity_quality is IdentityQuality.CONDITIONAL
    assert line.product_key == "STEP:MP-900"


def test_order_line_preserves_source_anomaly_without_policy_inference() -> None:
    payload = _order_line_payload()
    payload["shipped_quantity"] = Decimal("3.000000")
    payload["unit_price"] = Decimal("-1.0000")

    line = OrderLine.model_validate(payload)

    assert line.shipped_quantity == Decimal("3.000000")
    assert line.unit_price == Decimal("-1.0000")


def test_order_line_rejects_invalid_sales_order_key_shape() -> None:
    payload = _order_line_payload()
    payload["sales_order_key"] = "TDS:W123456"
    payload["order_line_key"] = "TDS:W123456:LINE:10"

    with pytest.raises(ValidationError) as exc_info:
        OrderLine.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "order_line_sales_order_key_invalid"


def test_order_line_rejects_line_key_mismatch() -> None:
    payload = _order_line_payload()
    payload["order_line_key"] = "TDS:101:W123456:evt-created-42:LINE:20"

    with pytest.raises(ValidationError) as exc_info:
        OrderLine.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "order_line_key_mismatch"


def test_order_line_rejects_product_key_mismatch() -> None:
    payload = _order_line_payload()
    payload["product_key"] = "STEP:MP-999"

    with pytest.raises(ValidationError) as exc_info:
        OrderLine.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "order_line_product_key_mismatch"


@pytest.mark.parametrize(
    "identity_quality",
    [IdentityQuality.VERIFIED, IdentityQuality.FALLBACK],
)
def test_order_line_rejects_nonconditional_identity(
    identity_quality: IdentityQuality,
) -> None:
    payload = _order_line_payload()
    payload["identity_quality"] = identity_quality

    with pytest.raises(ValidationError) as exc_info:
        OrderLine.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "order_line_identity_quality_invalid"


def test_order_line_rejects_invalid_inventory_warehouse_namespace() -> None:
    payload = _order_line_payload()
    payload["inventory_warehouse_key"] = "OTHER:202"

    with pytest.raises(ValidationError) as exc_info:
        OrderLine.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "order_line_warehouse_key_invalid"


def test_order_line_rejects_non_tds_provenance() -> None:
    payload = _order_line_payload()
    payload["provenance"] = _provenance_payload(
        source_system="OTHER",
        source_record_id="101*W123456:LINE:10",
    )

    with pytest.raises(ValidationError) as exc_info:
        OrderLine.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "order_line_source_system_invalid"


def test_order_line_rejects_source_timestamp_drift() -> None:
    payload = _order_line_payload()
    payload["source_updated_at"] = datetime(2026, 7, 21, 7, 0, tzinfo=UTC)

    with pytest.raises(ValidationError) as exc_info:
        OrderLine.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "order_line_source_updated_at_mismatch"


def test_order_line_rejects_negative_quantity() -> None:
    payload = _order_line_payload()
    payload["ordered_quantity"] = Decimal("-0.000001")

    with pytest.raises(ValidationError) as exc_info:
        OrderLine.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "greater_than_equal"


def test_order_line_rejects_float_quantity_input() -> None:
    payload = _order_line_payload()
    payload["ordered_quantity"] = 2.0

    with pytest.raises(ValidationError) as exc_info:
        OrderLine.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "is_instance_of"


def test_order_line_rejects_unknown_fields() -> None:
    payload = _order_line_payload()
    payload["array_position"] = 0

    with pytest.raises(ValidationError) as exc_info:
        OrderLine.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "extra_forbidden"
