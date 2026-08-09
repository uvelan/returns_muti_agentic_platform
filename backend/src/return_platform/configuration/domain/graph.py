"""Canonical graph configuration domain models.

Graph schemas, graph mappings, and sync policies are semantically distinct
and must never be conflated with each other.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict


class GraphSchemaNode(BaseModel):
    """A canonical graph schema definition (NOT a mapping or sync config)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_name: str | None = None
    configuration_release_id: str | None = None
    release_status: str | None = None
    approved_by: str | None = None
    approved_at: str | None = None
    schema_version: str | None = None
    policy_version: str | None = None
    prompt_version: str | None = None
    compiler_version: str | None = None
    runtime_mode: str | None = None
    # Logical source references — validated against SourcesConfig.sources keys
    sources: Mapping[str, Any] | None = None
    entities: Mapping[str, Any] | None = None
    nodes: Mapping[str, Any] | None = None
    relationships: list[Any] | None = None
    constraints: list[str] | None = None
    projection_profiles: list[str] | None = None
    prohibit_raw_source_payload: bool = True


class GraphMappingConfig(BaseModel):
    """Canonical mapping configuration (source → canonical entity field mapping).

    Distinct from GraphSchemaNode — a mapping is not a graph schema.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    canonical_entity: str | None = None
    full_order_id: str | None = None
    full_order_line_id: str | None = None
    allowlisted_order_fields: list[str] | None = None
    allowlisted_line_fields: list[str] | None = None
    # Preserve any extra payload from the mapping YAML for forward compatibility
    payload: Mapping[str, Any] | None = None


class GraphSyncConfig(BaseModel):
    """Canonical sync policy configuration.

    Distinct from GraphSchemaNode — a sync policy is not a graph schema.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: str | None = None
    projection_profile: str | None = None
    max_candidates: int | None = None
    strong_anchors: list[str] | None = None
    graph_readback_required: bool = False
    # Preserve any extra payload for forward compatibility
    payload: Mapping[str, Any] | None = None


class GraphRuntimeSettings(BaseModel):
    """Graph-layer runtime settings (not schema/mapping/sync specific)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    settings: Mapping[str, Any] | None = None


class GraphConfig(BaseModel):
    """Canonical graph configuration combining schemas, mappings, and sync policies."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    graphs: Mapping[str, GraphSchemaNode] = {}
    mappings: Mapping[str, GraphMappingConfig] = {}
    sync: Mapping[str, GraphSyncConfig] = {}
    settings: GraphRuntimeSettings | None = None
