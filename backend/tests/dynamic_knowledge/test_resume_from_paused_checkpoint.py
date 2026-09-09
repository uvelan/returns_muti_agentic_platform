"""A resumed turn answers into the checkpoint that is paused, not the dead
attempt on top of it.

A turn activity that times out mid-run -- a provider hanging past the ceiling
on 2026-09-09 -- leaves the thread's latest checkpoint past the `interrupt()`
the associate was answering. Resuming into it makes LangGraph refuse the
update ("Can receive only one value per step") on every retry, for good. The
locator reads the checkpointer's raw tuples: the poisoned checkpoint raises on
`aget_state` itself, so the graph-level view is exactly what cannot be used."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from return_platform.dynamic_knowledge.order_agent.coordinator import locate_paused_checkpoint


@dataclass
class _Tuple:
    """The parts of a `CheckpointTuple` the locator reads."""

    values: dict[str, Any]
    checkpoint_id: str
    pending_writes: list[tuple[str, str, Any]] = field(default_factory=list)
    config: dict[str, Any] = field(init=False)
    checkpoint: dict[str, Any] = field(init=False)

    def __post_init__(self) -> None:
        self.config = {"configurable": {"thread_id": "t", "checkpoint_id": self.checkpoint_id}}
        self.checkpoint = {"channel_values": dict(self.values)}


class _Saver:
    def __init__(self, history: list[_Tuple]) -> None:
        # Newest first, as `alist` yields.
        self._history = history

    async def alist(self, config: dict[str, Any]):
        for checkpoint in self._history:
            yield checkpoint


class _Graph:
    def __init__(self, history: list[_Tuple]) -> None:
        self.checkpointer = _Saver(history)

    async def aget_state(self, config: dict[str, Any]) -> Any:
        raise AssertionError("the locator must not build a state snapshot")


PAUSED = _Tuple(
    {"graph_generation_id": "g1", "step": "asked"},
    "cp-paused",
    [("task-1", "__interrupt__", ("which branch?",))],
)
DEAD = _Tuple(
    {"graph_generation_id": "g1", "step": "half-way"},
    "cp-dead",
    [("00000000-0000-0000-0000-000000000000", "correlation_id", "r1")],
)


@pytest.mark.asyncio
async def test_a_paused_latest_checkpoint_is_resumed_as_it_is() -> None:
    values, checkpoint_id, fork = await locate_paused_checkpoint(_Graph([PAUSED]), "t")
    assert values["step"] == "asked"
    assert checkpoint_id == "cp-paused"
    assert fork is False


@pytest.mark.asyncio
async def test_a_dead_attempt_on_top_is_skipped_for_the_paused_checkpoint_beneath() -> None:
    values, checkpoint_id, fork = await locate_paused_checkpoint(_Graph([DEAD, PAUSED]), "t")
    assert values["step"] == "asked"
    assert checkpoint_id == "cp-paused"
    assert fork is True


@pytest.mark.asyncio
async def test_a_thread_with_no_paused_checkpoint_resumes_the_latest() -> None:
    values, checkpoint_id, fork = await locate_paused_checkpoint(_Graph([DEAD]), "t")
    assert values["step"] == "half-way"
    assert checkpoint_id == "cp-dead"
    assert fork is False


@pytest.mark.asyncio
async def test_an_empty_thread_yields_nothing_to_pin() -> None:
    assert await locate_paused_checkpoint(_Graph([]), "t") == ({}, None, False)
