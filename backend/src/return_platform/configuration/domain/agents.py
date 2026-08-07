from pydantic import BaseModel, ConfigDict
from typing import Mapping, Any

class AgentConfigNode(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    implementation: str
    task_queue: str
    state_namespace: str
    prompt_ref: str | None = None
    ai_route_ref: str | None = None
    enabled: bool
    max_concurrency: int | None = None
    retry_policy: Mapping[str, Any] | None = None
    timeout: str | None = None

class AgentsConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    agents: Mapping[str, AgentConfigNode]
