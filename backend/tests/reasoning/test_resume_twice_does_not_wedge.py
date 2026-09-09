"""A paused checkpoint can be resumed more than once.

LangGraph accumulates the null task's input writes across resumes and hands the
saver the old ones plus the new. Under "first write wins" a checkpoint resumed
twice held two `correlation_id`s and `apply_writes` refused it -- and refused
`aget_state` too, since building the snapshot replays the same writes. Observed
2026-09-09 on a turn whose first resume timed out: every retry failed, for good.

This drives the real saver against Mongo through the sequence that wedged it."""

from __future__ import annotations

import pytest
from langgraph.graph import StateGraph
from langgraph.types import Command, interrupt
from typing_extensions import TypedDict

from return_platform.platform.reasoning.checkpoint import NULL_TASK_ID, SystemStoreCheckpointSaver
from tests.reasoning.conftest import ReasoningTestFixture


class _State(TypedDict, total=False):
    correlation_id: str | None
    answer: str | None
    answered: bool


def _ask(state: _State) -> _State:
    answer = interrupt("which branch?")
    return {"answer": answer, "answered": True}


def _graph(saver: SystemStoreCheckpointSaver):
    builder = StateGraph(_State)
    builder.add_node("ask", _ask)
    builder.set_entry_point("ask")
    builder.set_finish_point("ask")
    return builder.compile(checkpointer=saver)


@pytest.mark.asyncio
async def test_a_second_resume_after_a_dead_first_one_still_applies(
    reasoning_store: ReasoningTestFixture,
) -> None:
    saver = SystemStoreCheckpointSaver(reasoning_store.store, reasoning_store.encryptor)
    graph = _graph(saver)
    config = {"configurable": {"thread_id": "resume-twice-1"}}

    paused = await graph.ainvoke({"correlation_id": "r0"}, config)
    assert "__interrupt__" in paused

    # First resume: LangGraph records the input writes on the paused
    # checkpoint. Simulate the attempt dying before it advanced by writing the
    # same input a second time, which is what a retried activity does.
    paused_config = (await saver.aget_tuple(config)).config
    await saver.aput_writes(paused_config, [("correlation_id", "r1")], NULL_TASK_ID)
    await saver.aput_writes(
        paused_config, [("correlation_id", "r1"), ("correlation_id", "r2")], NULL_TASK_ID
    )

    stored = await saver.aget_tuple(config)
    assert stored is not None
    null_writes = [w for w in stored.pending_writes if w[0] == NULL_TASK_ID]
    assert [w[1:] for w in null_writes] == [("correlation_id", "r2")], (
        "one input write per channel, the last one given"
    )

    # Resuming into that as it stands is what raised InvalidUpdateError: the
    # loop loads r2, appends r3, and refuses two values for one channel. The
    # coordinator discards the dead attempt's input first.
    discarded = await saver.adiscard_input_writes(paused_config)
    assert discarded == 1
    stored = await saver.aget_tuple(config)
    assert stored is not None
    assert not [w for w in stored.pending_writes if w[0] == NULL_TASK_ID]
    assert [w for w in stored.pending_writes if w[1] == "__interrupt__"], (
        "the interrupt itself is the paused task's write and must survive the discard"
    )

    final = await graph.ainvoke(Command(resume="LAKEWOOD", update={"correlation_id": "r3"}), config)
    assert final["answered"] is True
    assert final["answer"] == "LAKEWOOD"
    assert final["correlation_id"] == "r3"
