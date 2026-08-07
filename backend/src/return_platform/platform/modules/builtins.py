"""Allowlisted implementation_id -> factory table.

Configuration never supplies an import path. Each concrete module implementation
registers itself here (or via a controlled package entry point) at import time; the
module registry resolves implementation_id strings only against this table (plan
Phase 1A security constraint; design doc rule 3.3).
"""

from __future__ import annotations

from return_platform.platform.modules.exceptions import (
    DuplicateImplementation,
    ModuleNotRegistered,
)


class BuiltinModuleFactories:
    """Process-wide allowlist of implementation_id -> factory object."""

    def __init__(self) -> None:
        self._factories: dict[str, object] = {}

    def register(self, implementation_id: str, factory: object) -> None:
        if implementation_id in self._factories:
            raise DuplicateImplementation(
                f"implementation_id {implementation_id!r} is already registered"
            )
        self._factories[implementation_id] = factory

    def resolve(self, implementation_id: str) -> object:
        try:
            return self._factories[implementation_id]
        except KeyError as exc:
            raise ModuleNotRegistered(
                f"implementation_id {implementation_id!r} is not an allowlisted built-in"
            ) from exc

    def all_ids(self) -> tuple[str, ...]:
        return tuple(self._factories)


builtin_module_factories = BuiltinModuleFactories()
