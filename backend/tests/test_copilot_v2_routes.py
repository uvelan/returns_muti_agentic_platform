from __future__ import annotations

from return_platform.api.copilot_v2 import router


def test_v2_router_exposes_only_copilot_operations() -> None:
    paths = {route.path for route in router.routes}

    assert paths == {
        "/api/v2/copilot/chat",
        "/api/v2/copilot/conversations",
        "/api/v2/copilot/conversations/{conversation_id}",
        "/api/v2/copilot/conversations/{conversation_id}/chat",
        "/api/v2/copilot/conversations/{conversation_id}/confirm",
        "/api/v2/copilot/conversations/{conversation_id}/details",
        "/api/v2/copilot/conversations/{conversation_id}/messages",
    }
    assert all(path.startswith("/api/v2/copilot") for path in paths)
