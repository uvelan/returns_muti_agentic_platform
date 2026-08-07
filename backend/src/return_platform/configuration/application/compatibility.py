"""Legacy compatibility adapter — manifest-driven canonical snapshot construction.

Translation rules (manifest module_type → canonical domain):

    AGENT      → ModulesConfig + AgentsConfig
    WORKFLOW   → ModulesConfig + WorkflowConfig
    SOURCE     → ModulesConfig + SourcesConfig
    GRAPH      → ModulesConfig + GraphConfig.graphs
    MAPPING    → ModulesConfig + GraphConfig.mappings
    SYNC       → ModulesConfig + GraphConfig.sync
    POLICY     → ModulesConfig only (payload preserved in module config)
    PLATFORM   → ModulesConfig + SystemStoreConfig / PlatformConfig
    INTEGRATION→ ModulesConfig + IntegrationsConfig

Invariants:
  - POLICY entries are NEVER mapped to IntegrationsConfig.
  - MAPPING entries are NEVER treated as GraphSchemaNodes.
  - SYNC entries are NEVER treated as GraphSchemaNodes.
  - IntegrationsConfig remains empty unless genuine INTEGRATION modules exist.
  - Dynamic Knowledge schemas (GRAPH modules) → GraphConfig.graphs only.
  - SystemStoreConfig is populated exclusively from PLATFORM modules.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Mapping

from return_platform.configuration.application.loader import (
    ConfigurationLoader,
    LoadedManifestModule,
)
from return_platform.configuration.domain.agents import AgentsConfig, AgentConfigNode
from return_platform.configuration.domain.ai import AiConfig
from return_platform.configuration.domain.features import FeaturesConfig
from return_platform.configuration.domain.graph import (
    GraphConfig,
    GraphMappingConfig,
    GraphSchemaNode,
    GraphSyncConfig,
)
from return_platform.configuration.domain.integrations import IntegrationsConfig, IntegrationDefinition
from return_platform.configuration.domain.modules import ModuleConfigNode, ModuleDependency, ModulesConfig
from return_platform.configuration.domain.platform import PlatformConfig
from return_platform.configuration.domain.release_model import RuntimeSnapshot
from return_platform.configuration.domain.sources import SourcesConfig, SourceConfigNode
from return_platform.configuration.domain.system_store import SystemStoreConfig
from return_platform.configuration.domain.workflow import WorkflowConfig, WorkflowDefinition

logger = logging.getLogger(__name__)


def _parse_dependencies(raw_deps: Any) -> list[ModuleDependency]:
    """Parse a dependency list from a module document."""
    if not isinstance(raw_deps, list):
        return []
    result = []
    for dep in raw_deps:
        if isinstance(dep, dict) and "module_id" in dep:
            result.append(
                ModuleDependency(
                    module_id=dep["module_id"],
                    version_constraint=dep.get("version_constraint"),
                )
            )
    return result


class LegacyCompatibilityAdapter:
    """Translates the existing config directory into a canonical RuntimeSnapshot.

    Uses the manifest as the authoritative source of which files are active.
    Unreferenced YAML files under config/ are never loaded.
    """

    def __init__(self, config_dir: Path) -> None:
        self._loader = ConfigurationLoader(config_dir)
        self._config_dir = config_dir

    def build_canonical_snapshot(self) -> RuntimeSnapshot:
        # 1. Load and validate manifest
        manifest = self._loader.load_manifest()
        loaded_modules = self._loader.load_manifest_entries(manifest)

        # 2. Partition by module type
        agents_map: Dict[str, AgentConfigNode] = {}
        workflows_map: Dict[str, WorkflowDefinition] = {}
        sources_map: Dict[str, SourceConfigNode] = {}
        graphs_map: Dict[str, GraphSchemaNode] = {}
        mappings_map: Dict[str, GraphMappingConfig] = {}
        sync_map: Dict[str, GraphSyncConfig] = {}
        integrations_map: Dict[str, IntegrationDefinition] = {}
        modules_config_map: Dict[str, ModuleConfigNode] = {}

        # Singleton-loaded domain state
        system_store_config: SystemStoreConfig = SystemStoreConfig()
        platform_config: PlatformConfig = PlatformConfig()

        for manifest_id, loaded in loaded_modules.items():
            doc = loaded.document
            module_type = loaded.module_type
            payload = doc.get("payload", {}) or {}

            deps = _parse_dependencies(doc.get("dependencies"))

            # Always record in ModulesConfig to preserve every module
            modules_config_map[manifest_id] = ModuleConfigNode(
                path=loaded.path,
                enabled=True,
                module_id=manifest_id,
                module_type=module_type,
                schema_version=str(doc.get("schema_version", "")) or None,
                configuration_version=str(doc.get("configuration_version", "")) or None,
                owner=doc.get("owner"),
                status=doc.get("status"),
                dependencies=deps or None,
                config=payload if isinstance(payload, dict) else None,
            )

            # Route into canonical domain
            if module_type == "AGENT":
                agent_key = manifest_id.removeprefix("agent.")
                if isinstance(payload, dict):
                    try:
                        agents_map[agent_key] = AgentConfigNode(**payload)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("agent_%s_payload_invalid: %s", manifest_id, exc)

            elif module_type == "WORKFLOW":
                wf_key = manifest_id.removeprefix("workflow.")
                if isinstance(payload, dict):
                    stages_raw = payload.get("stages", [])
                    workflows_map[wf_key] = WorkflowDefinition(
                        context_only_handoffs=payload.get("context_only_handoffs", True),
                        direct_agent_calls_allowed=payload.get("direct_agent_calls_allowed", False),
                        stages=stages_raw if isinstance(stages_raw, list) else [],
                    )

            elif module_type == "SOURCE":
                src_key = manifest_id.removeprefix("source.")
                if isinstance(payload, dict) and "connector_type" in payload:
                    try:
                        sources_map[src_key] = SourceConfigNode(**payload)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("source_%s_payload_invalid: %s", manifest_id, exc)

            elif module_type == "GRAPH":
                graph_key = manifest_id.removeprefix("graph.")
                if isinstance(payload, dict):
                    try:
                        graphs_map[graph_key] = GraphSchemaNode(**payload)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("graph_%s_payload_invalid: %s", manifest_id, exc)

            elif module_type == "MAPPING":
                # Mappings → GraphConfig.mappings — NOT IntegrationsConfig
                mapping_key = manifest_id.removeprefix("mapping.")
                if isinstance(payload, dict):
                    mappings_map[mapping_key] = GraphMappingConfig(
                        canonical_entity=payload.get("canonical_entity"),
                        full_order_id=payload.get("full_order_id"),
                        full_order_line_id=payload.get("full_order_line_id"),
                        allowlisted_order_fields=payload.get("allowlisted_order_fields"),
                        allowlisted_line_fields=payload.get("allowlisted_line_fields"),
                        payload=payload,
                    )

            elif module_type == "SYNC":
                # Sync → GraphConfig.sync — NOT IntegrationsConfig
                sync_key = manifest_id.removeprefix("sync.")
                if isinstance(payload, dict):
                    sync_map[sync_key] = GraphSyncConfig(
                        mode=payload.get("mode"),
                        projection_profile=payload.get("projection_profile"),
                        max_candidates=payload.get("max_candidates"),
                        strong_anchors=payload.get("strong_anchors"),
                        graph_readback_required=payload.get("graph_readback_required", False),
                        payload=payload,
                    )

            elif module_type == "POLICY":
                # Policies are preserved in ModulesConfig only.
                # They must NEVER be mapped to IntegrationsConfig.
                # Payload is already captured in modules_config_map above.
                logger.debug("policy_%s_preserved_in_modules_only", manifest_id)

            elif module_type == "PLATFORM":
                # Platform modules route into SystemStoreConfig or PlatformConfig
                # based on the manifest ID
                if "system_store" in manifest_id:
                    system_store_config = SystemStoreConfig(
                        provider=payload.get("provider", "MONGODB"),
                        allowed_providers=payload.get("allowed_providers"),
                        auto_bootstrap_missing_structures=payload.get(
                            "auto_bootstrap_missing_structures", True
                        ),
                        migration_mode=payload.get("migration_mode"),
                        fail_closed_on_drift=payload.get("fail_closed_on_drift", True),
                        migration_lock_required=payload.get("migration_lock_required", True),
                        structures={},
                    )

            elif module_type == "INTEGRATION":
                # Only real INTEGRATION modules populate IntegrationsConfig
                int_key = manifest_id.removeprefix("integration.")
                integrations_map[int_key] = IntegrationDefinition(
                    module_id=manifest_id,
                    module_type=module_type,
                    schema_version=str(doc.get("schema_version", "")) or None,
                    configuration_version=str(doc.get("configuration_version", "")) or None,
                    owner=doc.get("owner"),
                    status=doc.get("status"),
                    payload=payload if isinstance(payload, dict) else None,
                )

        # 3. Singleton configs — loaded by name, not by manifest module
        ai_raw = self._loader.load_file("ai_gateway.yaml")
        if isinstance(ai_raw, dict) and ai_raw:
            try:
                ai_config = AiConfig(**ai_raw)
            except Exception as exc:  # noqa: BLE001
                logger.warning("ai_gateway_payload_invalid: %s", exc)
                ai_config = AiConfig()
        else:
            ai_config = AiConfig()

        returns_raw = self._loader.load_file("returns/production.yaml")
        features_dict = (
            returns_raw.get("features", {})
            if isinstance(returns_raw, dict)
            else {}
        )
        platform_dict = (
            returns_raw.get("platform", {})
            if isinstance(returns_raw, dict)
            else {}
        )
        features_config = FeaturesConfig(
            flags=features_dict if isinstance(features_dict, dict) else {}
        )
        if isinstance(platform_dict, dict) and platform_dict:
            try:
                platform_config = PlatformConfig(**platform_dict)
            except Exception as exc:  # noqa: BLE001
                logger.warning("returns_production_platform_invalid: %s", exc)

        # 4. Also load dynamic knowledge schemas into GraphConfig.graphs
        #    These are not in the manifest but represent the graph-knowledge layer.
        dk_dir = self._config_dir / "dynamic_knowledge"
        if dk_dir.exists():
            for yaml_file in sorted(dk_dir.glob("*.yaml")):
                try:
                    dk_content = self._loader.load_file(
                        f"dynamic_knowledge/{yaml_file.name}"
                    )
                    if isinstance(dk_content, dict):
                        dk_id = yaml_file.stem
                        graphs_map[dk_id] = GraphSchemaNode(
                            schema_name=dk_id,
                            configuration_release_id=dk_content.get(
                                "configuration_release_id"
                            ),
                            release_status=dk_content.get("release_status"),
                            approved_by=dk_content.get("approved_by"),
                            approved_at=dk_content.get("approved_at"),
                            schema_version=str(
                                dk_content.get("schema_version", "")
                            ) or None,
                            policy_version=str(
                                dk_content.get("policy_version", "")
                            ) or None,
                            prompt_version=str(
                                dk_content.get("prompt_version", "")
                            ) or None,
                            compiler_version=str(
                                dk_content.get("compiler_version", "")
                            ) or None,
                            runtime_mode=dk_content.get("runtime_mode"),
                            sources=dk_content.get("sources"),
                            entities=dk_content.get("entities"),
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "dynamic_knowledge_%s_load_failed: %s", yaml_file.stem, exc
                    )

        return RuntimeSnapshot(
            platform=platform_config,
            system_store=system_store_config,
            modules=ModulesConfig(
                schema_version=manifest.schema_version,
                release_id=manifest.release_id,
                status=manifest.status.value,
                modules=modules_config_map,
            ),
            agents=AgentsConfig(agents=agents_map),
            workflow=WorkflowConfig(workflow=workflows_map),
            sources=SourcesConfig(sources=sources_map),
            integrations=IntegrationsConfig(integrations=integrations_map),
            graph=GraphConfig(
                graphs=graphs_map,
                mappings=mappings_map,
                sync=sync_map,
            ),
            ai=ai_config,
            features=features_config,
        )


def build_snapshot_from_legacy_configs(config_dir: Path) -> RuntimeSnapshot:
    """Convenience function for building a snapshot from the canonical config directory."""
    adapter = LegacyCompatibilityAdapter(config_dir)
    return adapter.build_canonical_snapshot()
