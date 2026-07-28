import logging

from return_platform.data_platform.operational_generation.apply_models import ExecutionRun
from return_platform.data_platform.operational_generation.graph_sync import (
    request_graph_sync,
    verify_graph_sync,
)
from return_platform.data_platform.operational_generation.write_models import OperationalWritePlan
from return_platform.operations.order_discovery.candidate_retriever import CandidateRetriever
from return_platform.operations.order_discovery.source_operations import SourceOperations

logger = logging.getLogger(__name__)


async def validate_post_write(run: ExecutionRun, plan: OperationalWritePlan) -> bool:
    # AIG9: Request graph sync after authoritative source writes
    run_id_str = str(run.run_id)
    assets = set()
    for step in plan.saga_steps:
        for tg in step.transaction_groups:
            for op in tg.operations:
                assets.add(op.asset_id)

    request_graph_sync(run_id_str, list(assets))

    # AIG9: Post-write verification
    if not SourceOperations.verify_source_integrity(run_id_str):
        return False

    sync_result = verify_graph_sync(run_id_str)
    if sync_result["status"] != "VERIFIED":
        return False

    # Verify Copilot lookup
    if not CandidateRetriever.verify_exact_lookup("DUMMY_ORDER_ID"):
        return False

    return True
