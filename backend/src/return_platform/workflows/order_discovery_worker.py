"""Temporal worker registration for the Order Discovery workflow."""

import re
from typing import Final

from temporalio.client import Client
from temporalio.worker import Worker

from return_platform.workflows.order_discovery_activities import OrderDiscoveryActivities
from return_platform.workflows.order_discovery_workflow import OrderDiscoveryWorkflow

__all__ = ["ORDER_DISCOVERY_TASK_QUEUE", "create_order_discovery_worker"]

ORDER_DISCOVERY_TASK_QUEUE: Final = "return-platform-order-discovery-v1"
_TASK_QUEUE_PATTERN: Final = re.compile(r"^[a-z][a-z0-9-]{0,126}$")


def create_order_discovery_worker(
    client: Client,
    activities: OrderDiscoveryActivities,
    *,
    task_queue: str = ORDER_DISCOVERY_TASK_QUEUE,
) -> Worker:
    """Create one worker with the exact workflow and activity."""
    if not isinstance(task_queue, str) or _TASK_QUEUE_PATTERN.fullmatch(task_queue) is None:
        raise ValueError("task_queue is invalid")
    return Worker(
        client,
        task_queue=task_queue,
        workflows=(OrderDiscoveryWorkflow,),
        activities=(activities.run_order_discovery_turn,),
    )
