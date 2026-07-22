"""Canonical warehouse and warehouse-product contracts."""

from decimal import Decimal
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

__all__ = ["Warehouse", "WarehouseProduct"]

_WAREHOUSE_NAMESPACE = "FERGUSON:"
_PRODUCT_NAMESPACE = "STEP:"
_STEP_SOURCE_SYSTEM = "STEP"

WarehouseIdentifier = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=1,
        max_length=256,
        pattern=r"^[^:*\s\x00-\x1f\x7f]+$",
    ),
]
"""Warehouse identifier safe for canonical composite keys."""

InventoryQuantity = Annotated[
    Decimal,
    Field(
        strict=True,
        allow_inf_nan=False,
        max_digits=20,
        decimal_places=6,
    ),
]
"""Signed inventory quantity preserved from the source record."""

NonNegativeInventoryQuantity = Annotated[
    Decimal,
    Field(
        strict=True,
        allow_inf_nan=False,
        ge=0,
        max_digits=20,
        decimal_places=6,
    ),
]
"""Non-negative inventory threshold."""


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


class Warehouse(CanonicalBaseModel):
    """Canonical Ferguson warehouse or branch location."""

    warehouse_key: CanonicalIdentifier
    warehouse_id: WarehouseIdentifier
    warehouse_name: NonBlankText | None = None
    branch_id: CanonicalIdentifier | None = None
    active: bool
    provenance: SourceProvenance

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        """Require the finalized Ferguson warehouse identity."""
        expected_key = f"{_WAREHOUSE_NAMESPACE}{self.warehouse_id}"
        if self.warehouse_key != expected_key:
            _raise_validation_error(
                "warehouse_key_mismatch",
                "warehouse_key does not match warehouse_id",
            )
        return self


class WarehouseProduct(CanonicalBaseModel):
    """Product inventory facts scoped to one Ferguson warehouse."""

    warehouse_product_key: CanonicalIdentifier
    product_key: CanonicalIdentifier
    warehouse_key: CanonicalIdentifier
    product_warehouse_id: CanonicalIdentifier | None = None
    bin_location: NonBlankText | None = None
    rank_code: NonBlankText | None = None
    quantity_on_hand: InventoryQuantity | None = None
    quantity_available: InventoryQuantity | None = None
    reorder_point: NonNegativeInventoryQuantity | None = None
    status: NonBlankText | None = None
    source_updated_at: UtcDateTime | None = None
    provenance: SourceProvenance

    @model_validator(mode="after")
    def validate_identity_and_provenance(self) -> Self:
        """Reject invalid references and duplicated source-evidence drift."""
        _validate_namespaced_key(
            self.product_key,
            namespace=_PRODUCT_NAMESPACE,
            error_type="warehouse_product_product_key_invalid",
            field_name="product_key",
        )
        _validate_namespaced_key(
            self.warehouse_key,
            namespace=_WAREHOUSE_NAMESPACE,
            error_type="warehouse_product_warehouse_key_invalid",
            field_name="warehouse_key",
        )

        expected_key = f"{self.product_key}:{self.warehouse_key}"
        if self.warehouse_product_key != expected_key:
            _raise_validation_error(
                "warehouse_product_key_mismatch",
                "warehouse_product_key does not match product and warehouse",
            )

        if self.provenance.source_system != _STEP_SOURCE_SYSTEM:
            _raise_validation_error(
                "warehouse_product_source_system_invalid",
                "warehouse-product provenance source_system must be STEP",
            )

        if self.source_updated_at != self.provenance.source_updated_at:
            _raise_validation_error(
                "warehouse_product_source_updated_at_mismatch",
                "source_updated_at does not match provenance",
            )

        return self
