import uuid
from collections.abc import Sequence

from .write_models import Operation, TransactionGroup


def partition_transaction_groups(operations: Sequence[Operation]) -> list[TransactionGroup]:
    # Group operations by target_channel consecutively
    # If the channel changes, it creates a new group.

    if not operations:
        return []

    groups = []
    current_channel = operations[0].target_channel
    current_ops = []

    for op in operations:
        if op.target_channel == current_channel:
            current_ops.append(op)
        else:
            groups.append(
                TransactionGroup(
                    group_id=str(uuid.uuid4()),
                    target_channel=current_channel,
                    operations=tuple(current_ops),
                )
            )
            current_channel = op.target_channel
            current_ops = [op]

    if current_ops:
        groups.append(
            TransactionGroup(
                group_id=str(uuid.uuid4()),
                target_channel=current_channel,
                operations=tuple(current_ops),
            )
        )

    return groups
