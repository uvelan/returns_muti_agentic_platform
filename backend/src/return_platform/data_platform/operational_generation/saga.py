import logging
from datetime import UTC, datetime

from return_platform.data_platform.operational_generation.apply_models import (
    ExecutionRun,
    ExecutionRunState,
)
from return_platform.data_platform.operational_generation.execution_repository import (
    ExecutionRepository,
)
from return_platform.data_platform.operational_generation.receipts import create_receipt
from return_platform.data_platform.operational_generation.transaction_executor import (
    execute_transaction_group,
)
from return_platform.data_platform.operational_generation.write_models import OperationalWritePlan

logger = logging.getLogger(__name__)


async def execute_saga(
    run: ExecutionRun, plan: OperationalWritePlan, repository: ExecutionRepository
) -> None:
    run.state = ExecutionRunState.APPLYING
    run.updated_at = datetime.now(UTC)
    repository.save_run(run)

    try:
        for idx, step in enumerate(plan.saga_steps):
            for tg in step.transaction_groups:
                try:
                    await execute_transaction_group(tg)
                    receipt = create_receipt(
                        run_id=run.run_id,
                        step_index=idx,
                        target_channel=tg.target_channel,
                        operations_count=len(tg.operations),
                        success=True,
                    )
                    repository.save_receipt(receipt)
                except Exception as e:
                    receipt = create_receipt(
                        run_id=run.run_id,
                        step_index=idx,
                        target_channel=tg.target_channel,
                        operations_count=len(tg.operations),
                        success=False,
                        error=str(e),
                    )
                    repository.save_receipt(receipt)
                    raise e

        run.state = ExecutionRunState.APPLIED
        run.updated_at = datetime.now(UTC)
        repository.save_run(run)
    except Exception as e:
        logger.error(f"Saga failed: {e}")
        run.state = ExecutionRunState.PARTIALLY_FAILED
        run.error = str(e)
        run.updated_at = datetime.now(UTC)
        repository.save_run(run)
        raise e
