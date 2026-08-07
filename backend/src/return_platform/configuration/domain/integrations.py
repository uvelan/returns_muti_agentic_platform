from pydantic import BaseModel, ConfigDict
from typing import Mapping, Any

class IntegrationDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    credential_ref: str | None = None
    retry_policy: Mapping[str, Any] | None = None
    enabled: bool = True

class IntegrationsConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    integrations: Mapping[str, IntegrationDefinition]
