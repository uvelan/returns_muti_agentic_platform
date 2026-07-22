"""Canonical sales-order and order-line contracts."""

from datetime import date
from decimal import Decimal
from typing import Annotated, Never, Self

from pydantic import Field, StringConstraints, model_validator
from pydantic_core import PydanticCustomError

from return_platform.canonical.base import (
    CanonicalBaseModel,
    CanonicalIdentifier,
    IdentityQuality,
    NonBlankText,
    SourceProvenance,
    UtcDateTime,
    VersionReference,
)
from return_platform.canonical.customer import Address

__all__ = [
    "OrderLine",
    "SalesOrder",
]

_TDS_SOURCE_SYSTEM = "TDS"
_TDS_ORDER_NAMESPACE = "TDS:"
_CUSTOMER_ACCOUNT_NAMESPACE = "CUSTOMER_CDM:"
_WAREHOUSE_NAMESPACE = "FERGUSON:"
_PRODUCT_NAMESPACE = "STEP:"
_EXPECTED_SALES_ORDER_KEY_PARTS = 4

CompositeKeyComponent = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=1,
        max_length=256,
        pattern=r"^[^:*\s\x00-\x1f\x7f]+$",
    ),
]
"""Identity component safe for colon- and asterisk-delimited keys."""

CanonicalAmount = Annotated[
    Decimal,
    Field(
        strict=True,
        allow_inf_nan=False,
        max_digits=20,
        decimal_places=4,
    ),
]
"""Bounded signed monetary fact preserved from the source record."""

NonNegativeAmount = Annotated[
    Decimal,
    Field(
        strict=True,
        allow_inf_nan=False,
        ge=0,
        max_digits=20,
        decimal_places=4,
    ),
]
"""Bounded non-negative monetary total."""

NonNegativeQuantity = Annotated[
    Decimal,
    Field(
        strict=True,
        allow_inf_nan=False,
        ge=0,
        max_digits=20,
        decimal_places=6,
    ),
]
"""Bounded non-negative quantity supporting fractional units."""


def _raise_validation_error(
    error_type: str,
    message: str,
) -> Never:
    """Raise a stable Pydantic validation error."""
    raise PydanticCustomError(error_type, message)


def _validate_namespaced_key(
    value: str,
    *,
    namespace: str,
    error_type: str,
    field_name: str,
) -> None:
    """Require a non-empty, delimiter-safe namespace suffix."""
    if not value.startswith(namespace):
        _raise_validation_error(
            error_type,
            f"{field_name} must use the required namespace",
        )

    suffix = value.removeprefix(namespace)
    if not suffix or ":" in suffix:
        _raise_validation_error(
            error_type,
            f"{field_name} contains an invalid namespace suffix",
        )


def _validate_sales_order_key_shape(value: str) -> None:
    """Require the finalized TDS account/order/instance key shape."""
    parts = value.split(":")
    if len(parts) != _EXPECTED_SALES_ORDER_KEY_PARTS or parts[0] != "TDS" or not all(parts[1:]):
        _raise_validation_error(
            "order_line_sales_order_key_invalid",
            "sales_order_key must use TDS:accountId:orderId:orderInstanceKey",
        )


class SalesOrder(CanonicalBaseModel):
    """Canonical sales-order header normalized from the TDS salesInv source."""

    sales_order_key: CanonicalIdentifier
    source_document_id: CanonicalIdentifier
    source_system: CanonicalIdentifier
    account_id: CompositeKeyComponent
    order_id: CompositeKeyComponent
    order_instance_key: CompositeKeyComponent
    customer_account_key: CanonicalIdentifier
    customer_id: CompositeKeyComponent
    customer_name_snapshot: NonBlankText | None = None
    customer_po_number: NonBlankText | None = None
    job_name: NonBlankText | None = None
    sales_type: NonBlankText | None = None
    order_status: NonBlankText | None = None
    order_date: date | None = None
    invoice_date: date | None = None
    request_date: date | None = None
    order_total_amount: NonNegativeAmount | None = None
    recorded_refund_amount: NonNegativeAmount | None = None
    payment_authorization_code: NonBlankText | None = None
    selling_warehouse_key: CanonicalIdentifier | None = None
    ship_from_warehouse_key: CanonicalIdentifier | None = None
    ship_to: Address | None = None
    source_updated_at: UtcDateTime | None = None
    source_version: VersionReference | None = None
    identity_quality: IdentityQuality
    provenance: SourceProvenance

    @model_validator(mode="after")
    def validate_identity_and_provenance(self) -> Self:
        """Reject composite-key ambiguity and duplicate source-evidence drift."""
        self._validate_source_system()
        self._validate_composite_identity()
        self._validate_warehouse_keys()
        self._validate_provenance()
        return self

    def _validate_source_system(self) -> None:
        """Require the authoritative TDS source namespace."""
        if self.source_system != _TDS_SOURCE_SYSTEM:
            _raise_validation_error(
                "sales_order_source_system_invalid",
                "source_system must be TDS",
            )

    def _validate_composite_identity(self) -> None:
        """Validate physical, canonical, and customer-account identities."""
        expected_document_id = f"{self.account_id}*{self.order_id}"
        if self.source_document_id != expected_document_id:
            _raise_validation_error(
                "sales_order_source_document_id_mismatch",
                "source_document_id does not match account_id and order_id",
            )

        expected_key = (
            f"{_TDS_ORDER_NAMESPACE}{self.account_id}:{self.order_id}:{self.order_instance_key}"
        )
        if self.sales_order_key != expected_key:
            _raise_validation_error(
                "sales_order_key_mismatch",
                "sales_order_key does not match its identity components",
            )

        expected_account_key = f"{_CUSTOMER_ACCOUNT_NAMESPACE}{self.account_id}*{self.customer_id}"
        if self.customer_account_key != expected_account_key:
            _raise_validation_error(
                "sales_order_customer_account_key_mismatch",
                "customer_account_key does not match account_id and customer_id",
            )

    def _validate_warehouse_keys(self) -> None:
        """Validate optional selling and ship-from warehouse references."""
        for field_name, warehouse_key in (
            ("selling_warehouse_key", self.selling_warehouse_key),
            ("ship_from_warehouse_key", self.ship_from_warehouse_key),
        ):
            if warehouse_key is not None:
                _validate_namespaced_key(
                    warehouse_key,
                    namespace=_WAREHOUSE_NAMESPACE,
                    error_type="sales_order_warehouse_key_invalid",
                    field_name=field_name,
                )

    def _validate_provenance(self) -> None:
        """Require duplicated source metadata to match provenance exactly."""
        if self.source_system != self.provenance.source_system:
            _raise_validation_error(
                "sales_order_source_system_mismatch",
                "source_system does not match provenance",
            )

        if self.source_document_id != self.provenance.source_record_id:
            _raise_validation_error(
                "sales_order_source_record_id_mismatch",
                "source_document_id does not match provenance source_record_id",
            )

        if self.source_updated_at != self.provenance.source_updated_at:
            _raise_validation_error(
                "sales_order_source_updated_at_mismatch",
                "source_updated_at does not match provenance",
            )

        if self.source_version != self.provenance.source_version:
            _raise_validation_error(
                "sales_order_source_version_mismatch",
                "source_version does not match provenance",
            )


class OrderLine(CanonicalBaseModel):
    """Canonical order-line fact whose identity is conditional in graph v1."""

    order_line_key: CanonicalIdentifier
    sales_order_key: CanonicalIdentifier
    source_line_number: CompositeKeyComponent
    product_key: CanonicalIdentifier
    product_id_snapshot: CanonicalIdentifier | None = None
    master_product_id: CompositeKeyComponent
    product_description_snapshot: NonBlankText | None = None
    ordered_quantity: NonNegativeQuantity | None = None
    shipped_quantity: NonNegativeQuantity | None = None
    unit_price: CanonicalAmount | None = None
    line_net_amount: CanonicalAmount | None = None
    unit_of_measure: NonBlankText | None = None
    inventory_warehouse_key: CanonicalIdentifier | None = None
    line_status: NonBlankText | None = None
    source_updated_at: UtcDateTime | None = None
    identity_quality: IdentityQuality
    provenance: SourceProvenance

    @model_validator(mode="after")
    def validate_identity_and_provenance(self) -> Self:
        """Enforce conditional line identity and source-derived references."""
        _validate_sales_order_key_shape(self.sales_order_key)

        expected_key = f"{self.sales_order_key}:LINE:{self.source_line_number}"
        if self.order_line_key != expected_key:
            _raise_validation_error(
                "order_line_key_mismatch",
                "order_line_key does not match sales_order_key and line number",
            )

        expected_product_key = f"{_PRODUCT_NAMESPACE}{self.master_product_id}"
        if self.product_key != expected_product_key:
            _raise_validation_error(
                "order_line_product_key_mismatch",
                "product_key does not match master_product_id",
            )

        if self.identity_quality is not IdentityQuality.CONDITIONAL:
            _raise_validation_error(
                "order_line_identity_quality_invalid",
                "order-line identity must remain CONDITIONAL",
            )

        if self.inventory_warehouse_key is not None:
            _validate_namespaced_key(
                self.inventory_warehouse_key,
                namespace=_WAREHOUSE_NAMESPACE,
                error_type="order_line_warehouse_key_invalid",
                field_name="inventory_warehouse_key",
            )

        if self.provenance.source_system != _TDS_SOURCE_SYSTEM:
            _raise_validation_error(
                "order_line_source_system_invalid",
                "order-line provenance source_system must be TDS",
            )

        if self.source_updated_at != self.provenance.source_updated_at:
            _raise_validation_error(
                "order_line_source_updated_at_mismatch",
                "source_updated_at does not match provenance",
            )

        return self
