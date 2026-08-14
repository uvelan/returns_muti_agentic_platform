"""AgentDescriptor: the one canonical shape describing a configured agent.

Merges the two descriptors a prior review found: agents.registry.ReturnAgentRegistry
(a frozen dataclass with no metadata at all) and
dynamic_knowledge.agents.registry.IndependentAgentDescriptor (had the right identity
fields but no implementation_id, no enabled flag, no resolution to an executable).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

if TYPE_CHECKING:
    from return_platform.configuration.return_configuration import AgentConfiguration

NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]


class AgentDescriptor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    agent_id: NonBlank
    implementation_id: NonBlank
    task_queue: NonBlank
    state_namespace: NonBlank
    prompt_ref: NonBlank | None = None
    policy_ref: NonBlank | None = None
    ai_route_ref: NonBlank | None = None
    enabled: bool = True
    timeout_seconds: float = Field(default=30.0, gt=0.0)
    retry_max_attempts: int = Field(default=3, ge=1)
    max_concurrency: int = Field(default=10, ge=1)
    requests_per_minute: int = Field(default=60, ge=1)
    circuit_breaker_failure_threshold: int = Field(default=5, ge=1)

    @classmethod
    def from_configuration(cls, agent_id: str, config: AgentConfiguration) -> AgentDescriptor:
        return cls(
            agent_id=agent_id,
            implementation_id=config.implementation_id,
            task_queue=config.task_queue,
            state_namespace=config.state_namespace,
            prompt_ref=config.prompt_ref,
            policy_ref=config.policy_ref,
            ai_route_ref=config.ai_route_ref,
            enabled=config.enabled,
            timeout_seconds=config.timeout_seconds,
            retry_max_attempts=config.retry_max_attempts,
            max_concurrency=config.max_concurrency,
            requests_per_minute=config.requests_per_minute,
            circuit_breaker_failure_threshold=config.circuit_breaker_failure_threshold,
        )
