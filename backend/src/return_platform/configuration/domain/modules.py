from pydantic import BaseModel, ConfigDict
from typing import Mapping, Any

class ModuleConfigNode(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")
    enabled: bool = True
    path: str | None = None
    config: Mapping[str, Any] | None = None

class ModulesConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")
    modules: Mapping[str, ModuleConfigNode] = {}
