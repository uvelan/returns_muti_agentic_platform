from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict


class AgentConfigNode(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str | None = None
    enabled: bool = True
    execution_mode: str | None = None
    ai_assisted: bool = True
    input_contexts: list[str] = []
    output_context: str | None = None
    capabilities: list[str] = []
    forbidden_capabilities: list[str] = []
    direct_agent_calls_allowed: bool = False
    idempotency_required: bool = True
    implementation: str | None = None
    task_queue: str | None = None
    state_namespace: str | None = None
    prompt_ref: str | None = None
    ai_route_ref: str | None = None
    max_concurrency: int | None = None
    retry_policy: Mapping[str, Any] | None = None
    timeout: str | None = None


class AgentsConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    agents: Mapping[str, AgentConfigNode] = {}
