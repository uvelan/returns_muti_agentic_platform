from pydantic import BaseModel, ConfigDict
from typing import Mapping, Any, List

class WorkflowDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    context_only_handoffs: bool = True
    direct_agent_calls_allowed: bool = False
    stages: List[str] = []

class WorkflowConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    workflow: Mapping[str, WorkflowDefinition] = {}
