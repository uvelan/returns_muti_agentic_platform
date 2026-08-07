from return_platform.configuration.domain.release_model import RuntimeSnapshot
from return_platform.configuration.domain.platform import PlatformConfig
from return_platform.configuration.domain.system_store import SystemStoreConfig
from return_platform.configuration.domain.modules import ModulesConfig
from return_platform.configuration.domain.agents import AgentsConfig
from return_platform.configuration.domain.workflow import WorkflowConfig
from return_platform.configuration.domain.sources import SourcesConfig
from return_platform.configuration.domain.integrations import IntegrationsConfig
from return_platform.configuration.domain.graph import GraphConfig
from return_platform.configuration.domain.ai import AiConfig
import hashlib
import json
from return_platform.configuration.domain.features import FeaturesConfig

def compute_checksum(snapshot: RuntimeSnapshot) -> str:
    """Deterministic SHA-256 checksum of the canonical snapshot.

    Uses the same serialisation for release creation, validation
    verification, activation pointer, and runtime view comparison.
    """
    raw_json = json.dumps(snapshot.model_dump(), sort_keys=True)
    return hashlib.sha256(raw_json.encode("utf-8")).hexdigest()

class SnapshotBuilder:
    def create_snapshot(
        self,
        platform: PlatformConfig,
        system_store: SystemStoreConfig,
        modules: ModulesConfig,
        agents: AgentsConfig,
        workflow: WorkflowConfig,
        sources: SourcesConfig,
        integrations: IntegrationsConfig,
        graph: GraphConfig,
        ai: AiConfig,
        features: FeaturesConfig
    ) -> RuntimeSnapshot:
        return RuntimeSnapshot(
            platform=platform,
            system_store=system_store,
            modules=modules,
            agents=agents,
            workflow=workflow,
            sources=sources,
            integrations=integrations,
            graph=graph,
            ai=ai,
            features=features
        )
