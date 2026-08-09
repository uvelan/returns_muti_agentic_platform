"""Deprecated: re-exports `return_platform.ai.routing.routes` and `.selection`."""

from return_platform.ai.routing.routes import AIRoute, build_routes
from return_platform.ai.routing.selection import (
    AIRoutePool,
    CircuitState,
    RouteAcquireResult,
    RouteHealth,
)

__all__ = [
    "AIRoute",
    "AIRoutePool",
    "CircuitState",
    "RouteAcquireResult",
    "RouteHealth",
    "build_routes",
]
