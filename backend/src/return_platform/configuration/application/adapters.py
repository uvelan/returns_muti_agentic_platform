import yaml
from pathlib import Path
from return_platform.configuration.domain.release import RuntimeSnapshot
from return_platform.configuration.domain.platform import PlatformConfig
from return_platform.configuration.domain.ai import AiConfig
from return_platform.configuration.domain.modules import ModulesConfig
from return_platform.configuration.domain.agents import AgentsConfig
from return_platform.configuration.domain.workflow import WorkflowConfig
from return_platform.configuration.domain.sources import SourcesConfig
from return_platform.configuration.domain.integrations import IntegrationsConfig
from return_platform.configuration.domain.graph import GraphConfig

def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r") as f:
        return yaml.safe_load(f) or {}

def build_snapshot_from_legacy_configs(config_dir: Path) -> RuntimeSnapshot:
    """Compatibility adapter mapping old fragmented config into the new canonical model."""
    
    # AI Gateway
    ai_raw = load_yaml(config_dir / "ai_gateway.yaml")
    ai_config = AiConfig(**ai_raw) if ai_raw else None
    
    # Returns Production
    returns_raw = load_yaml(config_dir / "returns" / "production.yaml")
    
    # V2 manifest
    manifest_raw = load_yaml(config_dir / "manifest.yaml")
    modules_config = ModulesConfig(**manifest_raw) if manifest_raw else None
    
    return RuntimeSnapshot(
        platform=None,
        system_store=None,
        modules=modules_config,
        agents=None,
        workflow=None,
        sources=None,
        integrations=None,
        graph=None,
        ai=ai_config,
        features=None
    )
