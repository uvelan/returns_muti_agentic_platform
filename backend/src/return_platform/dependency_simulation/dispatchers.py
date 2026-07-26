"""Transactional-outbox dispatchers backed by the safe dependency simulator."""

from __future__ import annotations

from return_platform.dependency_simulation.models import DependencyKind, SimulationOperationRequest
from return_platform.dependency_simulation.service import DependencySimulationService
from return_platform.operations.integrations.outbox import DispatchResult, OutboxCommand


class SimulationTopicDispatcher:
    def __init__(self, service: DependencySimulationService, dependency: DependencyKind, operation: str) -> None:
        self._service = service
        self._dependency = dependency
        self._operation = operation

    async def dispatch(self, command: OutboxCommand) -> DispatchResult:
        operation = await self._service.execute(
            SimulationOperationRequest(
                dependency=self._dependency,
                operation=self._operation,
                sessionId=command.aggregate_id,
                idempotencyKey=command.idempotency_key,
                payload=command.payload,
                useAiNarrative=True,
                signalWorkflow=False,
            )
        )
        if operation.status.value != "CONFIRMED":
            raise RuntimeError(operation.errorCode or "SIMULATED_OPERATION_FAILED")
        return DispatchResult(
            external_reference=operation.externalReference,
            response_digest=operation.responsePayload.get("responseDigest"),
        )
