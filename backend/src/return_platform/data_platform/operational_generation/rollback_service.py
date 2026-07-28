import logging
from datetime import UTC, datetime
from uuid import UUID

from return_platform.data_platform.operational_generation.apply_models import (
    ExecutionRun,
    ExecutionRunState,
)
from return_platform.data_platform.operational_generation.compensation import compensate_saga
from return_platform.data_platform.operational_generation.execution_repository import (
    ExecutionRepository,
)
from return_platform.data_platform.operational_generation.write_models import (
    OperationalWritePlan,
    RollbackFeasibility,
)

logger = logging.getLogger(__name__)


class RollbackService:
    def __init__(self, repository: ExecutionRepository) -> None:
        self.repository = repository

    async def rollback(self, run_id: UUID, plan: OperationalWritePlan) -> ExecutionRun:
        run = self.repository.get_run(run_id)
        if not run:
            raise ValueError("Run not found")

        if run.state not in (ExecutionRunState.APPLIED, ExecutionRunState.PARTIALLY_FAILED):
            raise ValueError(f"Cannot rollback run in state {run.state}")

        for step in plan.saga_steps:
            if step.rollback_feasibility == RollbackFeasibility.BLOCKED:
                run.state = ExecutionRunState.ROLLBACK_BLOCKED
                run.updated_at = datetime.now(UTC)
                self.repository.save_run(run)
                return run

        run.state = ExecutionRunState.ROLLING_BACK
        run.updated_at = datetime.now(UTC)
        self.repository.save_run(run)

        try:
            await compensate_saga(run, plan, self.repository)
            if run.state == ExecutionRunState.COMPENSATED:
                run.state = ExecutionRunState.ROLLED_BACK
            elif run.state == ExecutionRunState.PARTIALLY_FAILED:
                run.state = ExecutionRunState.ROLLBACK_FAILED
        except Exception as e:
            logger.error(f"Rollback failed: {e}")
            run.state = ExecutionRunState.ROLLBACK_FAILED
            run.error = str(e)

        run.updated_at = datetime.now(UTC)
        self.repository.save_run(run)
        return run
