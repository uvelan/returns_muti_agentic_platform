"""Registration tests for the dedicated Return Temporal worker."""

from collections.abc import Callable, Sequence
from typing import Any, cast

import pytest
from temporalio import activity, workflow
from temporalio.client import Client

from return_platform.workflows import worker as worker_module
from return_platform.workflows.persistence import (
    ReturnSessionPersistenceReceipt,
    ReturnSessionRepositoryPort,
)
from return_platform.workflows.worker import (
    RETURN_WORKFLOW_TASK_QUEUE,
    create_return_workflow_worker,
)


class _UnusedRepository:
    async def initialize(self, *args: object) -> ReturnSessionPersistenceReceipt:
        raise AssertionError("not called")

    async def transition(self, *args: object) -> ReturnSessionPersistenceReceipt:
        raise AssertionError("not called")


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
    repository = cast(ReturnSessionRepositoryPort, _UnusedRepository())
    monkeypatch.setattr(worker_module, "Worker", fake_worker)

    created = create_return_workflow_worker(client, repository)

    assert created is sentinel
    assert captured == {
        "client": client,
        "task_queue": RETURN_WORKFLOW_TASK_QUEUE,
        "workflows": (
            "return-platform-return-v1",
            "return-platform-production-return-v2",
        ),
        "activities": (
            "initialize_return_session",
            "transition_return_session",
        ),
    }


@pytest.mark.parametrize(
    "task_queue",
    ("", "Return-Platform", "contains spaces", "-starts-with-dash", "x" * 128),
)
def test_rejects_invalid_task_queue_before_worker_creation(task_queue: str) -> None:
    client = cast(Client, object())
    repository = cast(ReturnSessionRepositoryPort, _UnusedRepository())

    with pytest.raises(ValueError, match="task_queue is invalid"):
        create_return_workflow_worker(client, repository, task_queue=task_queue)


def test_standalone_worker_uses_only_platform_persistence_boundary() -> None:
    source = __import__("inspect").getsource(worker_module)

    assert "ReturnSessionRepositoryPort" in source
    assert "ReturnSessionActivities" in source
    assert "data_platform.sources" not in source
    assert "openai" not in source.lower()
