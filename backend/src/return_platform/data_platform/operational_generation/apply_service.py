import logging
from datetime import UTC, datetime
from uuid import uuid4

from return_platform.data_platform.operational_generation.apply_models import (
    ExecutionRun,
    ExecutionRunState,
)
from return_platform.data_platform.operational_generation.execution_lock import ExecutionLock
from return_platform.data_platform.operational_generation.execution_repository import (
    ExecutionRepository,
)
from return_platform.data_platform.operational_generation.metrics import global_execution_metrics
from return_platform.data_platform.operational_generation.post_write_validation import (
    validate_post_write,
)
from return_platform.data_platform.operational_generation.saga import execute_saga
from return_platform.data_platform.operational_generation.write_models import OperationalWritePlan

logger = logging.getLogger(__name__)


class ApplyService:
    def __init__(self, repository: ExecutionRepository, lock: ExecutionLock) -> None:
        self.repository = repository
        self.lock = lock

    async def apply_plan(self, plan: OperationalWritePlan) -> ExecutionRun:
        existing_run = self.repository.get_run_by_plan(plan.plan_id)
        if existing_run:
            if existing_run.state in (ExecutionRunState.APPLYING, ExecutionRunState.APPLIED):
                return existing_run

            if existing_run.state in (
                ExecutionRunState.PARTIALLY_FAILED,
                ExecutionRunState.COMPENSATING,
                ExecutionRunState.COMPENSATED,
                ExecutionRunState.ROLLING_BACK,
                ExecutionRunState.ROLLED_BACK,
                ExecutionRunState.ROLLBACK_BLOCKED,
                ExecutionRunState.ROLLBACK_FAILED,
            ):
                raise ValueError(f"Cannot apply plan because run is in state {existing_run.state}")

            run = existing_run
        else:
            run = ExecutionRun(
                run_id=uuid4(),
                plan_id=plan.plan_id,
                state=ExecutionRunState.APPROVED,
                started_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            self.repository.save_run(run)

        if not self.lock.acquire(run.run_id):
            raise RuntimeError("Concurrent apply execution rejected")

        try:
            await execute_saga(run, plan, self.repository)
            if run.state == ExecutionRunState.APPLIED:
                is_valid = await validate_post_write(run, plan)
                if not is_valid:
                    logger.error("Post write validation failed")

            global_execution_metrics.record_run(success=(run.state == ExecutionRunState.APPLIED))
            return run
        finally:
            self.lock.release(run.run_id)
