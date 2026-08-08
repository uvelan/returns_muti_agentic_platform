"""A checkpoint written by one saver instance is visible to a brand-new saver instance
against the same Mongo data -- restart-durability, the entire point of a durable
reasoning platform over InMemorySaver."""

from __future__ import annotations

import pytest
from langgraph.graph import StateGraph
from typing_extensions import TypedDict

from return_platform.platform.reasoning.checkpoint import SystemStoreCheckpointSaver
from tests.reasoning.conftest import ReasoningTestFixture


class _State(TypedDict):
    value: int


def _double(state: _State) -> _State:
    return {"value": state["value"] * 2}


@pytest.mark.asyncio
async def test_a_new_saver_instance_sees_checkpoints_from_a_prior_one(
    reasoning_store: ReasoningTestFixture,
) -> None:
    first_saver = SystemStoreCheckpointSaver(reasoning_store.store, reasoning_store.encryptor)
    builder = StateGraph(_State)
    builder.add_node("double", _double)
    builder.set_entry_point("double")
    builder.set_finish_point("double")
    graph = builder.compile(checkpointer=first_saver)

    thread_config = {"configurable": {"thread_id": "restart-thread-1"}}
    result = await graph.ainvoke({"value": 21}, thread_config)
    assert result["value"] == 42

    # Simulate a process restart: a completely new saver instance, no shared Python
    # state with the one that wrote the checkpoint -- only the Mongo data is shared.
    second_saver = SystemStoreCheckpointSaver(reasoning_store.store, reasoning_store.encryptor)
    latest = await second_saver.aget_tuple(thread_config)

    assert latest is not None
    assert latest.checkpoint["channel_values"]["value"] == 42


@pytest.mark.asyncio
async def test_wrong_encryption_key_cannot_decrypt_another_process_instances_checkpoint(
    reasoning_store: ReasoningTestFixture,
) -> None:
    from return_platform.platform.secrets.envelope import (
        AesGcmEnvelopeEncryptor,
        EnvelopeDecryptionError,
    )

    saver = SystemStoreCheckpointSaver(reasoning_store.store, reasoning_store.encryptor)
    builder = StateGraph(_State)
    builder.add_node("double", _double)
    builder.set_entry_point("double")
    builder.set_finish_point("double")
    graph = builder.compile(checkpointer=saver)
    thread_config = {"configurable": {"thread_id": "restart-thread-2"}}
    await graph.ainvoke({"value": 1}, thread_config)

    wrong_key_saver = SystemStoreCheckpointSaver(
        reasoning_store.store, AesGcmEnvelopeEncryptor(key=b"1" * 32, key_ref="wrong-key")
    )
    with pytest.raises(EnvelopeDecryptionError):
        await wrong_key_saver.aget_tuple(thread_config)
