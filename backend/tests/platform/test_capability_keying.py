"""Verifies capability resolution is keyed by (capability, contract), not capability
alone -- the defect this design corrected (design doc section 7.3).

Every call below passes a Protocol class as the `contract` argument. mypy's
`type-abstract` check flags that as "only a concrete class can be given" because
Protocols cannot be instantiated -- but `publish`/`resolve` never instantiate
`contract`, only `isinstance()` against it, so the check is a false positive for this
pattern. This is the known, narrow, per-call-site suppression convention every future
`bootstrap/adapters/*.py` factory will need for the same reason (see
platform/capabilities/README.md).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import pytest

from return_platform.platform.capabilities.contracts import CapabilityName
from return_platform.platform.capabilities.errors import (
    CapabilityNotPublished,
    CapabilityTypeMismatch,
    DuplicateCapability,
)
from return_platform.platform.capabilities.registry import InMemoryCapabilityRegistry


@runtime_checkable
class AgentAiPort(Protocol):
    async def invoke(self, task_id: str) -> str: ...


@runtime_checkable
class SchemaReasoningPort(Protocol):
    async def reason(self, task_id: str) -> str: ...


class AgentAiAdapter:
    async def invoke(self, task_id: str) -> str:
        return f"agent:{task_id}"


class AnalyzerAiAdapter:
    async def reason(self, task_id: str) -> str:
        return f"analyzer:{task_id}"


class WrongShape:
    def unrelated(self) -> None: ...


def test_one_capability_serves_two_consumer_shapes() -> None:
    registry = InMemoryCapabilityRegistry()
    registry.publish(
        CapabilityName.AI_INVOCATION,
        AgentAiPort,  # type: ignore[type-abstract]
        "bootstrap",
        AgentAiAdapter(),
    )
    registry.publish(
        CapabilityName.AI_INVOCATION,
        SchemaReasoningPort,  # type: ignore[type-abstract]
        "bootstrap",
        AnalyzerAiAdapter(),
    )

    agent_port = registry.resolve(
        CapabilityName.AI_INVOCATION,
        AgentAiPort,  # type: ignore[type-abstract]
    )
    analyzer_port = registry.resolve(
        CapabilityName.AI_INVOCATION,
        SchemaReasoningPort,  # type: ignore[type-abstract]
    )

    assert isinstance(agent_port, AgentAiPort)
    assert isinstance(analyzer_port, SchemaReasoningPort)


def test_duplicate_capability_and_contract_pair_is_rejected() -> None:
    registry = InMemoryCapabilityRegistry()
    registry.publish(
        CapabilityName.AI_INVOCATION,
        AgentAiPort,  # type: ignore[type-abstract]
        "bootstrap",
        AgentAiAdapter(),
    )

    with pytest.raises(DuplicateCapability):
        registry.publish(
            CapabilityName.AI_INVOCATION,
            AgentAiPort,  # type: ignore[type-abstract]
            "bootstrap",
            AgentAiAdapter(),
        )


def test_publishing_a_shape_mismatch_is_rejected_at_publication() -> None:
    registry = InMemoryCapabilityRegistry()

    with pytest.raises(CapabilityTypeMismatch):
        registry.publish(CapabilityName.AI_INVOCATION, AgentAiPort, "bootstrap", WrongShape())


def test_resolving_an_unpublished_pair_raises() -> None:
    registry = InMemoryCapabilityRegistry()

    with pytest.raises(CapabilityNotPublished):
        registry.resolve(
            CapabilityName.AI_INVOCATION,
            AgentAiPort,  # type: ignore[type-abstract]
        )


def test_resolve_optional_returns_none_instead_of_raising() -> None:
    registry = InMemoryCapabilityRegistry()

    assert (
        registry.resolve_optional(
            CapabilityName.AI_INVOCATION,
            AgentAiPort,  # type: ignore[type-abstract]
        )
        is None
    )
