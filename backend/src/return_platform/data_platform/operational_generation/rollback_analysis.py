from collections.abc import Sequence

from .write_models import OperationType, RollbackFeasibility, SagaStep


def classify_rollback_feasibility(steps: Sequence[SagaStep]) -> RollbackFeasibility:
    # Basic classification:
    # If any step is domain command, it might require compensation.
    # For now, if all are inserts to source, it's safe.

    requires_compensation = False

    for step in steps:
        for tg in step.transaction_groups:
            for op in tg.operations:
                if op.type == OperationType.DOMAIN_COMMAND:
                    requires_compensation = True

    if requires_compensation:
        return RollbackFeasibility.COMPENSATION_REQUIRED

    return RollbackFeasibility.SAFE
