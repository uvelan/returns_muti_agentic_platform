"""Deterministic tests for canonical customer contracts."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from return_platform.canonical.customer import (
    Address,
    ContactPoint,
    Customer,
    CustomerAccount,
)

_DIGEST = "a" * 64
_SOURCE_UPDATED_AT = datetime(2026, 7, 20, 6, 0, tzinfo=UTC)


def _provenance_payload(
    *,
    source_record_id: str,
    source_updated_at: datetime | None = _SOURCE_UPDATED_AT,
) -> dict[str, object]:
    return {
        "source_system": "CUSTOMER_CDM",
        "source_database": "eventMessages",
        "source_asset": "customerOutboundCDM",
        "source_record_id": source_record_id,
        "source_updated_at": source_updated_at,
        "source_version": "42",
        "source_event_id": "evt-42",
        "source_hash": _DIGEST,
        "observed_at": datetime(2026, 7, 20, 6, 1, tzinfo=UTC),
        "mapping_version": "canonical-v1",
        "configuration_version": "data-platform-v1",
        "configuration_digest": _DIGEST,
    }


def _customer_payload() -> dict[str, object]:
    return {
        "customer_key": "CUSTOMER_CDM:party-100",
        "party_id": "party-100",
        "party_number": "100",
        "party_name": "Acme Plumbing",
        "organization_name": "Acme Plumbing LLC",
        "party_type": "ORGANIZATION",
        "status": "ACTIVE",
        "source_system": "CUSTOMER_CDM",
        "source_record_id": "party-100",
        "source_updated_at": _SOURCE_UPDATED_AT,
        "provenance": _provenance_payload(source_record_id="party-100"),
    }


def _account_payload() -> dict[str, object]:
    return {
        "account_key": "CUSTOMER_CDM:101*customer-200",
        "customer_key": "CUSTOMER_CDM:party-100",
        "account_number": "101*customer-200",
        "customer_id": "customer-200",
        "account_name": "Acme Plumbing - Branch 101",
        "account_type": "COMMERCIAL",
        "account_status": "ACTIVE",
        "branch_id": "101",
        "master_account": "MASTER-200",
        "payment_terms": "NET30",
        "preferred_ship_via_code": "GROUND",
        "shipping_instructions": "Deliver to receiving entrance",
        "ship_to_phone": "+1 555 010 2000",
        "provenance": _provenance_payload(source_record_id="101*customer-200"),
    }


def test_customer_accepts_confirmed_identity_and_provenance() -> None:
    customer = Customer.model_validate(_customer_payload())

    assert customer.customer_key == "CUSTOMER_CDM:party-100"
    assert customer.party_id == "party-100"
    assert customer.provenance.source_record_id == "party-100"


def test_customer_rejects_identity_mismatch() -> None:
    payload = _customer_payload()
    payload["customer_key"] = "CUSTOMER_CDM:party-999"

    with pytest.raises(ValidationError) as exc_info:
        Customer.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "customer_key_mismatch"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_system", "OTHER"),
        ("source_record_id", "party-999"),
        ("source_updated_at", datetime(2026, 7, 21, 6, 0, tzinfo=UTC)),
    ],
)
def test_customer_rejects_duplicate_source_metadata_drift(
    field: str,
    value: object,
) -> None:
    payload = _customer_payload()
    payload[field] = value

    with pytest.raises(ValidationError) as exc_info:
        Customer.model_validate(payload)

    expected_error = f"customer_{field}_mismatch"
    assert exc_info.value.errors()[0]["type"] == expected_error


def test_customer_allows_missing_optional_descriptive_fields() -> None:
    payload = _customer_payload()
    for field in (
        "party_number",
        "party_name",
        "organization_name",
        "party_type",
        "status",
    ):
        payload[field] = None

    customer = Customer.model_validate(payload)

    assert customer.party_name is None
    assert customer.status is None


def test_customer_rejects_unknown_fields() -> None:
    payload = _customer_payload()
    payload["unknown"] = "value"

    with pytest.raises(ValidationError) as exc_info:
        Customer.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "extra_forbidden"


def test_customer_account_accepts_confirmed_account_format() -> None:
    account = CustomerAccount.model_validate(_account_payload())

    assert account.account_key == "CUSTOMER_CDM:101*customer-200"
    assert account.customer_id == "customer-200"


def test_customer_account_rejects_account_key_mismatch() -> None:
    payload = _account_payload()
    payload["account_key"] = "CUSTOMER_CDM:999*customer-200"

    with pytest.raises(ValidationError) as exc_info:
        CustomerAccount.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "customer_account_key_mismatch"


@pytest.mark.parametrize(
    "account_number",
    ["101", "101*", "*customer-200", "101*customer-200*duplicate"],
)
def test_customer_account_rejects_malformed_account_number(
    account_number: str,
) -> None:
    payload = _account_payload()
    payload["account_number"] = account_number
    payload["account_key"] = f"CUSTOMER_CDM:{account_number}"

    with pytest.raises(ValidationError) as exc_info:
        CustomerAccount.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "customer_account_number_invalid"


def test_customer_account_rejects_customer_id_mismatch() -> None:
    payload = _account_payload()
    payload["customer_id"] = "customer-999"

    with pytest.raises(ValidationError) as exc_info:
        CustomerAccount.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "customer_account_customer_id_mismatch"


def test_customer_account_rejects_foreign_customer_key_namespace() -> None:
    payload = _account_payload()
    payload["customer_key"] = "OTHER:party-100"

    with pytest.raises(ValidationError) as exc_info:
        CustomerAccount.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == "customer_account_customer_namespace_invalid"


def test_contact_point_accepts_email_only() -> None:
    contact = ContactPoint(
        contact_id="contact-1",
        contact_type="BILLING",
        email="billing@example.com",
        primary=True,
    )

    assert contact.email == "billing@example.com"
    assert contact.phone is None


def test_contact_point_accepts_phone_with_searchable_phone() -> None:
    contact = ContactPoint(
        contact_id="contact-2",
        contact_type="SHIPPING",
        phone="+1 (555) 010-2000",
        searchable_phone="+15550102000",
    )

    assert contact.searchable_phone == "+15550102000"


@pytest.mark.parametrize(
    ("phone", "searchable_phone"),
    [("+1 (555) 010-2000", None), (None, "+15550102000")],
)
def test_contact_point_requires_phone_pair(
    phone: str | None,
    searchable_phone: str | None,
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        ContactPoint(
            contact_id="contact-3",
            contact_type="SHIPPING",
            phone=phone,
            searchable_phone=searchable_phone,
        )

    assert exc_info.value.errors()[0]["type"] == "contact_phone_pair_required"


def test_contact_point_requires_at_least_one_contact_method() -> None:
    with pytest.raises(ValidationError) as exc_info:
        ContactPoint(contact_id="contact-4", contact_type="BILLING")

    assert exc_info.value.errors()[0]["type"] == "contact_method_required"


@pytest.mark.parametrize(
    "email",
    ["missing-at.example.com", "person@example", "person @example.com"],
)
def test_contact_point_rejects_malformed_email(email: str) -> None:
    with pytest.raises(ValidationError):
        ContactPoint(
            contact_id="contact-5",
            contact_type="BILLING",
            email=email,
        )


def test_contact_point_rejects_reversed_effective_interval() -> None:
    effective_from = datetime(2026, 7, 20, 6, 0, tzinfo=UTC)

    with pytest.raises(ValidationError) as exc_info:
        ContactPoint(
            contact_id="contact-6",
            contact_type="BILLING",
            email="billing@example.com",
            effective_from=effective_from,
            effective_to=effective_from - timedelta(seconds=1),
        )

    assert exc_info.value.errors()[0]["type"] == "contact_effective_interval_invalid"


def test_address_trims_values_and_keeps_optional_fields_absent() -> None:
    address = Address(
        address_id="address-1",
        address_type="SHIPPING",
        line1="  100 Main Street  ",
        city="  Newport News  ",
    )

    assert address.line1 == "100 Main Street"
    assert address.city == "Newport News"
    assert address.postal_code is None


def test_address_rejects_blank_required_line() -> None:
    with pytest.raises(ValidationError):
        Address(
            address_id="address-2",
            address_type="SHIPPING",
            line1="   ",
        )
