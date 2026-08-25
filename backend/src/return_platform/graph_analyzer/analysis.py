"""Turn a selected source scope into a proposed system graph.

WHAT THIS REPLACES

`start_analysis` used to read the platform's own `SchemaRegistry` and nothing
else. A source configured through the UI was invisible to it, the analysis
context was stored and never read, and no model was ever called -- the
"proposal" was one entity per registry asset with a `HAS_X` edge wherever two
assets happened to share a field name. That is not analysis, and it could not
see the sources the user had actually selected.

HOW IT WORKS NOW

1. Resolve the selection to real objects, through the connectors.
2. Gather evidence: declared fields, declared indexes, declared relationships,
   approximate row counts, and a bounded masked sample.
3. Ask the shared reasoning port for a proposal, with that evidence framed as
   untrusted input.
4. Ground the model's answer back onto the evidence -- an entity or property
   that does not correspond to something discovered is dropped, not trusted.

WHEN NO MODEL IS AVAILABLE

The platform degrades when no provider credential is configured, so analysis
falls back to a deterministic proposal built only from *declared* source
metadata: primary and unique indexes become identifiers, declared foreign keys
and graph relationships become relationships. Nothing is inferred from a field
name resembling another field name. The run reports which path produced it, so
a deterministic proposal is never presented as a reasoned one.

THE BOUNDARY

Every read here goes through `SourceInspectionPort`, which has no free-form
query parameter. The output is a system-graph proposal; there is no code path
from this module to a source write, and `SchemaProposal` cannot carry a
statement to execute.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from return_platform.graph_analyzer.discovery import (
    inspection_port,
    merge_declared_identifiers,
    readable_identifiers,
)
from return_platform.graph_analyzer.models import (
    GraphEntity,
    GraphProperty,
    GraphRelationship,
)
from return_platform.graph_schema_analyzer.application.prompt_context import (
    build_prompt_blocks,
    neutralize_delimiters,
)
from return_platform.graph_schema_analyzer.ports.ai_port import (
    SchemaProposal,
    SchemaReasoningPort,
)
from return_platform.graph_schema_analyzer.ports.source_port import RelationshipKind

logger = logging.getLogger("return_platform.graph_analyzer.analysis")

#: Rows sampled per object to ground the proposal. Small on purpose: samples are
#: evidence about shape, and every extra row is more untrusted text in a prompt.
SAMPLE_ROWS_PER_OBJECT = 5

#: Rows profiled to find an identifier candidate when none is declared.
#: Larger than the prompt sample because no value leaves the connector --
#: `profile` returns counts and flags only, so a wider read costs nothing in
#: exposure and makes a uniqueness claim worth more.
PROFILE_ROWS_PER_OBJECT = 200

#: Objects one analysis may span. A selection larger than this is refused with a
#: message rather than silently truncated, because a proposal built from part of
#: the requested scope is not the proposal that was asked for.
MAX_OBJECTS_PER_ANALYSIS = 60

_TASK_DEFINITION = (
    "Propose a property-graph schema for the SYSTEM GRAPH from the source metadata "
    "provided. Every recommendation must target the system graph. You must never "
    "propose creating, altering, dropping or indexing anything in a source system: "
    "sources are read-only evidence. Ground every entity and property in a source "
    "object and field that appears in the metadata block."
)


@dataclass(frozen=True)
class ObjectEvidence:
    """Everything discovered about one selected source object."""

    object_id: str
    source_id: str
    source_name: str
    engine: str
    object_name: str
    fields: tuple[Mapping[str, Any], ...]
    identifier_fields: tuple[str, ...]
    indexed_fields: tuple[str, ...]
    relationships: tuple[Mapping[str, Any], ...]
    approximate_rows: int | None
    samples: tuple[Mapping[str, Any], ...] = field(default=())


async def gather_evidence(
    documents: Mapping[str, Mapping[str, Any]],
    selection: Sequence[tuple[str, str]],
) -> list[ObjectEvidence]:
    """Read declared metadata and a bounded sample for each selected object.

    `selection` is `(source_id, object_name)` pairs. One port is opened per
    source rather than per object, so a ten-table selection against one database
    is one connection and not ten.
    """
    by_source: dict[str, list[str]] = {}
    for source_id, object_name in selection:
        by_source.setdefault(source_id, []).append(object_name)

    evidence: list[ObjectEvidence] = []
    for source_id, object_names in by_source.items():
        document = documents.get(source_id)
        if document is None:
            continue
        async with inspection_port(document) as port:
            for object_name in object_names:
                try:
                    description = await port.describe_object(
                        source_id=source_id, object_name=object_name
                    )
                except Exception:  # noqa: BLE001 - one unreadable object is not fatal
                    logger.warning(
                        "graph_analyzer_object_unreadable source=%s object=%s",
                        source_id,
                        object_name,
                    )
                    continue
                try:
                    indexes = await port.list_indexes(source_id=source_id, object_name=object_name)
                except Exception:  # noqa: BLE001 - index metadata is advisory
                    indexes = ()
                try:
                    declared = await port.list_relationships(
                        source_id=source_id, object_name=object_name
                    )
                except Exception:  # noqa: BLE001 - a source may declare none
                    declared = ()
                try:
                    rows = await port.sample(
                        source_id=source_id,
                        object_name=object_name,
                        limit=SAMPLE_ROWS_PER_OBJECT,
                    )
                except Exception:  # noqa: BLE001 - metadata-only analysis is valid
                    rows = ()

                declared_identifiers = readable_identifiers(
                    tuple(
                        name
                        for index in indexes
                        if index.primary or index.unique
                        for name in index.fields
                    )
                )
                if not declared_identifiers:
                    # No declared key the connector can return -- the ordinary
                    # case for a MongoDB collection, whose only unique index is
                    # the `_id` the adapter deliberately never reads. The port
                    # already answers this question: `profile` reports which
                    # fields were non-null and unique across every row it saw.
                    # A candidate, not a certainty, which is why it is only
                    # consulted when nothing was declared.
                    try:
                        profile = await port.profile(
                            source_id=source_id,
                            object_name=object_name,
                            sample_size=PROFILE_ROWS_PER_OBJECT,
                        )
                    except Exception:  # noqa: BLE001 - profiling is best effort
                        profile = None
                    if profile is not None:
                        declared_identifiers = tuple(
                            item.field_name for item in profile.fields if item.identifier_candidate
                        )[:1]

                evidence.append(
                    ObjectEvidence(
                        object_id=f"{source_id}:{object_name}",
                        source_id=source_id,
                        source_name=str(document.get("name", source_id)),
                        engine=str(document.get("engine", "")),
                        object_name=object_name,
                        fields=merge_declared_identifiers(
                            tuple(
                                {
                                    "name": item.field_name,
                                    "type": item.declared_type,
                                    "nullable": item.nullable,
                                }
                                for item in description.fields
                            ),
                            tuple(
                                name
                                for index in indexes
                                if index.primary or index.unique
                                for name in index.fields
                            ),
                        ),
                        identifier_fields=declared_identifiers,
                        indexed_fields=tuple(name for index in indexes for name in index.fields),
                        relationships=tuple(
                            {
                                "kind": item.relationship_kind.value,
                                "from_object": item.from_object,
                                "from_fields": list(item.from_fields),
                                "to_object": item.to_object,
                                "to_fields": list(item.to_fields),
                            }
                            for item in declared
                        ),
                        approximate_rows=description.approximate_row_count,
                        samples=tuple(dict(row) for row in rows),
                    )
                )
    return evidence


def _safe_label(value: str, fallback: str) -> str:
    words = [part for part in "".join(c if c.isalnum() else " " for c in value).split() if part]
    candidate = "".join(word[:1].upper() + word[1:] for word in words)
    if not candidate or not candidate[0].isalpha():
        candidate = f"{fallback}{candidate}"
    return candidate


def _safe_type(value: str, fallback: str) -> str:
    words = [part for part in "".join(c if c.isalnum() else " " for c in value).split() if part]
    candidate = "_".join(word.upper() for word in words)
    return candidate if candidate and candidate[0].isalpha() else fallback


def safe_property_name(field_name: str) -> str:
    """A legal graph property name from a source field path.

    Mongo metadata reports nested fields as dotted paths
    (`salesHdrEventData.accountId`), and `GraphProperty.name` refuses anything
    outside `^[A-Za-z_][A-Za-z0-9_]*$` -- so one nested document anywhere in a
    selected collection made the entire analysis 422 with a raw pydantic
    message. The path's segments survive joined by underscores; the *original*
    path is what `sourceField` is for, and every caller keeps passing it there
    untouched, so grounding back to the source loses nothing.
    """
    cleaned = "".join(c if c.isalnum() or c == "_" else "_" for c in field_name)
    collapsed = "_".join(part for part in cleaned.split("_") if part) or "field"
    return collapsed if collapsed[0].isalpha() or collapsed[0] == "_" else f"f_{collapsed}"


def deterministic_proposal(
    evidence: Sequence[ObjectEvidence],
) -> tuple[list[GraphEntity], list[GraphRelationship]]:
    """A proposal built only from what the sources declare.

    Identifiers come from primary and unique indexes. Relationships come from
    declared foreign keys and graph relationships. Nothing is inferred from
    similar names -- a guessed edge presented beside a declared one would be
    indistinguishable to the validation that treats declared edges as fact.
    """
    entities: list[GraphEntity] = []
    by_object: dict[str, str] = {}
    for index, item in enumerate(evidence):
        entity_id = f"proposal:{item.object_id}"
        by_object[item.object_name] = entity_id
        leaf = item.object_name.rsplit(".", 1)[-1]
        identifiers = set(item.identifier_fields)
        indexed = set(item.indexed_fields)
        entities.append(
            GraphEntity(
                id=entity_id,
                name=_safe_label(leaf, "Entity"),
                description=(
                    f"Proposed from {item.source_name} · {item.object_name}"
                    + (
                        f" (~{item.approximate_rows} rows)"
                        if item.approximate_rows is not None
                        else ""
                    )
                ),
                x=0.0,
                y=0.0,
                properties=[
                    GraphProperty(
                        id=f"{entity_id}:{f['name']}",
                        name=safe_property_name(str(f["name"])),
                        dataType=str(f["type"]),
                        required=not bool(f["nullable"]),
                        identifier=str(f["name"]) in identifiers,
                        # A system graph index is proposed where the source
                        # declares one: the source's own index is evidence about
                        # lookup patterns. The index is created on the system
                        # graph; the source's is only read.
                        indexed=str(f["name"]) in indexed,
                        sourceObjectId=item.object_id,
                        sourceField=str(f["name"]),
                    )
                    for f in item.fields
                ],
                constraints=[
                    f"UNIQUE({safe_property_name(name)})" for name in sorted(identifiers)[:1]
                ],
                change="ADDED",
            )
        )
        _ = index

    relationships: list[GraphRelationship] = []
    seen: set[str] = set()
    for item in evidence:
        for declared in item.relationships:
            from_id = by_object.get(str(declared["from_object"]))
            to_id = by_object.get(str(declared["to_object"]))
            if from_id is None or to_id is None or from_id == to_id:
                continue
            kind = str(declared["kind"])
            fields = declared["from_fields"]
            name = (
                _safe_type(f"REFERENCES_{str(declared['to_object']).rsplit('.', 1)[-1]}", "RELATED")
                if kind == RelationshipKind.FOREIGN_KEY.value
                else _safe_type(str(declared["to_object"]), "RELATED")
            )
            identity = f"proposal:rel:{from_id}:{to_id}:{name}"
            if identity in seen:
                continue
            seen.add(identity)
            relationships.append(
                GraphRelationship(
                    id=identity,
                    name=name,
                    fromEntityId=from_id,
                    toEntityId=to_id,
                    direction="OUTBOUND",
                    properties=[],
                    sourceObjectId=item.object_id,
                    change="ADDED",
                )
            )
            _ = fields
    return entities, relationships


async def reasoned_proposal(
    reasoning: SchemaReasoningPort,
    *,
    analysis_id: str,
    evidence: Sequence[ObjectEvidence],
    context: str,
) -> SchemaProposal:
    """Ask the shared model route for a proposal over framed, untrusted evidence.

    Every string that came out of a source -- object names, field names, sample
    values -- and the operator's own context reach the model inside the six-block
    untrusted framing, with block delimiters neutralised. Source content cannot
    address the model as policy.
    """
    # The keys are `_render_metadata`'s contract -- `source_id`, `dataset_name`,
    # `field_name`, `declared_type` -- not this module's own vocabulary. The
    # first wiring used `dataset`/`source`/`name`/`type`, every lookup in the
    # renderer missed, and block 4 reached the model as rows of
    # "unknown: unknown": the model was asked to propose a schema over evidence
    # that named nothing, and the analysis fell back to deterministic on every
    # run while both sides looked healthy.
    metadata = [
        {
            "source_id": neutralize_delimiters(item.source_name),
            "dataset_name": neutralize_delimiters(item.object_name),
            "engine": item.engine,
            "approximate_rows": item.approximate_rows,
            "fields": [
                {
                    "field_name": neutralize_delimiters(str(f["name"])),
                    "declared_type": neutralize_delimiters(str(f["type"])),
                    "nullable": f["nullable"],
                    "identifier": str(f["name"]) in set(item.identifier_fields),
                    "indexed": str(f["name"]) in set(item.indexed_fields),
                }
                for f in item.fields
            ],
            "declared_relationships": item.relationships,
        }
        for item in evidence
    ]
    samples = {item.object_name: list(item.samples) for item in evidence if item.samples}
    blocks = build_prompt_blocks(
        task_definition=_TASK_DEFINITION,
        source_metadata=metadata,
        untrusted_samples=samples or None,
        user_requirements=context,
    )
    return await reasoning.propose_schema(
        analysis_id=analysis_id,
        snapshot_content_hash=_snapshot_hash(evidence),
        # `PromptBlock` is a pydantic model; the port takes plain mappings so it
        # cannot be handed something that renders itself differently downstream.
        prompt_blocks=[block.model_dump() for block in blocks],
    )


def _snapshot_hash(evidence: Sequence[ObjectEvidence]) -> str:
    """Identify the exact evidence a proposal was grounded in."""
    import hashlib
    import json

    payload = json.dumps(
        [
            {
                "object": item.object_id,
                "fields": [f["name"] for f in item.fields],
                "identifiers": list(item.identifier_fields),
            }
            for item in sorted(evidence, key=lambda entry: entry.object_id)
        ],
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def ground_proposal(
    proposal: SchemaProposal,
    evidence: Sequence[ObjectEvidence],
) -> tuple[list[GraphEntity], list[GraphRelationship]]:
    """Map a model proposal onto discovered evidence, dropping what is ungrounded.

    An entity naming a dataset that was never discovered, or a property naming a
    field that object does not have, is discarded rather than carried into the
    proposal. The model contributes structure and naming; it does not get to
    introduce source objects that do not exist.
    """
    by_dataset = {item.object_name: item for item in evidence}
    by_leaf = {item.object_name.rsplit(".", 1)[-1]: item for item in evidence}
    # The rendered form the model is *instructed* to echo: block 4 prints every
    # dataset as `{source_id}.{dataset_name}` and the prompt says to name it
    # "exactly as it appears in block 4" -- so a model that obeyed to the letter
    # answered `return_source (MONGODB).customers`, matched neither lookup
    # above, and every grounded entity was dropped as ungrounded. The grounder
    # accepts its own prompt's spelling, plus the leaf for a model that answered
    # with just the object name.
    by_rendered = {f"{item.source_name}.{item.object_name}": item for item in evidence}

    entities: list[GraphEntity] = []
    label_to_id: dict[str, str] = {}
    for node in proposal.nodes:
        proposed = node.source_dataset
        item = (
            by_dataset.get(proposed)
            or by_rendered.get(proposed)
            or by_leaf.get(proposed)
            or by_leaf.get(proposed.rsplit(".", 1)[-1])
        )
        if item is None:
            logger.info("graph_analyzer_dropped_ungrounded_entity dataset=%s", proposed)
            continue
        known_fields = {str(f["name"]): f for f in item.fields}
        entity_id = f"proposal:{item.object_id}"
        if entity_id in label_to_id.values():
            continue
        properties: list[GraphProperty] = []
        for raw in node.properties:
            name = str(raw.get("source_field") or raw.get("name") or "")
            declared = known_fields.get(name)
            if declared is None:
                continue
            properties.append(
                GraphProperty(
                    id=f"{entity_id}:{name}",
                    name=safe_property_name(str(raw.get("name") or name)),
                    dataType=str(declared["type"]),
                    required=not bool(declared["nullable"]),
                    identifier=bool(raw.get("identifier")) or name in set(item.identifier_fields),
                    indexed=bool(raw.get("indexed")) or name in set(item.indexed_fields),
                    sourceObjectId=item.object_id,
                    sourceField=name,
                )
            )
        if not properties:
            # Every property was ungrounded, so there is nothing to map. Fall
            # back to the declared fields rather than emitting an empty entity.
            properties = [
                GraphProperty(
                    id=f"{entity_id}:{f['name']}",
                    name=safe_property_name(str(f["name"])),
                    dataType=str(f["type"]),
                    required=not bool(f["nullable"]),
                    identifier=str(f["name"]) in set(item.identifier_fields),
                    indexed=str(f["name"]) in set(item.indexed_fields),
                    sourceObjectId=item.object_id,
                    sourceField=str(f["name"]),
                )
                for f in item.fields
            ]
        label = _safe_label(node.label, "Entity")
        label_to_id[label] = entity_id
        entities.append(
            GraphEntity(
                id=entity_id,
                name=label,
                description=neutralize_delimiters(node.rationale)[:1000],
                x=0.0,
                y=0.0,
                properties=properties,
                constraints=[f"UNIQUE({prop.name})" for prop in properties if prop.identifier][:1],
                change="ADDED",
            )
        )

    relationships: list[GraphRelationship] = []
    seen: set[str] = set()
    for edge in proposal.relationships:
        from_id = label_to_id.get(_safe_label(edge.from_label, "Entity"))
        to_id = label_to_id.get(_safe_label(edge.to_label, "Entity"))
        if from_id is None or to_id is None or from_id == to_id:
            continue
        name = _safe_type(edge.relationship_type, "RELATED")
        identity = f"proposal:rel:{from_id}:{to_id}:{name}"
        if identity in seen:
            continue
        seen.add(identity)
        relationships.append(
            GraphRelationship(
                id=identity,
                name=name,
                fromEntityId=from_id,
                toEntityId=to_id,
                direction="OUTBOUND",
                properties=[],
                sourceObjectId=None,
                change="ADDED",
            )
        )
    return entities, relationships
