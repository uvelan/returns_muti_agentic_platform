"""Minimal real ActiveSchema fixture shared by source_connectors Docker tests.

One MongoDB source/entity, one MSSQL source/entity -- just enough shape to
exercise scan()/targeted_read()/sample_*()/fetch_*() against real infra.
"""

from __future__ import annotations

from datetime import UTC, datetime

from return_platform.dynamic_knowledge.schema import ActiveSchema


def build_active_schema(*, mongo_collection: str, sql_table: str, sql_schema: str) -> ActiveSchema:
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
                "source_mongo": {
                    "source_asset_id": "source_mongo",
                    "connector_type": "MONGODB",
                    "connection_ref": "vault://source/mongo",
                    "object_ref": {"database": "db", "name": mongo_collection},
                    "incremental_cursor_field": "changed_at",
                },
                "source_sql": {
                    "source_asset_id": "source_sql",
                    "connector_type": "MSSQL",
                    "connection_ref": "vault://source/sql",
                    "object_ref": {
                        "database": "db",
                        "namespace": sql_schema,
                        "name": sql_table,
                    },
                    "incremental_cursor_field": "changed_at",
                },
            },
            "entities": {
                "entity_mongo": {
                    "entity_id": "entity_mongo",
                    "source_asset_id": "source_mongo",
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
                "entity_sql": {
                    "entity_id": "entity_sql",
                    "source_asset_id": "source_sql",
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
                                "displayable": True,
                                "operators": ["EXACT"],
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
                                "operators": ["GT", "GTE"],
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
                    "node_mongo": {
                        "projection_id": "node_mongo",
                        "entity_id": "entity_mongo",
                        "label": "ConfiguredMongo",
                        "key_fields": ["id"],
                        "property_fields": ["changed_at"],
                    },
                    "node_sql": {
                        "projection_id": "node_sql",
                        "entity_id": "entity_sql",
                        "label": "ConfiguredSql",
                        "key_fields": ["id"],
                        "property_fields": ["changed_at"],
                    },
                },
                "relationships": {},
                "constraints": [],
                "indexes": [],
            },
            "agent_policies": {},
        }
    )
