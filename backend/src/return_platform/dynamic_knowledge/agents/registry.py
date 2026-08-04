"""Independent agent registry without shared business-state coupling."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class IndependentAgentDescriptor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    agent_id: str
    task_queue: str
    state_namespace: str
    prompt_ref: str
    policy_ref: str
    standard_model_refs: tuple[str, ...]
    capabilities: frozenset[str]
    max_concurrency: int = Field(ge=1)
    requests_per_minute: int = Field(ge=1)
    circuit_breaker_failure_threshold: int = Field(ge=1)


class IndependentAgentRegistry:
    """Each agent owns state, queue, quotas, prompts, and policy."""

    def __init__(self, descriptors: tuple[IndependentAgentDescriptor, ...]) -> None:
        by_id = {item.agent_id: item for item in descriptors}
        if len(by_id) != len(descriptors):
            raise ValueError("duplicate agent_id")
        queues = [item.task_queue for item in descriptors]
        if len(queues) != len(set(queues)):
            raise ValueError("task queues must be independent")
        namespaces = [item.state_namespace for item in descriptors]
        if len(namespaces) != len(set(namespaces)):
            raise ValueError("state namespaces must be independent")
        self._descriptors = by_id

    def get(self, agent_id: str) -> IndependentAgentDescriptor:
        try:
            return self._descriptors[agent_id]
        except KeyError as exc:
            raise KeyError(f"agent {agent_id!r} is not registered") from exc

    def all(self) -> tuple[IndependentAgentDescriptor, ...]:
        return tuple(self._descriptors.values())
