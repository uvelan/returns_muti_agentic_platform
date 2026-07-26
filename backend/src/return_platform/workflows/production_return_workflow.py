"""Temporal wrapper for the production Ferguson return state machine."""

from __future__ import annotations

from temporalio import workflow

from return_platform.workflows.production_return_state import (
    ProductionReturnEvent,
    ProductionReturnEventType,
    ProductionReturnStage,
    ProductionReturnWorkflowInput,
    ProductionReturnWorkflowState,
    apply_production_return_event,
)

__all__ = [
    "ProductionReturnEvent",
    "ProductionReturnEventType",
    "ProductionReturnStage",
    "ProductionReturnWorkflow",
    "ProductionReturnWorkflowInput",
    "ProductionReturnWorkflowState",
    "apply_production_return_event",
]


@workflow.defn(name="return-platform-production-return-v2")
class ProductionReturnWorkflow:
    """Wait durably for human, OMC, carrier, and warehouse evidence signals."""

    def __init__(self) -> None:
        self._state: ProductionReturnWorkflowState | None = None

    @workflow.run
    async def run(
        self, workflow_input: ProductionReturnWorkflowInput
    ) -> ProductionReturnWorkflowState:
        self._state = ProductionReturnWorkflowState(
            session_id=workflow_input.session_id,
            correlation_id=workflow_input.correlation_id,
            workflow_version=workflow_input.workflow_version,
            assumption_set_version=workflow_input.assumption_set_version,
            stage=ProductionReturnStage.INTAKE,
            applied_event_ids=(),
        )
        await workflow.wait_condition(
            lambda: (
                self._state is not None and (self._state.case_fully_closed or self._state.cancelled)
            )
        )
        await workflow.wait_condition(workflow.all_handlers_finished)
        if self._state is None:
            raise RuntimeError("Production return workflow state was lost before completion")
        return self._state

    @workflow.query(name="production_state")
    def production_state(self) -> ProductionReturnWorkflowState:
        if self._state is None:
            raise RuntimeError("Production return workflow has not started")
        return self._state

    @workflow.update(name="record_production_event")
    async def record_production_event(
        self, event: ProductionReturnEvent
    ) -> ProductionReturnWorkflowState:
        if self._state is None:
            raise RuntimeError("Production return workflow has not started")
        self._state = apply_production_return_event(self._state, event)
        return self._state
