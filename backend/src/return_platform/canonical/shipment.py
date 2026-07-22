"""Canonical outbound shipment and tracking contracts."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Never, Self

from pydantic import Field, StringConstraints, model_validator
from pydantic_core import PydanticCustomError

from return_platform.canonical.base import (
    CanonicalBaseModel,
    CanonicalIdentifier,
    NonBlankText,
    SourceProvenance,
    UtcDateTime,
)

__all__ = [
    "CarrierTrackingQuality",
    "CarrierTrackingReference",
    "Shipment",
    "ShipmentItem",
    "TrackingEvent",
]

_SHIPMENT_NAMESPACE = "SHIPMENT:"
_PRODUCT_NAMESPACE = "STEP:"
_TDS_NAMESPACE = "TDS"
_EXPECTED_SALES_ORDER_KEY_PARTS = 4
_EXPECTED_ORDER_LINE_KEY_PARTS = 6

ShipmentIdentifier = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=1,
        max_length=256,
        pattern=r"^[^:*\s\x00-\x1f\x7f]+$",
    ),
]
"""Shipment identifier safe for canonical composite keys."""

ShipmentLineNumber = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=1,
        max_length=256,
        pattern=r"^[^:*\s\x00-\x1f\x7f]+$",
    ),
]
"""Source line number safe for shipment-item identity."""

NonNegativeShipmentQuantity = Annotated[
    Decimal,
    Field(
        strict=True,
        allow_inf_nan=False,
        ge=0,
        max_digits=20,
        decimal_places=6,
    ),
]
"""Bounded non-negative shipped quantity."""

TrackingSequence = Annotated[int, Field(strict=True, ge=0, le=2_147_483_647)]
"""Deterministic zero-based source-event sequence."""

TrackingText = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=1,
        max_length=1_024,
        pattern=r"^[^\x00-\x1f\x7f]+$",
    ),
]
"""Bounded carrier or tracking text without control characters."""


def _raise_validation_error(error_type: str, message: str) -> Never:
    """Raise a stable Pydantic validation error."""
    raise PydanticCustomError(error_type, message)


def _validate_namespaced_key(
    value: str,
    *,
    namespace: str,
    error_type: str,
    field_name: str,
) -> None:
    """Require a non-empty namespace suffix without nested delimiters."""
    if not value.startswith(namespace):
        _raise_validation_error(
            error_type,
            f"{field_name} must use the required namespace",
        )

    suffix = value.removeprefix(namespace)
    if not suffix or ":" in suffix or "*" in suffix:
        _raise_validation_error(
            error_type,
            f"{field_name} contains an invalid namespace suffix",
        )


def _sales_order_document_id(sales_order_key: str) -> str:
    """Return accountId*orderId from a finalized canonical order key."""
    parts = sales_order_key.split(":")
    if (
        len(parts) != _EXPECTED_SALES_ORDER_KEY_PARTS
        or parts[0] != _TDS_NAMESPACE
        or not all(parts[1:])
        or any("*" in component for component in parts[1:])
    ):
        _raise_validation_error(
            "shipment_sales_order_key_invalid",
            "sales_order_key must use TDS:accountId:orderId:orderInstanceKey",
        )
    return f"{parts[1]}*{parts[2]}"


def _validate_order_line_reference(
    order_line_key: str,
    *,
    source_line_number: str,
) -> None:
    """Validate a conditional OrderLine reference without inventing a join."""
    parts = order_line_key.split(":")
    if (
        len(parts) != _EXPECTED_ORDER_LINE_KEY_PARTS
        or parts[0] != _TDS_NAMESPACE
        or parts[-2] != "LINE"
        or not all(parts[1:])
        or any("*" in component for component in parts[1:])
    ):
        _raise_validation_error(
            "shipment_item_order_line_key_invalid",
            "order_line_key must use TDS:accountId:orderId:instance:LINE:lineNumber",
        )

    if parts[-1] != source_line_number:
        _raise_validation_error(
            "shipment_item_order_line_number_mismatch",
            "order_line_key line number does not match source_line_number",
        )


def _tracking_event_time_token(value: datetime) -> str:
    """Serialize a normalized UTC timestamp for deterministic identity."""
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


class CarrierTrackingQuality(StrEnum):
    """Current evidence quality for actual carrier tracking."""

    LEGACY_SOURCE = "LEGACY_SOURCE"


class CarrierTrackingReference(CanonicalBaseModel):
    """Optional actual carrier tracking sourced from a legacy asset."""

    carrier: NonBlankText
    tracking_number: TrackingText
    source_asset: NonBlankText
    source_updated_at: UtcDateTime | None = None
    quality: CarrierTrackingQuality


class Shipment(CanonicalBaseModel):
    """Canonical order-level shipment document and delivery summary."""

    shipment_key: CanonicalIdentifier
    shipment_id: ShipmentIdentifier
    sales_order_key: CanonicalIdentifier
    source_document_id: CanonicalIdentifier
    internal_tracking_reference: TrackingText | None = None
    shipment_status: NonBlankText | None = None
    carrier: NonBlankText | None = None
    carrier_type: NonBlankText | None = None
    estimated_delivery_at: UtcDateTime | None = None
    delivered_at: UtcDateTime | None = None
    source_system: CanonicalIdentifier
    source_updated_at: UtcDateTime | None = None
    provenance: SourceProvenance

    @model_validator(mode="after")
    def validate_identity_and_provenance(self) -> Self:
        """Validate shipment identity, order join, and source evidence."""
        expected_key = f"{_SHIPMENT_NAMESPACE}{self.shipment_id}"
        if self.shipment_key != expected_key:
            _raise_validation_error(
                "shipment_key_mismatch",
                "shipment_key does not match shipment_id",
            )

        expected_document_id = _sales_order_document_id(self.sales_order_key)
        if self.source_document_id != expected_document_id:
            _raise_validation_error(
                "shipment_source_document_id_mismatch",
                "source_document_id does not match sales_order_key",
            )

        if self.source_system != self.provenance.source_system:
            _raise_validation_error(
                "shipment_source_system_mismatch",
                "source_system does not match provenance",
            )

        if self.source_document_id != self.provenance.source_record_id:
            _raise_validation_error(
                "shipment_source_record_id_mismatch",
                "source_document_id does not match provenance source_record_id",
            )

        if self.source_updated_at != self.provenance.source_updated_at:
            _raise_validation_error(
                "shipment_source_updated_at_mismatch",
                "source_updated_at does not match provenance",
            )

        return self


class ShipmentItem(CanonicalBaseModel):
    """Canonical item within a shipment, with an optional conditional line join."""

    shipment_item_key: CanonicalIdentifier
    shipment_key: CanonicalIdentifier
    order_line_key: CanonicalIdentifier | None = None
    source_line_number: ShipmentLineNumber
    product_key: CanonicalIdentifier
    product_id_snapshot: CanonicalIdentifier | None = None
    quantity_shipped: NonNegativeShipmentQuantity | None = None
    delivered: bool | None = None
    status: NonBlankText | None = None
    provenance: SourceProvenance

    @model_validator(mode="after")
    def validate_identity_and_references(self) -> Self:
        """Reject ambiguous shipment-item identity and false line references."""
        _validate_namespaced_key(
            self.shipment_key,
            namespace=_SHIPMENT_NAMESPACE,
            error_type="shipment_item_shipment_key_invalid",
            field_name="shipment_key",
        )

        expected_key = f"{self.shipment_key}:{self.source_line_number}"
        if self.shipment_item_key != expected_key:
            _raise_validation_error(
                "shipment_item_key_mismatch",
                "shipment_item_key does not match shipment and line number",
            )

        _validate_namespaced_key(
            self.product_key,
            namespace=_PRODUCT_NAMESPACE,
            error_type="shipment_item_product_key_invalid",
            field_name="product_key",
        )

        if self.order_line_key is not None:
            _validate_order_line_reference(
                self.order_line_key,
                source_line_number=self.source_line_number,
            )

        return self


class TrackingEvent(CanonicalBaseModel):
    """Deterministically identified status event for one shipment."""

    tracking_event_key: CanonicalIdentifier
    shipment_key: CanonicalIdentifier
    sequence: TrackingSequence
    status: NonBlankText
    occurred_at: UtcDateTime
    location: NonBlankText | None = None
    description: NonBlankText | None = None
    source_system: CanonicalIdentifier
    provenance: SourceProvenance

    @model_validator(mode="after")
    def validate_identity_and_provenance(self) -> Self:
        """Validate deterministic event identity and source-system evidence."""
        _validate_namespaced_key(
            self.shipment_key,
            namespace=_SHIPMENT_NAMESPACE,
            error_type="tracking_event_shipment_key_invalid",
            field_name="shipment_key",
        )

        occurred_at_token = _tracking_event_time_token(self.occurred_at)
        expected_key = f"{self.shipment_key}:{occurred_at_token}:{self.sequence}"
        if self.tracking_event_key != expected_key:
            _raise_validation_error(
                "tracking_event_key_mismatch",
                "tracking_event_key does not match shipment, timestamp, and sequence",
            )

        if self.source_system != self.provenance.source_system:
            _raise_validation_error(
                "tracking_event_source_system_mismatch",
                "source_system does not match provenance",
            )

        return self
