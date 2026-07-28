import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from return_platform.data_platform.operational_generation import (
    CollisionPolicy,
    GenerationMode,
    GenerationRequest,
    HallucinationGuard,
    OperationalGenerator,
    OperationalPlanner,
    OperationType,
    RollbackFeasibility,
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


@pytest.fixture
def planner(registry: SchemaRegistry, guard: HallucinationGuard) -> OperationalPlanner:
    return OperationalPlanner(registry, guard)


@pytest.mark.asyncio
async def test_planner_deterministic_plan_and_checksums(
    generator: OperationalGenerator, planner: OperationalPlanner, registry: SchemaRegistry
) -> None:
    assets = [
        a
        for a in registry.assets
        if a.generated_data_policy == "ENABLED" and a.write_policy != "DENIED"
    ]
    if not assets:
        pytest.skip()

    req = GenerationRequest(
        asset_ids=(assets[0].asset_id,),
        record_count=2,
        deterministic_seed=123,
        tenant_id="test",
        date_from=datetime(2023, 1, 1, tzinfo=UTC),
        date_to=datetime(2023, 1, 31, tzinfo=UTC),
        generation_mode=GenerationMode.DETERMINISTIC,
        collision_policy=CollisionPolicy.REJECT,
        scenario_distribution={ScenarioType.POSITIVE: 2},
    )

    prop1 = await generator.generate_proposal(req)
    prop2 = await generator.generate_proposal(req)

    plan1 = planner.build_plan(prop1, "salt")
    plan2 = planner.build_plan(prop2, "salt")

    assert plan1.plan_checksum == plan2.plan_checksum
    assert plan1.idempotency_key == plan2.idempotency_key


@pytest.mark.asyncio
async def test_planner_rejects_omc_and_derived(
    generator: OperationalGenerator, planner: OperationalPlanner, registry: SchemaRegistry
) -> None:
    # For planner rejection, we mock a proposal to include OMC or DENIED.
    # We can't generate it via generator because generator blocks it.
    from return_platform.data_platform.operational_generation.models import (
        GeneratedRecord,
        GenerationProvenance,
        OperationalGenerationProposal,
    )

    asset = next((a for a in registry.assets if a.owner == "OMC"), None)
    if not asset:
        pytest.skip()

    prop = OperationalGenerationProposal(
        proposal_id=uuid.uuid4(),
        schema_release_id=registry.schema_version,
        schema_checksum="MOCK_CHECKSUM",
        deterministic_seed=1,
        generation_mode=GenerationMode.DETERMINISTIC,
        records=(
            GeneratedRecord(
                asset_id=asset.asset_id,
                temporary_record_key="1",
                values={},
                dependency_keys=(),
                generation_index=0,
            ),
        ),
        provenance=GenerationProvenance(
            timestamp=datetime.now(UTC), generator_version="1", metrics={}, ai_traces=[]
        ),
        proposal_checksum="xyz",
    )
    # mock checksum calculation so it passes step 1
    import return_platform.data_platform.operational_generation.planner as planner_mod

    orig_calc = planner_mod.calculate_proposal_checksum
    planner_mod.calculate_proposal_checksum = lambda p: p.proposal_checksum

    try:
        with pytest.raises(ValueError, match=r"OMC-owned"):
            planner.build_plan(prop, "salt")
    finally:
        planner_mod.calculate_proposal_checksum = orig_calc


@pytest.mark.asyncio
async def test_planner_transaction_groups_and_saga_ordering(
    generator: OperationalGenerator, planner: OperationalPlanner, registry: SchemaRegistry
) -> None:
    # Find a source admin writer asset that is generated
    assets = [
        a
        for a in registry.assets
        if a.generated_data_policy == "ENABLED"
        and a.write_policy != "DENIED"
        and "customer" in a.asset_id.lower()
    ]
    if not assets:
        pytest.skip()

    req = GenerationRequest(
        asset_ids=tuple(a.asset_id for a in assets[:2]),
        record_count=2,
        deterministic_seed=123,
        tenant_id="test",
        date_from=datetime(2023, 1, 1, tzinfo=UTC),
        date_to=datetime(2023, 1, 31, tzinfo=UTC),
        generation_mode=GenerationMode.DETERMINISTIC,
        collision_policy=CollisionPolicy.REJECT,
        scenario_distribution={ScenarioType.POSITIVE: 2},
    )

    prop = await generator.generate_proposal(req)
    plan = planner.build_plan(prop, "salt")

    # Check that transaction groups do not cross systems
    for step in plan.saga_steps:
        for tg in step.transaction_groups:
            channels = {op.target_channel for op in tg.operations}
            assert len(channels) == 1

    # Graph sync is appended
    has_sync = any(
        op.type == OperationType.GRAPH_SYNC_REQUEST
        for step in plan.saga_steps
        for tg in step.transaction_groups
        for op in tg.operations
    )
    # For assets that are SOURCE_ADMIN_WRITER
    assert has_sync

    # Rollback classification
    for step in plan.saga_steps:
        assert step.rollback_feasibility in (
            RollbackFeasibility.SAFE,
            RollbackFeasibility.COMPENSATION_REQUIRED,
        )
