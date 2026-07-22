"""Deterministic tests for shipment canonical contracts."""

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from return_platform.canonical.shipment import (
    CarrierTrackingQuality,
    CarrierTrackingReference,
    Shipment,
    ShipmentItem,
    TrackingEvent,
)

_DIGEST = "d" * 64
_SOURCE_UPDATED_AT = datetime(2026, 7, 20, 10, 0, tzinfo=UTC)
_OCCURRED_AT = datetime(2026, 7, 20, 10, 30, 15, 123456, tzinfo=UTC)


def _provenance_payload(
    *,
    source_system: str = "SHIPMENT",
    source_record_id: str = "101*W123456",
    source_updated_at: datetime | None = _SOURCE_UPDATED_AT,
) -> dict[str, object]:
    return {
        "source_system": source_system,
        "source_database": "shipment",
        "source_asset": "trans",
        "source_record_id": source_record_id,
        "source_updated_at": source_updated_at,
        "source_version": "shipment-42",
        "source_event_id": "shipment-event-42",
        "source_hash": _DIGEST,
        "observed_at": datetime(2026, 7, 20, 10, 31, tzinfo=UTC),
        "mapping_version": "canonical-v1",
        "configuration_version": "data-platform-v1",
        "configuration_digest": _DIGEST,
    }


def _shipment_payload() -> dict[str, object]:
    return {
        "shipment_key": "SHIPMENT:SHP-42",
        "shipment_id": "SHP-42",
        "sales_order_key": "TDS:101:W123456:evt-created-42",
        "source_document_id": "101*W123456",
        "internal_tracking_reference": "internal-trk-42",
        "shipment_status": "DELIVERED",
        "carrier": "Example Carrier",
        "carrier_type": "PARCEL",
        "estimated_delivery_at": datetime(2026, 7, 20, 12, 0, tzinfo=UTC),
        "delivered_at": datetime(2026, 7, 20, 11, 55, tzinfo=UTC),
        "source_system": "SHIPMENT",
        "source_updated_at": _SOURCE_UPDATED_AT,
        "provenance": _provenance_payload(),
    }


def _shipment_item_payload() -> dict[str, object]:
    return {
        "shipment_item_key": "SHIPMENT:SHP-42:10",
        "shipment_key": "SHIPMENT:SHP-42",
        "order_line_key": "TDS:101:W123456:evt-created-42:LINE:10",
        "source_line_number": "10",
        "product_key": "STEP:MP-900",
        "product_id_snapshot": "945184*474",
        "quantity_shipped": Decimal("2.000000"),
        "delivered": True,
        "status": "DELIVERED",
        "provenance": _provenance_payload(
            source_record_id="101*W123456:ITEM:10",
        ),
    }


def _tracking_event_payload() -> dict[str, object]:
    return {
        "tracking_event_key": ("SHIPMENT:SHP-42:2026-07-20T10:30:15.123456Z:0"),
        "shipment_key": "SHIPMENT:SHP-42",
        "sequence": 0,
        "status": "DELIVERED",
        "occurred_at": _OCCURRED_AT,
        "location": "Newport News, VA",
        "description": "Shipment delivered.",
        "source_system": "SHIPMENT",
        "provenance": _provenance_payload(
            source_record_id="101*W123456:EVENT:0",
        ),
    }


def test_carrier_tracking_reference_accepts_legacy_evidence() -> None:
    reference = CarrierTrackingReference.model_validate(
        {
            "carrier": "UPS",
            "tracking_number": "1Z 999 AA1 01 2345 6784",
            "source_asset": "orderOutbnd",
            "source_updated_at": _SOURCE_UPDATED_AT,
            "quality": CarrierTrackingQuality.LEGACY_SOURCE,
        },
    )

    assert reference.quality is CarrierTrackingQuality.LEGACY_SOURCE


def test_carrier_tracking_reference_rejects_unapproved_quality() -> None:
    with pytest.raises(ValidationError) as exc_info:
        CarrierTrackingReference.model_validate(
            {
                "carrier": "UPS",
                "tracking_number": "1Z999",
                "source_asset": "orderOutbnd",
                "quality": "VERIFIED",
            },
        )

    assert exc_info.value.errors()[0]["type"] == "is_instance_of"


def test_carrier_tracking_reference_rejects_control_characters() -> None:
    with pytest.raises(ValidationError) as exc_info:
        CarrierTrackingReference.model_validate(
            {
                "carrier": "UPS",
                "tracking_number": "1Z999\nforged",
                "source_asset": "orderOutbnd",
                "quality": CarrierTrackingQuality.LEGACY_SOURCE,
            },
        )

    assert exc_info.value.errors()[0]["type"] == "string_pattern_mismatch"


def test_shipment_accepts_confirmed_order_document_join() -> None:
    shipment = Shipment.model_validate(_shipment_payload())

    assert shipment.shipment_key == "SHIPMENT:SHP-42"
    assert shipment.source_document_id == "101*W123456"
    assert shipment.internal_tracking_reference == "internal-trk-42"


def test_shipment_rejects_key_mismatch() -> None:
    payload = _shipment_payload()
    payload["shipment_key"] = "SHIPMENT:SHP-99"

    with pytest.raises(ValidationError) as exc_info:
        Shipment.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "shipment_key_mismatch"


@pytest.mark.parametrize("shipment_id", ["SHP:42", "SHP*42", "SHP 42"])
def test_shipment_rejects_ambiguous_identifier(shipment_id: str) -> None:
    payload = _shipment_payload()
    payload["shipment_id"] = shipment_id

    with pytest.raises(ValidationError) as exc_info:
        Shipment.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "string_pattern_mismatch"


@pytest.mark.parametrize(
    "sales_order_key",
    [
        "TDS:101:W123456",
        "OTHER:101:W123456:evt-created-42",
        "TDS:101:W123456:evt:extra",
        "TDS:10*1:W123456:evt-created-42",
    ],
)
def test_shipment_rejects_invalid_sales_order_key(
    sales_order_key: str,
) -> None:
    payload = _shipment_payload()
    payload["sales_order_key"] = sales_order_key

    with pytest.raises(ValidationError) as exc_info:
        Shipment.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "shipment_sales_order_key_invalid"


def test_shipment_rejects_source_document_join_mismatch() -> None:
    payload = _shipment_payload()
    payload["source_document_id"] = "999*W123456"

    with pytest.raises(ValidationError) as exc_info:
        Shipment.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "shipment_source_document_id_mismatch"


@pytest.mark.parametrize(
    ("field", "value", "error_type"),
    [
        (
            "source_system",
            "OTHER",
            "shipment_source_system_mismatch",
        ),
        (
            "source_record_id",
            "999*W123456",
            "shipment_source_record_id_mismatch",
        ),
        (
            "source_updated_at",
            datetime(2026, 7, 21, 10, 0, tzinfo=UTC),
            "shipment_source_updated_at_mismatch",
        ),
    ],
)
def test_shipment_rejects_provenance_drift(
    field: str,
    value: object,
    error_type: str,
) -> None:
    payload = _shipment_payload()
    if field == "source_system":
        payload[field] = value
    else:
        provenance = _provenance_payload()
        provenance[field] = value
        payload["provenance"] = provenance

    with pytest.raises(ValidationError) as exc_info:
        Shipment.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == error_type


def test_shipment_normalizes_aware_delivery_timestamp_to_utc() -> None:
    payload = _shipment_payload()
    india = timezone(timedelta(hours=5, minutes=30))
    payload["delivered_at"] = datetime(2026, 7, 20, 17, 25, tzinfo=india)

    shipment = Shipment.model_validate(payload)

    assert shipment.delivered_at == datetime(2026, 7, 20, 11, 55, tzinfo=UTC)


def test_shipment_rejects_internal_reference_control_characters() -> None:
    payload = _shipment_payload()
    payload["internal_tracking_reference"] = "internal\nforged"

    with pytest.raises(ValidationError) as exc_info:
        Shipment.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "string_pattern_mismatch"


def test_shipment_does_not_accept_actual_tracking_as_unmodeled_fact() -> None:
    payload = _shipment_payload()
    payload["tracking_number"] = "1Z999"

    with pytest.raises(ValidationError) as exc_info:
        Shipment.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "extra_forbidden"


def test_shipment_item_accepts_conditional_order_line_reference() -> None:
    item = ShipmentItem.model_validate(_shipment_item_payload())

    assert item.order_line_key is not None
    assert item.source_line_number == "10"


def test_shipment_item_allows_unresolved_order_line_reference() -> None:
    payload = _shipment_item_payload()
    payload["order_line_key"] = None

    item = ShipmentItem.model_validate(payload)

    assert item.order_line_key is None


@pytest.mark.parametrize(
    ("field", "value", "error_type"),
    [
        (
            "shipment_key",
            "OTHER:SHP-42",
            "shipment_item_shipment_key_invalid",
        ),
        (
            "shipment_key",
            "SHIPMENT:SHP:42",
            "shipment_item_shipment_key_invalid",
        ),
        (
            "product_key",
            "OTHER:MP-900",
            "shipment_item_product_key_invalid",
        ),
        (
            "product_key",
            "STEP:MP:900",
            "shipment_item_product_key_invalid",
        ),
    ],
)
def test_shipment_item_rejects_invalid_namespaced_reference(
    field: str,
    value: str,
    error_type: str,
) -> None:
    payload = _shipment_item_payload()
    payload[field] = value

    with pytest.raises(ValidationError) as exc_info:
        ShipmentItem.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == error_type


def test_shipment_item_rejects_key_mismatch() -> None:
    payload = _shipment_item_payload()
    payload["shipment_item_key"] = "SHIPMENT:SHP-42:20"

    with pytest.raises(ValidationError) as exc_info:
        ShipmentItem.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "shipment_item_key_mismatch"


@pytest.mark.parametrize(
    "order_line_key",
    [
        "TDS:101:W123456:LINE:10",
        "OTHER:101:W123456:evt-created-42:LINE:10",
        "TDS:101:W123456:evt-created-42:ITEM:10",
        "TDS:10*1:W123456:evt-created-42:LINE:10",
    ],
)
def test_shipment_item_rejects_invalid_order_line_reference(
    order_line_key: str,
) -> None:
    payload = _shipment_item_payload()
    payload["order_line_key"] = order_line_key

    with pytest.raises(ValidationError) as exc_info:
        ShipmentItem.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "shipment_item_order_line_key_invalid"


def test_shipment_item_rejects_order_line_number_mismatch() -> None:
    payload = _shipment_item_payload()
    payload["order_line_key"] = "TDS:101:W123456:evt-created-42:LINE:20"

    with pytest.raises(ValidationError) as exc_info:
        ShipmentItem.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "shipment_item_order_line_number_mismatch"


def test_shipment_item_rejects_negative_quantity() -> None:
    payload = _shipment_item_payload()
    payload["quantity_shipped"] = Decimal("-0.000001")

    with pytest.raises(ValidationError) as exc_info:
        ShipmentItem.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "greater_than_equal"


def test_shipment_item_rejects_float_quantity() -> None:
    payload = _shipment_item_payload()
    payload["quantity_shipped"] = 2.0

    with pytest.raises(ValidationError) as exc_info:
        ShipmentItem.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "is_instance_of"


def test_shipment_item_rejects_integer_boolean_coercion() -> None:
    payload = _shipment_item_payload()
    payload["delivered"] = 1

    with pytest.raises(ValidationError) as exc_info:
        ShipmentItem.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "bool_type"


def test_shipment_item_rejects_package_identity_in_graph_v1() -> None:
    payload = _shipment_item_payload()
    payload["package_id"] = "PKG-42"

    with pytest.raises(ValidationError) as exc_info:
        ShipmentItem.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "extra_forbidden"


def test_tracking_event_accepts_deterministic_identity() -> None:
    event = TrackingEvent.model_validate(_tracking_event_payload())

    assert event.sequence == 0
    assert event.occurred_at == _OCCURRED_AT


def test_tracking_event_normalizes_timezone_before_identity_check() -> None:
    payload = _tracking_event_payload()
    india = timezone(timedelta(hours=5, minutes=30))
    payload["occurred_at"] = datetime(
        2026,
        7,
        20,
        16,
        0,
        15,
        123456,
        tzinfo=india,
    )

    event = TrackingEvent.model_validate(payload)

    assert event.occurred_at == _OCCURRED_AT


def test_tracking_event_rejects_key_mismatch() -> None:
    payload = _tracking_event_payload()
    payload["tracking_event_key"] = "SHIPMENT:SHP-42:2026-07-20T10:30:15.123456Z:1"

    with pytest.raises(ValidationError) as exc_info:
        TrackingEvent.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "tracking_event_key_mismatch"


def test_tracking_event_rejects_invalid_shipment_key() -> None:
    payload = _tracking_event_payload()
    payload["shipment_key"] = "SHIPMENT:SHP:42"

    with pytest.raises(ValidationError) as exc_info:
        TrackingEvent.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "tracking_event_shipment_key_invalid"


@pytest.mark.parametrize("sequence", [-1, True])
def test_tracking_event_rejects_invalid_sequence(sequence: object) -> None:
    payload = _tracking_event_payload()
    payload["sequence"] = sequence

    with pytest.raises(ValidationError) as exc_info:
        TrackingEvent.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] in {
        "greater_than_equal",
        "int_type",
    }


def test_tracking_event_rejects_naive_timestamp() -> None:
    payload = _tracking_event_payload()
    aware_timestamp = datetime(2026, 7, 20, 10, 30, tzinfo=UTC)
    payload["occurred_at"] = aware_timestamp.replace(tzinfo=None)

    with pytest.raises(ValidationError) as exc_info:
        TrackingEvent.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "timezone_aware"


def test_tracking_event_rejects_source_system_drift() -> None:
    payload = _tracking_event_payload()
    payload["source_system"] = "OTHER"

    with pytest.raises(ValidationError) as exc_info:
        TrackingEvent.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "tracking_event_source_system_mismatch"


def test_tracking_event_sequence_disambiguates_equal_timestamps() -> None:
    first_payload = _tracking_event_payload()
    second_payload = _tracking_event_payload()
    second_payload["sequence"] = 1
    second_payload["tracking_event_key"] = "SHIPMENT:SHP-42:2026-07-20T10:30:15.123456Z:1"

    first = TrackingEvent.model_validate(first_payload)
    second = TrackingEvent.model_validate(second_payload)

    assert first.tracking_event_key != second.tracking_event_key
