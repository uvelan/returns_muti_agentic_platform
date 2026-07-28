from return_platform.data_platform.schema_registry import DataAssetSchema


def resolve_execution_channel(asset: DataAssetSchema) -> str:
    if asset.owner == "OMC":
        raise ValueError(f"Asset {asset.asset_id} is OMC-owned and denied for generation.")

    if asset.write_policy == "DENIED":
        raise ValueError(f"Asset {asset.asset_id} is protected and denied for generation.")

    if asset.ownership == "DERIVED_PROJECTION":
        raise ValueError(
            f"Asset {asset.asset_id} is a derived projection and cannot receive direct writes."
        )

    # Heuristics based on prompt mappings (or schema fields if available)
    # The registry usually defines write_adapter_key
    if hasattr(asset, "write_adapter_key") and asset.write_adapter_key:
        return asset.write_adapter_key

    # Fallback to mapping based on common names if adapter key is missing
    asset_id = asset.asset_id.lower()
    if (
        "mongodb" in asset_id
        or "sales_inv" in asset_id
        or "customer" in asset_id
        or "shipment" in asset_id
        or "product" in asset_id
    ):
        return "SOURCE_ADMIN_WRITER"

    if "platform" in asset_id or "domain" in asset_id:
        return "DOMAIN_API_ONLY"

    return "UNKNOWN_ADAPTER"


def is_source_writer(channel: str) -> bool:
    return channel == "SOURCE_ADMIN_WRITER"


def is_domain_api(channel: str) -> bool:
    return channel == "DOMAIN_API_ONLY"
