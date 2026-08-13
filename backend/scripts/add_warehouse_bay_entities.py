"""W2.4: put `warehouse` and `bay` into the descriptor, through the analyzer.

    inspect the real source  ->  SourceSchemaSnapshot
      -> propose_reanalysis  ->  AddEntity / AddProperty, one per real column
      -> the modelling batch ->  typed commands an analyst would have sent
      -> apply_mutations     ->  GraphSchemaShape
      -> compile_addition    ->  ActiveSchema, merged onto the baseline
      -> the descriptor file

Nobody types a field name, a type or a physical path anywhere in this script.
Every property of `bay` comes from `ReanalysisService`'s proposal, which builds
one `AddProperty` per column `SourceInspectionPort.describe_object` reported. The
only human decisions here are the ones the analyzer's design says are human --
what the thing is called, what identifies it, and how it relates to something
else -- and each is a typed `MutationCommand`, not an edit.

**Why the bay table is also the warehouse source.** There is no warehouse master
anywhere this platform can reach. `docs/SEED_DATA_GENERATION.md` documents an
invented `warehouseMaster` collection; `return_source` does not contain it, and
declaring an entity against a collection that is not there would put paths in the
descriptor that resolve on nothing -- the exact defect W2.6 spent its verification
budget removing. What does exist is `platform.bay_configuration.warehouse_id`, on
every row, NOT NULL, and the leading column of the table's lookup index. So
`warehouse` is projected out of the bay table as a dimension, the same way
`customer` is projected out of `salesInv`: one node per distinct id, carrying only
what the source actually states about it. It is not a warehouse master and its
description says so.

Run it against a reachable SQL Server:

    PYTHONPATH=backend/src backend/.venv/Scripts/python.exe \
        backend/scripts/add_warehouse_bay_entities.py --write
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from return_platform.bootstrap.adapters.analyzer_schema_addition import compile_addition
from return_platform.bootstrap.adapters.analyzer_source_observation import (
    SourceObservation,
    dataset_metadata_of,
)
from return_platform.bootstrap.adapters.source_inspection_sqlserver import (
    build_sqlserver_source_inspection_adapter,
)
from return_platform.configuration.settings import Settings
from return_platform.dynamic_knowledge.config_loader import load_active_schema
from return_platform.dynamic_knowledge.fingerprint import sha256_digest
from return_platform.dynamic_knowledge.schema import (
    ActiveSchema,
    ConnectorType,
    SourceAssetDefinition,
)
from return_platform.graph_schema_analyzer.application.reanalysis_service import propose_reanalysis
from return_platform.graph_schema_analyzer.application.source_inspection import (
    build_scoped_source_inspection,
)
from return_platform.graph_schema_analyzer.domain.mutation import (
    AddEntity,
    AddProperty,
    AddRelationship,
    Cardinality,
    ChangeIdentifier,
    MutationCommand,
    PropertyType,
    RenameEntity,
)
from return_platform.graph_schema_analyzer.domain.schema_draft import GraphSchemaShape
from return_platform.graph_schema_analyzer.domain.source_scope import (
    InspectionScope,
    ObjectScope,
    SourceScope,
)
from return_platform.graph_schema_analyzer.domain.source_snapshot import (
    SampleClassification,
    SourceSchemaSnapshot,
)
from return_platform.source_connectors.sqlserver import SqlServerConnectionSettings

SOURCE_ID = "source_bays"
OBJECT_NAME = "platform.bay_configuration"

#: The label `_label_for` derives from the object name, and therefore the one the
#: proposal arrives under. Named rather than recomputed so a change in that
#: derivation fails loudly here instead of silently proposing an entity nothing
#: renames.
PROPOSED_LABEL = "PlatformBayConfiguration"

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DESCRIPTOR = (
    REPOSITORY_ROOT / "backend" / "config" / "dynamic_knowledge" / "active-schema.return-order.yaml"
)

#: The column the source changes on. Read from the catalogue like everything
#: else, but named here because the *source asset* has to declare it before the
#: entity exists, and the compiler refuses an entity that does not map it.
CURSOR_COLUMN = "updated_at"

BAY_SOURCE = SourceAssetDefinition(
    source_asset_id=SOURCE_ID,
    connector_type=ConnectorType.MSSQL,
    # The platform's own SQL Server, the same one `SqlServerSourceScanConnector`
    # is constructed against in `build_targeted_graph_access`. `namespace` is
    # load-bearing rather than documentation: `_resolve` reads both halves and
    # refuses a SQL source that gives only one.
    connection_ref="vault://data-sources/platform-sqlserver",
    object_ref={"namespace": "platform", "name": "bay_configuration"},
    incremental_cursor_field=CURSOR_COLUMN,
)


def modelling_commands() -> tuple[MutationCommand, ...]:
    """The decisions the analyzer's design leaves to a person.

    Every one is a typed command, and none of them names a source field that the
    proposal did not already establish exists.
    """
    return (
        RenameEntity(
            label=PROPOSED_LABEL,
            new_label="bay",
            rationale=(
                "the table is the bay master; its name is an implementation detail of "
                "where the platform keeps it"
            ),
        ),
        ChangeIdentifier(
            label="bay",
            identifier_properties=("bay_id",),
            rationale="bay_id is the table's primary key, so one value is one bay",
        ),
        AddEntity(
            label="warehouse",
            source_dataset=OBJECT_NAME,
            rationale=(
                "no warehouse master is reachable from this platform; warehouse_id on the "
                "bay table is the only place a warehouse is stated to exist"
            ),
        ),
        AddProperty(
            label="warehouse",
            property_name="warehouse_id",
            property_type=PropertyType.STRING,
            source_field=f"{OBJECT_NAME}.warehouse_id",
            rationale="the dimension this entity is projected on",
        ),
        AddProperty(
            label="warehouse",
            property_name="branch_id",
            property_type=PropertyType.STRING,
            source_field=f"{OBJECT_NAME}.branch_id",
            rationale="the only other warehouse-level column the source states",
        ),
        AddProperty(
            label="warehouse",
            property_name=CURSOR_COLUMN,
            property_type=PropertyType.DATETIME,
            source_field=f"{OBJECT_NAME}.{CURSOR_COLUMN}",
            rationale=(
                "the source syncs incrementally on this column and the compiler refuses "
                "an entity on it that does not map it"
            ),
        ),
        ChangeIdentifier(
            label="warehouse",
            identifier_properties=("warehouse_id",),
            rationale=(
                "one node per distinct id. The bay table repeats the id on every row; the "
                "node key is what collapses them, not entity-level `distinct`, which "
                "de-duplicates within one document and a SQL row is one document"
            ),
        ),
        AddRelationship(
            relationship_type="HAS_BAY",
            from_label="warehouse",
            to_label="bay",
            cardinality=Cardinality.ONE_TO_MANY,
            from_properties=("warehouse_id",),
            to_properties=("warehouse_id",),
            rationale=(
                "the edge bay scoring traverses: a warehouse's candidate bays, without a "
                "second query keyed on a property"
            ),
        ),
    )


async def observe(settings: Settings) -> SourceObservation:
    """Describe one object, through the scoped tool layer and nothing wider.

    The scope grants exactly `platform.bay_configuration`. It is not decoration:
    `ScopedSourceInspection` refuses any other object inbound and filters
    listings outbound, so this script cannot read a table it was not written to
    read even if a later edit asked it to.
    """
    adapter = build_sqlserver_source_inspection_adapter(
        SqlServerConnectionSettings(
            server=settings.sqlserver_host,
            port=settings.sqlserver_port,
            user=settings.sqlserver_user,
            password=settings.sqlserver_password.get_secret_value(),
            database=settings.sqlserver_database,
            timeout_seconds=int(settings.operation_timeout_seconds),
        ),
        source_id=SOURCE_ID,
    )
    inspection = build_scoped_source_inspection(
        adapter,
        scope=InspectionScope(
            sources=(
                SourceScope(
                    source_id=SOURCE_ID,
                    objects=(ObjectScope(object_name=OBJECT_NAME),),
                    max_sample_rows=100,
                ),
            )
        ),
    )
    validation = await inspection.validate(source_id=SOURCE_ID)
    if not validation.reachable:
        raise SystemExit(f"{SOURCE_ID} is not reachable: {validation.detail}")
    return SourceObservation(
        description=await inspection.describe_object(source_id=SOURCE_ID, object_name=OBJECT_NAME),
        indexes=tuple(await inspection.list_indexes(source_id=SOURCE_ID, object_name=OBJECT_NAME)),
        profile=await inspection.profile(
            source_id=SOURCE_ID, object_name=OBJECT_NAME, sample_size=100
        ),
    )


def proposed_commands(observation: SourceObservation) -> tuple[MutationCommand, ...]:
    """What the analyzer proposes for a dataset no draft reads: the whole entity.

    Through `propose_reanalysis` rather than by walking the description here, so
    the column-to-property translation is the analyzer's own and a column it
    cannot type is reported by the analyzer rather than dropped by this script.
    """
    captured = datetime.now(UTC)
    empty = SourceSchemaSnapshot.create(
        snapshot_id="before-warehouse-bay",
        analysis_id="w2.4",
        datasets=(),
        sample_classification=SampleClassification.NONE,
        captured_at=captured,
    )
    after = SourceSchemaSnapshot.create(
        snapshot_id="after-warehouse-bay",
        analysis_id="w2.4",
        datasets=(dataset_metadata_of(observation),),
        sample_classification=SampleClassification.NONE,
        captured_at=captured,
    )
    proposal = propose_reanalysis(
        draft_id="w2.4-warehouse-bay",
        shape=GraphSchemaShape(),
        before=empty,
        after=after,
        from_sequence=0,
    )
    unresolved = [change for change in proposal.changes if change.requires_human_decision]
    if unresolved:
        raise SystemExit(
            "the analyzer could not express part of this source as commands:\n"
            + "\n".join(f"  {change.element}: {change.detail}" for change in unresolved)
        )
    return proposal.mutations


def build(baseline: ActiveSchema, observation: SourceObservation) -> ActiveSchema:
    return compile_addition(
        baseline=baseline,
        commands=(*proposed_commands(observation), *modelling_commands()),
        new_sources={SOURCE_ID: BAY_SOURCE},
        observations={OBJECT_NAME: observation},
        # Order Discovery is the agent that answers "where is my return now".
        # Without the grant the nodes exist and no plan naming them compiles.
        grant_to_agents=("order-discovery-agent",),
        configuration_release_id=baseline.configuration_release_id,
        schema_version=baseline.schema_version,
        approved_by=baseline.approved_by,
        approved_at=baseline.approved_at,
    )


#: Fields the model holds as a set, which `model_dump` turns into a list in
#: whatever order that set iterated. Sorted on the way out so two runs of this
#: script produce the same bytes -- string hashing is randomised per process, and
#: a generated descriptor that differs run to run is one nobody can review.
_UNORDERED = frozenset(
    {
        "operators",
        "aggregations",
        "displayable_by",
        "searchable_by",
        "on_demand_sync_by",
        "allowed_entity_ids",
        "allowed_roles",
        "allowed_business_capabilities",
    }
)


def _stable(value: Any, *, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {name: _stable(item, key=name) for name, item in value.items()}
    if isinstance(value, list):
        items = [_stable(item) for item in value]
        return sorted(items, key=str) if key in _UNORDERED else items
    return value


def _render(payload: dict[str, Any], *, indent: int) -> list[str]:
    text = yaml.safe_dump(_stable(payload), sort_keys=False, width=100, allow_unicode=True)
    pad = " " * indent
    return [f"{pad}{line}" if line.strip() else line for line in text.splitlines()]


def _differences(written: Any, compiled: Any, *, path: str) -> list[str]:
    """Where two dumps disagree, by path.

    A bare "these are not equal" on a 4,300-line configuration tells whoever runs
    this nothing they can act on.
    """
    if isinstance(written, dict) and isinstance(compiled, dict):
        found: list[str] = []
        for key in sorted(set(written) | set(compiled)):
            where = f"{path}.{key}"
            if key not in written:
                found.append(f"{where}: missing from the written file")
            elif key not in compiled:
                found.append(f"{where}: present in the written file and not compiled")
            else:
                found.extend(_differences(written[key], compiled[key], path=where))
        return found
    if written != compiled:
        return [f"{path}: written {written!r} != compiled {compiled!r}"]
    return []


def _index_of(lines: list[str], anchor: str) -> int:
    for position, line in enumerate(lines):
        if line == anchor:
            return position
    raise SystemExit(f"the descriptor has no {anchor!r} line; its layout has changed")


def splice(text: str, updated: ActiveSchema, baseline: ActiveSchema) -> str:
    """Insert the compiled fragment into the descriptor's own text.

    A whole-file `yaml.safe_dump` would be one line of code and would delete
    every comment in a 4,300-line configuration -- including the ones recording
    why `order_shipped_as` is `CONNECTED_SYNC` and why the return entities are in
    the agent policy. Those are the only place several decisions are written
    down, so the file is edited rather than regenerated, and the edit is verified
    by loading the result and comparing it to the compiled schema: a splice that
    landed anywhere wrong cannot produce an equal object.
    """
    lines = text.splitlines()
    new_entities = sorted(set(updated.entities) - set(baseline.entities))
    new_nodes = sorted(set(updated.graph.nodes) - set(baseline.graph.nodes))
    new_edges = sorted(set(updated.graph.relationships) - set(baseline.graph.relationships))
    new_sources = sorted(set(updated.sources) - set(baseline.sources))

    # Each block is inserted immediately before the line that ends it, from the
    # bottom of the file upwards so earlier insertions do not move later anchors.
    insertions = [
        (
            _index_of(lines, "agent_policies:"),
            _render(
                {
                    name: updated.graph.relationships[name].model_dump(mode="json")
                    for name in new_edges
                },
                indent=4,
            ),
        ),
        (
            _index_of(lines, "  relationships:"),
            _render(
                {name: updated.graph.nodes[name].model_dump(mode="json") for name in new_nodes},
                indent=4,
            ),
        ),
        (
            _index_of(lines, "graph:"),
            _render(
                {name: updated.entities[name].model_dump(mode="json") for name in new_entities},
                indent=2,
            ),
        ),
        (
            _index_of(lines, "entities:"),
            _render(
                {name: updated.sources[name].model_dump(mode="json") for name in new_sources},
                indent=2,
            ),
        ),
    ]
    for position, rendered in insertions:
        lines[position:position] = rendered

    for agent_id, policy in updated.agent_policies.items():
        granted = policy.allowed_entity_ids - baseline.agent_policies[agent_id].allowed_entity_ids
        if not granted:
            continue
        start = _index_of(lines, f"  {agent_id}:")
        cursor = start + 1
        while lines[cursor].strip() != "allowed_entity_ids:":
            cursor += 1
        cursor += 1
        while lines[cursor].startswith("    - "):
            cursor += 1
        lines[cursor:cursor] = [f"    - {entity_id}" for entity_id in sorted(granted)]

    spliced = "\n".join(lines) + "\n"
    # The checksum is over the parsed file rather than over the model, because
    # that is what `load_active_schema` digests.
    parsed = yaml.safe_load(spliced)
    parsed.pop("configuration_checksum", None)
    digest = sha256_digest(parsed)
    return (
        "\n".join(
            line
            if not line.startswith("configuration_checksum:")
            else f"configuration_checksum: {digest}"
            for line in spliced.splitlines()
        )
        + "\n"
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="write the descriptor; without it the run only reports what it would add",
    )
    arguments = parser.parse_args()

    settings = Settings()  # type: ignore[call-arg]
    baseline = load_active_schema(DESCRIPTOR)
    observation = await observe(settings)
    print(
        f"observed {OBJECT_NAME}: {len(observation.description.fields)} columns, "
        f"{len(observation.indexes)} indexes, "
        f"{observation.description.approximate_row_count} rows"
    )

    updated = build(baseline, observation)
    added = sorted(set(updated.entities) - set(baseline.entities))
    print(f"entities added: {added}")
    for entity_id in added:
        entity = updated.entities[entity_id]
        print(
            f"  {entity_id}: key={list(entity.natural_key)} "
            f"fields={len(entity.fields)} anchors={sorted(entity.strong_anchors)} "
            f"contract={entity.source_contract_status.value}"
        )

    if not arguments.write:
        print("dry run; pass --write to update the descriptor")
        return 0

    DESCRIPTOR.write_text(
        splice(DESCRIPTOR.read_text(encoding="utf-8"), updated, baseline), encoding="utf-8"
    )
    # Re-read through the loader -- which re-verifies the checksum -- and require
    # the result to be the schema that was compiled. A splice that landed in the
    # wrong block, or dropped a key, fails here rather than in production.
    # Compared as models, not as dumps: a `frozenset` field dumps to a list in
    # whatever order it iterated, so two dumps of one schema are not reliably
    # equal strings while the schemas themselves compare exactly.
    written = load_active_schema(DESCRIPTOR)
    # The checksum is the one field that *must* differ: it is a digest over the
    # file's content, and the content changed. `load_active_schema` has already
    # re-derived and verified it, so it is the one field this comparison has
    # nothing left to say about.
    updated = updated.model_copy(update={"configuration_checksum": written.configuration_checksum})
    if written != updated:
        raise SystemExit(
            "the written descriptor is not the schema that was compiled:\n"
            + "\n".join(
                f"  {line}"
                for line in _differences(
                    _stable(written.model_dump(mode="json")),
                    _stable(updated.model_dump(mode="json")),
                    path="",
                )
            )
        )
    print(f"wrote {DESCRIPTOR}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
