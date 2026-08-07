from pydantic import BaseModel, ConfigDict
from typing import Mapping

class FeatureFlags(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    flags: Mapping[str, bool]
