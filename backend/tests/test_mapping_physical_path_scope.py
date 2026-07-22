"""Tests for nested source-record physical path scope semantics."""

from typing import Protocol, cast

import pytest
from pydantic import ValidationError

from return_platform.canonical.base import IdentityQuality
from return_platform.data_platform.mapping import (
    CanonicalEntityMapping,
    CanonicalEntityType,
    IdentityMapping,
    PhysicalFieldMapping,
    PhysicalPathScope,
)


class _MutablePhysicalFieldMapping(Protocol):
    path_scope: PhysicalPathScope


def _account_mapping_payload() -> dict[str, object]:
    return {
        "mapping_id": "canonical.customer_account.v1",
        "version": "1.0",
        "source_id": "source.customer_cdm",
        "entity_type": "CustomerAccount",
        "record_path": "custAccts[]",
        "fields": [
            {
                "canonical_field": "account_number",
                "source_paths": ["accountNumber"],
                "required": True,
            },
            {
                "canonical_field": "customer_id",
                "source_paths": ["accountNumber"],
                "required": True,
                "handler": "account_number_customer_id_v1",
            },
            {
                "canonical_field": "customer_key",
                "path_scope": "DOCUMENT",
                "source_paths": ["partyId"],
                "required": True,
                "handler": "customer_key_v1",
            },
        ],
        "identity": {
            "key_field": "account_key",
            "handler": "customer_account_key_v1",
            "component_fields": ["account_number"],
            "identity_quality": "VERIFIED",
        },
        "depends_on": ["canonical.customer.v1"],
    }


def test_physical_field_mapping_defaults_to_record_scope() -> None:
    mapping = PhysicalFieldMapping(
        canonical_field="account_number",
        source_paths=("accountNumber",),
    )

    assert mapping.path_scope is PhysicalPathScope.RECORD


def test_physical_field_mapping_accepts_document_scope_from_yaml_text() -> None:
    mapping = PhysicalFieldMapping.model_validate(
        {
            "canonical_field": "customer_key",
            "path_scope": "DOCUMENT",
            "source_paths": ["partyId"],
            "handler": "customer_key_v1",
        },
    )

    assert mapping.path_scope is PhysicalPathScope.DOCUMENT
    assert mapping.source_paths == ("partyId",)


def test_physical_field_mapping_rejects_unknown_scope() -> None:
    with pytest.raises(ValidationError) as exc_info:
        PhysicalFieldMapping.model_validate(
            {
                "canonical_field": "customer_key",
                "path_scope": "PARENT",
                "source_paths": ["partyId"],
            },
        )

    assert exc_info.value.errors()[0]["type"] == "enum"


def test_nested_customer_account_mapping_can_bind_record_and_document_paths() -> None:
    mapping = CanonicalEntityMapping.model_validate(_account_mapping_payload())

    assert mapping.entity_type is CanonicalEntityType.CUSTOMER_ACCOUNT
    assert mapping.record_path == "custAccts[]"
    assert mapping.identity == IdentityMapping(
        key_field="account_key",
        handler="customer_account_key_v1",
        component_fields=("account_number",),
        identity_quality=IdentityQuality.VERIFIED,
    )
    assert tuple(field.path_scope for field in mapping.fields) == (
        PhysicalPathScope.RECORD,
        PhysicalPathScope.RECORD,
        PhysicalPathScope.DOCUMENT,
    )


def test_path_scope_is_immutable() -> None:
    mapping = PhysicalFieldMapping(
        canonical_field="customer_key",
        path_scope=PhysicalPathScope.DOCUMENT,
        source_paths=("partyId",),
    )

    mutable_mapping = cast("_MutablePhysicalFieldMapping", mapping)
    with pytest.raises(ValidationError) as exc_info:
        mutable_mapping.path_scope = PhysicalPathScope.RECORD

    assert exc_info.value.errors()[0]["type"] == "frozen_instance"
