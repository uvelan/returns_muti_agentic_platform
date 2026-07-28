from collections.abc import Sequence

from .write_models import OperationType, PlanImpact, SagaStep


def calculate_impact_summary(steps: Sequence[SagaStep]) -> PlanImpact:
    inserts = 0
    domains = 0
    syncs = 0
    channels = set()

    for step in steps:
        for tg in step.transaction_groups:
            channels.add(tg.target_channel)
            for op in tg.operations:
                if op.type == OperationType.INSERT:
                    inserts += 1
                elif op.type == OperationType.DOMAIN_COMMAND:
                    domains += 1
                elif op.type == OperationType.GRAPH_SYNC_REQUEST:
                    syncs += 1

    return PlanImpact(
        total_operations=inserts + domains + syncs,
        inserts=inserts,
        domain_commands=domains,
        graph_sync_requests=syncs,
        affected_channels=tuple(sorted(list(channels))),
    )
