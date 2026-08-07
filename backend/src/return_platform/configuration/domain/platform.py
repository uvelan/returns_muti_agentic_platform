from pydantic import BaseModel, ConfigDict
from typing import Mapping, Any

class PlatformConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    environment: str
    region: str
    limits: Mapping[str, Any] | None = None
    feature_gates: Mapping[str, bool] | None = None
