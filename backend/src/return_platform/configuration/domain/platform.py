from pydantic import BaseModel, ConfigDict
from typing import Mapping, Any

class PlatformConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")
    environment: str = "production"
    region: str = "us-east-1"
    limits: Mapping[str, Any] | None = None
    feature_gates: Mapping[str, bool] | None = None
