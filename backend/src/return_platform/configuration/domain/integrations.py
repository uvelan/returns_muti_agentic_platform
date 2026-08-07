from pydantic import BaseModel, ConfigDict
from typing import Mapping, Any

class IntegrationDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")
    enabled: bool = True

class IntegrationsConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")
    integrations: Mapping[str, IntegrationDefinition] = {}
