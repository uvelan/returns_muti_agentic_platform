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
async def test_generation_checksum(
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
        deterministic_seed=123,
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
        deterministic_seed=123,
        tenant_id="test_tenant",
        date_from=datetime(2023, 1, 1, tzinfo=UTC),
        date_to=datetime(2023, 1, 31, tzinfo=UTC),
        generation_mode=GenerationMode.DETERMINISTIC,
        collision_policy=CollisionPolicy.REJECT,
        scenario_distribution={ScenarioType.POSITIVE: 1},
    )

    req3 = GenerationRequest(
        asset_ids=(asset.asset_id,),
        record_count=1,
        deterministic_seed=999,
        tenant_id="test_tenant",
        date_from=datetime(2023, 1, 1, tzinfo=UTC),
        date_to=datetime(2023, 1, 31, tzinfo=UTC),
        generation_mode=GenerationMode.DETERMINISTIC,
        collision_policy=CollisionPolicy.REJECT,
        scenario_distribution={ScenarioType.POSITIVE: 1},
    )

    proposal1 = await generator.generate_proposal(req1)
    proposal2 = await generator.generate_proposal(req2)
    proposal3 = await generator.generate_proposal(req3)

    assert proposal1.proposal_checksum == proposal2.proposal_checksum
    assert proposal1.proposal_checksum != proposal3.proposal_checksum
