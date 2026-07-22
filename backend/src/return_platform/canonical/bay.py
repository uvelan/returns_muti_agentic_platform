"""Canonical platform-owned Bay and BayAssignment contracts."""

from collections import Counter
from typing import Annotated, Never, Self
from uuid import UUID

from pydantic import Field, StringConstraints, model_validator
from pydantic_core import PydanticCustomError

from return_platform.canonical.base import (
    CanonicalBaseModel,
    CanonicalIdentifier,
    NonBlankText,
    UtcDateTime,
    VersionReference,
)

__all__ = ["AssignmentEvidence", "Bay", "BayAssignment"]

_WAREHOUSE_NAMESPACE = "FERGUSON:"
_PLATFORM_BAY_PREFIX = "PLATFORM:"
_EXPECTED_BAY_KEY_PARTS = 5
_EXPECTED_ORDER_LINE_KEY_PARTS = 6
_EXPECTED_RETURN_KEY_PARTS = 3
_EXPECTED_RETURN_ITEM_KEY_PARTS = 4
_EXPECTED_SALES_ORDER_KEY_PARTS = 4

BayIdentifier = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[^:*\s\x00-\x1f\x7f]+$",
    ),
]
"""Bay identifier safe for platform canonical keys."""

PositivePackageCount = Annotated[int, Field(strict=True, ge=1, le=1_000_000)]
"""Bounded positive package count."""

BayPriority = Annotated[int, Field(strict=True, ge=0, le=1_000_000)]
"""Deterministic non-negative Bay selection priority."""


class AssignmentEvidence(CanonicalBaseModel):
    """One immutable evidence reference attached to a Bay assignment."""

    evidence_type: NonBlankText
    evidence_reference: NonBlankText


def _raise_validation_error(error_type: str, message: str) -> Never:
    """Raise a stable Pydantic validation error."""
    raise PydanticCustomError(error_type, message)


def _validate_warehouse_key(warehouse_key: str, *, error_type: str) -> None:
    """Validate the finalized Ferguson warehouse namespace."""
    if not warehouse_key.startswith(_WAREHOUSE_NAMESPACE):
        _raise_validation_error(
            error_type,
            "warehouse_key must use the FERGUSON namespace",
        )
    suffix = warehouse_key.removeprefix(_WAREHOUSE_NAMESPACE)
    if not suffix or ":" in suffix or "*" in suffix:
        _raise_validation_error(error_type, "warehouse_key has an invalid suffix")


def _bay_warehouse_key(bay_key: str, *, error_type: str) -> str:
    """Validate a platform Bay key and return its embedded Warehouse key."""
    parts = bay_key.split(":")
    if (
        len(parts) != _EXPECTED_BAY_KEY_PARTS
        or parts[0] != "PLATFORM"
        or parts[1] != "FERGUSON"
        or parts[3] != "BAY"
        or not parts[2]
        or not parts[4]
        or "*" in parts[2]
        or "*" in parts[4]
    ):
        _raise_validation_error(
            error_type,
            "bay_key must use PLATFORM:FERGUSON:warehouseId:BAY:bayId",
        )
    return f"FERGUSON:{parts[2]}"


def _validate_return_key(return_key: str) -> None:
    """Validate an OMC V1 or V2 Return key."""
    parts = return_key.split(":")
    if (
        len(parts) != _EXPECTED_RETURN_KEY_PARTS
        or parts[0] != "OMC"
        or parts[1] not in {"V1", "V2"}
        or not parts[2]
        or "*" in parts[2]
    ):
        _raise_validation_error(
            "bay_assignment_return_key_invalid",
            "return_key must use OMC:V1:returnId or OMC:V2:rmaId",
        )


def _validate_return_item_reference(return_item_key: str, *, return_key: str) -> None:
    """Validate ReturnItem shape and version consistency."""
    parts = return_item_key.split(":")
    if (
        len(parts) != _EXPECTED_RETURN_ITEM_KEY_PARTS
        or parts[0] != "OMC"
        or parts[1] not in {"V1", "V2"}
        or parts[2] != "ITEM"
        or not parts[3]
        or "*" in parts[3]
    ):
        _raise_validation_error(
            "bay_assignment_return_item_key_invalid",
            "return_item_key must use OMC:V1:ITEM:cartItemId or OMC:V2:ITEM:cartItemId",
        )

    return_parts = return_key.split(":")
    if parts[1] != return_parts[1]:
        _raise_validation_error(
            "bay_assignment_return_version_mismatch",
            "return_item_key and return_key must use the same OMC version",
        )


def _validate_sales_order_key(sales_order_key: str) -> None:
    """Validate a finalized SalesOrder key."""
    parts = sales_order_key.split(":")
    if (
        len(parts) != _EXPECTED_SALES_ORDER_KEY_PARTS
        or parts[0] != "TDS"
        or not all(parts[1:])
        or any("*" in component for component in parts[1:])
    ):
        _raise_validation_error(
            "bay_assignment_sales_order_key_invalid",
            "sales_order_key must use TDS:accountId:orderId:orderInstanceKey",
        )


def _validate_order_line_key(order_line_key: str, *, sales_order_key: str) -> None:
    """Validate that an OrderLine belongs to the referenced SalesOrder."""
    parts = order_line_key.split(":")
    if (
        len(parts) != _EXPECTED_ORDER_LINE_KEY_PARTS
        or parts[0] != "TDS"
        or parts[4] != "LINE"
        or not all(parts[1:])
        or any("*" in component for component in parts[1:])
    ):
        _raise_validation_error(
            "bay_assignment_order_line_key_invalid",
            "order_line_key must use TDS:accountId:orderId:instance:LINE:lineNumber",
        )

    if ":".join(parts[:4]) != sales_order_key:
        _raise_validation_error(
            "bay_assignment_order_line_parent_mismatch",
            "order_line_key does not belong to sales_order_key",
        )


def _reject_duplicate_text(values: tuple[str, ...], *, error_type: str) -> None:
    """Reject duplicate case-sensitive values in immutable tuples."""
    duplicates = [value for value, count in Counter(values).items() if count > 1]
    if duplicates:
        _raise_validation_error(error_type, "duplicate values are not allowed")


class Bay(CanonicalBaseModel):
    """Platform-owned return-staging Bay configuration."""

    bay_key: CanonicalIdentifier
    bay_id: BayIdentifier
    warehouse_key: CanonicalIdentifier
    branch_id: CanonicalIdentifier | None = None
    bay_name: NonBlankText
    bay_type: NonBlankText
    active: bool
    priority: BayPriority
    supported_shipping_paths: tuple[NonBlankText, ...] = ()
    supported_product_types: tuple[NonBlankText, ...] = ()
    maximum_package_count: PositivePackageCount
    overflow_bay_key: CanonicalIdentifier | None = None
    configuration_version: VersionReference

    @model_validator(mode="after")
    def validate_identity_and_configuration(self) -> Self:
        """Validate platform identity and bounded overflow configuration."""
        _validate_warehouse_key(
            self.warehouse_key,
            error_type="bay_warehouse_key_invalid",
        )
        expected_key = f"{_PLATFORM_BAY_PREFIX}{self.warehouse_key}:BAY:{self.bay_id}"
        if self.bay_key != expected_key:
            _raise_validation_error(
                "bay_key_mismatch",
                "bay_key does not match warehouse_key and bay_id",
            )

        _reject_duplicate_text(
            self.supported_shipping_paths,
            error_type="bay_duplicate_shipping_path",
        )
        _reject_duplicate_text(
            self.supported_product_types,
            error_type="bay_duplicate_product_type",
        )

        if self.overflow_bay_key is not None:
            overflow_warehouse_key = _bay_warehouse_key(
                self.overflow_bay_key,
                error_type="bay_overflow_key_invalid",
            )
            if overflow_warehouse_key != self.warehouse_key:
                _raise_validation_error(
                    "bay_overflow_warehouse_mismatch",
                    "overflow Bay must belong to the same Warehouse",
                )
            if self.overflow_bay_key == self.bay_key:
                _raise_validation_error(
                    "bay_overflow_self_reference",
                    "Bay cannot overflow to itself",
                )

        return self


class BayAssignment(CanonicalBaseModel):
    """Platform-owned operational assignment of a ReturnItem to a Bay."""

    assignment_id: UUID
    return_key: CanonicalIdentifier
    return_item_key: CanonicalIdentifier
    sales_order_key: CanonicalIdentifier
    order_line_key: CanonicalIdentifier
    warehouse_key: CanonicalIdentifier
    bay_key: CanonicalIdentifier
    package_count: PositivePackageCount
    status: NonBlankText
    assigned_by: NonBlankText
    confirmed_by: NonBlankText | None = None
    created_at: UtcDateTime
    confirmed_at: UtcDateTime | None = None
    released_at: UtcDateTime | None = None
    evidence: tuple[AssignmentEvidence, ...] = ()

    @model_validator(mode="after")
    def validate_references_and_timeline(self) -> Self:
        """Validate referential consistency and lifecycle timestamps."""
        _validate_return_key(self.return_key)
        _validate_return_item_reference(
            self.return_item_key,
            return_key=self.return_key,
        )
        _validate_sales_order_key(self.sales_order_key)
        _validate_order_line_key(
            self.order_line_key,
            sales_order_key=self.sales_order_key,
        )
        _validate_warehouse_key(
            self.warehouse_key,
            error_type="bay_assignment_warehouse_key_invalid",
        )
        bay_warehouse_key = _bay_warehouse_key(
            self.bay_key,
            error_type="bay_assignment_bay_key_invalid",
        )
        if bay_warehouse_key != self.warehouse_key:
            _raise_validation_error(
                "bay_assignment_bay_warehouse_mismatch",
                "bay_key does not belong to warehouse_key",
            )

        if (self.confirmed_by is None) != (self.confirmed_at is None):
            _raise_validation_error(
                "bay_assignment_confirmation_pair_invalid",
                "confirmed_by and confirmed_at must be set together",
            )

        if self.confirmed_at is not None and self.confirmed_at < self.created_at:
            _raise_validation_error(
                "bay_assignment_confirmation_time_invalid",
                "confirmed_at cannot precede created_at",
            )

        minimum_release_time = self.confirmed_at or self.created_at
        if self.released_at is not None and self.released_at < minimum_release_time:
            _raise_validation_error(
                "bay_assignment_release_time_invalid",
                "released_at cannot precede assignment confirmation or creation",
            )

        evidence_keys = tuple(
            f"{item.evidence_type}\x1f{item.evidence_reference}" for item in self.evidence
        )
        _reject_duplicate_text(
            evidence_keys,
            error_type="bay_assignment_duplicate_evidence",
        )

        return self
