from pydantic import BaseModel, ConfigDict
from typing import Mapping, Any, Optional
from datetime import datetime

class ReleaseStatus:
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"

class RuntimeSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    platform: Any | None = None
    system_store: Any | None = None
    modules: Any | None = None
    agents: Any | None = None
    workflow: Any | None = None
    sources: Any | None = None
    integrations: Any | None = None
    graph: Any | None = None
    ai: Any | None = None
    features: Any | None = None

class ConfigurationRelease(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    release_id: str
    status: str
    checksum: str
    created_at: datetime
    snapshot: RuntimeSnapshot
