from pydantic import BaseModel, ConfigDict
from typing import Mapping, Any

class ModuleConfigNode(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    path: str | None = None
    enabled: bool = True
    config: Mapping[str, Any] | None = None

class ModulesConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: str | None = None
    release_id: str | None = None
    status: str | None = None
    modules: Mapping[str, ModuleConfigNode] = {}
