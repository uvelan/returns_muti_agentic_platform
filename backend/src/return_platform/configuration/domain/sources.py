from pydantic import BaseModel, ConfigDict
from typing import Mapping, Any

class SourceConfigNode(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    connector_type: str
    connection_metadata: Mapping[str, Any] | None = None
    credential_ref: str | None = None
    enabled: bool
    discovery_policy: Mapping[str, Any] | None = None
    sampling_limits: Mapping[str, Any] | None = None
    query_limits: Mapping[str, Any] | None = None
    timeouts: Mapping[str, Any] | None = None

class SourcesConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    sources: Mapping[str, SourceConfigNode]
