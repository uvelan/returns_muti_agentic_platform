"""Canonical enterprise product contract."""

from typing import Annotated, Never, Self

from pydantic import StringConstraints, model_validator
from pydantic_core import PydanticCustomError

from return_platform.canonical.base import (
    CanonicalBaseModel,
    CanonicalIdentifier,
    NonBlankText,
    SourceProvenance,
    UtcDateTime,
)

__all__ = ["Product"]

_STEP_NAMESPACE = "STEP:"
_STEP_SOURCE_SYSTEM = "STEP"

MasterProductIdentifier = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=1,
        max_length=256,
        pattern=r"^[^:\s\x00-\x1f\x7f]+$",
    ),
]
"""Enterprise master-product identifier safe for STEP canonical keys."""

LongProductText = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=1,
        max_length=16_384,
    ),
]
"""Bounded source text for long descriptions and substitution notes."""

PhysicalMeasurementSnapshot = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=1,
        max_length=2_048,
    ),
]
"""Source-preserving measurement text until unit mappings are approved."""


def _raise_validation_error(
    error_type: str,
    message: str,
) -> Never:
    """Raise a stable Pydantic validation error."""
    raise PydanticCustomError(error_type, message)


class Product(CanonicalBaseModel):
    """Enterprise product mastered by STEP and enriched for return discovery."""

    product_key: CanonicalIdentifier
    master_product_id: MasterProductIdentifier
    product_id: CanonicalIdentifier | None = None
    vendor_product_code: NonBlankText | None = None
    upc: NonBlankText | None = None
    base_model_number: NonBlankText | None = None
    description: NonBlankText | None = None
    long_description: LongProductText | None = None
    product_status: NonBlankText | None = None
    category_id: CanonicalIdentifier | None = None
    category_name: NonBlankText | None = None
    brand_name: NonBlankText | None = None
    manufacturer_name: NonBlankText | None = None
    supplier_name: NonBlankText | None = None
    primary_vendor_id: CanonicalIdentifier | None = None
    unit_of_measure: NonBlankText | None = None
    unit_of_measure_description: NonBlankText | None = None
    serial_number_required: bool | None = None
    obsolete: bool | None = None
    substitute_product_key: CanonicalIdentifier | None = None
    substitution_notes: LongProductText | None = None
    weight: PhysicalMeasurementSnapshot | None = None
    dimensions: PhysicalMeasurementSnapshot | None = None
    source_updated_at: UtcDateTime | None = None
    provenance: SourceProvenance

    @model_validator(mode="after")
    def validate_identity_and_provenance(self) -> Self:
        """Reject STEP identity drift and unsafe substitute references."""
        expected_key = f"{_STEP_NAMESPACE}{self.master_product_id}"
        if self.product_key != expected_key:
            _raise_validation_error(
                "product_key_mismatch",
                "product_key does not match master_product_id",
            )

        if self.provenance.source_system != _STEP_SOURCE_SYSTEM:
            _raise_validation_error(
                "product_source_system_invalid",
                "product provenance source_system must be STEP",
            )

        if self.source_updated_at != self.provenance.source_updated_at:
            _raise_validation_error(
                "product_source_updated_at_mismatch",
                "source_updated_at does not match provenance",
            )

        if self.substitute_product_key is not None:
            if not self.substitute_product_key.startswith(_STEP_NAMESPACE):
                _raise_validation_error(
                    "product_substitute_namespace_invalid",
                    "substitute_product_key must use the STEP namespace",
                )

            substitute_id = self.substitute_product_key.removeprefix(
                _STEP_NAMESPACE,
            )
            if not substitute_id or ":" in substitute_id:
                _raise_validation_error(
                    "product_substitute_namespace_invalid",
                    "substitute_product_key contains an invalid suffix",
                )

            if self.substitute_product_key == self.product_key:
                _raise_validation_error(
                    "product_self_substitution_invalid",
                    "product cannot substitute itself",
                )

        return self
