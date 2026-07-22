"""Terminate stale running workflows created only by the live validator."""

import asyncio
import os

from temporalio.client import Client

_WORKFLOW_PREFIX = "return-live-validation-"


async def _cleanup() -> None:
    temporal_target = os.environ.get("PLATFORM_TEMPORAL_TARGET")
    if temporal_target is None:
        raise RuntimeError("PLATFORM_TEMPORAL_TARGET is required")
    client = await Client.connect(temporal_target)
    executions = client.list_workflows(
        'ExecutionStatus="Running" AND WorkflowId STARTS_WITH "return-live-validation-"',
        limit=100,
    )
    terminated = 0
    async for execution in executions:
        if not execution.id.startswith(_WORKFLOW_PREFIX):
            raise RuntimeError("Temporal visibility query returned an out-of-scope workflow")
        await client.get_workflow_handle(
            execution.id,
            run_id=execution.run_id,
        ).terminate(reason="cleanup stale live-validation execution")
        terminated += 1
    print(f"Stale live-validation workflows terminated: {terminated}")


if __name__ == "__main__":
    asyncio.run(_cleanup())
