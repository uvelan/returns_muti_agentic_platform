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
  - Translation is FAIL-CLOSED: malformed authoritative modules raise
    ConfigurationValidationError; warnings never substitute for errors.
  - POLICY entries are NEVER mapped to IntegrationsConfig.
  - MAPPING entries are NEVER treated as GraphSchemaNodes.
  - SYNC entries are NEVER treated as GraphSchemaNodes.
  - IntegrationsConfig remains empty unless genuine INTEGRATION modules exist.
  - Dynamic Knowledge schemas are loaded ONLY when declared in the manifest
    as GRAPH modules; directory globbing is forbidden.
  - SystemStoreConfig is populated exclusively from PLATFORM modules.
  - Singleton compatibility files (ai_gateway.yaml, returns/production.yaml)
    are loaded by explicit contract — NOT by directory discovery.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from return_platform.configuration.application.loader import (
    ConfigurationLoader,
)
from return_platform.configuration.domain.agents import AgentConfigNode, AgentsConfig
from return_platform.configuration.domain.ai import AiConfig
from return_platform.configuration.domain.errors import ConfigurationValidationError
from return_platform.configuration.domain.features import FeaturesConfig
from return_platform.configuration.domain.graph import (
    GraphConfig,
    GraphMappingConfig,
    GraphSchemaNode,
    GraphSyncConfig,
)
from return_platform.configuration.domain.integrations import (
    IntegrationDefinition,
    IntegrationsConfig,
)
from return_platform.configuration.domain.modules import (
    ModuleConfigNode,
    ModuleDependency,
    ModulesConfig,
)
from return_platform.configuration.domain.platform import PlatformConfig
from return_platform.configuration.domain.release_model import RuntimeSnapshot
from return_platform.configuration.domain.sources import SourceConfigNode, SourcesConfig
from return_platform.configuration.domain.system_store import (
    SystemStoreConfig,
    SystemStoreStructure,
)
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
    Unreferenced YAML files under config/ are NEVER loaded.
    Translation is FAIL-CLOSED — invalid module payloads raise immediately.
    """

    def __init__(self, config_dir: Path) -> None:
        self._loader = ConfigurationLoader(config_dir)
        self._config_dir = config_dir

    def build_canonical_snapshot(self) -> RuntimeSnapshot:
        # 1. Load and validate manifest
        manifest = self._loader.load_manifest()
        loaded_modules = self._loader.load_manifest_entries(manifest)

        # 2. Partition by module type
        agents_map: dict[str, AgentConfigNode] = {}
        workflows_map: dict[str, WorkflowDefinition] = {}
        sources_map: dict[str, SourceConfigNode] = {}
        graphs_map: dict[str, GraphSchemaNode] = {}
        mappings_map: dict[str, GraphMappingConfig] = {}
        sync_map: dict[str, GraphSyncConfig] = {}
        integrations_map: dict[str, IntegrationDefinition] = {}
        modules_config_map: dict[str, ModuleConfigNode] = {}

        # Singleton-loaded domain state
        system_store_config: SystemStoreConfig = SystemStoreConfig()
        platform_config: PlatformConfig = PlatformConfig()

        for manifest_id, loaded in loaded_modules.items():
            doc = loaded.document
            module_type = loaded.module_type
            payload = doc.get("payload", {}) or {}

            deps = _parse_dependencies(doc.get("dependencies"))

            # Always record in ModulesConfig to preserve every module.
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

            # Route into canonical domain — FAIL CLOSED on parse errors.
            if module_type == "AGENT":
                agent_key = manifest_id.removeprefix("agent.")
                if not isinstance(payload, dict):
                    raise ConfigurationValidationError(
                        f"AGENT module '{manifest_id}' payload is not a mapping"
                    )
                try:
                    agents_map[agent_key] = AgentConfigNode(**payload)
                except (ValidationError, TypeError) as exc:
                    raise ConfigurationValidationError(
                        f"AGENT module '{manifest_id}' payload failed validation: {exc}"
                    ) from exc

            elif module_type == "WORKFLOW":
                wf_key = manifest_id.removeprefix("workflow.")
                if not isinstance(payload, dict):
                    raise ConfigurationValidationError(
                        f"WORKFLOW module '{manifest_id}' payload is not a mapping"
                    )
                stages_raw = payload.get("stages", [])
                try:
                    workflows_map[wf_key] = WorkflowDefinition(
                        context_only_handoffs=payload.get("context_only_handoffs", True),
                        direct_agent_calls_allowed=payload.get("direct_agent_calls_allowed", False),
                        stages=stages_raw if isinstance(stages_raw, list) else [],
                    )
                except (ValidationError, TypeError) as exc:
                    raise ConfigurationValidationError(
                        f"WORKFLOW module '{manifest_id}' payload failed validation: {exc}"
                    ) from exc

            elif module_type == "SOURCE":
                src_key = manifest_id.removeprefix("source.")
                if not isinstance(payload, dict):
                    raise ConfigurationValidationError(
                        f"SOURCE module '{manifest_id}' payload is not a mapping"
                    )
                if "connector_type" not in payload:
                    raise ConfigurationValidationError(
                        f"SOURCE module '{manifest_id}' payload is missing required "
                        f"'connector_type' field"
                    )
                try:
                    sources_map[src_key] = SourceConfigNode(**payload)
                except (ValidationError, TypeError) as exc:
                    raise ConfigurationValidationError(
                        f"SOURCE module '{manifest_id}' payload failed validation: {exc}"
                    ) from exc

            elif module_type == "GRAPH":
                # GRAPH modules are loaded from the manifest only — no globbing.
                # Dynamic Knowledge schemas must be declared as GRAPH modules in manifest.yaml.
                graph_key = manifest_id.removeprefix("graph.")
                if not isinstance(payload, dict):
                    raise ConfigurationValidationError(
                        f"GRAPH module '{manifest_id}' payload is not a mapping"
                    )
                try:
                    graphs_map[graph_key] = GraphSchemaNode(**payload)
                except (ValidationError, TypeError) as exc:
                    raise ConfigurationValidationError(
                        f"GRAPH module '{manifest_id}' payload failed validation: {exc}"
                    ) from exc

            elif module_type == "MAPPING":
                # Mappings → GraphConfig.mappings — NOT IntegrationsConfig
                mapping_key = manifest_id.removeprefix("mapping.")
                if not isinstance(payload, dict):
                    raise ConfigurationValidationError(
                        f"MAPPING module '{manifest_id}' payload is not a mapping"
                    )
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
                if not isinstance(payload, dict):
                    raise ConfigurationValidationError(
                        f"SYNC module '{manifest_id}' payload is not a mapping"
                    )
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
                    if not isinstance(payload, dict):
                        raise ConfigurationValidationError(
                            f"PLATFORM module '{manifest_id}' payload is not a mapping"
                        )
                    structures_raw = payload.get("structures", {}) or {}
                    if not isinstance(structures_raw, dict):
                        raise ConfigurationValidationError(
                            f"PLATFORM module '{manifest_id}' 'structures' is not a mapping"
                        )
                    structures: dict[str, SystemStoreStructure] = {}
                    for logical_name, struct_payload in structures_raw.items():
                        if not isinstance(struct_payload, dict):
                            raise ConfigurationValidationError(
                                f"PLATFORM module '{manifest_id}' structure "
                                f"'{logical_name}' payload is not a mapping"
                            )
                        try:
                            structures[logical_name] = SystemStoreStructure(**struct_payload)
                        except (ValidationError, TypeError) as exc:
                            raise ConfigurationValidationError(
                                f"PLATFORM module '{manifest_id}' structure "
                                f"'{logical_name}' failed validation: {exc}"
                            ) from exc
                    system_store_config = SystemStoreConfig(
                        provider=payload.get("provider", "MONGODB"),
                        allowed_providers=payload.get("allowed_providers"),
                        auto_bootstrap_missing_structures=payload.get(
                            "auto_bootstrap_missing_structures", True
                        ),
                        migration_mode=payload.get("migration_mode"),
                        fail_closed_on_drift=payload.get("fail_closed_on_drift", True),
                        migration_lock_required=payload.get("migration_lock_required", True),
                        structures=structures,
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

        # 3. Singleton configs — loaded by EXPLICIT NAME, not by directory discovery.
        #    Only these two files are allowlisted as singleton compatibility contracts.
        ai_raw = self._loader.load_file("ai_gateway.yaml")
        if isinstance(ai_raw, dict) and ai_raw:
            try:
                ai_config = AiConfig(**ai_raw)
            except (ValidationError, TypeError) as exc:
                raise ConfigurationValidationError(
                    f"Singleton 'ai_gateway.yaml' failed validation: {exc}"
                ) from exc
        else:
            ai_config = AiConfig()

        returns_raw = self._loader.load_file("returns/production.yaml")
        features_dict = returns_raw.get("features", {}) if isinstance(returns_raw, dict) else {}
        platform_dict = returns_raw.get("platform", {}) if isinstance(returns_raw, dict) else {}
        features_config = FeaturesConfig(
            flags=features_dict if isinstance(features_dict, dict) else {}
        )
        if isinstance(platform_dict, dict) and platform_dict:
            try:
                platform_config = PlatformConfig(**platform_dict)
            except (ValidationError, TypeError) as exc:
                raise ConfigurationValidationError(
                    f"Singleton 'returns/production.yaml' platform section failed validation: {exc}"
                ) from exc

        # NOTE: Dynamic Knowledge schemas are NO LONGER globbed from dynamic_knowledge/*.
        # They must be declared as GRAPH modules in manifest.yaml.
        # If the project needs them, add explicit entries such as:
        #   modules:
        #     graph.active-schema.return-order:
        #       path: dynamic_knowledge/active-schema.return-order.yaml

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
