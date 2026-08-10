import yaml
from pathlib import Path


def migrate():
    path = Path("backend/config/schema_registry.yaml")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))

    for asset in data["assets"]:
        asset_id = asset["asset_id"]
        name = asset["name"]

        # Defaults
        asset["owner"] = "PLATFORM"
        asset["authoritative_system"] = "RETURNS_PLATFORM"
        asset["write_policy"] = "DENIED"
        asset["write_adapter_key"] = None
        asset["generated_data_policy"] = "DISABLED"
        asset["allowed_operations"] = []
        asset["dependency_fields"] = []
        asset["natural_keys"] = []
        asset["collision_policy"] = "REJECT"
        asset["pii_policy"] = "NONE"
        asset["rollback_policy"] = "NO_ACTION"
        asset["graph_sync_policy"] = "NONE"

        if "omc" in asset_id.lower() or "omc" in name.lower():
            asset["owner"] = "OMC"
            asset["authoritative_system"] = "OMC"
            asset["write_policy"] = "DENIED"
            asset["generated_data_policy"] = "DISABLED"
        elif "source.mongodb" in asset_id:
            asset["owner"] = "SOURCE"
            asset["authoritative_system"] = "SOURCE_ERP"
            asset["write_policy"] = "SOURCE_ADMIN_WRITER"
            asset["write_adapter_key"] = "source_admin"
            asset["generated_data_policy"] = "ENABLED"
            asset["allowed_operations"] = ["INSERT"]
            asset["rollback_policy"] = "DELETE"
            asset["graph_sync_policy"] = "SYNC_IMMEDIATELY"
        elif (
            "return_sessions" in asset_id
            or "support_cases" in asset_id
            or "return_support_ticket" in asset_id
            or "pickup" in asset_id
            or "shipment" in asset_id
        ):
            asset["owner"] = "PLATFORM"
            asset["authoritative_system"] = "RETURNS_PLATFORM"
            asset["write_policy"] = "DOMAIN_API_ONLY"
            asset["write_adapter_key"] = "domain_api"
            asset["generated_data_policy"] = "ENABLED"
            asset["allowed_operations"] = ["INSERT"]
            asset["rollback_policy"] = "DOMAIN_COMPENSATE"
            asset["graph_sync_policy"] = "BACKGROUND"
        elif (
            "operational_returns" in asset_id
            or "operational_events" in asset_id
            or "bay_" in asset_id
        ):
            asset["owner"] = "PLATFORM"
            asset["authoritative_system"] = "RETURNS_PLATFORM"
            asset["write_policy"] = "DIRECT_OPERATIONAL_INSERT"
            asset["write_adapter_key"] = "direct_operational"
            asset["generated_data_policy"] = "ENABLED"
            asset["allowed_operations"] = ["INSERT"]
            asset["rollback_policy"] = "DELETE"
            asset["graph_sync_policy"] = "BACKGROUND"
        elif (
            "graph" in asset_id
            or "projection" in asset_id
            or "search" in asset_id
            or "cdm" in asset_id.lower()
        ):
            asset["owner"] = "PLATFORM"
            asset["authoritative_system"] = "RETURNS_PLATFORM"
            asset["write_policy"] = "DERIVED_PROJECTION"
            asset["generated_data_policy"] = "DISABLED"
            asset["graph_sync_policy"] = "NONE"
        else:
            # Protected platform assets (audit, workers, jobs, etc)
            asset["owner"] = "PLATFORM_SYSTEM"
            asset["authoritative_system"] = "RETURNS_PLATFORM"
            asset["write_policy"] = "DENIED"
            asset["generated_data_policy"] = "DISABLED"

    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, sort_keys=False, default_flow_style=False)


if __name__ == "__main__":
    migrate()
