"""SystemStoreCheckpointSaver resolves storage via SystemStore -> logical structure ->
configured physical collection -- never a hardcoded collection name, so repointing the
manifest is the only way to repoint storage."""

from __future__ import annotations

import pytest
from langgraph.graph import StateGraph
from typing_extensions import TypedDict

from return_platform.platform.reasoning.checkpoint import SystemStoreCheckpointSaver
from tests.reasoning.conftest import ReasoningTestFixture


class _State(TypedDict):
    value: int


def _increment(state: _State) -> _State:
    return {"value": state["value"] + 1}


@pytest.mark.asyncio
async def test_checkpoints_land_in_the_manifest_declared_physical_collection(
    reasoning_store: ReasoningTestFixture,
) -> None:
    saver = SystemStoreCheckpointSaver(reasoning_store.store, reasoning_store.encryptor)
    builder = StateGraph(_State)
    builder.add_node("increment", _increment)
    builder.set_entry_point("increment")
    builder.set_finish_point("increment")
    graph = builder.compile(checkpointer=saver)

    thread_config = {"configurable": {"thread_id": "manifest-thread-1"}}
    await graph.ainvoke({"value": 1}, thread_config)

    physical_name = reasoning_store.physical_name("reasoning_checkpoints")
    raw_collection = reasoning_store.client.get_database("platform").get_collection(physical_name)
    count = await raw_collection.count_documents({"thread_id": "manifest-thread-1"})
    assert count > 0, (
        f"expected checkpoints in the manifest-declared physical collection "
        f"{physical_name!r}, found none"
    )
