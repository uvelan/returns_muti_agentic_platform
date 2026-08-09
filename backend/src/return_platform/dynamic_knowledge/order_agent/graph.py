"""Compiles the Order Discovery reasoning StateGraph from graph_nodes.py's
node factories -- the wiring/topology only lives here; every node's actual
behavior lives in graph_nodes.py.
"""

from __future__ import annotations

from collections.abc import Hashable
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from return_platform.dynamic_knowledge.order_agent.graph_nodes import (
    GraphDependencies,
    TurnRuntimeContext,
    make_clarify_node,
    make_decide_node,
    make_get_schema_node,
    make_graph_query_node,
    make_order_search_node,
    make_out_of_scope_node,
    make_replan_node,
    make_request_on_demand_sync_node,
    make_respond_node,
    make_validate_action_node,
    route_after_action,
    route_after_correctable_node,
    route_after_validate_action,
)
from return_platform.dynamic_knowledge.order_agent.state import OrderAgentGraphState

_AFTER_ACTION_TARGETS: dict[Hashable, str] = {
    "out_of_scope": "out_of_scope",
    "validate_action": "validate_action",
}
_AFTER_VALIDATE_ACTION_TARGETS: dict[Hashable, str] = {
    **_AFTER_ACTION_TARGETS,
    "get_schema": "get_schema",
    "graph_query": "graph_query",
    "order_search": "order_search",
    "request_on_demand_sync": "request_on_demand_sync",
    "clarify": "clarify",
    "replan": "replan",
    "respond": "respond",
}
_AFTER_CORRECTABLE_NODE_TARGETS: dict[Hashable, str] = {"decide": "decide", **_AFTER_ACTION_TARGETS}


def _route_after_terminal_node(state: dict[str, Any]) -> str:
    """Shared router for respond/clarify: a correction re-routes exactly like a
    fresh decide() would; success ends the graph."""
    if state.get("_corrected", False):
        return route_after_action(state)
    return "__end__"


_AFTER_TERMINAL_NODE_TARGETS: dict[Hashable, str] = {"__end__": END, **_AFTER_ACTION_TARGETS}


def build_order_agent_graph(
    deps: GraphDependencies, *, checkpointer: BaseCheckpointSaver[str] | None = None
) -> CompiledStateGraph[OrderAgentGraphState, TurnRuntimeContext]:
    graph: StateGraph[OrderAgentGraphState, TurnRuntimeContext] = StateGraph(
        OrderAgentGraphState, context_schema=TurnRuntimeContext
    )

    graph.add_node("decide", make_decide_node(deps))
    graph.add_node("validate_action", make_validate_action_node(deps))
    graph.add_node("out_of_scope", make_out_of_scope_node())
    graph.add_node("get_schema", make_get_schema_node(deps))
    graph.add_node("graph_query", make_graph_query_node(deps))
    graph.add_node("order_search", make_order_search_node(deps))
    graph.add_node("request_on_demand_sync", make_request_on_demand_sync_node(deps))
    graph.add_node("clarify", make_clarify_node(deps))
    graph.add_node("replan", make_replan_node())
    graph.add_node("respond", make_respond_node(deps))

    graph.set_entry_point("decide")
    graph.add_conditional_edges("decide", route_after_action, _AFTER_ACTION_TARGETS)
    graph.add_conditional_edges(
        "validate_action", route_after_validate_action, _AFTER_VALIDATE_ACTION_TARGETS
    )
    graph.add_edge("get_schema", "decide")
    graph.add_conditional_edges(
        "graph_query", route_after_correctable_node, _AFTER_CORRECTABLE_NODE_TARGETS
    )
    graph.add_edge("order_search", "decide")
    graph.add_conditional_edges(
        "request_on_demand_sync", route_after_correctable_node, _AFTER_CORRECTABLE_NODE_TARGETS
    )
    graph.add_conditional_edges("clarify", _route_after_terminal_node, _AFTER_TERMINAL_NODE_TARGETS)
    graph.add_edge("replan", "decide")
    graph.add_conditional_edges("respond", _route_after_terminal_node, _AFTER_TERMINAL_NODE_TARGETS)
    # out_of_scope always raises OrderAgentFailure before returning -- this edge
    # is structural only, never actually traversed.
    graph.add_edge("out_of_scope", END)

    return graph.compile(checkpointer=checkpointer)
