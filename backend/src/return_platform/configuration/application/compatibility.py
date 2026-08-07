from pathlib import Path
import logging

from return_platform.configuration.domain.release import RuntimeSnapshot
from return_platform.configuration.domain.platform import PlatformConfig
from return_platform.configuration.domain.ai import AiConfig
from return_platform.configuration.domain.modules import ModulesConfig, ModuleConfigNode
from return_platform.configuration.domain.agents import AgentsConfig, AgentConfigNode
from return_platform.configuration.domain.workflow import WorkflowConfig, WorkflowDefinition
from return_platform.configuration.domain.sources import SourcesConfig, SourceConfigNode
from return_platform.configuration.domain.integrations import IntegrationsConfig, IntegrationDefinition
from return_platform.configuration.domain.graph import GraphConfig, GraphSchemaNode
from return_platform.configuration.domain.system_store import SystemStoreConfig
from return_platform.configuration.domain.features import FeaturesConfig
from return_platform.configuration.application.loader import ConfigurationLoader

logger = logging.getLogger(__name__)

class LegacyCompatibilityAdapter:
    def __init__(self, config_dir: Path) -> None:
        self._loader = ConfigurationLoader(config_dir)
        self._config_dir = config_dir

    def build_canonical_snapshot(self) -> RuntimeSnapshot:
        # 1. AI Gateway
        ai_raw = self._loader.load_file("ai_gateway.yaml")
        ai_config = AiConfig(**ai_raw) if ai_raw else AiConfig()

        # 2. Returns Production
        returns_raw = self._loader.load_file("returns/production.yaml")
        features_dict = returns_raw.get("features", {}) if isinstance(returns_raw, dict) else {}
        platform_dict = returns_raw.get("platform", {}) if isinstance(returns_raw, dict) else {}
        features_config = FeaturesConfig(flags=features_dict if isinstance(features_dict, dict) else {})
        platform_config = PlatformConfig(**platform_dict) if isinstance(platform_dict, dict) else PlatformConfig()

        # 3. Manifest / Modules
        manifest_raw = self._loader.load_file("manifest.yaml")
        modules_map = {}
        if isinstance(manifest_raw, dict) and "modules" in manifest_raw:
            for mod_id, mod_data in manifest_raw["modules"].items():
                if isinstance(mod_data, dict):
                    modules_map[mod_id] = ModuleConfigNode(**mod_data)
                else:
                    modules_map[mod_id] = ModuleConfigNode(path=str(mod_data))
        modules_config = ModulesConfig(modules=modules_map)

        # 4. System Store (platform/system_store.yaml ONLY)
        store_raw = self._loader.load_file("platform/system_store.yaml")
        store_payload = store_raw.get("payload", {}) if isinstance(store_raw, dict) and "payload" in store_raw else store_raw
        if isinstance(store_payload, dict):
            system_store_config = SystemStoreConfig(
                provider=store_payload.get("provider", "MONGODB"),
                allowed_providers=store_payload.get("allowed_providers"),
                auto_bootstrap_missing_structures=store_payload.get("auto_bootstrap_missing_structures", True),
                migration_mode=store_payload.get("migration_mode"),
                fail_closed_on_drift=store_payload.get("fail_closed_on_drift", True),
                migration_lock_required=store_payload.get("migration_lock_required", True),
                structures={}
            )
        else:
            system_store_config = SystemStoreConfig()

        # 5. Graph / Dynamic Knowledge (dynamic_knowledge/* & graph/* mapped explicitly to GraphConfig!)
        graph_map = {}
        graph_raw = self._loader.load_directory_yaml("graph")
        for g_id, g_content in graph_raw.items():
            payload = g_content.get("payload", g_content)
            if isinstance(payload, dict):
                graph_map[g_id] = GraphSchemaNode(**payload)

        dk_raw = self._loader.load_directory_yaml("dynamic_knowledge")
        for dk_id, dk_content in dk_raw.items():
            graph_map[dk_id] = GraphSchemaNode(
                schema_name=dk_id,
                configuration_release_id=dk_content.get("configuration_release_id"),
                release_status=dk_content.get("release_status"),
                approved_by=dk_content.get("approved_by"),
                approved_at=dk_content.get("approved_at"),
                schema_version=str(dk_content.get("schema_version", "")),
                policy_version=str(dk_content.get("policy_version", "")),
                prompt_version=str(dk_content.get("prompt_version", "")),
                compiler_version=str(dk_content.get("compiler_version", "")),
                runtime_mode=dk_content.get("runtime_mode"),
                sources=dk_content.get("sources"),
                entities=dk_content.get("entities")
            )
        graph_config = GraphConfig(graphs=graph_map)

        # 6. Agents
        agents_map = {}
        agents_raw = self._loader.load_directory_yaml("agents")
        for agent_id, agent_content in agents_raw.items():
            payload = agent_content.get("payload", agent_content)
            if isinstance(payload, dict):
                agents_map[agent_id] = AgentConfigNode(**payload)
        agents_config = AgentsConfig(agents=agents_map)

        # 7. Workflows
        workflows_map = {}
        workflows_raw = self._loader.load_directory_yaml("workflows")
        for wf_id, wf_content in workflows_raw.items():
            payload = wf_content.get("payload", wf_content)
            if isinstance(payload, dict):
                stages_raw = payload.get("stages", []) if isinstance(payload, dict) else []
                workflows_map[wf_id] = WorkflowDefinition(
                    context_only_handoffs=payload.get("context_only_handoffs", True),
                    direct_agent_calls_allowed=payload.get("direct_agent_calls_allowed", False),
                    stages=stages_raw
                )
        workflow_config = WorkflowConfig(workflow=workflows_map)

        # 8. Sources
        sources_map = {}
        sources_raw = self._loader.load_directory_yaml("sources")
        for src_id, src_content in sources_raw.items():
            payload = src_content.get("payload", src_content)
            if isinstance(payload, dict):
                sources_map[src_id] = SourceConfigNode(**payload)
        sources_config = SourcesConfig(sources=sources_map)

        # 9. Integrations (policies, mappings, sync)
        integrations_map = {}
        for sub_name in ["policies", "mappings", "sync"]:
            sub_raw = self._loader.load_directory_yaml(sub_name)
            for item_id, item_content in sub_raw.items():
                payload = item_content.get("payload", item_content)
                if isinstance(payload, dict):
                    integrations_map[item_id] = IntegrationDefinition(
                        module_id=item_content.get("module_id", item_id),
                        module_type=item_content.get("module_type"),
                        schema_version=item_content.get("schema_version"),
                        configuration_version=item_content.get("configuration_version"),
                        owner=item_content.get("owner"),
                        status=item_content.get("status"),
                        dependencies=item_content.get("dependencies"),
                        payload=payload
                    )
        integrations_config = IntegrationsConfig(integrations=integrations_map)

        return RuntimeSnapshot(
            platform=platform_config,
            system_store=system_store_config,
            modules=modules_config,
            agents=agents_config,
            workflow=workflow_config,
            sources=sources_config,
            integrations=integrations_config,
            graph=graph_config,
            ai=ai_config,
            features=features_config
        )

def build_snapshot_from_legacy_configs(config_dir: Path) -> RuntimeSnapshot:
    adapter = LegacyCompatibilityAdapter(config_dir)
    return adapter.build_canonical_snapshot()
