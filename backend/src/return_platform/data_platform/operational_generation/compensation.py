import logging
from datetime import UTC, datetime

from return_platform.data_platform.operational_generation.apply_models import (
    ExecutionRun,
    ExecutionRunState,
)
from return_platform.data_platform.operational_generation.execution_repository import (
    ExecutionRepository,
)
from return_platform.data_platform.operational_generation.transaction_executor import (
    compensate_transaction_group,
)
from return_platform.data_platform.operational_generation.write_models import OperationalWritePlan

logger = logging.getLogger(__name__)


async def compensate_saga(
    run: ExecutionRun, plan: OperationalWritePlan, repository: ExecutionRepository
) -> None:
    receipts = repository.get_receipts(run.run_id)
    successful_step_indices = {r.step_index for r in receipts if r.success}

    run.state = ExecutionRunState.COMPENSATING
    run.updated_at = datetime.now(UTC)
    repository.save_run(run)

    partial = False
    delayed_graph_groups = []

    for idx, step in reversed(list(enumerate(plan.saga_steps))):
        if idx in successful_step_indices:
            for tg in reversed(step.transaction_groups):
                if tg.target_channel == "GRAPH_SYNC_ADAPTER":
                    delayed_graph_groups.append(tg)
                    continue
                try:
                    await compensate_transaction_group(tg)
                except Exception as e:
                    logger.error(f"Failed to compensate step {idx}, group {tg.target_channel}: {e}")
                    partial = True

    for tg in delayed_graph_groups:
        try:
            await compensate_transaction_group(tg)
        except Exception as e:
            logger.error(f"Failed to refresh graph projection after compensation: {e}")
            partial = True

    if partial:
        run.state = ExecutionRunState.PARTIALLY_FAILED
    else:
        run.state = ExecutionRunState.COMPENSATED

    run.updated_at = datetime.now(UTC)
    repository.save_run(run)
