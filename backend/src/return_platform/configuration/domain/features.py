from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict


class FeaturesConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    flags: Mapping[str, bool] = {}
