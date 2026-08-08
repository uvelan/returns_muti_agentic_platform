"""Reasoning thread ID allocation.

A thread is one reasoning *attempt*, not one conversation -- reusing
`order-discovery:<conversation_id>` across turns would carry stale working state
(`final_result`, `candidate_refs`, `query_budget`, `search_plan`, a stale
`clarification`) into the next turn, making correctness depend on every state field
being perfectly reset on every turn boundary. A new turn (or a `GenerationChanged`
restart) gets a genuinely new thread instead.

`turn_id` is allocated by the canonical conversation write, not inside the agent, so it
stays stable across Temporal retries -- this factory only formats the ID, it never
generates a `turn_id` itself.

The Analyzer is deliberately not per-turn: an `AnalysisSession` is itself the unit of
work, so its thread ID has no turn/attempt component. Do not "fix" this symmetrically.
"""

from __future__ import annotations

_ORDER_DISCOVERY_PREFIX = "order-discovery"
_GRAPH_SCHEMA_PREFIX = "graph-schema"


class ReasoningThreadIdFactory:
    @staticmethod
    def order_discovery_thread_id(*, conversation_id: str, turn_id: str, attempt: int) -> str:
        if attempt < 1:
            raise ValueError(f"attempt must be >= 1, got {attempt}")
        return f"{_ORDER_DISCOVERY_PREFIX}:{conversation_id}:{turn_id}:{attempt}"

    @staticmethod
    def graph_schema_thread_id(*, analysis_id: str) -> str:
        return f"{_GRAPH_SCHEMA_PREFIX}:{analysis_id}"
