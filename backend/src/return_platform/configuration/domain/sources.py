from pydantic import BaseModel, ConfigDict
from typing import Mapping, Any

class SourceConfigNode(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")
    connector_type: str | None = None
    enabled: bool = True

class SourcesConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")
    sources: Mapping[str, SourceConfigNode] = {}
