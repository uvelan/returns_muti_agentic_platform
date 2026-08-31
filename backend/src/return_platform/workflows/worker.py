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
            case_activities.resolve_business_deadline,
            # Names the customer on the case from the confirmed order. Without
            # it the only writer of `customer_name` is a reasoning model that is
            # not allowed to see one, so every case projects `customer: null`.
            case_activities.record_case_customer_identity,
            # **Was missing**, and found by
            # `test_every_activity_the_workflow_calls_is_registered_on_the_worker`
            # rather than by anyone reading this list. `_await_return_details`
            # calls it, no worker polled for it, and an unregistered activity
            # raises nothing and logs nothing -- the execution simply schedules
            # the task and waits. It is reached only when
            # `timings.return_details_required` is on, which is what kept the
            # stall from being noticed. Registered here beside its siblings, on
            # the same reasoning the gate's block below states: what a *case*
            # runs is decided by its pinned release, and a worker that has not
            # registered an activity leaves such a case stopped.
            case_activities.case_has_return_details,
            case_activities.request_bay_assignment,
            # The policy gate (3A.7). This list having exactly eight entries,
            # none of which evaluated a rule set, was the audit's proof that no
            # return on the case path was ever checked against a policy.
            case_activities.evaluate_case_eligibility,
            case_activities.draft_support_request,
            case_activities.open_support_work_item,
            case_activities.send_support_reminder,
            case_activities.record_support_outcome,
            case_activities.synchronize_return_records,
            # The template review gate (contracts.md sect. 6). Registered
            # unconditionally beside the rest rather than behind a flag on the
            # gate's configuration: whether a *case* runs the gate is decided
            # by its pinned release and its history patch marker, and a worker
            # that had not registered these would leave a case that legitimately
            # entered the gate stalled on an unknown activity -- exactly the
            # failure this list's own comment above describes.
            case_activities.record_template_draft,
            case_activities.record_template_revision,
            case_activities.rerender_template_draft,
            case_activities.hold_unsettled_reviews,
            case_activities.snapshot_sent_template,
            # The clarification round-trip (contracts.md sect. 9, 10). Same
            # reasoning as the gate's activities directly above: whether a case
            # asks a clarification is decided by its pinned release, and a
            # worker that had not registered these would strand an answered
            # clarification on an unknown activity -- with the associate's
            # answer on file, unrelayed, and Support still waiting.
            case_activities.record_clarification_answer,
            case_activities.relay_clarification_to_support,
        )
    return Worker(
        client,
        task_queue=task_queue,
        workflows=workflows,
        activities=registered,
    )
