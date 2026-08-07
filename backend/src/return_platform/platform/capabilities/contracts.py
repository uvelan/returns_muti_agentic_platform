"""Capability registry contracts.

Registrations are keyed by (capability, contract), not by capability alone -- one
provider can back several differently-shaped consumer ports (design doc section 7.3,
section 13.1).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel, ConfigDict


class CapabilityName(StrEnum):
    AI_INVOCATION = "ai.invocation"
    AI_INTERCEPTION = "ai.interception"
    GRAPH_QUERY = "graph.query"
    GRAPH_LIFECYCLE = "graph.lifecycle"
    GRAPH_SYNC = "graph.sync"
    SOURCE_DISCOVERY = "source.discovery"
    SOURCE_SCAN = "source.scan"
    AGENT_EXECUTION = "agent.execution"
    WORK_QUEUE = "work.queue"


class CapabilityPublication(BaseModel):
    """Record of one (capability, contract) -> instance binding."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    capability: CapabilityName
    contract_name: str
    provider_module_id: str


T = TypeVar("T")


@runtime_checkable
class CapabilityRegistry(Protocol):
    """Cross-module access without cross-module imports."""

    def publish(
        self,
        capability: CapabilityName,
        contract: type[T],
        provider_module_id: str,
        instance: T,
    ) -> None:
        """Register `instance` as satisfying `contract` for `capability`.

        Checks that `instance` structurally satisfies `contract` at publication time.
        This proves attribute existence only -- see platform/capabilities/README.md for
        why static and behavioral checks are still required. Raises DuplicateCapability
        only when the exact (capability, contract) pair was already published.
        """

    def resolve(self, capability: CapabilityName, contract: type[T]) -> T:
        """Exact lookup on (capability, contract). Raises CapabilityNotPublished."""

    def resolve_optional(self, capability: CapabilityName, contract: type[T]) -> T | None: ...

    def list(self) -> tuple[CapabilityPublication, ...]: ...
