from pydantic import BaseModel, ConfigDict
from typing import Mapping, Any

class GraphConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")
    graphs: Mapping[str, Any] = {}
    settings: Mapping[str, Any] | None = None
