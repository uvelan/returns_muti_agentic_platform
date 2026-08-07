from pydantic import BaseModel, ConfigDict
from typing import Mapping, Any, List

class AgentConfigNode(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")
    name: str | None = None
    enabled: bool = True
    implementation: str | None = None
    task_queue: str | None = None
    state_namespace: str | None = None

class AgentsConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")
    agents: Mapping[str, AgentConfigNode] = {}
