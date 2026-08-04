"""Canonical schema and request fingerprinting."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from return_platform.dynamic_knowledge.schema import ActiveSchema


def _canonical(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [_canonical(item) for item in value]
        if isinstance(value, (set, frozenset)):
            return sorted(
                items, key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":"))
            )
        return items
    return value


def canonical_json(value: Any) -> str:
    """Serialize a structure deterministically."""

    return json.dumps(_canonical(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_digest(value: Any) -> str:
    """Return a SHA-256 digest over canonical JSON."""

    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def graph_schema_fingerprint(schema: ActiveSchema) -> str:
    """Fingerprint every input that changes business graph meaning."""

    sources = {
        source_id: {
            "connector_type": source.connector_type.value,
            "object_ref": source.object_ref,
            "incremental_cursor_field": source.incremental_cursor_field,
        }
        for source_id, source in schema.sources.items()
    }
    entities = {
        entity_id: {
            "source_asset_id": entity.source_asset_id,
            "natural_key": entity.natural_key,
            "fields": {
                field_id: {
                    "physical_path": field.physical_path,
                    "graph_property": field.graph_property,
                    "data_type": field.data_type.value,
                    "nullable": field.nullable,
                }
                for field_id, field in entity.fields.items()
            },
        }
        for entity_id, entity in schema.entities.items()
    }
    graph = schema.graph.model_dump(mode="json")
    semantic_payload = {
        "schema_version": schema.schema_version,
        "compiler_version": schema.compiler_version,
        "sources": sources,
        "entities": entities,
        "graph": graph,
    }
    return sha256_digest(semantic_payload)


def on_demand_request_digest(
    *,
    tenant_scope: str,
    source_asset_id: str,
    entity_id: str,
    strong_anchor_id: str,
    normalized_anchors: Mapping[str, Any],
    schema_version: str,
    graph_generation_id: str,
    mapping_version: str,
) -> str:
    """Build the canonical idempotency key for targeted synchronization."""

    return sha256_digest(
        {
            "tenant_scope": tenant_scope,
            "source_asset_id": source_asset_id,
            "entity_id": entity_id,
            "strong_anchor_id": strong_anchor_id,
            "normalized_anchors": normalized_anchors,
            "schema_version": schema_version,
            "graph_generation_id": graph_generation_id,
            "mapping_version": mapping_version,
        }
    )
