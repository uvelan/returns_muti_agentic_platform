from collections.abc import Mapping
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class WorkflowStageHandlerType(StrEnum):
    """How a workflow stage's business logic is invoked.

    ACTIVITY -- driven by native orchestrator code -- is the only member, and
    describes every stage of every configured workflow.

    There used to be an `AGENT` member, meaning "resolve an implementation by
    agent_id through `AgentRegistry.resolve()`". It is gone with that dispatch
    path (AGT-02). Nothing could execute an AGENT-typed stage: the only module
    that ever declared one was a fixture written for the test that asserted it,
    no live workflow used it, and `return_session.yaml` records why -- none of
    the six agents has a 1:1 request-shape mapping to any of its stages. A
    handler type the runtime cannot honour is configuration that lies.
    """

    ACTIVITY = "ACTIVITY"


class WorkflowStageHandler(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: WorkflowStageHandlerType


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
