"""Ordered module lifecycle: dependency-respecting initialization order, cleanup on
partial-initialization failure, reverse-order shutdown, and health rollup.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from return_platform.platform.modules.contracts import HealthStatus, ModuleHealth
from return_platform.platform.modules.descriptor import ModuleDescriptor
from return_platform.platform.modules.exceptions import (
    InitializationCycle,
    MissingInitializationDependency,
)


@runtime_checkable
class Initializable(Protocol):
    """The minimal shape initialize_all()/shutdown_all() need.

    Every ModuleRuntime satisfies this structurally; test doubles only need these two
    methods, not the full ModuleRuntime surface.
    """

    async def initialize(self) -> None: ...

    async def shutdown(self) -> None:
        """Must tolerate being called after initialize() raised partway through --
        e.g. a module that opened a connection pool and then failed a later
        validation step must still be able to close that pool here. initialize_all()
        calls shutdown() on a module whose own initialize() failed, not just on
        modules that succeeded before it."""


def topological_order(descriptors: Sequence[ModuleDescriptor]) -> tuple[str, ...]:
    """Order module_ids so every module's initialization_dependencies precede it.

    Raises MissingInitializationDependency if a declared dependency is not among
    `descriptors`. Raises InitializationCycle if the dependency graph has a cycle --
    a module depending on itself is a cycle of length one and is caught the same way,
    with no separate check needed.
    """
    by_id = {descriptor.module_id: descriptor for descriptor in descriptors}
    for descriptor in descriptors:
        for dependency in descriptor.initialization_dependencies:
            if dependency not in by_id:
                raise MissingInitializationDependency(
                    f"module {descriptor.module_id!r} depends on unregistered module {dependency!r}"
                )

    order: list[str] = []
    visited: set[str] = set()
    visiting: set[str] = set()

    def visit(module_id: str) -> None:
        if module_id in visited:
            return
        if module_id in visiting:
            raise InitializationCycle(
                f"initialization dependency cycle detected at module {module_id!r}"
            )
        visiting.add(module_id)
        for dependency in by_id[module_id].initialization_dependencies:
            visit(dependency)
        visiting.discard(module_id)
        visited.add(module_id)
        order.append(module_id)

    for descriptor in descriptors:
        visit(descriptor.module_id)

    return tuple(order)


async def initialize_all(modules: Sequence[Initializable]) -> None:
    """Initialize every module in order.

    Fails closed AND cleans up: if a module raises, that module itself is shut down
    first (it may have partially-initialized resources -- see Initializable.shutdown),
    then every module already initialized is shut down in reverse order, before the
    original exception propagates. The caller never holds a partially-initialized
    module list. If cleanup itself also raises, both the original failure and the
    cleanup failures are reported together.
    """
    initialized: list[Initializable] = []
    for module in modules:
        try:
            await module.initialize()
        except Exception as exc:
            cleanup_errors: list[Exception] = []
            try:
                await module.shutdown()
            except Exception as cleanup_exc:
                cleanup_errors.append(cleanup_exc)
            for already_initialized in reversed(initialized):
                try:
                    await already_initialized.shutdown()
                except Exception as cleanup_exc:
                    cleanup_errors.append(cleanup_exc)
            if cleanup_errors:
                raise ExceptionGroup(
                    "initialization failed and cleanup encountered further errors",
                    [exc, *cleanup_errors],
                ) from None
            raise
        initialized.append(module)


async def shutdown_all(modules: Sequence[Initializable]) -> None:
    """Shut down every module in reverse order, best-effort.

    Every module gets a shutdown attempt regardless of earlier failures; failures are
    collected and raised together so one module's failure never prevents another's
    cleanup from running.
    """
    errors: list[Exception] = []
    for module in reversed(modules):
        try:
            await module.shutdown()
        except Exception as exc:
            errors.append(exc)
    if errors:
        raise ExceptionGroup("module shutdown failed", errors)


def rollup_health(healths: Sequence[ModuleHealth]) -> HealthStatus:
    """Worst-of aggregation: any UNAVAILABLE wins, then any DEGRADED, else HEALTHY."""
    statuses = {health.status for health in healths}
    if HealthStatus.UNAVAILABLE in statuses:
        return HealthStatus.UNAVAILABLE
    if HealthStatus.DEGRADED in statuses:
        return HealthStatus.DEGRADED
    return HealthStatus.HEALTHY
