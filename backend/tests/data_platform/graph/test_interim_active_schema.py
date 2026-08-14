from __future__ import annotations

from datetime import UTC, datetime

import pytest
from _interim_active_schema import build_interim_active_schema

from return_platform.dynamic_knowledge.graph.constraints import (
    required_node_constraints,
    required_relationship_indexes,
)
from return_platform.dynamic_knowledge.graph.projector import GenericGraphProjector
from return_platform.dynamic_knowledge.graph.write_compiler import (
    compile_relationship_cardinality_checks,
    compile_relationship_reconciliation,
)
from return_platform.dynamic_knowledge.on_demand_sync.contracts import (
    ProjectionReadScope,
    RawSourceDocument,
    RawSourcePage,
)
from return_platform.dynamic_knowledge.on_demand_sync.extraction import GenericSourceRecordExtractor
from return_platform.dynamic_knowledge.schema import (
    ActiveSchema,
    EntitySourceAccess,
    RelationshipSourceAccess,
)


@pytest.fixture
def schema() -> ActiveSchema:
    return build_interim_active_schema(
        configuration_release_id="release-1",
        configuration_checksum="a" * 64,
        approved_by="admin",
        approved_at=datetime(2026, 8, 7, tzinfo=UTC),
    )


def test_schema_builds_and_validates(schema: ActiveSchema) -> None:
    assert len(schema.entities) == 11
    assert len(schema.graph.relationships) == 11


def test_order_line_stays_seed_only_and_unverified(schema: ActiveSchema) -> None:
    order_line = schema.entities["order_line"]
    assert order_line.source_access is EntitySourceAccess.SEED_ONLY
    assert order_line.source_contract_status.value == "UNVERIFIED"


@pytest.mark.parametrize(
    "relationship_id",
    ["sales_order_has_order_line", "order_line_references_product", "return_item_for_line"],
)
def test_order_line_touching_relationships_are_capped_seed_only(
    schema: ActiveSchema, relationship_id: str
) -> None:
    assert schema.graph.relationships[relationship_id].access is RelationshipSourceAccess.SEED_ONLY


def test_connected_sync_relationships_stay_connected_sync(schema: ActiveSchema) -> None:
    assert (
        schema.graph.relationships["customer_placed_order"].access
        is RelationshipSourceAccess.CONNECTED_SYNC
    )
    assert (
        schema.graph.relationships["return_has_return_item"].access
        is RelationshipSourceAccess.CONNECTED_SYNC
    )


def test_every_entity_node_has_a_derivable_constraint(schema: ActiveSchema) -> None:
    constraints = required_node_constraints(schema)
    assert len(constraints) == len(schema.graph.nodes)
    assert all(c.graph_properties[0] == "graph_generation_id" for c in constraints)


def test_every_relationship_compiles_a_stage_b_reconciliation_statement(
    schema: ActiveSchema,
) -> None:
    for relationship_id in schema.graph.relationships:
        compiled = compile_relationship_reconciliation(
            schema, relationship_id, graph_generation_id="gen-1"
        )
        assert "MERGE (a)-[rel:" in compiled.cypher
        # cardinality checks must compile too, even when no bound is configured (empty tuple)
        compile_relationship_cardinality_checks(
            schema, relationship_id, graph_generation_id="gen-1"
        )


def test_every_relationship_has_a_derivable_index_on_both_endpoints(schema: ActiveSchema) -> None:
    indexes = required_relationship_indexes(schema)
    assert len(indexes) == 2 * len(schema.graph.relationships)


def _page(document: dict[str, object]) -> RawSourcePage:
    return RawSourcePage(
        documents=(
            RawSourceDocument(operation="UPSERT", document=document, source_identity="doc-1"),
        ),
        observed_at=datetime(2026, 8, 7, tzinfo=UTC),
    )


def test_customer_extraction_coalesces_identity_and_hashes_contact_fields(
    schema: ActiveSchema,
) -> None:
    document = {
        "partyId": "900781",
        "customerId": None,
        "customerName": "Acme Plumbing",
        "phoneNumber": "555-0100",
        "email": None,
        "updatedAt": "2026-08-01T00:00:00Z",
        "accounts": [{"accountNumber": "232385"}, {"accountNumber": "28634"}],
    }
    secret = "s" * 32
    extractor = GenericSourceRecordExtractor(
        resolved_secrets={"vault://return-platform/contact-lookup#hmac_key": secret}
    )
    mutations = extractor.extract(
        schema=schema,
        source_asset_id="customer_outbound",
        page=_page(document),
        read_scope=ProjectionReadScope.COMPLETE_SOURCE_DOCUMENT,
    )
    customer_mutations = [m for m in mutations if m.entity_id == "customer"]
    account_mutations = [m for m in mutations if m.entity_id == "customer_account"]
    assert len(customer_mutations) == 1
    assert customer_mutations[0].record is not None
    assert customer_mutations[0].record.values["customer_key"] == "900781"
    assert customer_mutations[0].record.values["phone_hash"]
    assert "email_hash" not in customer_mutations[0].record.values

    assert len(account_mutations) == 2
    account_keys = {m.resolved_key["account_number"] for m in account_mutations}
    assert account_keys == {"232385", "28634"}
    assert all(
        m.record is not None and m.record.values["customer_key"] == "900781"
        for m in account_mutations
    )


@pytest.mark.asyncio
async def test_sales_order_projection_produces_a_node_with_customer_id_property(
    schema: ActiveSchema,
) -> None:
    document = {
        "salesHdrEventData": {
            "orderId": "WE130468",
            "orderStatus": "SHIPPED",
            "sellWhseId": "12",
            "shipFromWhseId": "12",
            "srcSysCode": "ESO",
        },
        "salesHdr": {
            "salesHdrData": {"custId": "900781", "custName": "Acme Plumbing"},
            "shipping": {"shipViaCode": "GROUND"},
        },
        "updatedAt": "2026-08-01T00:00:00Z",
    }
    extractor = GenericSourceRecordExtractor()
    mutations = extractor.extract(
        schema=schema,
        source_asset_id="sales_inventory",
        page=_page(document),
        read_scope=ProjectionReadScope.COMPLETE_SOURCE_DOCUMENT,
    )
    order_mutations = [m for m in mutations if m.entity_id == "sales_order"]
    assert len(order_mutations) == 1
    batch = await GenericGraphProjector().project(schema=schema, mutations=tuple(order_mutations))
    assert len(batch.node_mutations) == 1
    node = batch.node_mutations[0]
    assert node.key_values == {"order_id": "WE130468"}
    assert node.properties["customer_id"] == "900781"
