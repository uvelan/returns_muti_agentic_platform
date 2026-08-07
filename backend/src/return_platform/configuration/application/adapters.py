import yaml
from pathlib import Path
from pydantic import ValidationError
import logging

from return_platform.configuration.domain.release import RuntimeSnapshot
from return_platform.configuration.domain.platform import PlatformConfig
from return_platform.configuration.domain.ai import AiConfig
from return_platform.configuration.domain.modules import ModulesConfig, ModuleConfigNode
from return_platform.configuration.domain.agents import AgentsConfig, AgentConfigNode
from return_platform.configuration.domain.workflow import WorkflowConfig, WorkflowDefinition
from return_platform.configuration.domain.sources import SourcesConfig, SourceConfigNode
from return_platform.configuration.domain.integrations import IntegrationsConfig, IntegrationDefinition
from return_platform.configuration.domain.graph import GraphConfig
from return_platform.configuration.domain.system_store import SystemStoreConfig, SystemStoreStructure
from return_platform.configuration.domain.features import FeaturesConfig

logger = logging.getLogger(__name__)

def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r") as f:
        return yaml.safe_load(f) or {}

def build_snapshot_from_legacy_configs(config_dir: Path) -> RuntimeSnapshot:
    """Compatibility adapter mapping old fragmented config into the new canonical model."""
    try:
        # 1. AI Gateway
        ai_raw = load_yaml(config_dir / "ai_gateway.yaml")
        ai_config = AiConfig(**ai_raw) if ai_raw else AiConfig()

        # 2. Returns Production
        returns_raw = load_yaml(config_dir / "returns" / "production.yaml")
        features_dict = returns_raw.get("features", {}) if isinstance(returns_raw, dict) else {}
        platform_dict = returns_raw.get("platform", {}) if isinstance(returns_raw, dict) else {}
        features_config = FeaturesConfig(flags=features_dict if isinstance(features_dict, dict) else {})
        platform_config = PlatformConfig(**platform_dict) if isinstance(platform_dict, dict) else PlatformConfig()

        # 3. Manifest / Modules
        manifest_raw = load_yaml(config_dir / "manifest.yaml")
        modules_map = {}
        if isinstance(manifest_raw, dict) and "modules" in manifest_raw:
            for mod_id, mod_data in manifest_raw["modules"].items():
                if isinstance(mod_data, dict):
                    modules_map[mod_id] = ModuleConfigNode(**mod_data)
                else:
                    modules_map[mod_id] = ModuleConfigNode(path=str(mod_data))
        modules_config = ModulesConfig(modules=modules_map)

        # 4. System Store & Dynamic Knowledge
        store_raw = load_yaml(config_dir / "platform" / "system_store.yaml")
        dk_dir = config_dir / "dynamic_knowledge"
        dk_structures = {}
        if dk_dir.exists() and dk_dir.is_dir():
            for dk_file in dk_dir.glob("*.yaml"):
                dk_content = load_yaml(dk_file)
                if isinstance(dk_content, dict):
                    struct_name = dk_file.stem
                    dk_structures[struct_name] = SystemStoreStructure(**dk_content)
        
        store_payload = store_raw.get("payload", {}) if isinstance(store_raw, dict) and "payload" in store_raw else store_raw
        if isinstance(store_payload, dict):
            system_store_config = SystemStoreConfig(
                provider=store_payload.get("provider", "MONGODB"),
                allowed_providers=store_payload.get("allowed_providers"),
                auto_bootstrap_missing_structures=store_payload.get("auto_bootstrap_missing_structures", True),
                migration_mode=store_payload.get("migration_mode"),
                fail_closed_on_drift=store_payload.get("fail_closed_on_drift", True),
                migration_lock_required=store_payload.get("migration_lock_required", True),
                structures=dk_structures
            )
        else:
            system_store_config = SystemStoreConfig(structures=dk_structures)

        # 5. Agents
        agents_dir = config_dir / "agents"
        agents_map = {}
        if agents_dir.exists() and agents_dir.is_dir():
            for agent_file in agents_dir.glob("*.yaml"):
                agent_content = load_yaml(agent_file)
                if isinstance(agent_content, dict):
                    agent_id = agent_content.get("module_id", agent_file.stem)
                    payload = agent_content.get("payload", agent_content)
                    if isinstance(payload, dict):
                        agents_map[agent_id] = AgentConfigNode(**payload)
                    else:
                        agents_map[agent_id] = AgentConfigNode()
        agents_config = AgentsConfig(agents=agents_map)

        # 6. Workflows
        workflows_dir = config_dir / "workflows"
        workflows_map = {}
        if workflows_dir.exists() and workflows_dir.is_dir():
            for wf_file in workflows_dir.glob("*.yaml"):
                wf_content = load_yaml(wf_file)
                if isinstance(wf_content, dict):
                    wf_id = wf_content.get("module_id", wf_file.stem)
                    payload = wf_content.get("payload", wf_content)
                    if isinstance(payload, dict):
                        stages_raw = payload.get("stages", []) if isinstance(payload, dict) else []
                        workflows_map[wf_id] = WorkflowDefinition(stages=stages_raw)
                    else:
                        workflows_map[wf_id] = WorkflowDefinition()
        workflow_config = WorkflowConfig(workflow=workflows_map)

        # 7. Sources
        sources_dir = config_dir / "sources"
        sources_map = {}
        if sources_dir.exists() and sources_dir.is_dir():
            for src_file in sources_dir.glob("*.yaml"):
                src_content = load_yaml(src_file)
                if isinstance(src_content, dict):
                    src_id = src_content.get("module_id", src_file.stem)
                    payload = src_content.get("payload", src_content)
                    if isinstance(payload, dict):
                        sources_map[src_id] = SourceConfigNode(**payload)
                    else:
                        sources_map[src_id] = SourceConfigNode()
        sources_config = SourcesConfig(sources=sources_map)

        # 8. Graph
        graph_dir = config_dir / "graph"
        graph_map = {}
        if graph_dir.exists() and graph_dir.is_dir():
            for g_file in graph_dir.glob("*.yaml"):
                g_content = load_yaml(g_file)
                if isinstance(g_content, dict):
                    g_id = g_content.get("module_id", g_file.stem)
                    graph_map[g_id] = g_content.get("payload", g_content)
        graph_config = GraphConfig(graphs=graph_map)

        # 9. Integrations (policies, mappings, sync)
        integrations_map = {}
        for sub_name in ["policies", "mappings", "sync"]:
            sub_dir = config_dir / sub_name
            if sub_dir.exists() and sub_dir.is_dir():
                for item_file in sub_dir.glob("*.yaml"):
                    item_content = load_yaml(item_file)
                    if isinstance(item_content, dict):
                        item_id = item_content.get("module_id", item_file.stem)
                        payload = item_content.get("payload", item_content)
                        if isinstance(payload, dict):
                            integrations_map[item_id] = IntegrationDefinition(**payload)
                        else:
                            integrations_map[item_id] = IntegrationDefinition()
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
    except Exception as e:
        logger.error(f"Failed to translate legacy configuration: {e}", exc_info=True)
        raise ValueError(f"Failed to translate required canonical configuration domain: {e}") from e
