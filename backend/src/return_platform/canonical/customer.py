"""Canonical customer, account, contact, and address contracts."""

from typing import Annotated, Self

from pydantic import StringConstraints, model_validator
from pydantic_core import PydanticCustomError

from return_platform.canonical.base import (
    CanonicalBaseModel,
    CanonicalIdentifier,
    NonBlankText,
    SourceProvenance,
    UtcDateTime,
)

__all__ = [
    "Address",
    "ContactPoint",
    "Customer",
    "CustomerAccount",
]

_EMAIL_PATTERN = r"^[^\s@]+@[^\s@]+\.[^\s@]+$"
_SEARCHABLE_PHONE_PATTERN = r"^\+?[0-9]{7,20}$"
_ACCOUNT_PART_COUNT = 2
_CUSTOMER_NAMESPACE = "CUSTOMER_CDM:"

EmailAddress = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=3,
        max_length=320,
        pattern=_EMAIL_PATTERN,
    ),
]
"""Strict bounded email address suitable for canonical storage."""

SearchablePhone = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        pattern=_SEARCHABLE_PHONE_PATTERN,
    ),
]
"""Normalized phone token containing only an optional leading plus and digits."""


class ContactPoint(CanonicalBaseModel):
    """Customer contact value object embedded in canonical records."""

    contact_id: CanonicalIdentifier
    contact_type: NonBlankText
    email: EmailAddress | None = None
    phone: NonBlankText | None = None
    searchable_phone: SearchablePhone | None = None
    first_name: NonBlankText | None = None
    last_name: NonBlankText | None = None
    primary: bool = False
    status: NonBlankText | None = None
    effective_from: UtcDateTime | None = None
    effective_to: UtcDateTime | None = None

    @model_validator(mode="after")
    def validate_contact_methods(self) -> Self:
        """Require usable contact data and a coherent effective interval."""
        if (self.phone is None) != (self.searchable_phone is None):
            raise PydanticCustomError(
                "contact_phone_pair_required",
                "phone and searchable_phone must be provided together",
            )

        if self.email is None and self.phone is None:
            raise PydanticCustomError(
                "contact_method_required",
                "contact point requires email or phone",
            )

        if (
            self.effective_from is not None
            and self.effective_to is not None
            and self.effective_to < self.effective_from
        ):
            raise PydanticCustomError(
                "contact_effective_interval_invalid",
                "effective_to cannot precede effective_from",
            )

        return self


class Address(CanonicalBaseModel):
    """Customer address value object embedded in canonical records."""

    address_id: CanonicalIdentifier
    address_type: NonBlankText
    line1: NonBlankText
    line2: NonBlankText | None = None
    line3: NonBlankText | None = None
    line4: NonBlankText | None = None
    city: NonBlankText | None = None
    state: NonBlankText | None = None
    postal_code: NonBlankText | None = None
    country: NonBlankText | None = None
    primary: bool = False


class Customer(CanonicalBaseModel):
    """Enterprise customer party normalized from Customer CDM."""

    customer_key: CanonicalIdentifier
    party_id: CanonicalIdentifier
    party_number: NonBlankText | None = None
    party_name: NonBlankText | None = None
    organization_name: NonBlankText | None = None
    party_type: NonBlankText | None = None
    status: NonBlankText | None = None
    source_system: CanonicalIdentifier
    source_record_id: NonBlankText
    source_updated_at: UtcDateTime | None = None
    provenance: SourceProvenance

    @model_validator(mode="after")
    def validate_identity_and_provenance(self) -> Self:
        """Reject identity drift and duplicate source metadata divergence."""
        expected_key = f"{_CUSTOMER_NAMESPACE}{self.party_id}"
        if self.customer_key != expected_key:
            raise PydanticCustomError(
                "customer_key_mismatch",
                "customer_key does not match party_id",
            )

        if self.source_system != self.provenance.source_system:
            raise PydanticCustomError(
                "customer_source_system_mismatch",
                "source_system does not match provenance",
            )

        if self.source_record_id != self.provenance.source_record_id:
            raise PydanticCustomError(
                "customer_source_record_id_mismatch",
                "source_record_id does not match provenance",
            )

        if self.source_updated_at != self.provenance.source_updated_at:
            raise PydanticCustomError(
                "customer_source_updated_at_mismatch",
                "source_updated_at does not match provenance",
            )

        return self


class CustomerAccount(CanonicalBaseModel):
    """Branch- or business-specific account owned by one customer party."""

    account_key: CanonicalIdentifier
    customer_key: CanonicalIdentifier
    account_number: CanonicalIdentifier
    customer_id: CanonicalIdentifier
    account_name: NonBlankText | None = None
    account_type: NonBlankText | None = None
    account_status: NonBlankText | None = None
    branch_id: CanonicalIdentifier | None = None
    master_account: NonBlankText | None = None
    payment_terms: NonBlankText | None = None
    preferred_ship_via_code: NonBlankText | None = None
    shipping_instructions: NonBlankText | None = None
    ship_to_phone: NonBlankText | None = None
    provenance: SourceProvenance

    @model_validator(mode="after")
    def validate_account_identity(self) -> Self:
        """Enforce the confirmed Customer CDM account identity format."""
        expected_key = f"{_CUSTOMER_NAMESPACE}{self.account_number}"
        if self.account_key != expected_key:
            raise PydanticCustomError(
                "customer_account_key_mismatch",
                "account_key does not match account_number",
            )

        if not self.customer_key.startswith(_CUSTOMER_NAMESPACE):
            raise PydanticCustomError(
                "customer_account_customer_namespace_invalid",
                "customer_key must use the CUSTOMER_CDM namespace",
            )

        account_parts = self.account_number.split("*")
        if len(account_parts) != _ACCOUNT_PART_COUNT or not all(account_parts):
            raise PydanticCustomError(
                "customer_account_number_invalid",
                "account_number must use LOGON*customerId format",
            )

        _, account_customer_id = account_parts
        if account_customer_id != self.customer_id:
            raise PydanticCustomError(
                "customer_account_customer_id_mismatch",
                "account_number customerId does not match customer_id",
            )

        return self
