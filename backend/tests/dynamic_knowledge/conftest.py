from __future__ import annotations

from datetime import UTC, datetime

import pytest

from return_platform.dynamic_knowledge.schema import ActiveSchema


@pytest.fixture
def active_schema() -> ActiveSchema:
    return ActiveSchema.model_validate(
        {
            "configuration_release_id": "release-1",
            "configuration_checksum": "a" * 64,
            "release_status": "ACTIVE",
            "approved_by": "admin",
            "approved_at": datetime(2026, 8, 4, tzinfo=UTC),
            "schema_version": "2026.08.1",
            "policy_version": "2026.08.1",
            "prompt_version": "2026.08.1",
            "compiler_version": "1.0.0",
            "runtime_mode": "CONNECTED_SYNC",
            "sources": {
                "source_a": {
                    "source_asset_id": "source_a",
                    "connector_type": "MONGODB",
                    "object_ref": {"database": "db", "name": "objects"},
                    "incremental_cursor_field": "changed_at",
                },
                "source_b": {
                    "source_asset_id": "source_b",
                    "connector_type": "POSTGRESQL",
                    "object_ref": {
                        "database": "db",
                        "namespace": "public",
                        "name": "related_objects",
                    },
                },
            },
            "entities": {
                "entity_a": {
                    "entity_id": "entity_a",
                    "source_asset_id": "source_a",
                    "fields": {
                        "id": {
                            "field_id": "id",
                            "physical_path": ["configured_id"],
                            "graph_property": "configured_id",
                            "data_type": "STRING",
                            "nullable": False,
                            "capabilities": {
                                "searchable": True,
                                "filterable": True,
                                "distinct": True,
                                "aggregatable": False,
                                "displayable": True,
                                "on_demand_sync_anchor": True,
                                "operators": ["EXACT", "EQUALS"],
                                "aggregations": [],
                            },
                            "permissions": {
                                "searchable_by": ["associate"],
                                "displayable_by": ["associate"],
                                "on_demand_sync_by": ["associate"],
                            },
                        },
                        "name": {
                            "field_id": "name",
                            "physical_path": ["configured_name"],
                            "graph_property": "configured_name",
                            "data_type": "STRING",
                            "capabilities": {
                                "searchable": True,
                                "filterable": True,
                                "distinct": True,
                                "aggregatable": False,
                                "displayable": True,
                                "on_demand_sync_anchor": False,
                                "operators": ["EXACT", "PREFIX", "CONTAINS"],
                                "aggregations": [],
                            },
                            "permissions": {
                                "searchable_by": ["associate"],
                                "displayable_by": ["associate"],
                            },
                        },
                        "count_value": {
                            "field_id": "count_value",
                            "physical_path": ["configured_count"],
                            "graph_property": "configured_count",
                            "data_type": "INTEGER",
                            "capabilities": {
                                "searchable": False,
                                "filterable": True,
                                "distinct": True,
                                "aggregatable": True,
                                "displayable": True,
                                "on_demand_sync_anchor": False,
                                "operators": ["GT", "GTE", "LT", "LTE", "BETWEEN"],
                                "aggregations": [
                                    "COUNT",
                                    "COUNT_DISTINCT",
                                    "MIN",
                                    "MAX",
                                    "SUM",
                                    "AVERAGE",
                                ],
                            },
                            "permissions": {
                                "searchable_by": ["associate"],
                                "displayable_by": ["associate"],
                            },
                        },
                        "changed_at": {
                            "field_id": "changed_at",
                            "physical_path": ["configured_changed_at"],
                            "graph_property": "configured_changed_at",
                            "data_type": "DATETIME",
                            "nullable": False,
                            "capabilities": {
                                "searchable": False,
                                "filterable": True,
                                "distinct": False,
                                "aggregatable": False,
                                "displayable": False,
                                "on_demand_sync_anchor": False,
                                "operators": ["GT", "GTE"],
                                "aggregations": [],
                            },
                            "permissions": {"searchable_by": ["associate"]},
                        },
                    },
                    "natural_key": ["id"],
                    "strong_anchors": {
                        "exact_id": {
                            "anchor_id": "exact_id",
                            "fields": [
                                {
                                    "field_id": "id",
                                    "allowed_operators": ["EXACT", "EQUALS"],
                                    "required": True,
                                }
                            ],
                            "minimum_fields_present": 1,
                            "maximum_expected_matches": 1,
                            "on_demand_sync_allowed": True,
                        }
                    },
                },
                "entity_b": {
                    "entity_id": "entity_b",
                    "source_asset_id": "source_b",
                    "fields": {
                        "id": {
                            "field_id": "id",
                            "physical_path": ["related_id"],
                            "graph_property": "related_id",
                            "data_type": "STRING",
                            "nullable": False,
                            "capabilities": {
                                "searchable": True,
                                "filterable": True,
                                "distinct": True,
                                "displayable": True,
                                "operators": ["EXACT"],
                            },
                            "permissions": {
                                "searchable_by": ["associate"],
                                "displayable_by": ["associate"],
                            },
                        },
                        "parent_id": {
                            "field_id": "parent_id",
                            "physical_path": ["configured_parent_id"],
                            "graph_property": "configured_parent_id",
                            "data_type": "STRING",
                            "capabilities": {
                                "searchable": True,
                                "filterable": True,
                                "operators": ["EXACT"],
                            },
                            "permissions": {"searchable_by": ["associate"]},
                        },
                    },
                    "natural_key": ["id"],
                    "strong_anchors": {},
                },
            },
            "graph": {
                "database": "business_knowledge",
                "nodes": {
                    "node_a": {
                        "projection_id": "node_a",
                        "entity_id": "entity_a",
                        "label": "ConfiguredAlpha",
                        "key_fields": ["id"],
                        "property_fields": ["name", "count_value", "changed_at"],
                    },
                    "node_b": {
                        "projection_id": "node_b",
                        "entity_id": "entity_b",
                        "label": "ConfiguredBeta",
                        "key_fields": ["id"],
                        "property_fields": ["parent_id"],
                    },
                },
                "relationships": {
                    "a_to_b": {
                        "relationship_id": "a_to_b",
                        "relationship_type": "CONFIGURED_LINK",
                        "source_entity_id": "entity_a",
                        "target_entity_id": "entity_b",
                        "source_match_fields": ["id"],
                        "target_match_fields": ["parent_id"],
                    }
                },
                "constraints": [],
                "indexes": [],
            },
            "agent_policies": {
                "agent_a": {
                    "agent_id": "agent_a",
                    "task_queue": "agent-a-queue",
                    "allowed_business_capabilities": [
                        "order-discovery",
                        "candidate-disambiguation",
                    ],
                    "allowed_roles": ["associate"],
                    "allowed_entity_ids": ["entity_a", "entity_b"],
                    "standard_model_refs": ["model://standard/one"],
                    "max_reasoning_steps": 8,
                    "max_graph_queries_per_turn": 12,
                    "max_correction_attempts": 2,
                }
            },
        }
    )
