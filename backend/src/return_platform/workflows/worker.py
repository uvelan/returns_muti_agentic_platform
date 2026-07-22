"""Temporal worker registration for the durable Return workflow."""

import re
from typing import Final

from temporalio.client import Client
from temporalio.worker import Worker

from return_platform.workflows.activities import ReturnSessionActivities
from return_platform.workflows.persistence import ReturnSessionRepositoryPort
from return_platform.workflows.return_workflow import ReturnWorkflow

__all__ = ["RETURN_WORKFLOW_TASK_QUEUE", "create_return_workflow_worker"]

RETURN_WORKFLOW_TASK_QUEUE: Final = "return-platform-return-v1"
_TASK_QUEUE_PATTERN: Final = re.compile(r"^[a-z][a-z0-9-]{0,126}$")


def create_return_workflow_worker(
    client: Client,
    repository: ReturnSessionRepositoryPort,
    *,
    task_queue: str = RETURN_WORKFLOW_TASK_QUEUE,
) -> Worker:
    """Create one worker with the exact workflow and persistence activities."""
    if not isinstance(task_queue, str) or _TASK_QUEUE_PATTERN.fullmatch(task_queue) is None:
        raise ValueError("task_queue is invalid")
    activities = ReturnSessionActivities(repository)
    return Worker(
        client,
        task_queue=task_queue,
        workflows=(ReturnWorkflow,),
        activities=(
            activities.initialize_return_session,
            activities.transition_return_session,
        ),
    )
