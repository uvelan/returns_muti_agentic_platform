from pydantic import BaseModel, ConfigDict
from typing import Mapping, Any

class FeaturesConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")
    flags: Mapping[str, bool] = {}
