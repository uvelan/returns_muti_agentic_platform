from pathlib import Path
from typing import Any

import pytest

from return_platform.data_platform.operational_generation.guard import HallucinationGuard
from return_platform.data_platform.operational_generation.models import (
    FindingCode,
    OperationProposal,
    ValidationResultState,
)
from return_platform.data_platform.schema_registry import SchemaRegistry, load_schema_registry


@pytest.fixture
def registry() -> SchemaRegistry:
    project_root = Path(__file__).parent.parent.parent.parent
    registry_path = project_root / "backend" / "config" / "schema_registry.yaml"
    return load_schema_registry(registry_path)

@pytest.fixture
def guard(registry: SchemaRegistry) -> HallucinationGuard:
    return HallucinationGuard(registry)

def test_missing_asset(guard: HallucinationGuard) -> None:
    proposal = OperationProposal(asset_id="unknown_asset", records=[{"a": 1}])
    result = guard.validate(proposal)
    assert result.state == ValidationResultState.INVALID_PROPOSAL
    assert result.findings[0].code == FindingCode.MISSING_ASSET

def test_omc_denial(guard: HallucinationGuard, registry: SchemaRegistry) -> None:
    # Find an OMC asset
    omc_asset = next(a for a in registry.assets if a.owner == "OMC")
    proposal = OperationProposal(asset_id=omc_asset.asset_id, records=[{}])
    result = guard.validate(proposal)
    assert result.state == ValidationResultState.POLICY_DENIED
    assert any(f.code == FindingCode.OMC_DENIAL for f in result.findings)

def test_derived_projection_denial(guard: HallucinationGuard, registry: SchemaRegistry) -> None:
    # Find a derived projection
    derived_asset = next((a for a in registry.assets if a.ownership == "DERIVED_PROJECTION"), None)
    if not derived_asset:
        pytest.skip("No DERIVED_PROJECTION asset in registry")
    proposal = OperationProposal(asset_id=derived_asset.asset_id, records=[{}])
    result = guard.validate(proposal)
    assert result.state == ValidationResultState.POLICY_DENIED
    assert any(f.code == FindingCode.DERIVED_PROJECTION_DENIAL for f in result.findings)

def test_unknown_field_rejection(guard: HallucinationGuard, registry: SchemaRegistry) -> None:
    # Use a safe writable asset
    asset = next(a for a in registry.assets if a.generated_data_policy == "ENABLED" and a.write_policy != "DENIED")
    proposal = OperationProposal(asset_id=asset.asset_id, records=[{"unknown_xyz": "value"}])
    result = guard.validate(proposal)
    assert result.state == ValidationResultState.INVALID_RECORD
    assert any(f.code == FindingCode.UNKNOWN_FIELD and f.field_path == "unknown_xyz" for f in result.findings)

def test_missing_required_field(guard: HallucinationGuard, registry: SchemaRegistry) -> None:
    asset = next(a for a in registry.assets if a.generated_data_policy == "ENABLED" and a.write_policy != "DENIED" and any(f.required for f in a.fields))
    proposal = OperationProposal(asset_id=asset.asset_id, records=[{}])
    result = guard.validate(proposal)
    assert result.state == ValidationResultState.INVALID_RECORD
    assert any(f.code == FindingCode.MISSING_REQUIRED_FIELD for f in result.findings)

def test_invalid_type(guard: HallucinationGuard, registry: SchemaRegistry) -> None:
    asset = next(a for a in registry.assets if a.generated_data_policy == "ENABLED" and a.write_policy != "DENIED")
    string_field = next(f.name for f in asset.fields if f.type == "string")
    proposal = OperationProposal(asset_id=asset.asset_id, records=[{string_field: 123}])
    result = guard.validate(proposal)
    assert result.state == ValidationResultState.INVALID_RECORD
    assert any(f.code == FindingCode.INVALID_TYPE and f.field_path == string_field for f in result.findings)

def test_no_input_mutation(guard: HallucinationGuard, registry: SchemaRegistry) -> None:
    asset = next(a for a in registry.assets if a.generated_data_policy == "ENABLED" and a.write_policy != "DENIED")
    records = [{"some_field": "value"}]
    proposal = OperationProposal(asset_id=asset.asset_id, records=records)
    guard.validate(proposal)
    # Ensure original list is unchanged
    assert records == [{"some_field": "value"}]

def test_duplicate_natural_keys(guard: HallucinationGuard, registry: SchemaRegistry) -> None:
    asset = next((a for a in registry.assets if a.generated_data_policy == "ENABLED" and a.natural_keys and a.write_policy != "DENIED"), None)
    if not asset:
        pytest.skip("No asset with natural keys and GENERATED_DATA_POLICY=ENABLED")

    key_field = asset.natural_keys[0]
    # Provide two records with the same natural key
    proposal = OperationProposal(asset_id=asset.asset_id, records=[{key_field: "same"}, {key_field: "same"}])
    result = guard.validate(proposal)
    assert result.state == ValidationResultState.INVALID_RECORD
    assert any(f.code == FindingCode.DUPLICATE_NATURAL_KEY for f in result.findings)

class MockResolver:
    def exists(self, field_path: str, value: Any) -> bool:
        return bool(value == "valid_fk")

def test_invalid_foreign_key(guard: HallucinationGuard, registry: SchemaRegistry) -> None:
    asset = next((a for a in registry.assets if a.generated_data_policy == "ENABLED" and a.dependency_fields and a.write_policy != "DENIED"), None)
    if not asset:
        pytest.skip("No asset with dependency_fields and GENERATED_DATA_POLICY=ENABLED")

    dep_field = asset.dependency_fields[0]
    # valid fk
    proposal_valid = OperationProposal(asset_id=asset.asset_id, records=[{dep_field: "valid_fk"}])
    res_valid = guard.validate(proposal_valid, resolver=MockResolver())
    assert not any(f.code == FindingCode.INVALID_FOREIGN_KEY for f in res_valid.findings)

    # invalid fk
    proposal_invalid = OperationProposal(asset_id=asset.asset_id, records=[{dep_field: "invalid_fk"}])
    res_invalid = guard.validate(proposal_invalid, resolver=MockResolver())
    assert any(f.code == FindingCode.INVALID_FOREIGN_KEY for f in res_invalid.findings)

def test_pii_policy_failures(guard: HallucinationGuard, registry: SchemaRegistry) -> None:
    asset = next((a for a in registry.assets if a.generated_data_policy == "ENABLED" and a.pii_policy == "STRICT_SYNTHETIC" and a.write_policy != "DENIED"), None)
    if not asset:
        pytest.skip("No asset with STRICT_SYNTHETIC PII policy and GENERATED_DATA_POLICY=ENABLED")

    def pii_validator(field: str, value: Any) -> bool:
        return bool(value == "synthetic")

    some_field = asset.fields[0].name
    proposal = OperationProposal(asset_id=asset.asset_id, records=[{some_field: "real"}])
    result = guard.validate(proposal, pii_validator=pii_validator)
    assert result.state == ValidationResultState.INVALID_RECORD
    assert any(f.code == FindingCode.PII_VIOLATION for f in result.findings)

def test_deterministic_finding_order(guard: HallucinationGuard, registry: SchemaRegistry) -> None:
    asset = next(a for a in registry.assets if a.generated_data_policy == "ENABLED" and a.write_policy != "DENIED")
    # Missing required field, unknown field
    proposal = OperationProposal(asset_id=asset.asset_id, records=[{"unknown_z": 1}, {"unknown_a": 1}])
    result = guard.validate(proposal)
    # Check if ordered
    sorted_findings = sorted(result.findings, key=lambda f: (f.record_index or -1, f.field_path or "", f.code.value))
    assert result.findings == sorted_findings
