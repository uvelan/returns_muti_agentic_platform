"""Generic extraction: raw source documents -> DynamicRecordMutation.

Every decision this module makes is driven entirely by the active schema's
EntityDefinition (record_path / explode / where / distinct / key_resolution)
and FieldDefinition (physical_path / path_origin / derive) -- see schema.py.
No source-, customer-, product-, or order-specific branching belongs here;
that lives only in configuration.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from return_platform.dynamic_knowledge.on_demand_sync.contracts import (
    DynamicRecordMutation,
    DynamicSourceRecord,
    ProjectionReadScope,
    RawSourcePage,
)
from return_platform.dynamic_knowledge.schema import (
    ActiveSchema,
    DeriveOperation,
    EntityDefinition,
    FieldDerivation,
    PathOrigin,
)


class SourceRecordExtractor(Protocol):
    def extract(
        self,
        *,
        schema: ActiveSchema,
        source_asset_id: str,
        page: RawSourcePage,
        read_scope: ProjectionReadScope,
    ) -> tuple[DynamicRecordMutation, ...]: ...


class ExtractionError(ValueError):
    """A configured extraction rule could not be applied to a source document."""


_MISSING = object()

_Chain = tuple[Mapping[str, Any], ...]


class GenericSourceRecordExtractor:
    """The one, schema-driven extractor every source asset shares."""

    def extract(
        self,
        *,
        schema: ActiveSchema,
        source_asset_id: str,
        page: RawSourcePage,
        read_scope: ProjectionReadScope,
    ) -> tuple[DynamicRecordMutation, ...]:
        mutations: list[DynamicRecordMutation] = []
        entities = [
            entity
            for entity in schema.entities.values()
            if entity.source_asset_id == source_asset_id
        ]
        for entity in entities:
            projection_id = schema.entity_node(entity.entity_id).projection_id
            for document in page.documents:
                if document.operation == "DELETE":
                    mutations.extend(
                        _delete_mutations(entity, document, source_asset_id, projection_id, read_scope)
                    )
                    continue
                if document.document is None:
                    continue
                mutations.extend(
                    _upsert_mutations(
                        entity, document, source_asset_id, projection_id, read_scope
                    )
                )
        return tuple(mutations)


def _delete_mutations(
    entity: EntityDefinition,
    document: Any,
    source_asset_id: str,
    projection_id: str,
    read_scope: ProjectionReadScope,
) -> list[DynamicRecordMutation]:
    if document.source_key_values is None:
        return []
    return [
        DynamicRecordMutation(
            operation="DELETE",
            record=None,
            entity_id=entity.entity_id,
            projection_id=projection_id,
            source_asset_id=source_asset_id,
            source_identity=document.source_identity,
            resolved_key=dict(document.source_key_values),
            read_scope=read_scope,
        )
    ]


def _upsert_mutations(
    entity: EntityDefinition,
    document: Any,
    source_asset_id: str,
    projection_id: str,
    read_scope: ProjectionReadScope,
) -> list[DynamicRecordMutation]:
    root = document.document
    chains = _walk_record_path(root, entity.record_path) if entity.explode else [()]
    seen_natural_keys: set[tuple[Any, ...]] = set()
    mutations: list[DynamicRecordMutation] = []
    for chain in chains:
        if not _passes_where(entity, chain, root):
            continue
        values = _extract_fields(entity, chain, root)
        natural_key = {key: values[key] for key in entity.natural_key if key in values}
        if len(natural_key) != len(entity.natural_key):
            continue  # a required natural-key field could not be resolved; not confirmable
        if entity.distinct:
            key_tuple = tuple(sorted(natural_key.items()))
            if key_tuple in seen_natural_keys:
                continue
            seen_natural_keys.add(key_tuple)
        mutations.append(
            DynamicRecordMutation(
                operation="UPSERT",
                record=DynamicSourceRecord(
                    source_asset_id=source_asset_id,
                    entity_id=entity.entity_id,
                    natural_key=natural_key,
                    values=values,
                ),
                entity_id=entity.entity_id,
                projection_id=projection_id,
                source_asset_id=source_asset_id,
                source_identity=document.source_identity,
                # RawSourceDocument carries no dedicated version field today; a connector
                # that wants source_version populated must fold it into source_identity
                # or wait for a future RawSourceDocument.source_version field.
                source_version=None,
                resolved_key=natural_key,
                read_scope=read_scope,
            )
        )
    return mutations


def _walk_record_path(current: Any, path: tuple[str, ...]) -> list[_Chain]:
    """Branch over every list encountered while consuming ``path``; descend through dicts."""

    return _walk(current, path, ())


def _walk(current: Any, path: tuple[str, ...], chain: _Chain) -> list[_Chain]:
    if isinstance(current, list):
        results: list[_Chain] = []
        for element in current:
            if isinstance(element, Mapping):
                results.extend(_walk(element, path, (*chain, element)))
        return results
    if not path:
        return [chain] if isinstance(current, Mapping) else []
    segment, *rest = path
    if not isinstance(current, Mapping) or segment not in current:
        return []
    return _walk(current[segment], tuple(rest), chain)


def _current_record(chain: _Chain, root: Mapping[str, Any]) -> Mapping[str, Any]:
    return chain[-1] if chain else root


def _parent_record(chain: _Chain, root: Mapping[str, Any]) -> Mapping[str, Any]:
    return chain[-2] if len(chain) >= 2 else root


def _passes_where(entity: EntityDefinition, chain: _Chain, root: Mapping[str, Any]) -> bool:
    if not entity.where:
        return True
    current = _current_record(chain, root)
    for selector in entity.where:
        if _resolve_path(current, selector.physical_path) != selector.equals:
            return False
    return True


def _extract_fields(
    entity: EntityDefinition, chain: _Chain, root: Mapping[str, Any]
) -> dict[str, Any]:
    current = _current_record(chain, root)
    parent = _parent_record(chain, root)
    sources = {
        PathOrigin.ROOT_DOCUMENT: root,
        PathOrigin.CURRENT_RECORD: current,
        PathOrigin.PARENT_RECORD: parent,
    }
    values: dict[str, Any] = {}
    for field_id, field in entity.fields.items():
        if field.derive is not None or field.physical_path is None:
            continue
        value = _resolve_path(sources[field.path_origin], field.physical_path)
        if value is not _MISSING:
            values[field_id] = value
    # Derivations are validated (schema.py) to reference only non-derived sibling
    # fields, so a single second pass always has its inputs already resolved.
    for field_id, field in entity.fields.items():
        if field.derive is None:
            continue
        source_value = values.get(field.derive.source_field)
        if source_value is None:
            continue
        derived = _apply_derive(field.derive, source_value)
        if derived is not None:
            values[field_id] = derived
    return values


def _resolve_path(record: Any, path: tuple[str, ...]) -> Any:
    current = record
    for segment in path:
        if not isinstance(current, Mapping) or segment not in current:
            return _MISSING
        current = current[segment]
    return current


def _apply_derive(derivation: FieldDerivation, source_value: Any) -> Any:
    if derivation.operation is DeriveOperation.SPLIT_PART:
        parts = str(source_value).split(derivation.delimiter)
        if derivation.index >= len(parts):
            return None
        return parts[derivation.index]
    raise ExtractionError(f"unsupported derive operation: {derivation.operation!r}")
