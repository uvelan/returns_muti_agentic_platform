from __future__ import annotations

import pytest
from pydantic import ValidationError

from return_platform.dynamic_knowledge.schema import ActiveSchema


def _with_entity_a_patch(active_schema: ActiveSchema, **patch: object) -> dict[str, object]:
    raw = active_schema.model_dump(mode="json")
    raw["entities"]["entity_a"].update(patch)
    return raw


def test_field_requires_exactly_one_of_physical_path_or_derive(
    active_schema: ActiveSchema,
) -> None:
    raw = _with_entity_a_patch(active_schema)
    raw["entities"]["entity_a"]["fields"]["name"]["physical_path"] = None
    with pytest.raises(ValidationError, match="exactly one of physical_path or derive"):
        ActiveSchema.model_validate(raw)


def test_field_with_both_physical_path_and_derive_is_rejected(
    active_schema: ActiveSchema,
) -> None:
    raw = _with_entity_a_patch(active_schema)
    raw["entities"]["entity_a"]["fields"]["name"]["derive"] = {
        "operation": "SPLIT_PART",
        "source_field": "id",
        "delimiter": "*",
        "index": 0,
    }
    with pytest.raises(ValidationError, match="exactly one of physical_path or derive"):
        ActiveSchema.model_validate(raw)


def test_ownership_policy_requires_explode(active_schema: ActiveSchema) -> None:
    raw = _with_entity_a_patch(
        active_schema,
        ownership_policy={"mode": "REPLACE_CHILD_SET", "owner_identity": "SOURCE_DOCUMENT"},
    )
    with pytest.raises(ValidationError, match="not exploded from a parent document"):
        ActiveSchema.model_validate(raw)


def test_ownership_policy_is_accepted_on_an_exploded_entity(active_schema: ActiveSchema) -> None:
    raw = _with_entity_a_patch(
        active_schema,
        record_path=["items"],
        explode=True,
        ownership_policy={"mode": "REPLACE_CHILD_SET", "owner_identity": "SOURCE_DOCUMENT"},
    )
    schema = ActiveSchema.model_validate(raw)
    assert schema.entities["entity_a"].ownership_policy is not None


def test_split_part_derive_requires_delimiter_and_index(active_schema: ActiveSchema) -> None:
    raw = _with_entity_a_patch(active_schema)
    raw["entities"]["entity_a"]["fields"]["name"]["physical_path"] = None
    raw["entities"]["entity_a"]["fields"]["name"]["derive"] = {
        "operation": "SPLIT_PART",
        "source_field": "id",
    }
    with pytest.raises(ValidationError, match="SPLIT_PART derive requires"):
        ActiveSchema.model_validate(raw)


def test_contact_lookup_digest_derive_requires_vault_key_reference(
    active_schema: ActiveSchema,
) -> None:
    raw = _with_entity_a_patch(active_schema)
    raw["entities"]["entity_a"]["fields"]["name"]["physical_path"] = None
    raw["entities"]["entity_a"]["fields"]["name"]["derive"] = {
        "operation": "CONTACT_LOOKUP_DIGEST",
        "source_field": "id",
        "contact_kind": "PHONE",
        "key_reference": "not-a-vault-uri",
        "key_version": 1,
    }
    with pytest.raises(ValidationError, match="vault://"):
        ActiveSchema.model_validate(raw)


def test_contact_lookup_digest_derive_requires_contact_kind_and_key_version(
    active_schema: ActiveSchema,
) -> None:
    raw = _with_entity_a_patch(active_schema)
    raw["entities"]["entity_a"]["fields"]["name"]["physical_path"] = None
    raw["entities"]["entity_a"]["fields"]["name"]["derive"] = {
        "operation": "CONTACT_LOOKUP_DIGEST",
        "source_field": "id",
        "key_reference": "vault://return-platform/contact-lookup#hmac_key",
    }
    with pytest.raises(ValidationError, match="contact_kind"):
        ActiveSchema.model_validate(raw)


def test_coalesce_derive_requires_at_least_two_fields(active_schema: ActiveSchema) -> None:
    raw = _with_entity_a_patch(active_schema)
    raw["entities"]["entity_a"]["fields"]["name"]["physical_path"] = None
    raw["entities"]["entity_a"]["fields"]["name"]["derive"] = {
        "operation": "COALESCE",
        "fields": ["id"],
    }
    with pytest.raises(ValidationError, match="at least two candidate fields"):
        ActiveSchema.model_validate(raw)


def test_coalesce_derive_rejects_source_field(active_schema: ActiveSchema) -> None:
    raw = _with_entity_a_patch(active_schema)
    raw["entities"]["entity_a"]["fields"]["name"]["physical_path"] = None
    raw["entities"]["entity_a"]["fields"]["name"]["derive"] = {
        "operation": "COALESCE",
        "source_field": "id",
        "fields": ["id", "count_value"],
    }
    with pytest.raises(ValidationError, match="uses fields, not source_field"):
        ActiveSchema.model_validate(raw)


def test_derived_field_resolves_against_a_sibling_field(active_schema: ActiveSchema) -> None:
    raw = _with_entity_a_patch(active_schema)
    raw["entities"]["entity_a"]["fields"]["name"]["physical_path"] = None
    raw["entities"]["entity_a"]["fields"]["name"]["derive"] = {
        "operation": "SPLIT_PART",
        "source_field": "id",
        "delimiter": "*",
        "index": 0,
    }
    schema = ActiveSchema.model_validate(raw)
    assert schema.entities["entity_a"].fields["name"].derive is not None
    assert schema.entities["entity_a"].fields["name"].derive.source_field == "id"


def test_derived_field_cannot_reference_unknown_field(active_schema: ActiveSchema) -> None:
    raw = _with_entity_a_patch(active_schema)
    raw["entities"]["entity_a"]["fields"]["name"]["physical_path"] = None
    raw["entities"]["entity_a"]["fields"]["name"]["derive"] = {
        "operation": "SPLIT_PART",
        "source_field": "does_not_exist",
        "delimiter": "*",
        "index": 0,
    }
    with pytest.raises(ValidationError, match="unknown field"):
        ActiveSchema.model_validate(raw)


def test_derived_field_cannot_derive_from_itself(active_schema: ActiveSchema) -> None:
    raw = _with_entity_a_patch(active_schema)
    raw["entities"]["entity_a"]["fields"]["name"]["physical_path"] = None
    raw["entities"]["entity_a"]["fields"]["name"]["derive"] = {
        "operation": "SPLIT_PART",
        "source_field": "name",
        "delimiter": "*",
        "index": 0,
    }
    with pytest.raises(ValidationError, match="cannot derive from itself"):
        ActiveSchema.model_validate(raw)


def test_chained_derivation_is_rejected(active_schema: ActiveSchema) -> None:
    raw = _with_entity_a_patch(active_schema)
    raw["entities"]["entity_a"]["fields"]["name"]["physical_path"] = None
    raw["entities"]["entity_a"]["fields"]["name"]["derive"] = {
        "operation": "SPLIT_PART",
        "source_field": "count_value",
        "delimiter": "*",
        "index": 0,
    }
    raw["entities"]["entity_a"]["fields"]["count_value"]["physical_path"] = None
    raw["entities"]["entity_a"]["fields"]["count_value"]["derive"] = {
        "operation": "SPLIT_PART",
        "source_field": "id",
        "delimiter": "*",
        "index": 0,
    }
    with pytest.raises(ValidationError, match="itself derived"):
        ActiveSchema.model_validate(raw)


def test_explode_requires_record_path(active_schema: ActiveSchema) -> None:
    raw = _with_entity_a_patch(active_schema, explode=True)
    with pytest.raises(ValidationError, match="no record_path"):
        ActiveSchema.model_validate(raw)


def test_explode_with_record_path_and_where_selector_is_valid(
    active_schema: ActiveSchema,
) -> None:
    raw = _with_entity_a_patch(
        active_schema,
        record_path=["nested", "items"],
        explode=True,
        where=[{"physical_path": ["itemType"], "equals": "PRIMARY"}],
        distinct=True,
    )
    schema = ActiveSchema.model_validate(raw)
    assert schema.entities["entity_a"].explode is True
    assert schema.entities["entity_a"].where[0].equals == "PRIMARY"


def test_key_resolution_source_field_must_be_known(active_schema: ActiveSchema) -> None:
    raw = _with_entity_a_patch(
        active_schema,
        key_resolution={"strategy": "SOURCE_FIELD", "source_field": "does_not_exist"},
    )
    with pytest.raises(ValidationError, match="unknown field"):
        ActiveSchema.model_validate(raw)


def test_key_resolution_source_field_valid(active_schema: ActiveSchema) -> None:
    raw = _with_entity_a_patch(
        active_schema,
        key_resolution={"strategy": "SOURCE_FIELD", "source_field": "id"},
    )
    schema = ActiveSchema.model_validate(raw)
    assert schema.entities["entity_a"].key_resolution.source_field == "id"


def test_key_resolution_hmac_requires_vault_reference() -> None:
    from return_platform.dynamic_knowledge.schema import KeyResolution

    with pytest.raises(ValidationError, match="vault://"):
        KeyResolution.model_validate(
            {
                "strategy": "DETERMINISTIC_HMAC",
                "fields": ["id", "name"],
                "key_reference": "not-a-vault-reference",
                "key_version": 1,
            }
        )


def test_key_resolution_hmac_rejects_plain_hash_by_only_offering_hmac_strategy() -> None:
    """There is intentionally no DETERMINISTIC_HASH strategy -- only DETERMINISTIC_HMAC --
    so an enumerable-identity (e.g. a phone number) can never be configured as a plain hash."""
    from return_platform.dynamic_knowledge.schema import KeyResolutionStrategy

    assert set(KeyResolutionStrategy) == {
        KeyResolutionStrategy.SOURCE_FIELD,
        KeyResolutionStrategy.DETERMINISTIC_HMAC,
    }


def test_key_resolution_hmac_valid() -> None:
    from return_platform.dynamic_knowledge.schema import KeyResolution

    resolution = KeyResolution.model_validate(
        {
            "strategy": "DETERMINISTIC_HMAC",
            "fields": ["party_id", "contact_point_type", "normalized_contact_value"],
            "key_reference": "vault://graph-identities/contact-point-hmac",
            "key_version": 1,
        }
    )
    assert resolution.key_version == 1


def test_where_selector_rejects_empty_path() -> None:
    from return_platform.dynamic_knowledge.schema import WhereSelector

    with pytest.raises(ValidationError, match="non-empty segments"):
        WhereSelector.model_validate({"physical_path": [], "equals": "PHONE"})
