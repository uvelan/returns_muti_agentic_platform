"""REBIND_ON_RESUME, and the stale-cache consequence that comes with it.

A clarification pause can last days. The paused checkpoint records the
generation the turn was reading, and every graph node reads
`graph_generation_id` out of that state -- so before this, a resumed turn kept
querying whatever generation it started on, however many rebuilds had happened
since. That is strict pinning, arrived at by accident rather than by policy, and
it is the opposite of the Phase 12 default.

The subtle half is the cache. `orderSearchCache` holds a `CandidateSet` stamped
with the generation it was built from, and `CandidateSet.validate_selection`
raises "candidate set belongs to a stale graph generation" on mismatch. Rebinding
the generation without dropping that cache would turn the associate's answer --
"the second one" -- into a hard error instead of a fresh search. Both halves are
asserted here; a rebind that only moved the id would pass a weaker test and fail
in production.
"""

from __future__ import annotations

from typing import Any, TypedDict

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from return_platform.dynamic_knowledge.order_agent.coordinator import _cache_for_generation
from return_platform.dynamic_knowledge.schema import AgentPolicy, GenerationBinding


class _RebindState(TypedDict, total=False):
    """Module level on purpose: `from __future__ import annotations` turns these
    into strings, and LangGraph resolves them with `get_type_hints` against the
    defining module's globals -- a TypedDict declared inside a test function
    cannot see its own imports and fails with a bare NameError."""

    graph_generation_id: str
    order_search_cache: dict[str, Any] | None
    answer: str


def _cache(generation_id: str) -> dict[str, object]:
    """The shape order_search node writes, trimmed to what matters here."""
    return {
        "signature": "sig-1",
        "evidenceRef": "evidence-1",
        "shown": 2,
        "totalFound": 7,
        "candidateSet": {
            "candidate_set_id": "cs-1",
            "graph_generation_id": generation_id,
            "candidate_ids": ["cand-1", "cand-2"],
        },
    }


def test_a_cache_from_the_same_generation_survives() -> None:
    cache = _cache("gen-1")
    assert _cache_for_generation(cache, "gen-1") is cache


def test_a_cache_from_a_previous_generation_is_dropped() -> None:
    """Paging through it would serve candidates from a generation that may
    already be retired, and selecting one raises from validate_selection."""
    assert _cache_for_generation(_cache("gen-old"), "gen-new") is None


def test_a_cache_without_a_candidate_set_is_left_alone() -> None:
    """It predates the stamp and carries no selection to invalidate; dropping it
    would discard a usable paging cursor for no safety gain."""
    cache = {"signature": "sig-1", "evidenceRef": "evidence-1", "shown": 2}
    assert _cache_for_generation(cache, "gen-new") is cache


def test_no_cache_stays_no_cache() -> None:
    assert _cache_for_generation(None, "gen-1") is None


def test_a_malformed_candidate_set_does_not_crash_the_turn() -> None:
    """Defensive because this runs on every turn against a persisted document
    that older code wrote: a shape surprise must not take the conversation
    down."""
    assert _cache_for_generation({"candidateSet": "not-a-dict"}, "gen-1") is not None
    assert _cache_for_generation({"candidateSet": {}}, "gen-1") is not None


def test_the_binding_default_is_rebind_not_pinning() -> None:
    """Phase 12 makes REBIND_ON_RESUME the platform default. If this ever
    flipped, conversations would silently start holding generations against
    retirement again -- the exact behaviour this slice removed."""
    policy = AgentPolicy(
        agent_id="order-discovery",
        task_queue="order-discovery",
        allowed_business_capabilities=frozenset({"order-discovery"}),
        allowed_roles=frozenset({"associate"}),
        allowed_entity_ids=frozenset({"order"}),
        standard_model_refs=("model-a",),
    )

    assert policy.generation_binding is GenerationBinding.REBIND_ON_RESUME


def test_strict_pinning_is_expressible() -> None:
    """The plan requires strict pinning "where configured" -- it has to be
    reachable from schema config, not just an enum nobody can select."""
    policy = AgentPolicy(
        agent_id="order-discovery",
        task_queue="order-discovery",
        generation_binding=GenerationBinding.STRICT_PINNING,
        allowed_business_capabilities=frozenset({"order-discovery"}),
        allowed_roles=frozenset({"associate"}),
        allowed_entity_ids=frozenset({"order"}),
        standard_model_refs=("model-a",),
    )

    assert policy.generation_binding is GenerationBinding.STRICT_PINNING


@pytest.mark.parametrize(
    "binding", [GenerationBinding.REBIND_ON_RESUME, GenerationBinding.STRICT_PINNING]
)
def test_binding_round_trips_through_configuration(binding: GenerationBinding) -> None:
    """Schema config is YAML, so the value has to survive being a plain string."""
    policy = AgentPolicy.model_validate(
        {
            "agent_id": "order-discovery",
            "task_queue": "order-discovery",
            "generation_binding": binding.value,
            "allowed_business_capabilities": ["order-discovery"],
            "allowed_roles": ["associate"],
            "allowed_entity_ids": ["order"],
            "standard_model_refs": ["model-a"],
        }
    )

    assert policy.generation_binding is binding


# --- the SDK mechanism the rebind depends on --------------------------------


@pytest.mark.asyncio
async def test_langgraph_applies_a_state_update_delivered_with_a_resume() -> None:
    """`Command(resume=..., update=...)` is the whole rebind mechanism.

    If `update` were ignored on a resume -- or applied before the interrupted
    node re-ran and then overwritten by it -- the coordinator would silently
    keep the stale generation and every test above would still pass. Verified
    against a real compiled graph rather than assumed from the docs.
    """

    def _ask(state: _RebindState) -> dict[str, Any]:
        reply = interrupt({"question": "which order?"})
        # Reads generation *after* resuming, exactly as the real nodes do.
        return {"answer": f"{reply}@{state['graph_generation_id']}"}

    graph = StateGraph(_RebindState)
    graph.add_node("ask", _ask)
    graph.add_edge(START, "ask")
    graph.add_edge("ask", END)
    compiled = graph.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "rebind-1"}}

    paused = await compiled.ainvoke(
        {
            "graph_generation_id": "gen-old",
            "order_search_cache": {"candidateSet": {"graph_generation_id": "gen-old"}},
        },
        config=config,  # type: ignore[arg-type]
    )
    assert paused.get("__interrupt__")

    resumed = await compiled.ainvoke(
        Command(
            resume="ORD-1",
            update={"graph_generation_id": "gen-new", "order_search_cache": None},
        ),
        config=config,  # type: ignore[arg-type]
    )

    assert resumed["graph_generation_id"] == "gen-new"
    assert resumed["order_search_cache"] is None
    # The node observed the rebound value, not the one it paused on.
    assert resumed["answer"] == "ORD-1@gen-new"
