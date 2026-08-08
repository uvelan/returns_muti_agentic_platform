"""Validated configuration loader for the reasoning platform (`config/reasoning.yaml`)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CheckpointRetentionConfiguration(StrictModel):
    activeRunsExpire: bool = False
    terminalRetentionHours: float = Field(default=168.0, gt=0.0)
    abandonAfterHours: float = Field(default=720.0, gt=0.0)

    @model_validator(mode="after")
    def validate_not_settable_to_true_in_production(self) -> CheckpointRetentionConfiguration:
        if self.activeRunsExpire:
            raise ValueError(
                "checkpointRetention.activeRunsExpire must stay false -- a fixed TTL "
                "from creation would delete the checkpoints of a still-resumable run"
            )
        return self


class ExecutionConfiguration(StrictModel):
    bounded: bool = True


class ReasoningConfiguration(StrictModel):
    schemaVersion: str = Field(min_length=1, max_length=32)
    enabled: bool = True
    checkpointStore: Literal["SYSTEM_STORE"] = "SYSTEM_STORE"
    checkpointEncryption: bool = True
    checkpointRetention: CheckpointRetentionConfiguration
    execution: ExecutionConfiguration


class LoadedReasoningConfiguration(StrictModel):
    path: Path
    sha256: str
    configuration: ReasoningConfiguration


def load_reasoning_configuration(path: Path) -> LoadedReasoningConfiguration:
    resolved = path.expanduser().resolve(strict=True)
    raw = resolved.read_bytes()
    payload = yaml.safe_load(raw)
    if not isinstance(payload, dict):
        raise ValueError("Reasoning configuration must be a YAML object.")
    return LoadedReasoningConfiguration(
        path=resolved,
        sha256=hashlib.sha256(raw).hexdigest(),
        configuration=ReasoningConfiguration.model_validate(payload),
    )
