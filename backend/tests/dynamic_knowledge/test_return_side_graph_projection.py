"""The return side of the graph: cases, RMAs, items, and where a parcel is staged.

The graph projected only the order side. A case, the RMAs Support issued against
it, the items each RMA covers and the bay a parcel was staged into existed only
in MongoDB, so the Order Discovery agent could find an order and then had nothing
to say about whether it had ever been returned.

These exercise the shipped path with nothing substituted: the production
descriptor (`config/dynamic_knowledge/active-schema.return-order.yaml`), the real
`GenericSourceRecordExtractor`, the real `GenericGraphProjector` and the real
Stage B Cypher compiler. The documents are shaped exactly as
`OperationalRepository` writes them -- `create_case`, `create_return_record`,
`create_case_return_item`, `persist_return_intake_records` and
`WarehousePlacementService.assign` are the five writers whose output has to
project, and a fixture that invented a tidier shape would prove nothing.

Cross-source edges are asserted through `compile_relationship_reconciliation`
rather than through the projector. The projector only sees one batch, and a case
and its RMA arrive from two different collections, so Stage A cannot join them by
construction -- Stage B is where those edges are made, and asserting them on the
projector would be asserting the wrong thing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from neo4j import AsyncGraphDatabase

from return_platform.dynamic_knowledge.config_loader import load_active_schema
from return_platform.dynamic_knowledge.graph.projector import GenericGraphProjector
from return_platform.dynamic_knowledge.graph.write_compiler import (
    compile_relationship_reconciliation,
)
from return_platform.dynamic_knowledge.integration.neo4j_gateway import Neo4jKnowledgeGateway
from return_platform.dynamic_knowledge.on_demand_sync.contracts import (
    GraphMutationBatch,
    ProjectionReadScope,
    RawSourceDocument,
    RawSourcePage,
)
from return_platform.dynamic_knowledge.on_demand_sync.extraction import (
    GenericSourceRecordExtractor,
)
from return_platform.dynamic_knowledge.schema import ActiveSchema, EntitySourceAccess

pytestmark = pytest.mark.asyncio

SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "config"
    / "dynamic_knowledge"
    / "active-schema.return-order.yaml"
)
AGENT_ID = "order-discovery-agent"
NOW = datetime(2026, 8, 12, 9, 0, tzinfo=UTC)

CASE_ID = "case-7f3a"
RECORD_ID = "rec-1"
SESSION_ID = "sess-9"

RETURN_ENTITY_IDS = frozenset(
    {"return_case", "return_record", "return_item", "return_handling_unit"}
)


@pytest.fixture(scope="module")
def schema() -> ActiveSchema:
    return load_active_schema(SCHEMA_PATH)


# ---------------------------------------------------------------------------
# Documents, exactly as OperationalRepository writes them
# ---------------------------------------------------------------------------


def _case_document() -> dict[str, Any]:
    """`OperationalRepository.create_case`, after Support opened a work item."""
    return {
        "_id": "objectid-stand-in",
        "caseId": CASE_ID,
        "tenantId": "tenant-a",
        "principalId": "associate-1",
        "branchId": "CHARLOTTE",
        "status": "AWAITING_SUPPORT",
        "channelAConversationId": "conv-1",
        "channelBWorkItemId": "wi-1",
        "confirmedOrderReference": "CW273354",
        "confirmationKey": "tenant-a|conv-1|CW273354|L1,L2",
        "sessionId": SESSION_ID,
        "workflowId": "return-case-7f3a",
        "configurationReleaseId": "order-discovery-release-414faac",
        "graphGenerationId": "gen-1",
        "version": 2,
        "createdAt": NOW,
        "updatedAt": NOW,
    }


def _return_record_document() -> dict[str, Any]:
    """`create_return_record` then `update_return_record`, as
    `ReturnCaseActivities.record_support_outcome` writes it."""
    return {
        "returnRecordId": RECORD_ID,
        "caseId": CASE_ID,
        "returnReference": "RMA-1001",
        "status": "ISSUED",
        "returnLocation": "DC-7",
        "trackingReference": "1Z999AA10123456784",
        "labelReference": "LBL-1",
        "shippingInstructionReference": None,
        "sourceSystem": "RETURN_SUPPORT",
        "version": 1,
        "createdAt": NOW,
        "updatedAt": NOW,
    }


def _case_item_document(*, item_id: str, line: str, record_id: str | None) -> dict[str, Any]:
    """`create_case_return_item`. `returnRecordId` is null until Support says
    which RMA covers the line."""
    return {
        "returnItemId": item_id,
        "caseId": CASE_ID,
        "returnRecordId": record_id,
        "orderLineId": line,
        "productReference": "3180140",
        "quantity": 2,
        "reason": "Damaged on arrival",
        "condition": "DAMAGED",
        "packageReference": None,
        "version": 0,
        "createdAt": NOW,
        "updatedAt": NOW,
    }


def _session_item_document() -> dict[str, Any]:
    """`persist_return_intake_records`. The older shape in the same collection:
    a string uuid `_id`, a session instead of a case, and its own quantity and
    reason columns."""
    return {
        "_id": "0f2e5b1c-2b2c-4a4e-9a63-6f6a1c1f2d3e",
        "returnItemId": f"{SESSION_ID}:L9",
        "sessionId": SESSION_ID,
        "orderLineId": "L9",
        "productId": "3180999",
        "requestedQuantity": 1,
        "approvedQuantity": None,
        "reasonCode": "WRONG_ITEM",
        "attachmentIds": [],
        "disposition": "PENDING",
        "version": 0,
        "createdBy": "associate-1",
        "createdAt": NOW,
        "updatedAt": NOW,
    }


def _handling_unit_document() -> dict[str, Any]:
    """`persist_return_intake_records` then `WarehousePlacementService.assign`,
    which is what puts `bayId`/`warehouseId` on the document at all."""
    return {
        "_id": "1a2b3c4d-5e6f-4071-8293-a4b5c6d7e8f9",
        "handlingUnitId": f"{SESSION_ID}:HU:1",
        "sessionId": SESSION_ID,
        "sequence": 1,
        "handlingUnitType": "PACKAGE",
        "returnItemAllocations": [{"returnItemId": f"{SESSION_ID}:L9", "quantity": 1}],
        "physicalStatus": "WAREHOUSE_STAGED",
        "shippingInstructionId": None,
        "trackingNumber": "1Z999AA10123456784",
        "bolReference": None,
        "licensePlateIds": [],
        "bayId": "BAY-04",
        "warehouseId": "1969",
        "reservationId": "res-1",
        "assignmentId": "asn-1",
        "version": 1,
        "createdBy": "associate-1",
        "createdAt": NOW,
        "updatedAt": NOW,
    }


def _page(*documents: dict[str, Any]) -> RawSourcePage:
    return RawSourcePage(
        documents=tuple(
            RawSourceDocument(
                operation="UPSERT",
                document=document,
                source_identity=str(document.get("_id", document.get("caseId", "x"))),
            )
            for document in documents
        ),
        next_cursor=None,
        high_watermark=None,
        observed_at=NOW,
    )


async def _project(
    schema: ActiveSchema, source_asset_id: str, *documents: dict[str, Any]
) -> GraphMutationBatch:
    mutations = GenericSourceRecordExtractor().extract(
        schema=schema,
        source_asset_id=source_asset_id,
        page=_page(*documents),
        read_scope=ProjectionReadScope.COMPLETE_SOURCE_DOCUMENT,
    )
    return await GenericGraphProjector().project(schema=schema, mutations=mutations)


def _node(batch: GraphMutationBatch, label_entity_id: str) -> Any:
    matching = [m for m in batch.node_mutations if m.entity_id == label_entity_id]
    assert len(matching) == 1, f"expected one {label_entity_id} node, got {len(matching)}"
    return matching[0]


# ---------------------------------------------------------------------------
# The schema itself
# ---------------------------------------------------------------------------


def test_the_return_side_is_bound_to_the_platform_store_not_the_upstream_one(
    schema: ActiveSchema,
) -> None:
    """The failure this catches is a silently empty projection.

    `MongoDBSourceScanConnector` reads from the database it was constructed
    with and ignores what the source declares, so a return-side source that
    named `return_source` would be scanned against the Ferguson collections,
    find no `cases` collection, and complete with zero documents and no error.
    `GraphSyncService` routes on exactly this value.
    """
    for entity_id in sorted(RETURN_ENTITY_IDS):
        source = schema.sources[schema.entities[entity_id].source_asset_id]
        assert source.object_ref["database"] == "return_platform", entity_id


def test_every_return_side_source_cursors_on_a_timestamp(schema: ActiveSchema) -> None:
    """Not on `_id`.

    Two of these collections are written with a string uuid `_id`
    (`persist_return_intake_records`), and the ObjectId cursoring a source
    without an `incremental_cursor_field` falls back to would compare a string
    against an ObjectId -- paging through them in BSON type order rather than in
    write order, which silently skips documents.
    """
    for entity_id in sorted(RETURN_ENTITY_IDS):
        entity = schema.entities[entity_id]
        source = schema.sources[entity.source_asset_id]
        assert source.incremental_cursor_field == "source_updated_at", entity_id
        assert entity.fields["source_updated_at"].physical_path == ("updatedAt",), entity_id


def test_the_agent_policy_names_every_return_side_entity(schema: ActiveSchema) -> None:
    """A projected entity the policy omits is invisible, and reads as a bug.

    `SchemaQueryGuard` refuses any plan whose start or traversal entity is not
    allow-listed, with `ORDER_AGENT_OUT_OF_SCOPE` -- which looks like a
    permissions problem rather than a missing line in the schema.
    """
    allowed = schema.agent_policies[AGENT_ID].allowed_entity_ids
    assert RETURN_ENTITY_IDS <= set(allowed)


def test_no_return_side_entity_offers_an_on_demand_sync_anchor(schema: ActiveSchema) -> None:
    """On-demand sync reaches the *upstream* store, not this one.

    `runtime_factory` binds the on-demand connector to `source_mongo_database`,
    so a strong anchor on a platform-store entity would compile a targeted read
    against a collection that is not there. The entities stay CONNECTED_SYNC --
    the batch sync does reach them -- and simply declare no anchors.
    """
    for entity_id in sorted(RETURN_ENTITY_IDS):
        entity = schema.entities[entity_id]
        assert entity.source_access is EntitySourceAccess.CONNECTED_SYNC, entity_id
        assert entity.strong_anchors == {}, entity_id
        assert not any(
            field.capabilities.on_demand_sync_anchor for field in entity.fields.values()
        ), entity_id


def test_no_return_side_field_claims_the_generation_property(schema: ActiveSchema) -> None:
    """`graph_generation_id` is the writer's physical key scope.

    Every node MERGE is `{graph_generation_id: $generationId, ...keys}` and then
    `SET n += row.properties`. A field mapped to that graph property would
    overwrite the scope with a source value and detach the node from its own
    generation.
    """
    for entity_id in sorted(RETURN_ENTITY_IDS):
        for field in schema.entities[entity_id].fields.values():
            assert field.graph_property != "graph_generation_id", entity_id


async def test_the_compact_schema_describes_the_return_side_to_the_model(
    schema: ActiveSchema,
) -> None:
    """Reachability is two things, and the allow-list is only one of them.

    The model plans from `compact_schema`. An entity it does not describe is one
    the model has no way to name, however open the policy is.

    The real gateway, holding a real driver that is never connected: this method
    reads only the schema, and substituting a stand-in would test the stand-in's
    idea of what the model is shown.
    """
    driver = AsyncGraphDatabase.driver("bolt://localhost:7687", auth=("neo4j", "unused"))
    try:
        compact = await Neo4jKnowledgeGateway(driver, database="neo4j").compact_schema(
            schema, AGENT_ID
        )
    finally:
        await driver.close()

    assert RETURN_ENTITY_IDS <= set(compact["entities"])
    assert compact["entities"]["return_record"]["description"], "an entity with no description"
    # The traversals the return questions are made of.
    assert {
        "case_covers_order",
        "case_raised_return_record",
        "case_includes_return_item",
        "return_record_covers_item",
        "case_staged_as",
    } <= set(compact["relationships"])


# ---------------------------------------------------------------------------
# Extraction and node projection
# ---------------------------------------------------------------------------


async def test_a_case_document_projects_one_returncase_node(schema: ActiveSchema) -> None:
    batch = await _project(schema, "source_return_cases", _case_document())

    node = _node(batch, "return_case")
    assert schema.graph.nodes[node.projection_id].label == "ReturnCase"
    assert node.key_values == {"case_id": CASE_ID}
    assert node.properties["case_status"] == "AWAITING_SUPPORT"
    # The join to the order side. Without it the case is an island.
    assert node.properties["confirmed_order_reference"] == "CW273354"
    # The only link to where the parcels are.
    assert node.properties["session_id"] == SESSION_ID


async def test_an_rma_without_a_number_yet_still_projects(schema: ActiveSchema) -> None:
    """A record exists from the moment the case decides to raise it.

    `returnReference` is null in that window. Dropping the node until Support
    answers would make "we have asked, no number yet" unsayable -- which is
    precisely the state an associate is standing at the counter in.
    """
    draft = _return_record_document() | {"returnReference": None, "status": "DRAFT"}

    batch = await _project(schema, "source_return_records", draft)

    node = _node(batch, "return_record")
    assert node.key_values == {"return_record_id": RECORD_ID}
    assert node.properties["return_reference"] is None
    assert node.properties["return_record_status"] == "DRAFT"


async def test_both_item_shapes_in_one_collection_project(schema: ActiveSchema) -> None:
    """`operational_return_items` holds two shapes written by two flows.

    The case-scoped item carries `caseId`/`quantity`/`reason`; the older
    session-scoped intake item carries `sessionId`/`requestedQuantity`/
    `reasonCode`. They are mapped side by side rather than coalesced: a
    requested quantity and a declared quantity are not the same measurement, and
    folding them would assert an equivalence the data does not have.
    """
    batch = await _project(
        schema,
        "source_return_items",
        _case_item_document(item_id="item-1", line="L1", record_id=RECORD_ID),
        _session_item_document(),
    )

    items = {m.key_values["return_item_id"]: m for m in batch.node_mutations}
    assert set(items) == {"item-1", f"{SESSION_ID}:L9"}

    case_item = items["item-1"].properties
    assert case_item["case_id"] == CASE_ID
    assert case_item["return_record_id"] == RECORD_ID
    assert case_item["quantity"] == 2
    assert case_item["return_reason"] == "Damaged on arrival"

    session_item = items[f"{SESSION_ID}:L9"].properties
    assert session_item["session_id"] == SESSION_ID
    assert session_item["requested_quantity"] == 1
    assert session_item["reason_code"] == "WRONG_ITEM"
    # Absent, not blank: the case-scoped columns simply are not on this document.
    assert "case_id" not in session_item
    assert "quantity" not in session_item


async def test_a_staged_handling_unit_carries_its_warehouse_and_bay(
    schema: ActiveSchema,
) -> None:
    """The only answer to "where is this parcel now".

    `return_record.return_location` is where Support said to send it, which is
    not the same claim.
    """
    batch = await _project(schema, "source_handling_units", _handling_unit_document())

    node = _node(batch, "return_handling_unit")
    assert schema.graph.nodes[node.projection_id].label == "ReturnHandlingUnit"
    assert node.key_values == {"handling_unit_id": f"{SESSION_ID}:HU:1"}
    assert node.properties["warehouse_id"] == "1969"
    assert node.properties["bay_id"] == "BAY-04"
    assert node.properties["physical_status"] == "WAREHOUSE_STAGED"
    # Nothing reaches the graph that the projection did not declare. That is
    # what keeps `returnItemAllocations` and `licensePlateIds` out: Neo4j
    # properties are scalars, and a list of maps fails the write outright rather
    # than degrading.
    projection = schema.graph.nodes[node.projection_id]
    entity = schema.entities[projection.entity_id]
    declared = {entity.fields[field_id].graph_property for field_id in projection.property_fields}
    assert set(node.properties) <= declared


async def test_a_handling_unit_before_assignment_projects_without_a_bay(
    schema: ActiveSchema,
) -> None:
    """A planned parcel has no `bayId` key at all -- the assignment `$set` is
    what creates it. The node must still exist, or a return in flight is
    invisible until somebody stages it."""
    planned = {
        key: value
        for key, value in _handling_unit_document().items()
        if key not in {"bayId", "warehouseId", "reservationId", "assignmentId"}
    } | {"physicalStatus": "PLANNED"}

    batch = await _project(schema, "source_handling_units", planned)

    node = _node(batch, "return_handling_unit")
    assert node.properties["physical_status"] == "PLANNED"
    assert "bay_id" not in node.properties


# ---------------------------------------------------------------------------
# Stage B: the cross-source joins
# ---------------------------------------------------------------------------


def _reconciliation(schema: ActiveSchema, relationship_id: str) -> str:
    return compile_relationship_reconciliation(
        schema, relationship_id, graph_generation_id="gen-1"
    ).cypher


def test_a_case_joins_the_order_it_was_raised_against(schema: ActiveSchema) -> None:
    """`Customer -PLACED_ORDER-> SalesOrder <-COVERS_ORDER- ReturnCase` is the
    path that answers "has this customer returned this before"."""
    cypher = _reconciliation(schema, "case_covers_order")

    assert "MATCH (a:`ReturnCase`" in cypher
    assert "MATCH (b:`SalesOrder`" in cypher
    assert "a.`confirmed_order_reference` = b.`sales_order_number`" in cypher
    # A case that has confirmed nothing must not join every order.
    assert "a.`confirmed_order_reference` IS NOT NULL" in cypher
    assert "MERGE (a)-[rel:`COVERS_ORDER`]->(b)" in cypher


def test_a_case_joins_its_rmas_and_its_items_separately(schema: ActiveSchema) -> None:
    """Two edges, not one chain.

    An item exists on the case before any RMA covers it, so reaching items only
    through the record would drop exactly the ones an associate is still waiting
    on Support to place.
    """
    assert "a.`case_id` = b.`case_id`" in _reconciliation(schema, "case_raised_return_record")
    assert "a.`case_id` = b.`case_id`" in _reconciliation(schema, "case_includes_return_item")


def test_an_rma_joins_only_the_items_it_covers(schema: ActiveSchema) -> None:
    """Decision D6 in the graph: one RMA, N items, and an item on no RMA yet
    joins nothing rather than defaulting to the first one."""
    cypher = _reconciliation(schema, "return_record_covers_item")

    assert "a.`return_record_id` = b.`return_record_id`" in cypher
    assert "a.`return_record_id` IS NOT NULL" in cypher
    assert "MERGE (a)-[rel:`COVERS_RETURN_ITEM`]->(b)" in cypher


def test_a_case_joins_its_parcels_by_session(schema: ActiveSchema) -> None:
    """The only join there is: a handling unit records its session and never its
    case."""
    cypher = _reconciliation(schema, "case_staged_as")

    assert "a.`session_id` = b.`session_id`" in cypher
    assert "a.`session_id` IS NOT NULL" in cypher
    assert "MERGE (a)-[rel:`STAGED_AS`]->(b)" in cypher


def test_the_case_to_parcel_edge_is_declared_unbounded(schema: ActiveSchema) -> None:
    """`cases.sessionId` is indexed but not uniquely.

    Two cases sharing a session is a state the store permits, so a
    `maximum_sources_per_target` here would turn an oddity in the data into a
    `RelationshipCardinalityViolation` that rolls back the entire Stage B
    transaction -- taking the rest of the graph's relationships with it.
    """
    relationship = schema.graph.relationships["case_staged_as"]

    assert relationship.maximum_sources_per_target is None
    assert relationship.maximum_targets_per_source is None


def test_an_rma_and_an_item_belong_to_exactly_one_case(schema: ActiveSchema) -> None:
    """Bounded where the document shape really does guarantee it: a return
    record and an item each carry one `caseId`, so a second owning case would
    mean two case nodes sharing a key, which the node key forbids."""
    for relationship_id in ("case_raised_return_record", "case_includes_return_item"):
        assert schema.graph.relationships[relationship_id].maximum_sources_per_target == 1
