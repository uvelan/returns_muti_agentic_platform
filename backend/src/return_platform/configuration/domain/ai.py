from pydantic import BaseModel, ConfigDict
from typing import Mapping, Any

class AiConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schemaVersion: str | None = None
    domain: str | None = None
    circuitBreaker: Mapping[str, Any] | None = None
    retry: Mapping[str, Any] | None = None
    rateLimits: Mapping[str, Any] | None = None
    providerLimits: Mapping[str, Any] | None = None
    tasks: Mapping[str, Any] | None = None
    routes: Mapping[str, Any] | None = None
    providers: Mapping[str, Any] | None = None
    safety: Mapping[str, Any] | None = None
    interception: Mapping[str, Any] | None = None
