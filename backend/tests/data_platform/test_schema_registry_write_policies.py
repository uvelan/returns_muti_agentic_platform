import hashlib
import json
from pathlib import Path

from return_platform.data_platform.schema_registry import load_schema_registry


def test_schema_registry_write_policies() -> None:
    project_root = Path(__file__).parent.parent.parent.parent
    registry_path = project_root / "backend" / "config" / "schema_registry.yaml"
    registry = load_schema_registry(registry_path)

    # Hashed over the *normalized text*, not the raw bytes. This file is checked
    # out with CRLF on Windows and LF on Linux, so `read_bytes` gives a different
    # digest per platform for a file nobody edited -- and since the digest is
    # written into a committed evidence artefact below, that produced a spurious
    # one-line diff on every run.
    registry_hash = hashlib.sha256(
        registry_path.read_text(encoding="utf-8").replace("\r\n", "\n").encode("utf-8")
    ).hexdigest()

    assets_by_owner: dict[str, int] = {}
    assets_by_write_policy: dict[str, int] = {}
    generation_enabled: list[str] = []
    denied_assets: list[str] = []
    omc_writable: list[str] = []
    unclassified: list[str] = []

    for asset in registry.assets:
        # Every asset has owner
        assert asset.owner, f"Asset {asset.asset_id} missing owner"
        # Every asset has authoritative_system
        assert asset.authoritative_system, f"Asset {asset.asset_id} missing authoritative_system"
        # Every asset has write_policy
        assert asset.write_policy, f"Asset {asset.asset_id} missing write_policy"
        # Every asset has generated_data_policy
        assert asset.generated_data_policy, f"Asset {asset.asset_id} missing generated_data_policy"

        # Enabled assets have a write_adapter_key
        if asset.generated_data_policy == "ENABLED":
            assert asset.write_adapter_key is not None, (
                f"Asset {asset.asset_id} missing write_adapter_key"
            )
            generation_enabled.append(asset.asset_id)

        # OMC assets are DENIED
        if asset.owner == "OMC":
            assert asset.write_policy == "DENIED", f"OMC asset {asset.asset_id} must be DENIED"
            if asset.write_policy != "DENIED":
                omc_writable.append(asset.asset_id)

        # Protected platform assets are DENIED
        if asset.owner == "PLATFORM_SYSTEM":
            assert asset.write_policy == "DENIED", f"Platform asset {asset.asset_id} must be DENIED"

        # Derived projections are DERIVED_PROJECTION
        if asset.ownership == "DERIVED_PROJECTION":
            assert asset.write_policy == "DERIVED_PROJECTION", (
                f"Derived asset {asset.asset_id} must be DERIVED_PROJECTION"
            )

        if asset.write_policy == "DENIED":
            assert not asset.allowed_operations, (
                f"DENIED asset {asset.asset_id} cannot have operations"
            )
            denied_assets.append(asset.asset_id)

        if not asset.write_policy:
            unclassified.append(asset.asset_id)

        assets_by_owner[asset.owner] = assets_by_owner.get(asset.owner, 0) + 1
        assets_by_write_policy[asset.write_policy] = (
            assets_by_write_policy.get(asset.write_policy, 0) + 1
        )

    assert not omc_writable, f"OMC assets must not be writable: {omc_writable}"
    assert not unclassified, f"All assets must have a write policy: {unclassified}"

    inventory = {
        "stage": "AIG1",
        "schemaRegistryChecksum": registry_hash,
        "totalAssets": len(registry.assets),
        "assetsByOwner": assets_by_owner,
        "assetsByWritePolicy": assets_by_write_policy,
        "generationEnabledAssets": sorted(generation_enabled),
        "deniedAssets": sorted(denied_assets),
        "omcWritableAssets": sorted(omc_writable),
        "unclassifiedAssets": sorted(unclassified),
        "validationStatus": "PASS",
    }

    evidence_dir = project_root / "docs" / "evidence" / "ai_studio_operational_generation" / "aig1"
    evidence_dir.mkdir(parents=True, exist_ok=True)

    # `newline=""` so the LF that `json.dumps` emits is written as an LF. Plain
    # `write_text` opens in text mode, which on Windows translates every one to
    # CRLF -- the second, independent reason this committed artefact came back
    # modified after a run that changed nothing.
    inventory_path = evidence_dir / "asset_policy_inventory.json"
    with inventory_path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(json.dumps(inventory, indent=2))
