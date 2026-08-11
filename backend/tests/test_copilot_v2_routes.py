from __future__ import annotations

from return_platform.dynamic_knowledge.api.order_agent import router


def test_v2_router_exposes_only_dynamic_order_agent_operation() -> None:
    """Everything here is the order agent's own conversation resource.

    The versioned prefix is a known exception the frontend carries by name, so
    what matters is that it does not grow into a general surface: one write --
    the turn -- and reads of that same resource. A route for anything else
    belongs on a canonical path, not behind this exception.
    """
    paths = {route.path for route in router.routes}
    assert paths == {
        "/api/v2/order-agent/conversations",
        "/api/v2/order-agent/conversations/{conversation_id}/turns",
        "/api/v2/order-agent/conversations/{conversation_id}/transcript",
    }
    writes = {
        (route.path, method)
        for route in router.routes
        for method in getattr(route, "methods", set())
        if method not in {"GET", "HEAD"}
    }
    assert writes == {("/api/v2/order-agent/conversations/{conversation_id}/turns", "POST")}
    assert all(path.startswith("/api/v2/order-agent") for path in paths)
