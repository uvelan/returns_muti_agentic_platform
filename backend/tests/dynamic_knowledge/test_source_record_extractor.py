from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from return_platform.dynamic_knowledge.on_demand_sync.contracts import (
    ProjectionReadScope,
    RawSourceDocument,
    RawSourcePage,
)
from return_platform.dynamic_knowledge.on_demand_sync.extraction import (
    ExtractionError,
    GenericSourceRecordExtractor,
)
from return_platform.dynamic_knowledge.schema import ActiveSchema
from return_platform.security.contact_evidence import contact_lookup_digest

FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "ferguson_source_samples"


def _load(name: str) -> dict[str, object]:
    with (FIXTURE_DIR / name).open(encoding="utf-8") as stream:
        return json.load(stream)


def _customer_account_schema(active_schema: ActiveSchema) -> ActiveSchema:
    """Reshape entity_b (backed by source_b) into a customer_account-style exploded entity,
    reusing the generic conftest fixture's entities/sources rather than inventing new ones."""
    raw = active_schema.model_dump(mode="json")
    raw["entities"]["entity_b"] = {
        "entity_id": "entity_b",
        "source_asset_id": "source_b",
        "record_path": ["party", "partyMainCusts"],
        "explode": True,
        "distinct": True,
        "fields": {
            "id": {
                "field_id": "id",
                "physical_path": ["mainCusts"],
                "path_origin": "CURRENT_RECORD",
                "graph_property": "related_id",
                "data_type": "STRING",
                "nullable": False,
                "capabilities": {"searchable": True, "filterable": True, "operators": ["EXACT"]},
                "permissions": {"searchable_by": ["associate"], "displayable_by": ["associate"]},
            },
            "parent_id": {
                "field_id": "parent_id",
                "physical_path": ["partyId"],
                "path_origin": "ROOT_DOCUMENT",
                "graph_property": "configured_parent_id",
                "data_type": "STRING",
                "capabilities": {"searchable": True, "filterable": True, "operators": ["EXACT"]},
                "permissions": {"searchable_by": ["associate"]},
            },
        },
        "natural_key": ["id"],
        "strong_anchors": {},
    }
    raw["graph"]["nodes"]["node_b"]["property_fields"] = ["parent_id"]
    return ActiveSchema.model_validate(raw)


def _phone_only_contact_point_schema(active_schema: ActiveSchema) -> ActiveSchema:
    raw = active_schema.model_dump(mode="json")
    raw["entities"]["entity_b"] = {
        "entity_id": "entity_b",
        "source_asset_id": "source_b",
        "record_path": ["party", "customerContactPoints"],
        "explode": True,
        "where": [{"physical_path": ["contactPointType"], "equals": "PHONE"}],
        "fields": {
            "id": {
                "field_id": "id",
                "physical_path": ["contactPointId"],
                "graph_property": "related_id",
                "data_type": "STRING",
                "nullable": False,
                "capabilities": {"searchable": True, "filterable": True, "operators": ["EXACT"]},
                "permissions": {"searchable_by": ["associate"], "displayable_by": ["associate"]},
            },
            "parent_id": {
                "field_id": "parent_id",
                "physical_path": ["searchPhoneNumber"],
                "graph_property": "configured_parent_id",
                "data_type": "STRING",
                "capabilities": {"searchable": True, "filterable": True, "operators": ["EXACT"]},
                "permissions": {"searchable_by": ["associate"]},
            },
        },
        "natural_key": ["id"],
        "strong_anchors": {},
    }
    raw["graph"]["nodes"]["node_b"]["property_fields"] = ["parent_id"]
    return ActiveSchema.model_validate(raw)


def _derived_logon_schema(active_schema: ActiveSchema) -> ActiveSchema:
    raw = active_schema.model_dump(mode="json")
    raw["entities"]["entity_a"]["fields"]["logon"] = {
        "field_id": "logon",
        "physical_path": None,
        "derive": {
            "operation": "SPLIT_PART",
            "source_field": "id",
            "delimiter": "*",
            "index": 0,
        },
        "graph_property": "logon",
        "data_type": "STRING",
        "capabilities": {"searchable": True, "filterable": True, "operators": ["EXACT"]},
        "permissions": {"searchable_by": ["associate"]},
    }
    raw["graph"]["nodes"]["node_a"]["property_fields"].append("logon")
    return ActiveSchema.model_validate(raw)


def _contact_digest_schema(active_schema: ActiveSchema) -> ActiveSchema:
    raw = active_schema.model_dump(mode="json")
    raw["entities"]["entity_a"]["fields"]["phone_hash"] = {
        "field_id": "phone_hash",
        "physical_path": None,
        "derive": {
            "operation": "CONTACT_LOOKUP_DIGEST",
            "source_field": "name",
            "contact_kind": "PHONE",
            "key_reference": "vault://return-platform/contact-lookup#hmac_key",
            "key_version": 1,
        },
        "graph_property": "phone_hash",
        "data_type": "STRING",
        "capabilities": {"searchable": True, "filterable": True, "operators": ["EXACT"]},
        "permissions": {"searchable_by": ["associate"]},
    }
    raw["graph"]["nodes"]["node_a"]["property_fields"].append("phone_hash")
    return ActiveSchema.model_validate(raw)


def _coalesce_schema(active_schema: ActiveSchema) -> ActiveSchema:
    raw = active_schema.model_dump(mode="json")
    raw["entities"]["entity_a"]["fields"]["preferred_id"] = {
        "field_id": "preferred_id",
        "physical_path": None,
        "derive": {"operation": "COALESCE", "fields": ["name", "id"]},
        "graph_property": "preferred_id",
        "data_type": "STRING",
        "capabilities": {"searchable": True, "filterable": True, "operators": ["EXACT"]},
        "permissions": {"searchable_by": ["associate"]},
    }
    raw["graph"]["nodes"]["node_a"]["property_fields"].append("preferred_id")
    return ActiveSchema.model_validate(raw)


def _page(document: dict[str, object], source_identity: str = "doc-1") -> RawSourcePage:
    return RawSourcePage(
        documents=(
            RawSourceDocument(
                operation="UPSERT", document=document, source_identity=source_identity
            ),
        ),
        observed_at=datetime(2026, 8, 6, tzinfo=UTC),
    )


def test_exploded_customer_account_deduplicates_identical_natural_key(
    active_schema: ActiveSchema,
) -> None:
    schema = _customer_account_schema(active_schema)
    document = _load("customer_outbound_cdm.json")
    mutations = GenericSourceRecordExtractor().extract(
        schema=schema,
        source_asset_id="source_b",
        page=_page(document),
        read_scope=ProjectionReadScope.COMPLETE_SOURCE_DOCUMENT,
    )
    # The real fixture has 3 partyMainCusts entries but one is a duplicate --
    # distinct=True must collapse it to 2 accounts, not 3.
    assert len(mutations) == 2
    ids = {mutation.resolved_key["id"] for mutation in mutations}
    assert ids == {"PLYMOUTH*232385", "MINNWW*28634"}
    assert all(mutation.operation == "UPSERT" for mutation in mutations)
    assert all(
        mutation.record is not None and mutation.record.values["parent_id"] == "900781"
        for mutation in mutations
    )


def test_where_selector_admits_only_phone_contact_points(active_schema: ActiveSchema) -> None:
    schema = _phone_only_contact_point_schema(active_schema)
    document = _load("customer_outbound_cdm.json")
    mutations = GenericSourceRecordExtractor().extract(
        schema=schema,
        source_asset_id="source_b",
        page=_page(document),
        read_scope=ProjectionReadScope.COMPLETE_SOURCE_DOCUMENT,
    )
    # Fixture has one PHONE and one FAX contact point -- FAX must never surface here.
    assert len(mutations) == 1
    assert mutations[0].record is not None
    assert mutations[0].record.values["id"] == "MASTER*900781-0000*0"


def test_derived_field_splits_composite_order_key(active_schema: ActiveSchema) -> None:
    schema = _derived_logon_schema(active_schema)
    document = {"configured_id": "DALLAS*WE130468", "configured_name": "n", "configured_count": 1}
    mutations = GenericSourceRecordExtractor().extract(
        schema=schema,
        source_asset_id="source_a",
        page=_page(document),
        read_scope=ProjectionReadScope.COMPLETE_SOURCE_DOCUMENT,
    )
    assert len(mutations) == 1
    assert mutations[0].record is not None
    assert mutations[0].record.values["logon"] == "DALLAS"
    assert mutations[0].record.values["id"] == "DALLAS*WE130468"


def test_contact_lookup_digest_derive_matches_security_module_output(
    active_schema: ActiveSchema,
) -> None:
    schema = _contact_digest_schema(active_schema)
    document = {"configured_id": "A-1", "configured_name": "555-0100", "configured_count": 1}
    key_reference = "vault://return-platform/contact-lookup#hmac_key"
    secret = "s" * 32
    mutations = GenericSourceRecordExtractor(resolved_secrets={key_reference: secret}).extract(
        schema=schema,
        source_asset_id="source_a",
        page=_page(document),
        read_scope=ProjectionReadScope.COMPLETE_SOURCE_DOCUMENT,
    )
    assert len(mutations) == 1
    assert mutations[0].record is not None
    assert mutations[0].record.values["phone_hash"] == contact_lookup_digest(
        "555-0100", "PHONE", secret
    )


def test_contact_lookup_digest_derive_is_omitted_for_blank_source_value(
    active_schema: ActiveSchema,
) -> None:
    schema = _contact_digest_schema(active_schema)
    document = {"configured_id": "A-1", "configured_name": "   ", "configured_count": 1}
    key_reference = "vault://return-platform/contact-lookup#hmac_key"
    mutations = GenericSourceRecordExtractor(resolved_secrets={key_reference: "s" * 32}).extract(
        schema=schema,
        source_asset_id="source_a",
        page=_page(document),
        read_scope=ProjectionReadScope.COMPLETE_SOURCE_DOCUMENT,
    )
    assert len(mutations) == 1
    assert mutations[0].record is not None
    assert "phone_hash" not in mutations[0].record.values


def test_contact_lookup_digest_derive_fails_loudly_when_secret_unresolved(
    active_schema: ActiveSchema,
) -> None:
    schema = _contact_digest_schema(active_schema)
    document = {"configured_id": "A-1", "configured_name": "555-0100", "configured_count": 1}
    with pytest.raises(ExtractionError, match="unresolved secret"):
        GenericSourceRecordExtractor().extract(
            schema=schema,
            source_asset_id="source_a",
            page=_page(document),
            read_scope=ProjectionReadScope.COMPLETE_SOURCE_DOCUMENT,
        )


def test_coalesce_derive_picks_the_first_non_null_candidate(active_schema: ActiveSchema) -> None:
    schema = _coalesce_schema(active_schema)
    document = {"configured_id": "A-1", "configured_name": "n", "configured_count": 1}
    mutations = GenericSourceRecordExtractor().extract(
        schema=schema,
        source_asset_id="source_a",
        page=_page(document),
        read_scope=ProjectionReadScope.COMPLETE_SOURCE_DOCUMENT,
    )
    assert len(mutations) == 1
    assert mutations[0].record is not None
    assert mutations[0].record.values["preferred_id"] == "n"


def test_coalesce_derive_falls_through_to_a_later_candidate_when_earlier_ones_are_absent(
    active_schema: ActiveSchema,
) -> None:
    schema = _coalesce_schema(active_schema)
    document = {"configured_id": "A-1", "configured_count": 1}  # configured_name absent
    mutations = GenericSourceRecordExtractor().extract(
        schema=schema,
        source_asset_id="source_a",
        page=_page(document),
        read_scope=ProjectionReadScope.COMPLETE_SOURCE_DOCUMENT,
    )
    assert len(mutations) == 1
    assert mutations[0].record is not None
    assert mutations[0].record.values["preferred_id"] == "A-1"


def test_partial_targeted_read_scope_is_preserved_on_the_mutation(
    active_schema: ActiveSchema,
) -> None:
    document = {"configured_id": "A-1", "configured_name": "n", "configured_count": 1}
    mutations = GenericSourceRecordExtractor().extract(
        schema=active_schema,
        source_asset_id="source_a",
        page=_page(document),
        read_scope=ProjectionReadScope.PARTIAL_TARGETED_READ,
    )
    assert len(mutations) == 1
    assert mutations[0].read_scope is ProjectionReadScope.PARTIAL_TARGETED_READ


def test_delete_document_without_key_values_produces_no_mutation(
    active_schema: ActiveSchema,
) -> None:
    page = RawSourcePage(
        documents=(RawSourceDocument(operation="DELETE", document=None, source_identity="doc-1"),),
        observed_at=datetime(2026, 8, 6, tzinfo=UTC),
    )
    mutations = GenericSourceRecordExtractor().extract(
        schema=active_schema,
        source_asset_id="source_a",
        page=page,
        read_scope=ProjectionReadScope.COMPLETE_SOURCE_DOCUMENT,
    )
    assert mutations == ()


def test_delete_document_with_key_values_produces_delete_mutation(
    active_schema: ActiveSchema,
) -> None:
    page = RawSourcePage(
        documents=(
            RawSourceDocument(
                operation="DELETE",
                document=None,
                source_identity="doc-1",
                source_key_values={"id": "A-1"},
            ),
        ),
        observed_at=datetime(2026, 8, 6, tzinfo=UTC),
    )
    mutations = GenericSourceRecordExtractor().extract(
        schema=active_schema,
        source_asset_id="source_a",
        page=page,
        read_scope=ProjectionReadScope.COMPLETE_SOURCE_DOCUMENT,
    )
    assert len(mutations) == 1
    assert mutations[0].operation == "DELETE"
    assert mutations[0].record is None
    assert mutations[0].resolved_key == {"id": "A-1"}


def test_missing_natural_key_field_produces_no_mutation(active_schema: ActiveSchema) -> None:
    document = {"configured_name": "n"}  # configured_id (the natural key) is absent
    mutations = GenericSourceRecordExtractor().extract(
        schema=active_schema,
        source_asset_id="source_a",
        page=_page(document),
        read_scope=ProjectionReadScope.COMPLETE_SOURCE_DOCUMENT,
    )
    assert mutations == ()
