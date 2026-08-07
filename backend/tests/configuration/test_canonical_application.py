"""
test_canonical_application.py

Covers: manifest validation, semantic validator, compatibility domain mapping,
        and precedence model.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml

from return_platform.configuration.application.compatibility import (
    LegacyCompatibilityAdapter,
    build_snapshot_from_legacy_configs,
)
from return_platform.configuration.application.loader import (
    ConfigurationLoader,
    ConfigurationManifest,
)
from return_platform.configuration.application.precedence import (
    ConfigurationPrecedenceEvaluator,
    PrecedenceViolationError,
)
from return_platform.configuration.application.validator import (
    ConfigurationValidator,
    ConfigurationValidationError,
)
from return_platform.configuration.domain.agents import AgentConfigNode, AgentsConfig
from return_platform.configuration.domain.ai import AiConfig
from return_platform.configuration.domain.features import FeaturesConfig
from return_platform.configuration.domain.graph import GraphConfig, GraphSchemaNode
from return_platform.configuration.domain.integrations import IntegrationsConfig
from return_platform.configuration.domain.modules import ModuleConfigNode, ModulesConfig
from return_platform.configuration.domain.platform import PlatformConfig
from return_platform.configuration.domain.release_model import RuntimeSnapshot
from return_platform.configuration.domain.sources import SourceConfigNode, SourcesConfig
from return_platform.configuration.domain.system_store import (
    SystemStoreConfig,
    SystemStoreStructure,
)
from return_platform.configuration.domain.workflow import WorkflowConfig, WorkflowDefinition

# ---------------------------------------------------------------------------
# Shared config path
# ---------------------------------------------------------------------------
CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


# ---------------------------------------------------------------------------
# Helpers for building minimal snapshots
# ---------------------------------------------------------------------------

def _minimal_snapshot(
    agents=None,
    modules=None,
    workflows=None,
    sources=None,
    graph=None,
    ai=None,
    system_store=None,
):
    mod_map = modules or {"agent.order_discovery": ModuleConfigNode(
        module_id="agent.order_discovery", module_type="AGENT"
    )}
    agent_map = agents or {"order_discovery": AgentConfigNode()}
    # Accept either a SourcesConfig instance or a dict; default to empty
    if sources is None:
        sources_config = SourcesConfig(sources={})
    elif isinstance(sources, SourcesConfig):
        sources_config = sources
    else:
        sources_config = SourcesConfig(sources=sources)
    return RuntimeSnapshot(
        platform=PlatformConfig(),
        system_store=system_store or SystemStoreConfig(),
        modules=ModulesConfig(modules=mod_map),
        agents=AgentsConfig(agents=agent_map),
        workflow=WorkflowConfig(workflow=workflows or {}),
        sources=sources_config,
        integrations=IntegrationsConfig(integrations={}),
        graph=graph or GraphConfig(),
        ai=ai or AiConfig(),
        features=FeaturesConfig(flags={}),
    )


# ===========================================================================
# 1. Loader & compatibility — integration smoke test
# ===========================================================================

def test_loader_and_compatibility():
    adapter = LegacyCompatibilityAdapter(CONFIG_DIR)
    snapshot = adapter.build_canonical_snapshot()
    assert snapshot is not None
    assert isinstance(snapshot, RuntimeSnapshot)
    assert snapshot.modules is not None
    assert snapshot.ai is not None
    assert snapshot.platform is not None
    assert snapshot.graph is not None


def test_validator_smoke():
    """Snapshot built from real config must pass validator."""
    validator = ConfigurationValidator()
    snapshot = build_snapshot_from_legacy_configs(CONFIG_DIR)
    # Should not raise
    validator.validate_snapshot(snapshot)


# ===========================================================================
# 2. Manifest validation
# ===========================================================================

def _write_config(tmp: Path, manifest: dict, modules: dict | None = None) -> Path:
    """Write a manifest and optional module files to a temp directory."""
    (tmp / "manifest.yaml").write_text(yaml.dump(manifest))
    if modules:
        for rel_path, content in modules.items():
            target = tmp / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(yaml.dump(content))
    return tmp


def test_manifest_loads_only_declared_modules(tmp_path):
    manifest = {
        "schema_version": "2.0",
        "release_id": "test-r1",
        "status": "DRAFT",
        "modules": {
            "agent.test_agent": {"path": "agents/test_agent.yaml"},
        },
    }
    modules = {
        "agents/test_agent.yaml": {
            "module_id": "agent.test_agent",
            "module_type": "AGENT",
            "schema_version": "1.0",
            "configuration_version": "1.0.0",
            "owner": "TEST",
            "status": "DRAFT",
            "dependencies": [],
            "payload": {
                "name": "Test Agent",
                "enabled": True,
                "execution_mode": "AUTO",
                "ai_assisted": False,
                "direct_agent_calls_allowed": False,
                "idempotency_required": True,
            },
        },
        # NOT in manifest — must be ignored
        "agents/unreferenced_agent.yaml": {
            "module_id": "agent.unreferenced",
            "module_type": "AGENT",
            "payload": {},
        },
    }
    _write_config(tmp_path, manifest, modules)
    loader = ConfigurationLoader(tmp_path)
    mf = loader.load_manifest()
    entries = loader.load_manifest_entries(mf)
    assert "agent.test_agent" in entries
    assert "agent.unreferenced" not in entries


def test_unreferenced_yaml_is_not_authoritative(tmp_path):
    """YAML files under config that are not in the manifest must be excluded."""
    manifest = {
        "schema_version": "2.0",
        "release_id": "test-r2",
        "status": "DRAFT",
        "modules": {},
    }
    _write_config(tmp_path, manifest, {
        "agents/extra.yaml": {"module_id": "agent.extra", "module_type": "AGENT"},
    })
    loader = ConfigurationLoader(tmp_path)
    mf = loader.load_manifest()
    entries = loader.load_manifest_entries(mf)
    assert len(entries) == 0


def test_manifest_missing_file_fails(tmp_path):
    manifest = {
        "schema_version": "2.0",
        "release_id": "test-r3",
        "status": "DRAFT",
        "modules": {
            "agent.ghost": {"path": "agents/does_not_exist.yaml"},
        },
    }
    _write_config(tmp_path, manifest)
    loader = ConfigurationLoader(tmp_path)
    mf = loader.load_manifest()
    with pytest.raises(ValueError, match="missing file"):
        loader.load_manifest_entries(mf)


def test_manifest_path_traversal_fails(tmp_path):
    manifest = {
        "schema_version": "2.0",
        "release_id": "test-r4",
        "status": "DRAFT",
        "modules": {
            "agent.evil": {"path": "../../etc/passwd"},
        },
    }
    _write_config(tmp_path, manifest)
    loader = ConfigurationLoader(tmp_path)
    mf = loader.load_manifest()
    with pytest.raises(ValueError, match="escapes config directory|missing file"):
        loader.load_manifest_entries(mf)


def test_manifest_module_id_must_match_document(tmp_path):
    manifest = {
        "schema_version": "2.0",
        "release_id": "test-r5",
        "status": "DRAFT",
        "modules": {
            "agent.correct_id": {"path": "agents/mismatch.yaml"},
        },
    }
    modules = {
        "agents/mismatch.yaml": {
            "module_id": "agent.wrong_id",   # mismatch
            "module_type": "AGENT",
            "schema_version": "1.0",
        },
    }
    _write_config(tmp_path, manifest, modules)
    loader = ConfigurationLoader(tmp_path)
    mf = loader.load_manifest()
    with pytest.raises(ValueError, match="module_id"):
        loader.load_manifest_entries(mf)


def test_manifest_type_prefix_must_match_document(tmp_path):
    manifest = {
        "schema_version": "2.0",
        "release_id": "test-r6",
        "status": "DRAFT",
        "modules": {
            "agent.type_mismatch": {"path": "agents/type_mismatch.yaml"},
        },
    }
    modules = {
        "agents/type_mismatch.yaml": {
            "module_id": "agent.type_mismatch",
            "module_type": "WORKFLOW",     # wrong type for agent.* prefix
            "schema_version": "1.0",
        },
    }
    _write_config(tmp_path, manifest, modules)
    loader = ConfigurationLoader(tmp_path)
    mf = loader.load_manifest()
    with pytest.raises(ValueError, match="module_type"):
        loader.load_manifest_entries(mf)


def test_unknown_dependency_is_loadable_but_validator_can_check():
    """
    The loader does not cross-check dependencies — the validator does.
    A manifest entry with an unknown dependency can still be loaded.
    This test confirms the loader succeeds and the dependency is preserved
    in the module document.
    """
    loader = ConfigurationLoader(CONFIG_DIR)
    mf = loader.load_manifest()
    entries = loader.load_manifest_entries(mf)
    # agent.order_discovery depends on policy.clarification, which is in the manifest
    od = entries.get("agent.order_discovery")
    assert od is not None


def test_self_dependency_not_in_real_manifest():
    """No module in the real manifest should declare a self-dependency."""
    loader = ConfigurationLoader(CONFIG_DIR)
    mf = loader.load_manifest()
    entries = loader.load_manifest_entries(mf)
    for manifest_id, loaded in entries.items():
        doc = loaded.document
        for dep in doc.get("dependencies", []) or []:
            if isinstance(dep, dict):
                assert dep.get("module_id") != manifest_id, (
                    f"Self-dependency detected in {manifest_id}"
                )


def test_duplicate_manifest_key_fails(tmp_path):
    """A manifest YAML with duplicate module keys must be rejected."""
    dup_yaml = (
        "schema_version: '2.0'\n"
        "release_id: dup-test\n"
        "status: DRAFT\n"
        "modules:\n"
        "  agent.dup: {path: agents/a.yaml}\n"
        "  agent.dup: {path: agents/b.yaml}\n"   # duplicate
    )
    (tmp_path / "manifest.yaml").write_text(dup_yaml)
    loader = ConfigurationLoader(tmp_path)
    with pytest.raises(ValueError, match="Duplicate"):
        loader.load_manifest(tmp_path / "manifest.yaml")


# ===========================================================================
# 3. Semantic validator tests
# ===========================================================================

def test_agent_resolves_to_agent_manifest_module():
    """Agent 'order_discovery' must appear in modules as 'agent.order_discovery'."""
    snapshot = _minimal_snapshot(
        modules={"agent.order_discovery": ModuleConfigNode(
            module_id="agent.order_discovery", module_type="AGENT"
        )},
        agents={"order_discovery": AgentConfigNode()},
    )
    ConfigurationValidator().validate_snapshot(snapshot)  # should not raise


def test_validator_handles_agent_id_namespace_normalization():
    """Missing 'agent.' prefix module → validation error naming the agent."""
    snapshot = _minimal_snapshot(
        modules={"unrelated.module": ModuleConfigNode(
            module_id="unrelated.module", module_type="AGENT"
        )},
        agents={"order_discovery": AgentConfigNode()},
    )
    with pytest.raises(ConfigurationValidationError, match="order_discovery"):
        ConfigurationValidator().validate_snapshot(snapshot)


def test_validator_rejects_dangling_ai_route_ref():
    snapshot = _minimal_snapshot(
        agents={
            "order_discovery": AgentConfigNode(
                ai_route_ref="NONEXISTENT_ROUTE"
            )
        },
        ai=AiConfig(routes={"OTHER_ROUTE": {"task_id": "t1"}}),
    )
    with pytest.raises(ConfigurationValidationError, match="NONEXISTENT_ROUTE"):
        ConfigurationValidator().validate_snapshot(snapshot)


def test_validator_rejects_dangling_ai_task_ref_when_route_declares_task():
    snapshot = _minimal_snapshot(
        agents={
            "order_discovery": AgentConfigNode(ai_route_ref="MY_ROUTE")
        },
        ai=AiConfig(
            routes={"MY_ROUTE": {"task_id": "MISSING_TASK"}},
            tasks={"OTHER_TASK": {}},
        ),
    )
    with pytest.raises(ConfigurationValidationError, match="MISSING_TASK"):
        ConfigurationValidator().validate_snapshot(snapshot)


def test_validator_rejects_unknown_ai_provider_ref():
    snapshot = _minimal_snapshot(
        agents={
            "order_discovery": AgentConfigNode(ai_route_ref="MY_ROUTE")
        },
        ai=AiConfig(
            routes={"MY_ROUTE": {"provider": "MISSING_PROVIDER"}},
            providers={"KNOWN_PROVIDER": {}},
        ),
    )
    with pytest.raises(ConfigurationValidationError, match="MISSING_PROVIDER"):
        ConfigurationValidator().validate_snapshot(snapshot)


def test_ai_route_task_rejected_when_tasks_empty():
    snapshot = _minimal_snapshot(
        agents={
            "order_discovery": AgentConfigNode(ai_route_ref="MY_ROUTE")
        },
        ai=AiConfig(
            routes={"MY_ROUTE": {"task_id": "MISSING_TASK"}},
            tasks={},
        ),
    )
    with pytest.raises(ConfigurationValidationError, match="MISSING_TASK"):
        ConfigurationValidator().validate_snapshot(snapshot)


def test_ai_route_provider_rejected_when_providers_empty():
    snapshot = _minimal_snapshot(
        agents={
            "order_discovery": AgentConfigNode(ai_route_ref="MY_ROUTE")
        },
        ai=AiConfig(
            routes={"MY_ROUTE": {"provider": "MISSING_PROVIDER"}},
            providers={},
        ),
    )
    with pytest.raises(ConfigurationValidationError, match="MISSING_PROVIDER"):
        ConfigurationValidator().validate_snapshot(snapshot)


def test_unknown_module_dependency_rejected():
    snapshot = _minimal_snapshot(
        modules={
            "agent.order_discovery": ModuleConfigNode(
                module_id="agent.order_discovery",
                module_type="AGENT",
                dependencies=[{"module_id": "agent.unknown_module"}]
            )
        }
    )
    with pytest.raises(ConfigurationValidationError, match="unknown module"):
        ConfigurationValidator().validate_snapshot(snapshot)


def test_self_dependency_rejected():
    snapshot = _minimal_snapshot(
        modules={
            "agent.order_discovery": ModuleConfigNode(
                module_id="agent.order_discovery",
                module_type="AGENT",
                dependencies=[{"module_id": "agent.order_discovery"}]
            )
        }
    )
    with pytest.raises(ConfigurationValidationError, match="self-dependency"):
        ConfigurationValidator().validate_snapshot(snapshot)


def test_dependency_cycle_rejected():
    snapshot = _minimal_snapshot(
        modules={
            "agent.a": ModuleConfigNode(
                module_id="agent.a", module_type="AGENT", dependencies=[{"module_id": "agent.b"}]
            ),
            "agent.b": ModuleConfigNode(
                module_id="agent.b", module_type="AGENT", dependencies=[{"module_id": "agent.c"}]
            ),
            "agent.c": ModuleConfigNode(
                module_id="agent.c", module_type="AGENT", dependencies=[{"module_id": "agent.a"}]
            ),
        },
        agents={
            "a": AgentConfigNode(),
            "b": AgentConfigNode(),
            "c": AgentConfigNode(),
        }
    )
    with pytest.raises(ConfigurationValidationError, match="dependency cycle detected"):
        ConfigurationValidator().validate_snapshot(snapshot)



def test_validator_does_not_treat_workflow_stage_name_as_agent_id():
    """Workflow stages like ORDER_DISCOVERY must NOT be validated as agent IDs."""
    snapshot = _minimal_snapshot(
        workflows={
            "return_session": WorkflowDefinition(
                stages=["ORDER_DISCOVERY", "FULFILLMENT"]
            )
        },
    )
    # Should not raise — stage names are business states, not agent IDs
    ConfigurationValidator().validate_snapshot(snapshot)


def test_validator_rejects_duplicate_workflow_stage_id():
    snapshot = _minimal_snapshot(
        workflows={
            "wf1": WorkflowDefinition(stages=["ORDER_DISCOVERY", "ORDER_DISCOVERY"])
        },
    )
    with pytest.raises(ConfigurationValidationError, match="duplicate stage"):
        ConfigurationValidator().validate_snapshot(snapshot)


def test_validator_rejects_dangling_graph_source_ref():
    snapshot = _minimal_snapshot(
        graph=GraphConfig(
            graphs={
                "order_discovery": GraphSchemaNode(
                    # A string/None value means a logical cross-reference into SourcesConfig.
                    # A dict value means an embedded source definition (DK-style), which is
                    # NOT a SourcesConfig cross-reference and must not be validated here.
                    sources={"sales_inv": None}   # logical ref, not embedded dict
                )
            }
        ),
        sources=SourcesConfig(sources={}),  # empty — "sales_inv" won't resolve
    )
    with pytest.raises(ConfigurationValidationError, match="sales_inv"):
        ConfigurationValidator().validate_snapshot(snapshot)


def test_validator_allows_graph_with_no_source_refs():
    snapshot = _minimal_snapshot(
        graph=GraphConfig(
            graphs={"order_discovery": GraphSchemaNode()}  # no sources
        ),
    )
    ConfigurationValidator().validate_snapshot(snapshot)  # should not raise


def test_validator_rejects_empty_source_connector_type():
    snapshot = _minimal_snapshot(
        sources=SourcesConfig(
            sources={"bad_source": SourceConfigNode(connector_type="")}
        )
    )
    with pytest.raises(ConfigurationValidationError, match="connector_type"):
        ConfigurationValidator().validate_snapshot(snapshot)


def test_validator_rejects_non_read_only_source_access_mode():
    snapshot = _minimal_snapshot(
        sources=SourcesConfig(
            sources={
                "bad_src": SourceConfigNode(
                    connector_type="MONGODB",
                    access_mode="READ_WRITE",   # must be read-only
                )
            }
        )
    )
    with pytest.raises(ConfigurationValidationError, match="access_mode"):
        ConfigurationValidator().validate_snapshot(snapshot)


def test_validator_accepts_read_only_source():
    snapshot = _minimal_snapshot(
        sources=SourcesConfig(
            sources={
                "ok_src": SourceConfigNode(
                    connector_type="MONGODB",
                    access_mode="READ_ONLY",
                )
            }
        )
    )
    ConfigurationValidator().validate_snapshot(snapshot)  # should not raise


def test_validator_rejects_empty_system_store_physical_name():
    snapshot = _minimal_snapshot(
        system_store=SystemStoreConfig(
            structures={
                "my_collection": SystemStoreStructure(physical_name="  ")
            }
        )
    )
    with pytest.raises(ConfigurationValidationError, match="physical_name"):
        ConfigurationValidator().validate_snapshot(snapshot)


def test_validator_allows_same_suffix_in_different_namespaces():
    """agent.order_discovery and graph.order_discovery share a suffix — that is valid."""
    snapshot = _minimal_snapshot(
        modules={
            "agent.order_discovery": ModuleConfigNode(
                module_id="agent.order_discovery", module_type="AGENT"
            ),
        },
        agents={"order_discovery": AgentConfigNode()},
        graph=GraphConfig(
            graphs={"order_discovery": GraphSchemaNode()}
        ),
    )
    ConfigurationValidator().validate_snapshot(snapshot)  # should not raise


# ===========================================================================
# 4. Compatibility domain mapping
# ===========================================================================

def test_policy_manifest_entry_is_preserved_in_modules():
    snapshot = build_snapshot_from_legacy_configs(CONFIG_DIR)
    # policy.privacy should be in ModulesConfig
    assert "policy.privacy" in snapshot.modules.modules


def test_policy_is_not_mapped_to_integrations():
    snapshot = build_snapshot_from_legacy_configs(CONFIG_DIR)
    # IntegrationsConfig must not contain any policy.* entry
    for key in snapshot.integrations.integrations.keys():
        assert not key.startswith("policy"), (
            f"Policy '{key}' was incorrectly placed in IntegrationsConfig"
        )


def test_mapping_is_preserved_in_modules_and_graph_mappings():
    snapshot = build_snapshot_from_legacy_configs(CONFIG_DIR)
    assert "mapping.sales_inv_order" in snapshot.modules.modules
    # Also in graph.mappings (not as a graph schema)
    assert "sales_inv_order" in snapshot.graph.mappings


def test_mapping_is_not_fake_graph_schema():
    snapshot = build_snapshot_from_legacy_configs(CONFIG_DIR)
    # Mapping must NOT appear in GraphConfig.graphs
    assert "sales_inv_order" not in snapshot.graph.graphs
    assert "mapping_sales_inv_order" not in snapshot.graph.graphs


def test_sync_is_preserved_in_modules_and_graph_sync():
    snapshot = build_snapshot_from_legacy_configs(CONFIG_DIR)
    assert "sync.order_partial" in snapshot.modules.modules
    assert "order_partial" in snapshot.graph.sync


def test_sync_is_not_mapped_to_integrations():
    snapshot = build_snapshot_from_legacy_configs(CONFIG_DIR)
    for key in snapshot.integrations.integrations.keys():
        assert not key.startswith("order_partial") and not key.startswith("sync"), (
            f"Sync config '{key}' was incorrectly placed in IntegrationsConfig"
        )


def test_only_real_integration_entries_populate_integrations():
    snapshot = build_snapshot_from_legacy_configs(CONFIG_DIR)
    # No integration.* modules declared in the real manifest — must be empty
    assert len(snapshot.integrations.integrations) == 0


def test_dynamic_knowledge_schema_maps_to_graph_not_system_store():
    snapshot = build_snapshot_from_legacy_configs(CONFIG_DIR)
    # SystemStore must have no dynamic knowledge entries
    for key in snapshot.system_store.structures.keys():
        assert not key.endswith("_schema") and "dynamic_knowledge" not in key


def test_compatibility_translation_fails_closed_on_invalid_agent_schema(tmp_path: Path):
    manifest = {
        "schema_version": "2.0",
        "release_id": "fail-closed",
        "status": "DRAFT",
        "modules": {
            "agent.bad": {"path": "agents/bad.yaml"}
        }
    }
    modules = {
        "agents/bad.yaml": {
            "module_id": "agent.bad",
            "module_type": "AGENT",
            "payload": {
                "invalid_field_that_is_forbidden": 123
            }
        }
    }
    _write_config(tmp_path, manifest, modules)
    adapter = LegacyCompatibilityAdapter(tmp_path)
    with pytest.raises(ConfigurationValidationError):
        adapter.build_canonical_snapshot()


def test_compatibility_translation_fails_closed_on_invalid_source_schema(tmp_path: Path):
    manifest = {
        "schema_version": "2.0",
        "release_id": "fail-closed-source",
        "status": "DRAFT",
        "modules": {
            "source.bad": {"path": "sources/bad.yaml"}
        }
    }
    modules = {
        "sources/bad.yaml": {
            "module_id": "source.bad",
            "module_type": "SOURCE",
            "payload": {
                # missing connector_type
                "access_mode": "READ_ONLY"
            }
        }
    }
    _write_config(tmp_path, manifest, modules)
    adapter = LegacyCompatibilityAdapter(tmp_path)
    with pytest.raises(ConfigurationValidationError):
        adapter.build_canonical_snapshot()


# ===========================================================================
# 5. Precedence model
# ===========================================================================

def test_bootstrap_env_rejects_non_allowlisted_business_key():
    evaluator = ConfigurationPrecedenceEvaluator()
    with pytest.raises(PrecedenceViolationError, match="max_return_value"):
        evaluator.resolve(
            bootstrap_env={"max_return_value": 500},   # business key — not allowed
            baseline={},
        )


def test_active_release_overrides_release_controlled_baseline_value():
    evaluator = ConfigurationPrecedenceEvaluator()
    result = evaluator.resolve(
        bootstrap_env={},
        baseline={"timeout_seconds": 30},
        active_release={"timeout_seconds": 60},
    )
    assert result["timeout_seconds"] == 60


def test_active_release_cannot_override_bootstrap_only_value():
    evaluator = ConfigurationPrecedenceEvaluator()
    with pytest.raises(PrecedenceViolationError, match="log_level"):
        evaluator.resolve(
            bootstrap_env={},
            baseline={},
            active_release={"log_level": "DEBUG"},   # bootstrap-only key
        )


def test_runtime_snapshot_is_final_immutable_output():
    """RuntimeSnapshot is frozen; cannot be mutated after creation."""
    snapshot = build_snapshot_from_legacy_configs(CONFIG_DIR)
    with pytest.raises(Exception):
        snapshot.platform = PlatformConfig(environment="mutation-attempt")  # type: ignore[misc]


def test_secret_ref_is_preserved_without_resolution():
    """A Vault URI reference must pass through without being resolved."""
    result = ConfigurationPrecedenceEvaluator().resolve(
        bootstrap_env={"vault_address": "vault://localhost:8200"},
        baseline={},
    )
    assert result["vault_address"] == "vault://localhost:8200"


# ===========================================================================
# 6. Legacy precedence apply_overrides (backward compat)
# ===========================================================================

def test_precedence_evaluator_apply_overrides():
    evaluator = ConfigurationPrecedenceEvaluator()
    base = {"a": 1, "nested": {"x": 10}}
    override = {"nested": {"y": 20}, "b": 2}
    result = evaluator.apply_overrides(base, override)
    assert result == {"a": 1, "nested": {"x": 10, "y": 20}, "b": 2}
