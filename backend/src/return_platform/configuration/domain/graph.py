from pydantic import BaseModel, ConfigDict
from typing import Mapping, Any

class GraphConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    settings: Mapping[str, Any] | None = None
