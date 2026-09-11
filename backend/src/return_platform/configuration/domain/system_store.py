from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict


class SystemStoreStructure(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    physical_name: str
    schema_version: int | None = None
    encrypted: bool = False
    indexes: list[Mapping[str, Any]] | None = None


class SystemStoreConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    provider: str = "MONGODB"
    allowed_providers: list[str] | None = None
    auto_bootstrap_missing_structures: bool = False
    fail_closed_on_drift: bool = False
    structures: Mapping[str, SystemStoreStructure] = {}
