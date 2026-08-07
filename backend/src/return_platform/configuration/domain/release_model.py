"""
Canonical release domain model.

RuntimeSnapshot is immutable after creation.
ConfigurationRelease carries the full lifecycle provenance.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict

from return_platform.configuration.domain.release import ReleaseStatus
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


class RuntimeSnapshot(BaseModel):
    """Frozen canonical configuration snapshot.  Never modified after creation."""
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
    """Full lifecycle record for a configuration release."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    release_id: str
    status: ReleaseStatus
    snapshot: RuntimeSnapshot
    checksum: str
    created_at: datetime
    updated_at: datetime

    # Lifecycle provenance — nullable until the transition occurs
    validated_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    approved_by: Optional[str] = None
    activated_at: Optional[datetime] = None
    superseded_by: Optional[str] = None
