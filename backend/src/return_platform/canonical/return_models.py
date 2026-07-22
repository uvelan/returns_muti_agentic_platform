"""Canonical return, return-item, and freight-shipment contracts."""

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
    "FreightShipment",
    "Return",
    "ReturnItem",
    "ReturnVersion",
]

_OMC_SOURCE_SYSTEM = "OMC"
_OMC_RETURN_NAMESPACE = "OMC:"
_PRODUCT_NAMESPACE = "STEP:"
_TDS_NAMESPACE = "TDS"
_EXPECTED_SALES_ORDER_KEY_PARTS = 4
_EXPECTED_ORDER_LINE_KEY_PARTS = 6
_EXPECTED_RETURN_KEY_PARTS = 3

ReturnIdentifier = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=1,
        max_length=256,
        pattern=r"^[^:*\s\x00-\x1f\x7f]+$",
    ),
]
"""OMC identifier safe for canonical composite keys."""

NonNegativeReturnQuantity = Annotated[
    Decimal,
    Field(
        strict=True,
        allow_inf_nan=False,
        ge=0,
        max_digits=20,
        decimal_places=6,
    ),
]
"""Bounded non-negative returned quantity."""


class ReturnVersion(StrEnum):
    """Supported OMC return implementations."""

    V1 = "V1"
    V2 = "V2"


_RETURN_VERSION_VALUES = frozenset(version.value for version in ReturnVersion)


def _raise_validation_error(error_type: str, message: str) -> Never:
    """Raise a stable Pydantic validation error."""
    raise PydanticCustomError(error_type, message)


def _validate_sales_order_reference(
    sales_order_key: str,
    *,
    source_order_id: str,
) -> None:
    """Validate a conditional SalesOrder reference without guessing account context."""
    parts = sales_order_key.split(":")
    if (
        len(parts) != _EXPECTED_SALES_ORDER_KEY_PARTS
        or parts[0] != _TDS_NAMESPACE
        or not all(parts[1:])
        or any("*" in component for component in parts[1:])
    ):
        _raise_validation_error(
            "return_sales_order_key_invalid",
            "sales_order_key must use TDS:accountId:orderId:orderInstanceKey",
        )

    if parts[2] != source_order_id:
        _raise_validation_error(
            "return_sales_order_id_mismatch",
            "sales_order_key orderId does not match source_order_id",
        )


def _return_version_from_key(return_key: str) -> ReturnVersion:
    """Validate an OMC return key and return its encoded version."""
    parts = return_key.split(":")
    if (
        len(parts) != _EXPECTED_RETURN_KEY_PARTS
        or parts[0] != "OMC"
        or parts[1] not in _RETURN_VERSION_VALUES
        or not parts[2]
        or "*" in parts[2]
    ):
        _raise_validation_error(
            "return_key_invalid",
            "return_key must use OMC:V1:returnId or OMC:V2:rmaId",
        )
    return ReturnVersion(parts[1])


def _validate_order_line_reference(order_line_key: str) -> None:
    """Validate the shape of a conditional OrderLine reference."""
    parts = order_line_key.split(":")
    if (
        len(parts) != _EXPECTED_ORDER_LINE_KEY_PARTS
        or parts[0] != _TDS_NAMESPACE
        or parts[-2] != "LINE"
        or not all(parts[1:])
        or any("*" in component for component in parts[1:])
    ):
        _raise_validation_error(
            "return_item_order_line_key_invalid",
            "order_line_key must use TDS:accountId:orderId:instance:LINE:lineNumber",
        )


def _validate_product_reference(product_key: str) -> None:
    """Validate a conditional STEP Product reference."""
    if not product_key.startswith(_PRODUCT_NAMESPACE):
        _raise_validation_error(
            "return_item_product_key_invalid",
            "product_key must use the STEP namespace",
        )

    suffix = product_key.removeprefix(_PRODUCT_NAMESPACE)
    if not suffix or ":" in suffix or "*" in suffix:
        _raise_validation_error(
            "return_item_product_key_invalid",
            "product_key contains an invalid STEP identity suffix",
        )


class Return(CanonicalBaseModel):
    """Canonical OMC V1 Return or V2 RMA header."""

    return_key: CanonicalIdentifier
    return_version: ReturnVersion
    source_return_id: ReturnIdentifier
    sales_order_key: CanonicalIdentifier | None = None
    source_order_id: ReturnIdentifier
    return_status: NonBlankText | None = None
    shipping_path: NonBlankText | None = None
    created_at: UtcDateTime | None = None
    updated_at: UtcDateTime | None = None
    provenance: SourceProvenance

    @model_validator(mode="after")
    def validate_identity_and_provenance(self) -> Self:
        """Validate OMC identity, optional order join, and source evidence."""
        expected_key = f"{_OMC_RETURN_NAMESPACE}{self.return_version.value}:{self.source_return_id}"
        if self.return_key != expected_key:
            _raise_validation_error(
                "return_key_mismatch",
                "return_key does not match return_version and source_return_id",
            )

        if self.provenance.source_system != _OMC_SOURCE_SYSTEM:
            _raise_validation_error(
                "return_source_system_invalid",
                "return provenance source_system must be OMC",
            )

        if self.provenance.source_record_id != self.source_return_id:
            _raise_validation_error(
                "return_source_record_id_mismatch",
                "source_return_id does not match provenance source_record_id",
            )

        if self.sales_order_key is not None:
            _validate_sales_order_reference(
                self.sales_order_key,
                source_order_id=self.source_order_id,
            )

        if (
            self.created_at is not None
            and self.updated_at is not None
            and self.updated_at < self.created_at
        ):
            _raise_validation_error(
                "return_timestamp_order_invalid",
                "updated_at cannot precede created_at",
            )

        return self


class ReturnItem(CanonicalBaseModel):
    """Canonical returned item with fail-closed optional graph references."""

    return_item_key: CanonicalIdentifier
    return_key: CanonicalIdentifier
    source_cart_item_id: ReturnIdentifier
    order_line_key: CanonicalIdentifier | None = None
    product_key: CanonicalIdentifier | None = None
    omc_unique_id: CanonicalIdentifier | None = None
    returned_quantity: NonNegativeReturnQuantity
    return_reason: NonBlankText | None = None
    item_condition: NonBlankText | None = None
    return_item_status: NonBlankText | None = None
    provenance: SourceProvenance

    @model_validator(mode="after")
    def validate_identity_and_references(self) -> Self:
        """Reject false OMC, OrderLine, or Product relationships."""
        return_version = _return_version_from_key(self.return_key)
        expected_key = f"OMC:{return_version.value}:ITEM:{self.source_cart_item_id}"
        if self.return_item_key != expected_key:
            _raise_validation_error(
                "return_item_key_mismatch",
                "return_item_key does not match return version and cart item ID",
            )

        if self.provenance.source_system != _OMC_SOURCE_SYSTEM:
            _raise_validation_error(
                "return_item_source_system_invalid",
                "return-item provenance source_system must be OMC",
            )

        if self.provenance.source_record_id != self.source_cart_item_id:
            _raise_validation_error(
                "return_item_source_record_id_mismatch",
                "source_cart_item_id does not match provenance source_record_id",
            )

        if self.order_line_key is not None:
            _validate_order_line_reference(self.order_line_key)

        if self.product_key is not None:
            _validate_product_reference(self.product_key)
            if self.omc_unique_id is None:
                _raise_validation_error(
                    "return_item_product_bridge_evidence_required",
                    "omc_unique_id is required when product_key is resolved",
                )

        return self


class FreightShipment(CanonicalBaseModel):
    """Canonical OMC freight shipment attached to one Return or RMA."""

    freight_shipment_key: CanonicalIdentifier
    freight_shipment_id: ReturnIdentifier
    return_key: CanonicalIdentifier
    bol_number: NonBlankText | None = None
    carrier: NonBlankText | None = None
    scac: NonBlankText | None = None
    freight_status: NonBlankText | None = None
    quote_reference: NonBlankText | None = None
    created_at: UtcDateTime | None = None
    updated_at: UtcDateTime | None = None
    provenance: SourceProvenance

    @model_validator(mode="after")
    def validate_identity_and_provenance(self) -> Self:
        """Validate freight identity, Return reference, and source evidence."""
        expected_key = f"OMC:FREIGHT:{self.freight_shipment_id}"
        if self.freight_shipment_key != expected_key:
            _raise_validation_error(
                "freight_shipment_key_mismatch",
                "freight_shipment_key does not match freight_shipment_id",
            )

        _return_version_from_key(self.return_key)

        if self.provenance.source_system != _OMC_SOURCE_SYSTEM:
            _raise_validation_error(
                "freight_shipment_source_system_invalid",
                "freight provenance source_system must be OMC",
            )

        if self.provenance.source_record_id != self.freight_shipment_id:
            _raise_validation_error(
                "freight_shipment_source_record_id_mismatch",
                "freight_shipment_id does not match provenance source_record_id",
            )

        if (
            self.created_at is not None
            and self.updated_at is not None
            and self.updated_at < self.created_at
        ):
            _raise_validation_error(
                "freight_shipment_timestamp_order_invalid",
                "updated_at cannot precede created_at",
            )

        return self
