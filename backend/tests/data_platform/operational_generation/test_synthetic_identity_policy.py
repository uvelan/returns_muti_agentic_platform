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
async def test_synthetic_email_policy(
    generator: OperationalGenerator, registry: SchemaRegistry
) -> None:
    asset = next(
        a
        for a in registry.assets
        if a.generated_data_policy == "ENABLED"
        and any(f.name == "email" for f in a.fields)
        and a.write_policy != "DENIED"
    )

    req = GenerationRequest(
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

    proposal = await generator.generate_proposal(req)
    email = proposal.records[0].values.get("email")
    assert isinstance(email, str) and email.startswith("generated+")
    assert isinstance(email, str) and email.endswith("@example.invalid")


@pytest.mark.asyncio
async def test_synthetic_phone_policy(
    generator: OperationalGenerator, registry: SchemaRegistry
) -> None:
    asset = next(
        a
        for a in registry.assets
        if a.generated_data_policy == "ENABLED"
        and any(f.name == "phoneNumber" for f in a.fields)
        and a.write_policy != "DENIED"
    )

    req = GenerationRequest(
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

    proposal = await generator.generate_proposal(req)
    phone = proposal.records[0].values.get("phoneNumber")
    assert isinstance(phone, str) and phone.startswith("555-01")
