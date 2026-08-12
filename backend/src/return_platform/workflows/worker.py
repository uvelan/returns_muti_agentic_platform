"""Temporal worker registration for the durable Return workflow."""

import re
from collections.abc import Callable
from typing import Any, Final

from temporalio.client import Client
from temporalio.worker import Worker

from return_platform.workflows.activities import ReturnSessionActivities
from return_platform.workflows.persistence import ReturnSessionRepositoryPort
from return_platform.workflows.production_return_workflow import ProductionReturnWorkflow
from return_platform.workflows.return_case_activities import ReturnCaseActivities
from return_platform.workflows.return_case_workflow import ReturnCaseWorkflow
from return_platform.workflows.return_workflow import ReturnWorkflow

__all__ = ["RETURN_WORKFLOW_TASK_QUEUE", "create_return_workflow_worker"]

RETURN_WORKFLOW_TASK_QUEUE: Final = "return-platform-return-v1"
_TASK_QUEUE_PATTERN: Final = re.compile(r"^[a-z][a-z0-9-]{0,126}$")


def create_return_workflow_worker(
    client: Client,
    repository: ReturnSessionRepositoryPort,
    *,
    task_queue: str = RETURN_WORKFLOW_TASK_QUEUE,
    case_activities: ReturnCaseActivities | None = None,
) -> Worker:
    """Create one worker with the exact workflows and persistence activities.

    `ReturnCaseWorkflow` is registered whenever its activities are supplied.
    Optional because it needs a platform Mongo client and a support service,
    which a caller running only the stage workflows has no reason to build --
    and registering a workflow whose activities are absent would give a case
    that starts and then stalls on its first activity, which is worse than one
    that cannot start.
    """
    if not isinstance(task_queue, str) or _TASK_QUEUE_PATTERN.fullmatch(task_queue) is None:
        raise ValueError("task_queue is invalid")
    activities = ReturnSessionActivities(repository)
    workflows: tuple[type, ...] = (ReturnWorkflow, ProductionReturnWorkflow)
    registered: tuple[Callable[..., Any], ...] = (
        activities.initialize_return_session,
        activities.transition_return_session,
    )
    if case_activities is not None:
        workflows = (*workflows, ReturnCaseWorkflow)
        registered = (
            *registered,
            case_activities.record_case_status,
            case_activities.request_bay_assignment,
            case_activities.draft_support_request,
            case_activities.open_support_work_item,
            case_activities.send_support_reminder,
            case_activities.record_support_outcome,
            case_activities.synchronize_return_records,
        )
    return Worker(
        client,
        task_queue=task_queue,
        workflows=workflows,
        activities=registered,
    )
