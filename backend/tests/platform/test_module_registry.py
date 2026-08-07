"""Focused check for ModuleRegistry bookkeeping: uniqueness and capability-declaration
validation (Phase 1A). Construction/health are added once ModuleRuntime exists in
Phase 1B.
"""

from __future__ import annotations

import pytest

from return_platform.platform.capabilities.contracts import CapabilityName
from return_platform.platform.capabilities.registry import InMemoryCapabilityRegistry
from return_platform.platform.modules.descriptor import ModuleDescriptor, ModuleKind
from return_platform.platform.modules.exceptions import (
    CapabilityUnsatisfied,
    DuplicateImplementation,
    ModuleNotRegistered,
)
from return_platform.platform.modules.registry import ModuleRegistry


def _descriptor(
    module_id: str,
    implementation_id: str,
    required_platform_capabilities: frozenset[str] = frozenset(),
) -> ModuleDescriptor:
    return ModuleDescriptor(
        module_id=module_id,
        module_kind=ModuleKind.BUSINESS,
        implementation_id=implementation_id,
        version="1.0.0",
        capabilities=frozenset(),
        configuration_schema="modules.example",
        required_platform_capabilities=required_platform_capabilities,
    )


def test_duplicate_module_id_is_rejected() -> None:
    registry = ModuleRegistry()
    registry.register(_descriptor("returns", "built_in.returns"), factory=object())

    with pytest.raises(DuplicateImplementation):
        registry.register(_descriptor("returns", "built_in.returns_v2"), factory=object())


def test_duplicate_implementation_id_is_rejected() -> None:
    registry = ModuleRegistry()
    registry.register(_descriptor("returns", "built_in.shared"), factory=object())

    with pytest.raises(DuplicateImplementation):
        registry.register(_descriptor("support", "built_in.shared"), factory=object())


def test_resolving_an_unregistered_module_raises() -> None:
    registry = ModuleRegistry()

    with pytest.raises(ModuleNotRegistered):
        registry.resolve("nonexistent")


def test_validate_capabilities_passes_when_required_capability_is_published() -> None:
    registry = ModuleRegistry()
    registry.register(
        _descriptor(
            "returns",
            "built_in.returns",
            required_platform_capabilities=frozenset({"ai.invocation"}),
        ),
        factory=object(),
    )
    capabilities = InMemoryCapabilityRegistry()

    class Port:
        pass

    capabilities.publish(CapabilityName.AI_INVOCATION, Port, "ai", Port())

    registry.validate_capabilities("returns", capabilities)


def test_validate_capabilities_raises_when_nothing_publishes_it() -> None:
    registry = ModuleRegistry()
    registry.register(
        _descriptor(
            "returns",
            "built_in.returns",
            required_platform_capabilities=frozenset({"ai.invocation"}),
        ),
        factory=object(),
    )
    capabilities = InMemoryCapabilityRegistry()

    with pytest.raises(CapabilityUnsatisfied):
        registry.validate_capabilities("returns", capabilities)
