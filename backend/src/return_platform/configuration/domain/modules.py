"""Canonical module configuration — the manifest is the authoritative source."""
from __future__ import annotations

from typing import Any, List, Mapping, Optional

from pydantic import BaseModel, ConfigDict


class ModuleDependency(BaseModel):
    """Declared inter-module dependency from a manifest entry."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    module_id: str
    version_constraint: Optional[str] = None


class ModuleConfigNode(BaseModel):
    """Representation of a single manifest module entry.

    Preserves enough information so that policy, mapping, sync, and any
    other module type is never silently discarded even when no dedicated
    canonical domain object yet exists for that type.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    # Manifest-declared identity
    path: Optional[str] = None
    enabled: bool = True

    # Full module document fields (loaded from the module YAML)
    module_id: Optional[str] = None
    module_type: Optional[str] = None
    schema_version: Optional[str] = None
    configuration_version: Optional[str] = None
    owner: Optional[str] = None
    status: Optional[str] = None
    dependencies: Optional[List[ModuleDependency]] = None

    # Complete domain payload — preserved verbatim for domains without a
    # dedicated canonical model (e.g. policy) so nothing is silently lost.
    config: Optional[Mapping[str, Any]] = None


class ModulesConfig(BaseModel):
    """Canonical representation of the configuration manifest."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Optional[str] = None
    release_id: Optional[str] = None
    status: Optional[str] = None
    modules: Mapping[str, ModuleConfigNode] = {}
