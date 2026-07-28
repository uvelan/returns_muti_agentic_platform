from uuid import uuid4

import pytest

from return_platform.data_platform.operational_generation.apply_models import ExecutionRunState
from return_platform.data_platform.operational_generation.apply_service import ApplyService
from return_platform.data_platform.operational_generation.execution_lock import ExecutionLock
from return_platform.data_platform.operational_generation.execution_repository import (
    ExecutionRepository,
)
from return_platform.data_platform.operational_generation.rollback_service import RollbackService
from return_platform.data_platform.operational_generation.write_models import (
    Operation,
    OperationalWritePlan,
    OperationType,
    PlanImpact,
    RollbackFeasibility,
    SagaStep,
    TransactionGroup,
)


def get_mock_plan() -> OperationalWritePlan:
    return OperationalWritePlan(
        plan_id=uuid4(),
        proposal_checksum="abc",
        plan_checksum="def",
        schema_release_id="rel1",
        schema_checksum="sch1",
        saga_steps=(
            SagaStep(
                step_index=0,
                transaction_groups=(
                    TransactionGroup(
                        group_id=str(uuid4()),
                        target_channel="mock_adapter",
                        operations=(
                            Operation(
                                operation_id="op1",
                                type=OperationType.INSERT,
                                asset_id="source.mongodb.sales_inv",
                                payload={"a": 1},
                                target_channel="mock_adapter",
                                dependencies=(),
                            ),
                        ),
                    ),
                ),
                rollback_feasibility=RollbackFeasibility.SAFE,
            ),
        ),
        impact=PlanImpact(
            total_operations=1,
            inserts=1,
            domain_commands=0,
            graph_sync_requests=0,
            affected_channels=("mock_adapter",),
        ),
        idempotency_key="idempotency_1",
    )


@pytest.fixture
def repository() -> ExecutionRepository:
    return ExecutionRepository()


@pytest.fixture
def lock() -> ExecutionLock:
    return ExecutionLock()


@pytest.fixture
def apply_service(repository: ExecutionRepository, lock: ExecutionLock) -> ApplyService:
    return ApplyService(repository, lock)


@pytest.fixture
def rollback_service(repository: ExecutionRepository) -> RollbackService:
    return RollbackService(repository)


@pytest.mark.asyncio
async def test_duplicate_apply_idempotent(apply_service: ApplyService) -> None:
    plan = get_mock_plan()
    run1 = await apply_service.apply_plan(plan)
    run2 = await apply_service.apply_plan(plan)
    assert run1.run_id == run2.run_id
    assert run1.state == ExecutionRunState.APPLIED


@pytest.mark.asyncio
async def test_concurrent_apply_serialized(
    apply_service: ApplyService, lock: ExecutionLock
) -> None:
    plan = get_mock_plan()
    # Mocking lock acquire to fail to simulate concurrency
    lock.acquire = lambda r: False  # type: ignore
    with pytest.raises(RuntimeError, match="Concurrent apply"):
        await apply_service.apply_plan(plan)


@pytest.mark.asyncio
async def test_single_store_transaction_success(apply_service: ApplyService) -> None:
    plan = get_mock_plan()
    run = await apply_service.apply_plan(plan)
    assert run.state == ExecutionRunState.APPLIED
    receipts = apply_service.repository.get_receipts(run.run_id)
    assert len(receipts) == 1
    assert receipts[0].success


@pytest.mark.asyncio
async def test_cross_store_saga_success(apply_service: ApplyService) -> None:
    plan = get_mock_plan()
    plan = OperationalWritePlan(
        **{
            **plan.model_dump(),
            "saga_steps": (
                SagaStep(
                    step_index=0,
                    transaction_groups=(
                        TransactionGroup(
                            group_id=str(uuid4()),
                            target_channel="mock_adapter",
                            operations=(
                                Operation(
                                    operation_id="op1",
                                    type=OperationType.INSERT,
                                    asset_id="source.mongodb.sales_inv",
                                    payload={"a": 1},
                                    target_channel="mock_adapter",
                                    dependencies=(),
                                ),
                            ),
                        ),
                    ),
                    rollback_feasibility=RollbackFeasibility.SAFE,
                ),
                SagaStep(
                    step_index=1,
                    transaction_groups=(
                        TransactionGroup(
                            group_id=str(uuid4()),
                            target_channel="mock_adapter",
                            operations=(
                                Operation(
                                    operation_id="op2",
                                    type=OperationType.DOMAIN_COMMAND,
                                    asset_id="platform.mongodb.support_cases",
                                    payload={"b": 2},
                                    target_channel="mock_adapter",
                                    dependencies=(),
                                ),
                            ),
                        ),
                    ),
                    rollback_feasibility=RollbackFeasibility.SAFE,
                ),
            ),
        }
    )
    run = await apply_service.apply_plan(plan)
    assert run.state == ExecutionRunState.APPLIED
    receipts = apply_service.repository.get_receipts(run.run_id)
    assert len(receipts) == 2
    assert receipts[0].success
    assert receipts[1].success


@pytest.mark.asyncio
async def test_rollback_blocked_by_dependent_activity(
    apply_service: ApplyService, rollback_service: RollbackService
) -> None:
    plan = get_mock_plan()

    blocked_step = SagaStep(
        step_index=plan.saga_steps[0].step_index,
        transaction_groups=plan.saga_steps[0].transaction_groups,
        rollback_feasibility=RollbackFeasibility.BLOCKED,
    )
    plan = OperationalWritePlan(**{**plan.model_dump(), "saga_steps": (blocked_step,)})

    run = await apply_service.apply_plan(plan)

    run_rolled_back = await rollback_service.rollback(run.run_id, plan)
    assert run_rolled_back.state == ExecutionRunState.ROLLBACK_BLOCKED


@pytest.mark.asyncio
async def test_rollback_success(
    apply_service: ApplyService, rollback_service: RollbackService
) -> None:
    plan = get_mock_plan()
    run = await apply_service.apply_plan(plan)

    run_rolled_back = await rollback_service.rollback(run.run_id, plan)
    assert run_rolled_back.state == ExecutionRunState.ROLLED_BACK
