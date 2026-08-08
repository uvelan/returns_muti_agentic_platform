"""Architecture guard: no live API route lets a caller push a return session
to an arbitrary target stage.

Stage transitions must go through the durable Temporal-coordinated workflow
(`ReturnWorkflow.complete_stage`, driven by `ReturnOrchestrator`), evidenced
by a typed, stage-specific activity result -- never a generic
"advance this session to stage X" HTTP call. `dependency_simulator.py`'s
`/operations/{operation_id}/advance` route is a known, explicitly-excluded
exception: it drives a synthetic dependency-simulation harness for local/dev
testing, not a real ReturnSession.
"""

from __future__ import annotations

import importlib
import pkgutil

from fastapi.routing import APIRoute

import return_platform.api as api_package

_EXCLUDED_MODULES = frozenset({"return_platform.api.dependency_simulator"})


def _iter_api_routes() -> list[tuple[str, APIRoute]]:
    routes: list[tuple[str, APIRoute]] = []
    for module_info in pkgutil.iter_modules(api_package.__path__, prefix="return_platform.api."):
        if module_info.name in _EXCLUDED_MODULES:
            continue
        module = importlib.import_module(module_info.name)
        router = getattr(module, "router", None)
        if router is None:
            continue
        for route in router.routes:
            if isinstance(route, APIRoute):
                routes.append((module_info.name, route))
    return routes


def test_no_route_advances_a_return_session_to_an_arbitrary_stage() -> None:
    offending = [
        f"{module_name}: {route.methods} {route.path}"
        for module_name, route in _iter_api_routes()
        if "advance" in route.path.lower()
    ]
    assert offending == [], (
        "Found a generic 'advance' route outside the excluded dev/test harness. "
        "Return session stage transitions must go through the durable Temporal "
        f"workflow, not a generic HTTP advance call: {offending}"
    )


def test_dependency_simulator_advance_route_is_the_only_known_exception() -> None:
    """Guards the exclusion itself: if dependency_simulator ever stops defining
    an /advance route, the exclusion above is stale and should be removed."""
    module = importlib.import_module("return_platform.api.dependency_simulator")
    router = module.router
    paths = [route.path for route in router.routes if isinstance(route, APIRoute)]
    assert any("advance" in path.lower() for path in paths)
