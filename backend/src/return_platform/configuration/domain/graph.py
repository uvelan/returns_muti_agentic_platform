"""Canonical graph configuration domain models.

Graph schemas, graph mappings, and sync policies are semantically distinct
and must never be conflated with each other.
"""
from __future__ import annotations

from typing import Any, List, Mapping, Optional

from pydantic import BaseModel, ConfigDict


class GraphSchemaNode(BaseModel):
    """A canonical graph schema definition (NOT a mapping or sync config)."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_name: Optional[str] = None
    configuration_release_id: Optional[str] = None
    release_status: Optional[str] = None
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    schema_version: Optional[str] = None
    policy_version: Optional[str] = None
    prompt_version: Optional[str] = None
    compiler_version: Optional[str] = None
    runtime_mode: Optional[str] = None
    # Logical source references — validated against SourcesConfig.sources keys
    sources: Optional[Mapping[str, Any]] = None
    entities: Optional[Mapping[str, Any]] = None
    nodes: Optional[Mapping[str, Any]] = None
    relationships: Optional[List[Any]] = None
    constraints: Optional[List[str]] = None
    projection_profiles: Optional[List[str]] = None
    prohibit_raw_source_payload: bool = True


class GraphMappingConfig(BaseModel):
    """Canonical mapping configuration (source → canonical entity field mapping).

    Distinct from GraphSchemaNode — a mapping is not a graph schema.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    canonical_entity: Optional[str] = None
    full_order_id: Optional[str] = None
    full_order_line_id: Optional[str] = None
    allowlisted_order_fields: Optional[List[str]] = None
    allowlisted_line_fields: Optional[List[str]] = None
    # Preserve any extra payload from the mapping YAML for forward compatibility
    payload: Optional[Mapping[str, Any]] = None


class GraphSyncConfig(BaseModel):
    """Canonical sync policy configuration.

    Distinct from GraphSchemaNode — a sync policy is not a graph schema.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: Optional[str] = None
    projection_profile: Optional[str] = None
    max_candidates: Optional[int] = None
    strong_anchors: Optional[List[str]] = None
    graph_readback_required: bool = False
    # Preserve any extra payload for forward compatibility
    payload: Optional[Mapping[str, Any]] = None


class GraphRuntimeSettings(BaseModel):
    """Graph-layer runtime settings (not schema/mapping/sync specific)."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    settings: Optional[Mapping[str, Any]] = None


class GraphConfig(BaseModel):
    """Canonical graph configuration combining schemas, mappings, and sync policies."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    graphs: Mapping[str, GraphSchemaNode] = {}
    mappings: Mapping[str, GraphMappingConfig] = {}
    sync: Mapping[str, GraphSyncConfig] = {}
    settings: Optional[GraphRuntimeSettings] = None
