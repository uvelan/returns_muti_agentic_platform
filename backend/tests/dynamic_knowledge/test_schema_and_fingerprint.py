from __future__ import annotations

from pathlib import Path

from return_platform.dynamic_knowledge.config_loader import load_active_schema
from return_platform.dynamic_knowledge.fingerprint import graph_schema_fingerprint
from return_platform.dynamic_knowledge.schema import ActiveSchema


def test_active_configuration_example_has_valid_checksum() -> None:
    root = Path(__file__).parents[2]
    schema = load_active_schema(root / "config/dynamic_knowledge/active-schema.example.yaml")
    assert schema.release_status == "ACTIVE"
    assert schema.agent_policies["order-discovery-agent"].standard_model_refs


def test_graph_fingerprint_changes_when_projection_changes(active_schema: ActiveSchema) -> None:
    baseline = graph_schema_fingerprint(active_schema)
    raw = active_schema.model_dump(mode="json")
    raw["entities"]["entity_a"]["fields"]["name"]["graph_property"] = "renamed_configured_name"
    changed = ActiveSchema.model_validate(raw)
    assert graph_schema_fingerprint(changed) != baseline


def test_graph_fingerprint_ignores_connection_secret_reference_change(active_schema: ActiveSchema) -> None:
    baseline = graph_schema_fingerprint(active_schema)
    raw = active_schema.model_dump(mode="json")
    raw["sources"]["source_a"]["connection_ref"] = "vault://rotated/secret"
    changed = ActiveSchema.model_validate(raw)
    assert graph_schema_fingerprint(changed) == baseline
