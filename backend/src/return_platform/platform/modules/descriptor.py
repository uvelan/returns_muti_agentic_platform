"""Module identity and capability declaration.

Resolved by the module registry into a configured runtime instance starting in
Phase 1B (design doc section 7.5/7.6).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ModuleKind(StrEnum):
    PLATFORM = "PLATFORM"
    BUSINESS = "BUSINESS"
    ANALYZER = "ANALYZER"
    AI = "AI"
    CONFIGURATION = "CONFIGURATION"


class ModuleDescriptor(BaseModel):
    """Identity, kind, and capability contract for one activatable module."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    module_id: str
    module_kind: ModuleKind
    implementation_id: str
    version: str
    capabilities: frozenset[str]
    configuration_schema: str
    required_platform_capabilities: frozenset[str]
    initialization_dependencies: frozenset[str] = Field(
        default=frozenset(),
        description=(
            "module_ids that must complete initialize() before this module's "
            "initialize() runs -- e.g. a module whose initialize() uses a resolved "
            "capability from another module. Stable module IDs, not Python imports. "
            "See platform.modules.lifecycle.topological_order."
        ),
    )
