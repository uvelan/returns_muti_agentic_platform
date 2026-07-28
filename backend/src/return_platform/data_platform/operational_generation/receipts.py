from datetime import UTC, datetime
from uuid import UUID, uuid4

from return_platform.data_platform.operational_generation.apply_models import StepReceipt


def create_receipt(
    run_id: UUID,
    step_index: int,
    target_channel: str,
    operations_count: int,
    success: bool,
    error: str | None = None,
) -> StepReceipt:
    return StepReceipt(
        receipt_id=uuid4(),
        run_id=run_id,
        step_index=step_index,
        executed_at=datetime.now(UTC),
        success=success,
        error=error,
        target_channel=target_channel,
        operations_count=operations_count,
    )
