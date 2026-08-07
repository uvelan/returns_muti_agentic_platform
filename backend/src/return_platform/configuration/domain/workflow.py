from pydantic import BaseModel, ConfigDict
from typing import Mapping, Any, List

class WorkflowHandlerNode(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: str
    agent: str | None = None
    queue: str | None = None

class WorkflowStageNode(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str
    handler: WorkflowHandlerNode
    optional: bool = False
    conditional: bool = False

class WorkflowDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    stages: List[WorkflowStageNode]

class WorkflowConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    workflow: Mapping[str, WorkflowDefinition]
