"""Registration tests for the dedicated Order Discovery Temporal worker."""

from collections.abc import Callable, Sequence
from typing import Any, cast

import pytest
from temporalio import activity, workflow
from temporalio.client import Client

from return_platform.workflows import order_discovery_worker as worker_module
from return_platform.workflows.order_discovery_activities import OrderDiscoveryActivities
from return_platform.workflows.order_discovery_worker import (
    ORDER_DISCOVERY_TASK_QUEUE,
    create_order_discovery_worker,
)


def test_registers_exact_workflow_and_activity_names(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    sentinel = object()

    def fake_worker(
        client: Client,
        *,
        task_queue: str,
        workflows: Sequence[type],
        activities: Sequence[Callable[..., Any]],
    ) -> object:
        captured.update(
            client=client,
            task_queue=task_queue,
            workflows=tuple(workflow._Definition.must_from_class(item).name for item in workflows),
            activities=tuple(
                activity._Definition.must_from_callable(item).name for item in activities
            ),
        )
        return sentinel

    client = cast(Client, object())
    activities = OrderDiscoveryActivities(
        coordinator=cast(Any, object()), schema=cast(Any, object())
    )
    monkeypatch.setattr(worker_module, "Worker", fake_worker)

    created = create_order_discovery_worker(client, activities)

    assert created is sentinel
    assert captured == {
        "client": client,
        "task_queue": ORDER_DISCOVERY_TASK_QUEUE,
        "workflows": ("return-platform-order-discovery-v1",),
        "activities": ("run_order_discovery_turn",),
    }


@pytest.mark.parametrize(
    "task_queue",
    ("", "Return-Platform", "contains spaces", "-starts-with-dash", "x" * 128),
)
def test_rejects_invalid_task_queue_before_worker_creation(task_queue: str) -> None:
    client = cast(Client, object())
    activities = OrderDiscoveryActivities(
        coordinator=cast(Any, object()), schema=cast(Any, object())
    )

    with pytest.raises(ValueError, match="task_queue is invalid"):
        create_order_discovery_worker(client, activities, task_queue=task_queue)
