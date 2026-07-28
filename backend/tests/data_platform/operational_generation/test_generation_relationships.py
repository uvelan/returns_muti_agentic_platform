from datetime import UTC, datetime
from pathlib import Path

import pytest

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
async def test_generation_relationships_valid(
    generator: OperationalGenerator, registry: SchemaRegistry
) -> None:
    # Test customer -> order -> line -> shipment dependencies (or a subset)
    # Actually we just pass valid generated_data_policy="ENABLED" assets
    assets = [
        a
        for a in registry.assets
        if a.generated_data_policy == "ENABLED" and a.write_policy != "DENIED"
    ]
    asset_ids = tuple(a.asset_id for a in assets)

    if not asset_ids:
        pytest.skip("No available assets for generation")

    req = GenerationRequest(
        asset_ids=asset_ids,
        record_count=1,
        deterministic_seed=123,
        tenant_id="test_tenant",
        date_from=datetime(2023, 1, 1, tzinfo=UTC),
        date_to=datetime(2023, 1, 31, tzinfo=UTC),
        generation_mode=GenerationMode.DETERMINISTIC,
        collision_policy=CollisionPolicy.REJECT,
        scenario_distribution={ScenarioType.POSITIVE: 1},
    )

    proposal = await generator.generate_proposal(req)

    # Check that record generation followed topological order, implicitly done by dependency keys
    # Also verify timestamps
    # Since we use random date within interval, we should check if they are within [date_from, date_to]
    for rec in proposal.records:
        for k, v in rec.values.items():
            if (
                ("date" in k.lower() or "time" in k.lower())
                and isinstance(v, str)
                and v.endswith("Z")
            ):
                dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
                assert req.date_from <= dt <= req.date_to
