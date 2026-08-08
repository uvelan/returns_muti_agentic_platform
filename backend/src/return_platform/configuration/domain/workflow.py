from collections.abc import Mapping
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class WorkflowStageHandlerType(StrEnum):
    """How a workflow stage's business logic is invoked.

    ACTIVITY means the stage is driven by native orchestrator code (the
    current reality for every stage of `workflow.return_session` -- see
    that module's YAML for why none of them are AGENT yet). AGENT means the
    stage resolves an implementation through AgentRegistry.resolve() under
    the pinned configuration release.
    """

    AGENT = "AGENT"
    ACTIVITY = "ACTIVITY"


class WorkflowStageHandler(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: WorkflowStageHandlerType
    agent: str | None = None


class WorkflowStageEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    stage: str
    handler: WorkflowStageHandler | None = None


class WorkflowDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    context_only_handoffs: bool = True
    direct_agent_calls_allowed: bool = False
    stages: list[str | WorkflowStageEntry] = []

    def stage_ids(self) -> tuple[str, ...]:
        return tuple(entry if isinstance(entry, str) else entry.stage for entry in self.stages)

    def handler_for(self, stage_id: str) -> WorkflowStageHandler | None:
        for entry in self.stages:
            if isinstance(entry, WorkflowStageEntry) and entry.stage == stage_id:
                return entry.handler
        return None


class WorkflowConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    workflow: Mapping[str, WorkflowDefinition] = {}
