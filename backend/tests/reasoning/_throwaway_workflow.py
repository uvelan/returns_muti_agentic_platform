"""A minimal, test-only Temporal workflow used to prove real signal delivery.

Isolated in its own module with NO other imports: Temporal's workflow sandbox
re-validates the entire containing module for determinism on every worker start, so a
module holding a `@workflow.defn` must not import pymongo/cryptography/anything
non-deterministic, even transitively -- confirmed the hard way (see the execution
ledger's "real bugs found" section for Phase 5A).
"""

import asyncio

from temporalio import workflow


@workflow.defn
class ThrowawayReasoningTestWorkflow:
    def __init__(self) -> None:
        self._received_command_ids: list[str] = []

    @workflow.run
    async def run(self) -> None:
        await workflow.wait_condition(lambda: len(self._received_command_ids) > 0)
        await asyncio.sleep(2)

    @workflow.signal(name="REASONING_ABANDONED")
    def on_reasoning_abandoned(self, command_id: str) -> None:
        if command_id not in self._received_command_ids:
            self._received_command_ids.append(command_id)

    @workflow.query
    def received_command_ids(self) -> list[str]:
        return list(self._received_command_ids)
