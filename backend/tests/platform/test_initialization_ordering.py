"""Focused check: module initialization respects declared dependency edges rather
than just "the order the caller supplied" (design doc section 2.1 step 12).

A module may use its resolved ports during initialize() -- if it depends on another
module's capability, that module's initialize() must have already completed.
"""

from __future__ import annotations

import pytest

from return_platform.platform.modules.descriptor import ModuleDescriptor, ModuleKind
from return_platform.platform.modules.exceptions import (
    InitializationCycle,
    MissingInitializationDependency,
)
from return_platform.platform.modules.lifecycle import topological_order


def _descriptor(module_id: str, *depends_on: str) -> ModuleDescriptor:
    return ModuleDescriptor(
        module_id=module_id,
        module_kind=ModuleKind.BUSINESS,
        implementation_id=f"built_in.{module_id}",
        version="1.0.0",
        capabilities=frozenset(),
        configuration_schema="modules.example",
        required_platform_capabilities=frozenset(),
        initialization_dependencies=frozenset(depends_on),
    )


def test_modules_initialize_in_dependency_order() -> None:
    # graph_schema_analyzer depends on both ai and graph; graph depends on configuration
    configuration = _descriptor("configuration")
    graph = _descriptor("graph", "configuration")
    ai = _descriptor("ai")
    analyzer = _descriptor("graph_schema_analyzer", "ai", "graph")

    order = topological_order([analyzer, ai, graph, configuration])

    assert order.index("configuration") < order.index("graph")
    assert order.index("graph") < order.index("graph_schema_analyzer")
    assert order.index("ai") < order.index("graph_schema_analyzer")
    assert set(order) == {"configuration", "graph", "ai", "graph_schema_analyzer"}


def test_initialization_cycle_fails_startup() -> None:
    a = _descriptor("a", "b")
    b = _descriptor("b", "a")

    with pytest.raises(InitializationCycle):
        topological_order([a, b])


def test_self_dependency_fails_as_a_cycle() -> None:
    a = _descriptor("a", "a")

    with pytest.raises(InitializationCycle):
        topological_order([a])


def test_missing_initialization_dependency_fails_startup() -> None:
    a = _descriptor("a", "nonexistent")

    with pytest.raises(MissingInitializationDependency):
        topological_order([a])
