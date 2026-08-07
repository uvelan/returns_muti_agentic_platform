from pydantic import BaseModel, ConfigDict
from typing import Mapping, Any, List

class SystemStoreStructure(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    physical_name: str
    schema_version: int | None = None
    encrypted: bool = False
    indexes: List[Mapping[str, Any]] | None = None

class SystemStoreConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    provider: str
    allowed_providers: List[str] | None = None
    auto_bootstrap_missing_structures: bool = False
    migration_mode: str | None = None
    fail_closed_on_drift: bool = False
    migration_lock_required: bool = False
    structures: Mapping[str, SystemStoreStructure]
