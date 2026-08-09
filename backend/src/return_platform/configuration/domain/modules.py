"""Canonical module configuration — the manifest is the authoritative source."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict


class ModuleDependency(BaseModel):
    """Declared inter-module dependency from a manifest entry."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    module_id: str
    version_constraint: str | None = None


class ModuleConfigNode(BaseModel):
    """Representation of a single manifest module entry.

    Preserves enough information so that policy, mapping, sync, and any
    other module type is never silently discarded even when no dedicated
    canonical domain object yet exists for that type.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Manifest-declared identity
    path: str | None = None
    enabled: bool = True

    # Full module document fields (loaded from the module YAML)
    module_id: str | None = None
    module_type: str | None = None
    schema_version: str | None = None
    configuration_version: str | None = None
    owner: str | None = None
    status: str | None = None
    dependencies: list[ModuleDependency] | None = None

    # Complete domain payload — preserved verbatim for domains without a
    # dedicated canonical model (e.g. policy) so nothing is silently lost.
    config: Mapping[str, Any] | None = None


class ModulesConfig(BaseModel):
    """Canonical representation of the configuration manifest."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str | None = None
    release_id: str | None = None
    status: str | None = None
    modules: Mapping[str, ModuleConfigNode] = {}
