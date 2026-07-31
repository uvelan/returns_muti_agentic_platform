"""Validated configuration loader for dependency simulation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProviderPrice(StrictModel):
    input: int = Field(default=0, ge=0)
    output: int = Field(default=0, ge=0)


class SimulationAIConfiguration(StrictModel):
    enabled: bool = True
    taskId: str = Field(default="SIMULATOR_OPERATION_NARRATIVE_V1", min_length=1, max_length=128)
    providerOrder: tuple[Literal["GOOGLE", "NVIDIA", "OPENAI", "ANTHROPIC", "OLLAMA"], ...] = (
        "GOOGLE",
        "NVIDIA",
    )
    timeoutSeconds: float = Field(default=4.0, ge=0.25, le=30.0)
    maxOutputTokens: int = Field(default=256, ge=32, le=2_048)
    temperature: float = Field(default=0.0, ge=0.0, le=1.0)
    fallbackAlwaysEnabled: bool = True
    pricingMicrousdPerMillionTokens: dict[str, ProviderPrice] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_order(self) -> SimulationAIConfiguration:
        if not self.providerOrder or len(set(self.providerOrder)) != len(self.providerOrder):
            raise ValueError("AI providerOrder must contain unique lightweight providers.")
        if not self.fallbackAlwaysEnabled:
            raise ValueError("Deterministic fallback must always be enabled for simulation.")
        return self


class DependencyDefinition(StrictModel):
    operations: tuple[str, ...] = Field(min_length=1)
    statusSequence: tuple[str, ...] = ()


class DependencySimulationConfiguration(StrictModel):
    schemaVersion: str = Field(min_length=1, max_length=32)
    enabled: bool = True
    templateVersion: str = Field(min_length=1, max_length=128)
    modeBanner: str = Field(min_length=10, max_length=256)
    defaultScenario: str = Field(default="SUCCESS", min_length=1, max_length=64)
    ai: SimulationAIConfiguration
    dependencies: dict[str, DependencyDefinition]

    @model_validator(mode="after")
    def validate_dependencies(self) -> DependencySimulationConfiguration:
        required = {"OMC", "PARCEL", "FREIGHT", "LSI"}
        if set(self.dependencies) != required:
            raise ValueError("Simulation configuration must define OMC, PARCEL, FREIGHT, and LSI.")
        for name, definition in self.dependencies.items():
            if len(set(definition.operations)) != len(definition.operations):
                raise ValueError(f"{name} contains duplicate operations.")
        return self


class LoadedDependencySimulationConfiguration(StrictModel):
    path: Path
    sha256: str
    configuration: DependencySimulationConfiguration


def load_dependency_simulation_configuration(
    path: Path,
) -> LoadedDependencySimulationConfiguration:
    resolved = path.expanduser().resolve(strict=True)
    raw = resolved.read_bytes()
    payload = yaml.safe_load(raw)
    if not isinstance(payload, dict):
        raise ValueError("Dependency simulation configuration must be a YAML object.")
    return LoadedDependencySimulationConfiguration(
        path=resolved,
        sha256=hashlib.sha256(raw).hexdigest(),
        configuration=DependencySimulationConfiguration.model_validate(payload),
    )


def build_loaded_dependency_simulation_configuration(
    configuration: DependencySimulationConfiguration,
    *,
    path: Path,
) -> LoadedDependencySimulationConfiguration:
    """Build a digest-addressed loaded view from a validated graph payload."""

    encoded = json.dumps(
        configuration.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return LoadedDependencySimulationConfiguration(
        path=path,
        sha256=hashlib.sha256(encoded).hexdigest(),
        configuration=configuration,
    )
