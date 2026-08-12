"""An approved draft becomes a release the runtime can actually load.

The baseline is the real shipped schema, not a fixture. What is under test is
whether the analyzer's authoring form survives translation into the form sync
and the agent read, and a hand-built baseline would let the compiler agree with
a schema nobody runs.

The negative tests carry the weight. A compiler that silently produced *a*
release from an ambiguous draft would be worse than one that refused: the
release would be approved by name, deployed, and then quietly match nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from return_platform.bootstrap.adapters.analyzer_release_compiler import (
    ReleaseCompilationError,
    compile_active_schema,
    release_checksum,
)
from return_platform.configuration.settings import DEFAULT_DYNAMIC_KNOWLEDGE_SCHEMA_PATH
from return_platform.dynamic_knowledge.config_loader import load_active_schema
from return_platform.dynamic_knowledge.schema import ActiveSchema, RelationshipCardinality
from return_platform.graph_schema_analyzer.domain.schema_draft import GraphSchemaShape

APPROVED_AT = datetime(2026, 8, 12, tzinfo=UTC)


@pytest.fixture(scope="module")
def baseline() -> ActiveSchema:
    return load_active_schema(DEFAULT_DYNAMIC_KNOWLEDGE_SCHEMA_PATH)


def _dataset_of(baseline: ActiveSchema, entity_id: str) -> str:
    """A source name the baseline can resolve, taken from the baseline itself."""
    return baseline.entities[entity_id].source_asset_id


def _entity(
    dataset: str,
    properties: dict[str, str],
    identifiers: list[str],
) -> dict[str, Any]:
    return {
        "source_dataset": dataset,
        "properties": {
            name: {"type": "STRING", "source_field": source, "transformation": "NONE"}
            for name, source in properties.items()
        },
        "identifier_properties": identifiers,
        "ownership": "SOURCE_OWNED",
        "sync_mode": "ON_DEMAND",
    }


def _shape(baseline: ActiveSchema) -> GraphSchemaShape:
    """Two entities and the edge between them: the smallest real schema."""
    orders = _dataset_of(baseline, next(iter(baseline.entities)))
    # The baseline's source syncs incrementally, and a release whose entity
    # cannot supply the cursor is refused -- so a realistic draft maps it.
    cursor = baseline.sources[orders].incremental_cursor_field
    extra = {} if cursor is None else {cursor: cursor}
    return GraphSchemaShape(
        entities={
            "Order": _entity(
                orders, {"orderId": "orderId", "status": "status", **extra}, ["orderId"]
            ),
            "OrderLine": _entity(
                orders, {"lineId": "lines.lineId", "orderId": "orderId", **extra}, ["lineId"]
            ),
        },
        relationships=(
            {
                "relationship_type": "HAS_LINE",
                "from_label": "Order",
                "to_label": "OrderLine",
                "cardinality": "ONE_TO_MANY",
                "from_properties": ["orderId"],
                "to_properties": ["orderId"],
            },
        ),
    )


def _compile(shape: GraphSchemaShape, baseline: ActiveSchema) -> ActiveSchema:
    # Dumped, not passed: the compiler lives on the far side of the analyzer's
    # port boundary and speaks plain data, so the tests cross it the same way.
    return compile_active_schema(
        shape.model_dump(mode="json"),
        baseline=baseline,
        configuration_release_id="release_from_draft_1",
        schema_version="v2",
        approved_by="analyst-1",
        approved_at=APPROVED_AT,
    )


def test_an_approved_shape_compiles_to_a_loadable_release(baseline: ActiveSchema) -> None:
    """`ActiveSchema` validates its own cross-references on construction.

    So this is not only "it produced an object": entities must reference known
    sources, nodes known entities, and relationships known fields of projected
    entities. A compiler that got any of those wrong cannot reach this line.
    """
    release = _compile(_shape(baseline), baseline)

    assert set(release.entities) == {"Order", "OrderLine"}
    assert set(release.graph.nodes) == {"Order", "OrderLine"}
    assert release.approved_by == "analyst-1"
    assert release.release_status == "ACTIVE"


def test_a_dotted_source_field_becomes_a_path_into_the_record(baseline: ActiveSchema) -> None:
    release = _compile(_shape(baseline), baseline)

    assert release.entities["OrderLine"].fields["lineId"].physical_path == ("lines", "lineId")
    assert release.entities["Order"].fields["orderId"].physical_path == ("orderId",)


def test_identifiers_become_the_key_and_are_not_projected_twice(baseline: ActiveSchema) -> None:
    release = _compile(_shape(baseline), baseline)

    node = release.graph.nodes["Order"]
    assert node.key_fields == ("orderId",)
    # The writer merges on keys and sets properties; a field in both would be
    # written twice per node, once as identity and once as data.
    assert "orderId" not in node.property_fields
    assert "status" in node.property_fields


def test_a_stated_join_is_used_verbatim(baseline: ActiveSchema) -> None:
    release = _compile(_shape(baseline), baseline)

    relationship = release.graph.relationships["Order_HAS_LINE_OrderLine"]
    assert relationship.source_match_fields == ("orderId",)
    assert relationship.target_match_fields == ("orderId",)
    assert relationship.cardinality is RelationshipCardinality.ONE_TO_MANY
    # A declared cardinality that is not enforced is a comment. ONE_TO_MANY
    # means one source per target, and the projector fails loudly on more.
    assert relationship.maximum_sources_per_target == 1
    assert relationship.maximum_targets_per_source is None


def test_an_unstated_join_falls_back_to_the_other_entitys_identifier(
    baseline: ActiveSchema,
) -> None:
    """The ordinary foreign-key shape, resolved rather than assumed."""
    shape = _shape(baseline)
    relationship = dict(shape.relationships[0])
    del relationship["from_properties"]
    del relationship["to_properties"]
    compiled = _compile(shape.model_copy(update={"relationships": (relationship,)}), baseline)

    projection = compiled.graph.relationships["Order_HAS_LINE_OrderLine"]
    # Order's own key, because OrderLine carries it -- the child-holds-the-
    # parent's-id shape. The other direction is tried too, and neither being
    # available is a refusal rather than a guess.
    assert projection.source_match_fields == ("orderId",)
    assert projection.target_match_fields == ("orderId",)


def test_a_join_the_entity_cannot_make_is_refused(baseline: ActiveSchema) -> None:
    """The failure mode a fallback must never hide.

    A projection matching on a field the entity does not have produces zero
    edges at runtime and reads as missing data, weeks later, to someone who
    was not in the approval.
    """
    shape = _shape(baseline)
    relationship = dict(shape.relationships[0])
    relationship["from_properties"] = ["customerId"]
    with pytest.raises(ReleaseCompilationError, match="customerId"):
        _compile(shape.model_copy(update={"relationships": (relationship,)}), baseline)


def test_a_join_stated_on_one_side_only_is_refused(baseline: ActiveSchema) -> None:
    """Half a join is not a join.

    Falling back for the unstated side would silently pair a deliberate choice
    with a default, which is the one combination nobody intended.
    """
    shape = _shape(baseline)
    relationship = dict(shape.relationships[0])
    del relationship["to_properties"]
    with pytest.raises(ReleaseCompilationError, match="one side"):
        _compile(shape.model_copy(update={"relationships": (relationship,)}), baseline)


def test_two_entities_with_nothing_in_common_cannot_be_joined(baseline: ActiveSchema) -> None:
    orders = _dataset_of(baseline, next(iter(baseline.entities)))
    cursor = baseline.sources[orders].incremental_cursor_field
    extra = {} if cursor is None else {cursor: cursor}
    shape = GraphSchemaShape(
        entities={
            "Order": _entity(orders, {"orderId": "orderId", **extra}, ["orderId"]),
            "Bay": _entity(orders, {"bayId": "bayId", **extra}, ["bayId"]),
        },
        relationships=(
            {
                "relationship_type": "STAGED_AT",
                "from_label": "Order",
                "to_label": "Bay",
                "cardinality": "MANY_TO_ONE",
            },
        ),
    )
    with pytest.raises(ReleaseCompilationError, match="nothing to match on"):
        _compile(shape, baseline)


def test_an_entity_with_no_identifier_is_refused(baseline: ActiveSchema) -> None:
    """Sync would match nothing and insert the dataset again on every run."""
    shape = _shape(baseline)
    entities = dict(shape.entities)
    entities["Order"] = {**entities["Order"], "identifier_properties": []}
    with pytest.raises(ReleaseCompilationError, match="identifier"):
        _compile(shape.model_copy(update={"entities": entities, "relationships": ()}), baseline)


def test_a_source_the_platform_cannot_reach_is_refused(baseline: ActiveSchema) -> None:
    """A draft can name a dataset; only configuration says how to connect."""
    shape = _shape(baseline)
    entities = {"Order": _entity("nowhere.at.all", {"orderId": "orderId"}, ["orderId"])}
    with pytest.raises(ReleaseCompilationError, match="nowhere"):
        _compile(shape.model_copy(update={"entities": entities, "relationships": ()}), baseline)


def test_an_unmapped_property_is_refused(baseline: ActiveSchema) -> None:
    shape = _shape(baseline)
    entities = dict(shape.entities)
    order = dict(entities["Order"])
    order["properties"] = {**order["properties"], "status": {"type": "STRING", "source_field": ""}}
    entities["Order"] = order
    with pytest.raises(ReleaseCompilationError, match=r"Order\.status"):
        _compile(shape.model_copy(update={"entities": entities, "relationships": ()}), baseline)


def test_an_empty_shape_is_refused(baseline: ActiveSchema) -> None:
    with pytest.raises(ReleaseCompilationError):
        _compile(GraphSchemaShape(), baseline)


def test_policies_narrow_to_the_entities_the_release_has(baseline: ActiveSchema) -> None:
    """A draft that dropped an entity must not fail on a policy nobody edited.

    Narrowing only ever removes reach. Carrying the baseline's allow-list
    through unchanged would name entities the release does not define, which
    `ActiveSchema` rejects -- so every compilation of a reduced schema would
    fail for a reason that has nothing to do with the schema.
    """
    release = _compile(_shape(baseline), baseline)

    for policy in release.agent_policies.values():
        assert set(policy.allowed_entity_ids) <= set(release.entities)
    assert set(release.agent_policies) == set(baseline.agent_policies)


def test_the_checksum_covers_the_release_and_is_stable(baseline: ActiveSchema) -> None:
    first = _compile(_shape(baseline), baseline)
    again = _compile(_shape(baseline), baseline)

    assert first.configuration_checksum == again.configuration_checksum
    assert release_checksum(first) == first.configuration_checksum
    # And it is a digest of the content, not of the identity: change what the
    # release *says* and the checksum has to move, or it cannot tell an
    # approved release from an edited one.
    edited = first.model_copy(update={"schema_version": "v3"})
    assert release_checksum(edited) != first.configuration_checksum
