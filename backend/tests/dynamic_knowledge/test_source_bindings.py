"""Resolving a dataset name to the source it actually reads from.

The compiler used to do this itself by matching a draft's dataset against the
shipped schema's asset ids and object references. That worked for datasets
already in the file and failed for everything else, so an entity could not be
pointed at a source the baseline had never heard of and rebinding one was a
schema edit with an approval attached.

These cover the resolution rule -- overrides win, configuration fills in, an
unknown name resolves to nothing -- and the one ambiguity worth refusing.
"""

from __future__ import annotations

import pytest

from return_platform.configuration.settings import DEFAULT_DYNAMIC_KNOWLEDGE_SCHEMA_PATH
from return_platform.dynamic_knowledge.config_loader import load_active_schema
from return_platform.dynamic_knowledge.schema import ActiveSchema, SourceAssetDefinition
from return_platform.dynamic_knowledge.source_bindings import SourceBinding, catalogue_from


@pytest.fixture(scope="module")
def baseline() -> ActiveSchema:
    return load_active_schema(DEFAULT_DYNAMIC_KNOWLEDGE_SCHEMA_PATH)


def _elsewhere(source_asset_id: str = "restored_orders") -> SourceAssetDefinition:
    return SourceAssetDefinition(
        source_asset_id=source_asset_id,
        connector_type="MONGODB",
        connection_ref="vault://data-sources/restored-mongodb",
        object_ref={"database": "restore_2026", "name": "salesInv"},
    )


def test_a_dataset_resolves_through_configuration_by_default(baseline: ActiveSchema) -> None:
    catalogue = catalogue_from(baseline)
    asset_id = next(iter(baseline.sources))

    assert catalogue.resolve(asset_id) == baseline.sources[asset_id]


def test_a_source_answers_to_its_object_name_as_well_as_its_id(baseline: ActiveSchema) -> None:
    """An analyst naming the collection is not making a mistake.

    Drafts are written against what discovery showed, which is the collection,
    while the schema file names the asset.
    """
    catalogue = catalogue_from(baseline)
    asset = next(iter(baseline.sources.values()))
    collection = asset.object_ref.get("name")

    assert collection is not None
    assert catalogue.resolve(collection) == asset


def test_a_database_name_is_not_a_dataset_name(baseline: ActiveSchema) -> None:
    """The ambiguity worth refusing.

    Every asset in a database shares its name, so binding to it would resolve
    to whichever asset happened to be registered last -- silently, and to real
    data from the wrong collection.
    """
    catalogue = catalogue_from(baseline)
    database = next(iter(baseline.sources.values())).object_ref.get("database")

    assert database is not None
    assert catalogue.resolve(database) is None


def test_an_override_wins_over_configuration(baseline: ActiveSchema) -> None:
    """A rebinding exists precisely to disagree with the file."""
    asset_id = next(iter(baseline.sources))
    catalogue = catalogue_from(baseline, [SourceBinding(dataset=asset_id, asset=_elsewhere())])

    resolved = catalogue.resolve(asset_id)

    assert resolved is not None
    assert resolved.source_asset_id == "restored_orders"
    assert resolved.connection_ref == "vault://data-sources/restored-mongodb"


def test_a_binding_can_name_a_dataset_configuration_has_never_heard_of(
    baseline: ActiveSchema,
) -> None:
    """The case the old name-matching could not express at all."""
    catalogue = catalogue_from(
        baseline, [SourceBinding(dataset="warehouse_bays", asset=_elsewhere("warehouse_bays"))]
    )

    assert catalogue.resolve("warehouse_bays") is not None
    assert "warehouse_bays" in catalogue.datasets()


def test_an_unknown_dataset_resolves_to_nothing(baseline: ActiveSchema) -> None:
    """Not to a plausible neighbour. The compiler turns this into a refusal."""
    assert catalogue_from(baseline).resolve("no_such_dataset") is None


def test_overriding_does_not_remove_the_other_datasets(baseline: ActiveSchema) -> None:
    asset_id = next(iter(baseline.sources))
    catalogue = catalogue_from(baseline, [SourceBinding(dataset=asset_id, asset=_elsewhere())])

    assert set(baseline.sources).issubset(set(catalogue.datasets()))
