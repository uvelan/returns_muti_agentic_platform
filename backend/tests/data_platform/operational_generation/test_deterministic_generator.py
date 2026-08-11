from datetime import UTC, datetime
from pathlib import Path

import pytest
from record_paths import leaf_paths

from return_platform.data_platform.operational_generation import (
    CollisionPolicy,
    GenerationMode,
    GenerationRequest,
    HallucinationGuard,
    OperationalGenerator,
    ScenarioType,
)
from return_platform.data_platform.schema_registry import SchemaRegistry, load_schema_registry


@pytest.fixture
def registry() -> SchemaRegistry:
    project_root = Path(__file__).parent.parent.parent.parent.parent
    return load_schema_registry(project_root / "backend" / "config" / "schema_registry.yaml")


@pytest.fixture
def guard(registry: SchemaRegistry) -> HallucinationGuard:
    return HallucinationGuard(registry)


@pytest.fixture
def generator(registry: SchemaRegistry, guard: HallucinationGuard) -> OperationalGenerator:
    return OperationalGenerator(registry, guard)


@pytest.mark.asyncio
async def test_deterministic_generator_identical_proposals(
    generator: OperationalGenerator, registry: SchemaRegistry
) -> None:
    # Find a safe writable asset
    asset = next(
        a
        for a in registry.assets
        if a.generated_data_policy == "ENABLED" and a.write_policy != "DENIED"
    )

    req = GenerationRequest(
        asset_ids=(asset.asset_id,),
        record_count=2,
        deterministic_seed=42,
        tenant_id="test_tenant",
        date_from=datetime(2023, 1, 1, tzinfo=UTC),
        date_to=datetime(2023, 1, 31, tzinfo=UTC),
        generation_mode=GenerationMode.DETERMINISTIC,
        collision_policy=CollisionPolicy.REJECT,
        scenario_distribution={ScenarioType.POSITIVE: 2},
    )

    proposal1 = await generator.generate_proposal(req)
    proposal2 = await generator.generate_proposal(req)

    # same request + same seed -> identical proposal
    assert proposal1.proposal_checksum == proposal2.proposal_checksum
    assert proposal1.records == proposal2.records


@pytest.mark.asyncio
async def test_deterministic_generator_different_seed(
    generator: OperationalGenerator, registry: SchemaRegistry
) -> None:
    asset = next(
        a
        for a in registry.assets
        if a.generated_data_policy == "ENABLED" and a.write_policy != "DENIED"
    )

    req1 = GenerationRequest(
        asset_ids=(asset.asset_id,),
        record_count=1,
        deterministic_seed=42,
        tenant_id="test_tenant",
        date_from=datetime(2023, 1, 1, tzinfo=UTC),
        date_to=datetime(2023, 1, 31, tzinfo=UTC),
        generation_mode=GenerationMode.DETERMINISTIC,
        collision_policy=CollisionPolicy.REJECT,
        scenario_distribution={ScenarioType.POSITIVE: 1},
    )

    req2 = GenerationRequest(
        asset_ids=(asset.asset_id,),
        record_count=1,
        deterministic_seed=43,  # different seed
        tenant_id="test_tenant",
        date_from=datetime(2023, 1, 1, tzinfo=UTC),
        date_to=datetime(2023, 1, 31, tzinfo=UTC),
        generation_mode=GenerationMode.DETERMINISTIC,
        collision_policy=CollisionPolicy.REJECT,
        scenario_distribution={ScenarioType.POSITIVE: 1},
    )

    proposal1 = await generator.generate_proposal(req1)
    proposal2 = await generator.generate_proposal(req2)

    # different seed -> different generated identifiers
    assert proposal1.records[0].temporary_record_key != proposal2.records[0].temporary_record_key


@pytest.mark.asyncio
async def test_all_generated_fields_exist_in_schema(
    generator: OperationalGenerator, registry: SchemaRegistry
) -> None:
    asset = next(
        a
        for a in registry.assets
        if a.generated_data_policy == "ENABLED" and a.write_policy != "DENIED"
    )

    req = GenerationRequest(
        asset_ids=(asset.asset_id,),
        record_count=1,
        deterministic_seed=100,
        tenant_id="test_tenant",
        date_from=datetime(2023, 1, 1, tzinfo=UTC),
        date_to=datetime(2023, 1, 31, tzinfo=UTC),
        generation_mode=GenerationMode.DETERMINISTIC,
        collision_policy=CollisionPolicy.REJECT,
        scenario_distribution={ScenarioType.POSITIVE: 1},
    )

    proposal = await generator.generate_proposal(req)
    asset_schema = registry.asset(asset.asset_id)
    field_names = {f.name for f in asset_schema.fields}

    # Every *path* the record carries must be declared, not every top-level
    # key. A generated document is nested, so its top-level keys are the first
    # segment of a dotted registry name and would never match one.
    for record in proposal.records:
        for path, _ in leaf_paths(record.values):
            assert any(path == name or path.startswith(f"{name}.") for name in field_names), (
                f"undeclared path {path!r}"
            )


@pytest.mark.asyncio
async def test_prohibited_assets(generator: OperationalGenerator, registry: SchemaRegistry) -> None:
    req_base = {
        "record_count": 1,
        "deterministic_seed": 1,
        "tenant_id": "test_tenant",
        "date_from": datetime(2023, 1, 1, tzinfo=UTC),
        "date_to": datetime(2023, 1, 31, tzinfo=UTC),
        "generation_mode": GenerationMode.DETERMINISTIC,
        "collision_policy": CollisionPolicy.REJECT,
        "scenario_distribution": {ScenarioType.POSITIVE: 1},
    }

    omc_asset = next((a for a in registry.assets if a.owner == "OMC"), None)
    if omc_asset:
        req = GenerationRequest(asset_ids=(omc_asset.asset_id,), **req_base)
        with pytest.raises(ValueError, match="prohibited"):
            await generator.generate_proposal(req)

    denied_asset = next((a for a in registry.assets if a.write_policy == "DENIED"), None)
    if denied_asset:
        req = GenerationRequest(asset_ids=(denied_asset.asset_id,), **req_base)
        with pytest.raises(ValueError, match="prohibited"):
            await generator.generate_proposal(req)

    derived_asset = next((a for a in registry.assets if a.ownership == "DERIVED_PROJECTION"), None)
    if derived_asset:
        req = GenerationRequest(asset_ids=(derived_asset.asset_id,), **req_base)
        with pytest.raises(ValueError, match="prohibited"):
            await generator.generate_proposal(req)

    disabled_asset = next(
        (
            a
            for a in registry.assets
            if a.generated_data_policy != "ENABLED"
            and a.owner != "OMC"
            and a.write_policy != "DENIED"
            and a.ownership != "DERIVED_PROJECTION"
        ),
        None,
    )
    if disabled_asset:
        req = GenerationRequest(asset_ids=(disabled_asset.asset_id,), **req_base)
        with pytest.raises(ValueError, match="disabled"):
            await generator.generate_proposal(req)
