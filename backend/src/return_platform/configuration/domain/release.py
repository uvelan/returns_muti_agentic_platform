from pydantic import BaseModel, ConfigDict
from typing import Mapping, Any, Optional
from datetime import datetime
from enum import StrEnum

from return_platform.configuration.domain.platform import PlatformConfig
from return_platform.configuration.domain.system_store import SystemStoreConfig
from return_platform.configuration.domain.modules import ModulesConfig
from return_platform.configuration.domain.agents import AgentsConfig
from return_platform.configuration.domain.workflow import WorkflowConfig
from return_platform.configuration.domain.sources import SourcesConfig
from return_platform.configuration.domain.integrations import IntegrationsConfig
from return_platform.configuration.domain.graph import GraphConfig
from return_platform.configuration.domain.ai import AiConfig
from return_platform.configuration.domain.features import FeaturesConfig

class ReleaseStatus(StrEnum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"

class RuntimeSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    platform: PlatformConfig
    system_store: SystemStoreConfig
    modules: ModulesConfig
    agents: AgentsConfig
    workflow: WorkflowConfig
    sources: SourcesConfig
    integrations: IntegrationsConfig
    graph: GraphConfig
    ai: AiConfig
    features: FeaturesConfig

class ConfigurationRelease(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    release_id: str
    status: ReleaseStatus
    snapshot: RuntimeSnapshot
    created_at: datetime
    updated_at: datetime
    checksum: str
