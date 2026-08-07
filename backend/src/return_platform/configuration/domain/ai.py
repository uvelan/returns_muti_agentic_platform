from pydantic import BaseModel, ConfigDict
from typing import Mapping, Any

class AiConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    providers: Mapping[str, Any] | None = None
    routes: Mapping[str, Any] | None = None
    tasks: Mapping[str, Any] | None = None
    safety: Mapping[str, Any] | None = None
    interception: Mapping[str, Any] | None = None
