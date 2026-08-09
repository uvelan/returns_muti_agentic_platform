"""Loads `config/platform/system_store.yaml` into bootstrap-ready `StructureDefinition`s.

Deliberately bypasses the full configuration release/manifest pipeline
(`configuration.application.*`, DRAFT/VALIDATED/APPROVED/ACTIVE approval semantics) --
the system-store manifest declares which Mongo collections/indexes exist, not a
versioned business-schema release requiring CAS/checksum ceremony. A direct,
standalone YAML load is the correct level of machinery for infrastructure bootstrap,
which must be able to run before any release has ever been approved.

Defines its own local payload model rather than importing
`configuration.domain.system_store.SystemStoreConfig` -- `platform/*` must never
import a domain module (design doc section 13.1, rule R2a, enforced by
`tests/platform/test_layering.py`), even though the YAML shape happens to match.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict

from return_platform.platform.system_store.contracts import StructureDefinition


class _SystemStoreStructurePayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    physical_name: str
    schema_version: int | None = None
    encrypted: bool = False
    indexes: list[Mapping[str, Any]] | None = None


class _SystemStoreConfigPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    auto_bootstrap_missing_structures: bool = False
    fail_closed_on_drift: bool = False
    structures: Mapping[str, _SystemStoreStructurePayload] = {}


class _SystemStoreModuleFile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    payload: _SystemStoreConfigPayload


def load_system_store_config(path: Path) -> _SystemStoreConfigPayload:
    resolved = path.expanduser().resolve(strict=True)
    raw = yaml.safe_load(resolved.read_bytes())
    if not isinstance(raw, dict):
        raise ValueError("System store manifest must be a YAML object.")
    return _SystemStoreModuleFile.model_validate(raw).payload


def structure_definitions(
    config: _SystemStoreConfigPayload,
) -> tuple[StructureDefinition, ...]:
    definitions: list[StructureDefinition] = []
    for logical_name, structure in config.structures.items():
        if structure.schema_version is None:
            raise ValueError(f"system_store structure {logical_name!r} is missing a schema_version")
        definitions.append(
            StructureDefinition(
                logical_name=logical_name,
                physical_name=structure.physical_name,
                schema_version=structure.schema_version,
                encrypted=structure.encrypted,
                indexes=tuple(structure.indexes or ()),
            )
        )
    return tuple(definitions)


def load_system_store_structures(path: Path) -> tuple[StructureDefinition, ...]:
    return structure_definitions(load_system_store_config(path))
