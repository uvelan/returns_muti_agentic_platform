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


@workflow.defn(
    name="return-platform-production-return-v2",
    # `apply_production_return_event` raises `ValueError` to *reject* an event --
    # an out-of-order transition, or an effect already recorded. Without this,
    # Temporal treats any non-Failure exception as a workflow **task** failure:
    # the task retries forever, `execute_update` never returns, and the caller
    # sits until the 10-second RPC deadline before getting a generic 409. A
    # rejected transition is the common case for a UI -- a double-click, or a
    # screen acting on stale state -- so the common case was the slow one.
    #
    # Listing `ValueError` makes those rejections fail the *update* instead, so
    # the caller gets `WorkflowUpdateFailedError` carrying the real reason
    # immediately. Scoped to `ValueError` deliberately: it is the type the state
    # machine uses for "I refuse", and nothing else in this workflow raises it.
    failure_exception_types=[ValueError],
)
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
