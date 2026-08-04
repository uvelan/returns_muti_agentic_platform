from __future__ import annotations

from return_platform.dynamic_knowledge.api.order_agent import router


def test_v2_router_exposes_only_dynamic_order_agent_operation() -> None:
    paths = {route.path for route in router.routes}
    assert paths == {
        "/api/v2/order-agent/conversations/{conversation_id}/turns",
    }
    assert all(path.startswith("/api/v2/order-agent") for path in paths)
