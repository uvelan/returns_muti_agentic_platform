from pydantic import BaseModel, ConfigDict
from typing import Mapping, Any

class ModuleConfigNode(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    enabled: bool
    implementation: str
    config: Mapping[str, Any] | None = None

class ModulesConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    modules: Mapping[str, ModuleConfigNode]
