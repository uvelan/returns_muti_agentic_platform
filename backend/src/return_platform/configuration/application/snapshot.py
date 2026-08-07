from return_platform.configuration.domain.release import RuntimeSnapshot
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
