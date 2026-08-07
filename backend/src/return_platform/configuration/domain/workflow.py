from pydantic import BaseModel, ConfigDict
from typing import Mapping, Any, List

class WorkflowStageNode(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")
    id: str | None = None
    name: str | None = None

class WorkflowDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")
    stages: List[Any] = []

class WorkflowConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")
    workflow: Mapping[str, WorkflowDefinition] = {}
