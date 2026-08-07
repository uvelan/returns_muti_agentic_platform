from pydantic import BaseModel, ConfigDict
from typing import Mapping

class FeaturesConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    flags: Mapping[str, bool] = {}
