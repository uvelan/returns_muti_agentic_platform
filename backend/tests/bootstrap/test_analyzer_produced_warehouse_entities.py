"""W2.4: `warehouse` and `bay` came out of the analyzer, not out of an editor.

The step forbids hand-editing the descriptor, and provenance is not something a
comment can establish. What can be established is *reproduction*: given the
observation the analyzer's inspection port returned for
`platform.bay_configuration`, the analyzer path must produce exactly the two
entities the shipped descriptor holds -- same fields, same types, same
nullability, same capabilities, same anchors, same node labels, same edge.

The observation below is written out in full, and that is the one thing here a
person typed. It is not taken on trust:
`tests/dynamic_knowledge/test_warehouse_bay_source_contract_real_infra.py`
re-reads the live SQL Server and asserts the same catalogue, so the pair says
"this is what the source declares" and "this is what the descriptor derives from
it" without either half assuming the other.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from return_platform.bootstrap.adapters.analyzer_release_compiler import ReleaseCompilationError
from return_platform.bootstrap.adapters.analyzer_schema_addition import compile_addition
from return_platform.bootstrap.adapters.analyzer_source_observation import (
    SourceObservation,
    observed_capabilities,
)
from return_platform.configuration.settings import DEFAULT_DYNAMIC_KNOWLEDGE_SCHEMA_PATH
from return_platform.dynamic_knowledge.config_loader import load_active_schema
from return_platform.dynamic_knowledge.integration.neo4j_gateway import Neo4jKnowledgeGateway
from return_platform.dynamic_knowledge.schema import (
    ActiveSchema,
    EntitySourceAccess,
    FieldType,
    SourceContractStatus,
)
from return_platform.graph_schema_analyzer.domain.mutation import (
    AddEntity,
    AddProperty,
    ChangeIdentifier,
    MutationCommand,
    PropertyType,
)
from return_platform.graph_schema_analyzer.ports.source_port import (
    FieldDescription,
    IndexDescription,
    ObjectDescription,
    ObjectKind,
)

# Importing the script by path rather than duplicating its command batch: a copy
# would let the descriptor and the thing that produced it drift, which is the
# whole failure this module exists to detect.
pytest.register_assert_rewrite("add_warehouse_bay_entities")


@pytest.fixture(scope="module")
def script() -> object:
    import importlib.util
    import sys
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "scripts" / "add_warehouse_bay_entities.py"
    spec = importlib.util.spec_from_file_location("add_warehouse_bay_entities", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def descriptor() -> ActiveSchema:
    return load_active_schema(DEFAULT_DYNAMIC_KNOWLEDGE_SCHEMA_PATH)


#: `platform.bay_configuration` exactly as `SqlServerSourceInspectionAdapter`
#: reported it. Column order is `sys.columns.column_id` order and is preserved.
BAY_COLUMNS: tuple[tuple[str, str, bool], ...] = (
    ("bay_id", "varchar", False),
    ("bay_name", "nvarchar", False),
    ("warehouse_id", "varchar", False),
    ("branch_id", "varchar", False),
    ("bay_type", "varchar", False),
    ("active", "bit", False),
    ("priority", "int", False),
    ("supported_shipping_paths", "nvarchar", False),
    ("supported_product_types", "nvarchar", False),
    ("max_package_count", "int", False),
    ("overflow_bay_id", "varchar", True),
    ("hazardous_allowed", "bit", False),
    ("oversized_allowed", "bit", False),
    ("max_handling_unit_count", "int", True),
    ("max_pallet_count", "int", True),
    ("capacity_unit", "varchar", False),
    ("row_version_v2", "bigint", False),
    ("updated_at", "datetime2", False),
)

#: The primary key's *name* is server-generated (`PK__bay_conf__5327...`) and is
#: deliberately not part of this fixture: an index rebuilt on another instance
#: carries a different one, and a test asserting on it would fail for a reason
#: that has nothing to do with the schema.
BAY_INDEXES: tuple[tuple[tuple[str, ...], bool, bool], ...] = (
    (("bay_id",), True, True),
    (("warehouse_id", "active", "priority"), False, False),
)


def observation(source_id: str = "source_bays") -> SourceObservation:
    return SourceObservation(
        description=ObjectDescription(
            source_id=source_id,
            object_name="platform.bay_configuration",
            object_kind=ObjectKind.TABLE,
            fields=tuple(
                FieldDescription(field_name=name, declared_type=declared, nullable=nullable)
                for name, declared, nullable in BAY_COLUMNS
            ),
            approximate_row_count=6,
        ),
        indexes=tuple(
            IndexDescription(
                index_name=f"ix_{position}", fields=fields, unique=unique, primary=primary
            )
            for position, (fields, unique, primary) in enumerate(BAY_INDEXES)
        ),
    )


# ---------------------------------------------------------------------------
# The descriptor is what the analyzer produces
# ---------------------------------------------------------------------------


def test_the_shipped_entities_are_reproduced_by_the_analyzer_path(
    descriptor: ActiveSchema, script: object
) -> None:
    """Rerun the whole path against a baseline without them, and compare.

    This is the assertion the step's "not by hand-editing the descriptor" clause
    reduces to. A field added by hand -- or a capability quietly widened to make
    a query work -- would survive in the descriptor and not be reproduced here.
    """
    without = descriptor.model_dump(mode="json")
    for entity_id in ("warehouse", "bay"):
        del without["entities"][entity_id]
        del without["graph"]["nodes"][entity_id]
        for policy in without["agent_policies"].values():
            policy["allowed_entity_ids"] = [
                name for name in policy["allowed_entity_ids"] if name != entity_id
            ]
    del without["graph"]["relationships"]["warehouse_HAS_BAY_bay"]
    del without["sources"]["source_bays"]
    baseline = ActiveSchema.model_validate(without)

    rebuilt = script.build(baseline, observation())  # type: ignore[attr-defined]

    for entity_id in ("warehouse", "bay"):
        assert rebuilt.entities[entity_id] == descriptor.entities[entity_id]
        assert rebuilt.graph.nodes[entity_id] == descriptor.graph.nodes[entity_id]
    assert (
        rebuilt.graph.relationships["warehouse_HAS_BAY_bay"]
        == descriptor.graph.relationships["warehouse_HAS_BAY_bay"]
    )
    assert rebuilt.sources["source_bays"] == descriptor.sources["source_bays"]


def test_every_declared_path_is_a_column_the_source_reported(descriptor: ActiveSchema) -> None:
    """No path that resolves on nothing.

    W2.6's verification found three declared paths that existed on none of 100
    documents. A relational source makes the same check total rather than
    sampled: the catalogue is the contract, so every path either is a declared
    column or is not.
    """
    columns = {name for name, _, _ in BAY_COLUMNS}

    for entity_id in ("warehouse", "bay"):
        for field_id, field in descriptor.entities[entity_id].fields.items():
            assert field.physical_path is not None, f"{entity_id}.{field_id} has no path"
            assert len(field.physical_path) == 1, (
                f"{entity_id}.{field_id} declares a nested path into a relational row"
            )
            assert field.physical_path[0] in columns, (
                f"{entity_id}.{field_id} reads {field.physical_path[0]!r}, "
                "which the source does not declare"
            )


def test_bay_carries_every_column_and_warehouse_carries_only_what_it_can(
    descriptor: ActiveSchema,
) -> None:
    """`warehouse` is a dimension projected out of the bay table.

    So it holds the two warehouse-level columns the source states and the change
    timestamp it must map to sync -- and nothing else. A name, an address or a
    capacity would have to be invented, and `docs/SEED_DATA_GENERATION.md`
    records that the invented `warehouseMaster` shape is exactly that.
    """
    assert set(descriptor.entities["bay"].fields) == {name for name, _, _ in BAY_COLUMNS}
    assert set(descriptor.entities["warehouse"].fields) == {
        "warehouse_id",
        "branch_id",
        "updated_at",
    }


def test_nullability_comes_from_the_catalogue_rather_than_the_default(
    descriptor: ActiveSchema,
) -> None:
    """`FieldDefinition.nullable` defaults to True, which is wrong for 15 of 18.

    A guaranteed value declared optional makes every consumer branch on an
    absence that cannot happen, and the three genuinely nullable columns then
    look no different from the rest.
    """
    fields = descriptor.entities["bay"].fields
    nullable = {name for name, _, is_nullable in BAY_COLUMNS if is_nullable}

    assert {name for name, field in fields.items() if field.nullable} == nullable
    assert nullable == {"overflow_bay_id", "max_handling_unit_count", "max_pallet_count"}


def test_the_bay_anchor_comes_from_the_primary_key(descriptor: ActiveSchema) -> None:
    anchor = descriptor.entities["bay"].strong_anchors["exact_bay_id"]

    assert [field.field_id for field in anchor.fields] == ["bay_id"]
    assert anchor.maximum_expected_matches == 1
    assert anchor.on_demand_sync_allowed
    assert descriptor.entities["bay"].natural_key == ("bay_id",)


def test_a_non_unique_index_defines_no_anchor(descriptor: ActiveSchema) -> None:
    """`warehouse_id` on `bay` is anchorable and does not define an anchor.

    The lookup index bounds a read on it to *some* number of bays, and
    `maximum_expected_matches` would have to state which. No observation supplies
    that number, so declaring one would be inventing it -- and the field is still
    usable inside an anchor that is bounded some other way.
    """
    bay = descriptor.entities["bay"]

    assert bay.fields["warehouse_id"].capabilities.on_demand_sync_anchor
    assert set(bay.strong_anchors) == {"exact_bay_id"}


def test_warehouse_is_anchored_because_the_node_key_bounds_it(descriptor: ActiveSchema) -> None:
    """The bay table has no unique index on `warehouse_id`; the graph does.

    One node per distinct id is what the natural key means, so an exact anchor on
    it matches one *node* however many source rows carried the value. Without
    this rule a dimension projected out of a fact table could never be synced on
    demand, which is the whole of W2.7.
    """
    anchor = descriptor.entities["warehouse"].strong_anchors["exact_warehouse_id"]

    assert [field.field_id for field in anchor.fields] == ["warehouse_id"]
    assert anchor.maximum_expected_matches == 1
    assert not any(
        set(index.fields) == {"warehouse_id"} and index.unique for index in observation().indexes
    )


# ---------------------------------------------------------------------------
# The derivation rules, in isolation
# ---------------------------------------------------------------------------


def test_a_column_no_index_covers_is_displayable_and_nothing_more() -> None:
    """`bay_name` is real, and nothing declares it cheap to look up by.

    Granting it `searchable` would put an unindexed scan behind an agent query.
    """
    capabilities = observed_capabilities(
        observation(), column="bay_name", data_type=FieldType.STRING
    )

    assert capabilities.displayable
    assert not capabilities.searchable
    assert not capabilities.filterable
    assert capabilities.operators == frozenset()


def test_a_later_column_of_a_composite_index_is_filterable_and_not_searchable() -> None:
    """`active` sits second in `(warehouse_id, active, priority)`.

    It is only cheap once `warehouse_id` is constrained, and nothing in a
    capability can promise that, so it may narrow a result and may not start one.
    """
    capabilities = observed_capabilities(
        observation(), column="active", data_type=FieldType.BOOLEAN
    )

    assert capabilities.filterable
    assert not capabilities.searchable
    assert not capabilities.on_demand_sync_anchor


def test_an_unindexed_timestamp_gets_range_operators_and_not_equality() -> None:
    capabilities = observed_capabilities(
        observation(), column="updated_at", data_type=FieldType.DATETIME
    )

    assert capabilities.operators == frozenset({"GT", "GTE", "LT", "LTE", "BETWEEN"})
    assert capabilities.filterable


def test_permissions_never_grant_what_the_capability_denies(descriptor: ActiveSchema) -> None:
    """A field searchable by nobody is a capability the guard refuses every time.

    Which reads as a broken query rather than as a permission decision, so the
    two are derived from one another rather than written twice.
    """
    for entity_id in ("warehouse", "bay"):
        for field_id, field in descriptor.entities[entity_id].fields.items():
            where = f"{entity_id}.{field_id}"
            assert bool(field.permissions.searchable_by) == field.capabilities.searchable, where
            assert bool(field.permissions.displayable_by) == field.capabilities.displayable, where
            assert (
                bool(field.permissions.on_demand_sync_by)
                == field.capabilities.on_demand_sync_anchor
            ), where


# ---------------------------------------------------------------------------
# The compiler changes the addition depends on
# ---------------------------------------------------------------------------


def _probe(label: str) -> tuple[MutationCommand, ...]:
    """The smallest entity `compile_addition` accepts, under a given label.

    Two properties because the source syncs incrementally on `updated_at` and the
    compiler refuses an entity that does not map its cursor; an identifier
    because it refuses one that could not match a node on a second run.
    """
    return (
        AddEntity(label=label, source_dataset="platform.bay_configuration"),
        AddProperty(
            label=label,
            property_name="bay_id",
            property_type=PropertyType.STRING,
            source_field="platform.bay_configuration.bay_id",
        ),
        AddProperty(
            label=label,
            property_name="updated_at",
            property_type=PropertyType.DATETIME,
            source_field="platform.bay_configuration.updated_at",
        ),
        ChangeIdentifier(label=label, identifier_properties=("bay_id",)),
    )


def _compile_probe(descriptor: ActiveSchema, label: str, *, observe: bool = True) -> ActiveSchema:
    return compile_addition(
        baseline=descriptor,
        commands=_probe(label),
        observations={"platform.bay_configuration": observation()} if observe else None,
        configuration_release_id="probe",
        schema_version="probe",
        approved_by="tester",
        approved_at=datetime(2026, 8, 13, tzinfo=UTC),
    )


def test_a_dataset_qualified_source_field_becomes_a_bare_column(
    descriptor: ActiveSchema,
) -> None:
    """`ReanalysisService` writes `<dataset>.<column>`; a path is not a name.

    Compiled literally the path became `('platform', 'bay_configuration',
    'bay_id')`, which resolves on no row and projects null forever -- and every
    property a re-analysis has ever proposed carried that prefix.
    """
    release = _compile_probe(descriptor, "probe_entity")

    assert release.entities["probe_entity"].fields["bay_id"].physical_path == ("bay_id",)


def test_an_addition_that_would_replace_an_existing_entity_is_refused(
    descriptor: ActiveSchema,
) -> None:
    """Replacing one is destructive in D8's classification and needs a rebuild.

    Allowed here it would happen silently, under the word "addition".
    """
    with pytest.raises(ReleaseCompilationError, match="redefines existing entity"):
        _compile_probe(descriptor, "shipment")


def test_an_entity_compiled_with_no_observation_does_not_claim_to_be_verified(
    descriptor: ActiveSchema,
) -> None:
    """`SourceContractStatus` defaults to VERIFIED on the model.

    So the compiler used to assert, of every entity it produced, that its paths
    had been checked against a source -- having checked nothing.
    """
    release = _compile_probe(descriptor, "unchecked_entity", observe=False)

    assert (
        release.entities["unchecked_entity"].source_contract_status
        is SourceContractStatus.UNVERIFIED
    )
    assert descriptor.entities["bay"].source_contract_status is SourceContractStatus.VERIFIED


def test_the_graph_label_is_pascal_cased_and_the_entity_id_is_not(
    descriptor: ActiveSchema,
) -> None:
    """The runtime keeps entity id and node label apart; the draft has one name.

    Without the derivation a snake_case draft label produced a lowercase Neo4j
    label beside `SalesOrder` and `Shipment`.
    """
    assert descriptor.graph.nodes["bay"].label == "Bay"
    assert descriptor.graph.nodes["warehouse"].label == "Warehouse"
    assert descriptor.entities["bay"].entity_id == "bay"


# ---------------------------------------------------------------------------
# The step's Validation clause
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_both_entities_appear_in_compact_schema_for_the_permitted_agent(
    descriptor: ActiveSchema,
) -> None:
    """W2.4's Validation, on the shipped descriptor.

    `compact_schema` is built from the agent policy's entity list, so an entity
    projected into the graph and left out of the policy reads to the model as
    "this platform cannot answer that".
    """
    compact = await Neo4jKnowledgeGateway.compact_schema(
        object.__new__(Neo4jKnowledgeGateway),
        descriptor,
        "order-discovery-agent",
        principal_roles=frozenset({"associate"}),
    )

    assert "warehouse" in compact["entities"]
    assert "bay" in compact["entities"]
    assert compact["entities"]["bay"]["description"]
    assert compact["entities"]["bay"]["fields"]["bay_id"]["searchable"]
    assert "exact_warehouse_id" in compact["strongAnchors"]
    assert compact["relationships"]["warehouse_HAS_BAY_bay"]["from"] == "warehouse"


def test_both_entities_are_connected_so_the_sync_is_permitted(descriptor: ActiveSchema) -> None:
    """W2.7 has nothing to sync unless the descriptor permits it.

    Asserted on the descriptor as shipped rather than on a promoted copy: a test
    that raises an entity's access to make its own assertion pass proves the test
    can pass, not that the platform works.
    """
    for entity_id in ("warehouse", "bay"):
        assert descriptor.entities[entity_id].source_access is EntitySourceAccess.CONNECTED_SYNC
