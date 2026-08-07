import yaml
from pathlib import Path
from pydantic import ValidationError
import logging

from return_platform.configuration.domain.release import RuntimeSnapshot
from return_platform.configuration.domain.platform import PlatformConfig
from return_platform.configuration.domain.ai import AiConfig
from return_platform.configuration.domain.modules import ModulesConfig
from return_platform.configuration.domain.agents import AgentsConfig
from return_platform.configuration.domain.workflow import WorkflowConfig
from return_platform.configuration.domain.sources import SourcesConfig
from return_platform.configuration.domain.integrations import IntegrationsConfig
from return_platform.configuration.domain.graph import GraphConfig
from return_platform.configuration.domain.system_store import SystemStoreConfig
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
        # AI Gateway
        ai_raw = load_yaml(config_dir / "ai_gateway.yaml")
        ai_config = AiConfig(**ai_raw) if ai_raw else AiConfig()
        
        # Returns Production
        returns_raw = load_yaml(config_dir / "returns" / "production.yaml")
        features_config = FeaturesConfig(**returns_raw.get("features", {}))
        platform_config = PlatformConfig(**returns_raw.get("platform", {}))
        
        # V2 manifest
        manifest_raw = load_yaml(config_dir / "manifest.yaml")
        modules_config = ModulesConfig(**manifest_raw) if manifest_raw else ModulesConfig()
        
        # System Store
        store_raw = load_yaml(config_dir / "platform" / "system_store.yaml")
        system_store_config = SystemStoreConfig(**store_raw) if store_raw else SystemStoreConfig(provider="mongodb", structures={})
        
        # Other domains (load dynamically or mock if empty for now, but strict typing is enforced)
        agents_config = AgentsConfig()
        workflow_config = WorkflowConfig()
        sources_config = SourcesConfig()
        integrations_config = IntegrationsConfig()
        graph_config = GraphConfig()
        
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
    except ValidationError as e:
        logger.error(f"Failed to translate legacy configuration: {e}")
        raise ValueError("Failed to translate required canonical configuration domain") from e
