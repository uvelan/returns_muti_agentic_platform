from __future__ import annotations

import asyncio
from pathlib import Path

from return_platform.ai_gateway.configuration import ModelTier, load_ai_gateway_configuration
from return_platform.ai_gateway.providers import ProviderError, ProviderResponse
from return_platform.ai_gateway.routing import AIRoute, AIRoutePool
from return_platform.configuration.settings import Settings
from return_platform.dependency_simulation.configuration import load_dependency_simulation_configuration
from return_platform.dependency_simulation.models import (
    DependencyKind,
    SimulationOperationRequest,
)
from return_platform.dependency_simulation.repository import MemorySimulationRepository
from return_platform.dependency_simulation.service import DependencySimulationService


CONFIG = Path(__file__).resolve().parents[1] / "config" / "dependency_simulation.yaml"


def _service() -> tuple[DependencySimulationService, MemorySimulationRepository]:
    repository = MemorySimulationRepository()
    settings = Settings.model_construct(
        environment="test",
        google_api_key=None,
        nvidia_api_key=None,
        openai_api_key=None,
        anthropic_api_key=None,
        ollama_model=None,
    )
    return DependencySimulationService(
        repository,
        settings,
        load_dependency_simulation_configuration(CONFIG),
    ), repository


def test_ai_failure_uses_default_template_without_blocking_rma() -> None:
    async def run() -> None:
        service, repository = _service()
        operation = await service.execute(
            SimulationOperationRequest(
                dependency=DependencyKind.OMC,
                operation="CREATE_RMA",
                sessionId="RET-TEST-001",
                idempotencyKey="RET-TEST-001:CREATE_RMA",
                payload={"returnMethod": "PREPAID_PARCEL", "items": [{"quantity": 1}]},
                useAiNarrative=True,
            )
        )
        assert operation.status.value == "CONFIRMED"
        assert operation.externalReference is not None
        assert operation.externalReference.startswith("2SIM")
        assert operation.narrative.source == "DEFAULT_TEMPLATE"
        summary = await repository.ai_summary()
        assert summary.fallbackCount == 1
        assert summary.totalTokens == 0
    asyncio.run(run())


def test_idempotency_returns_same_rma() -> None:
    async def run() -> None:
        service, _ = _service()
        request = SimulationOperationRequest(
            dependency=DependencyKind.OMC,
            operation="CREATE_RMA",
            sessionId="RET-TEST-002",
            idempotencyKey="RET-TEST-002:CREATE_RMA",
            payload={"items": [{"quantity": 1}]},
            useAiNarrative=False,
        )
        first = await service.execute(request)
        second = await service.execute(request)
        assert first.id == second.id
        assert first.externalReference == second.externalReference
    asyncio.run(run())


def test_parcel_label_does_not_imply_carrier_acceptance() -> None:
    async def run() -> None:
        service, _ = _service()
        label = await service.execute(
            SimulationOperationRequest(
                dependency=DependencyKind.PARCEL,
                operation="CREATE_RETURN_LABEL",
                sessionId="RET-TEST-003",
                idempotencyKey="RET-TEST-003:LABEL",
                payload={"handlingUnitId": "HU-001"},
                useAiNarrative=False,
            )
        )
        assert label.simulatedState == "LABEL_CREATED"
        accepted = await service.execute(
            SimulationOperationRequest(
                dependency=DependencyKind.PARCEL,
                operation="ADVANCE_TRACKING",
                sessionId="RET-TEST-003",
                idempotencyKey="RET-TEST-003:ACCEPTED",
                payload={"trackingNumber": label.externalReference, "targetStatus": "PACKAGE_ACCEPTED"},
                useAiNarrative=False,
            )
        )
        assert accepted.simulatedState == "PACKAGE_ACCEPTED"
    asyncio.run(run())


def test_rga_requires_rtv_and_then_vendor_credit_can_complete() -> None:
    async def run() -> None:
        service, _ = _service()
        blocked = await service.execute(
            SimulationOperationRequest(
                dependency=DependencyKind.LSI,
                operation="CREATE_RGA",
                sessionId="RET-TEST-004",
                idempotencyKey="RET-TEST-004:RGA-BLOCKED",
                useAiNarrative=False,
            )
        )
        assert blocked.status.value == "MANUAL_REVIEW_REQUIRED"
        await service.execute(
            SimulationOperationRequest(
                dependency=DependencyKind.LSI,
                operation="RECORD_RECEIPT",
                sessionId="RET-TEST-004",
                idempotencyKey="RET-TEST-004:RECEIPT",
                useAiNarrative=False,
            )
        )
        await service.execute(
            SimulationOperationRequest(
                dependency=DependencyKind.LSI,
                operation="ASSIGN_LICENSE_PLATE",
                sessionId="RET-TEST-004",
                idempotencyKey="RET-TEST-004:LICENSE",
                useAiNarrative=False,
            )
        )
        await service.execute(
            SimulationOperationRequest(
                dependency=DependencyKind.LSI,
                operation="SET_PRODUCT_RESOLUTION",
                sessionId="RET-TEST-004",
                idempotencyKey="RET-TEST-004:RTV",
                payload={"productResolution": "RTV"},
                useAiNarrative=False,
            )
        )
        rga = await service.execute(
            SimulationOperationRequest(
                dependency=DependencyKind.LSI,
                operation="CREATE_RGA",
                sessionId="RET-TEST-004",
                idempotencyKey="RET-TEST-004:RGA",
                useAiNarrative=False,
            )
        )
        assert rga.status.value == "CONFIRMED"
        assert rga.externalReference is not None and rga.externalReference.startswith("RGA-SIM-")
        credit = await service.execute(
            SimulationOperationRequest(
                dependency=DependencyKind.LSI,
                operation="RECORD_VENDOR_CREDIT",
                sessionId="RET-TEST-004",
                idempotencyKey="RET-TEST-004:CREDIT",
                useAiNarrative=False,
            )
        )
        assert credit.simulatedState == "VENDOR_CREDIT_CONFIRMED"
    asyncio.run(run())


def test_freight_tender_booking_and_pickup_are_separate() -> None:
    async def run() -> None:
        service, _ = _service()
        for operation in ("REQUEST_QUOTES", "APPROVE_QUOTE", "CREATE_BOL"):
            await service.execute(
                SimulationOperationRequest(
                    dependency=DependencyKind.FREIGHT,
                    operation=operation,
                    sessionId="RET-TEST-005",
                    idempotencyKey=f"RET-TEST-005:{operation}",
                    useAiNarrative=False,
                )
            )
        states = []
        for operation in ("TENDER_SHIPMENT", "CONFIRM_BOOKING", "CONFIRM_PICKUP"):
            item = await service.execute(
                SimulationOperationRequest(
                    dependency=DependencyKind.FREIGHT,
                    operation=operation,
                    sessionId="RET-TEST-005",
                    idempotencyKey=f"RET-TEST-005:{operation}",
                    useAiNarrative=False,
                )
            )
            states.append(item.simulatedState)
        assert states == ["TENDERED", "BOOKED", "PICKED_UP"]
    asyncio.run(run())


def test_simulated_branch_parcel_events_fully_close_production_state_machine() -> None:
    from return_platform.workflows.production_return_state import (
        ProductionReturnEvent,
        ProductionReturnEventType,
        ProductionReturnStage,
        ProductionReturnWorkflowState,
        apply_production_return_event,
    )

    state = ProductionReturnWorkflowState(
        session_id="RET-TEST-E2E",
        correlation_id="CORR-TEST-E2E",
        workflow_version="return-platform-production-return-v2",
        assumption_set_version="FERGUSON-RETURN-ASSUMPTIONS-1.0",
        stage=ProductionReturnStage.ORDER_DISCOVERY,
        applied_event_ids=(),
    )
    sequence = (
        ProductionReturnEventType.DISCOVERY_CONFIRMED,
        ProductionReturnEventType.RETURN_DETAILS_CONFIRMED,
        ProductionReturnEventType.SUPPORT_REQUEST_CREATED,
        ProductionReturnEventType.SUPPORT_ACKNOWLEDGED,
        ProductionReturnEventType.OMC_RETURN_CREATED,
        ProductionReturnEventType.SHIPPING_INSTRUCTIONS_ISSUED,
        ProductionReturnEventType.PHYSICAL_HANDOFF_CONFIRMED,
        ProductionReturnEventType.RECEIPT_CONFIRMED,
        ProductionReturnEventType.LICENSE_PLATE_ASSIGNED,
        ProductionReturnEventType.CUSTOMER_RESOLUTION_COMPLETED,
        ProductionReturnEventType.PRODUCT_DISPOSITION_COMPLETED,
        ProductionReturnEventType.VENDOR_RECOVERY_REQUIRED,
        ProductionReturnEventType.WAREHOUSE_PROCESSING_COMPLETED,
        ProductionReturnEventType.VENDOR_RECOVERY_COMPLETED,
    )
    for index, event_type in enumerate(sequence, start=1):
        state = apply_production_return_event(
            state,
            ProductionReturnEvent(
                event_id=f"SIM-E2E-{index}",
                event_type=event_type,
                evidence_reference=f"SIMULATION:{index}",
            ),
        )

    assert state.stage is ProductionReturnStage.FULLY_CLOSED
    assert state.case_fully_closed is True
    assert state.customer_resolution_complete is True
    assert state.vendor_recovery_complete is True


def test_configured_lightweight_provider_failure_is_measured_and_falls_back() -> None:
    class FailingProvider:
        name = "GOOGLE"
        model = "lightweight-test-model"
        configured = True

        async def generate(self, request: object) -> object:
            del request
            raise RuntimeError("unexpected provider failure")

    async def run() -> None:
        repository = MemorySimulationRepository()
        settings = Settings.model_construct(
            environment="test",
            google_api_key=None,
            nvidia_api_key=None,
            openai_api_key=None,
            anthropic_api_key=None,
            ollama_model=None,
            ai_gateway_configuration_path=(CONFIG.parent / "ai_gateway.yaml"),
        )
        loaded_simulation = load_dependency_simulation_configuration(CONFIG)
        loaded_ai = load_ai_gateway_configuration(CONFIG.parent / "ai_gateway.yaml")
        route = AIRoute(
            route_id="google/lightweight-test-model/google-key-1",
            provider_name="GOOGLE",
            model="lightweight-test-model",
            credential_id="google-key-1",
            credential_fingerprint="test",
            tier=ModelTier.LIGHTWEIGHT,
            provider=FailingProvider(),
            provider_priority=0,
            model_priority=0,
            credential_priority=0,
        )
        service = DependencySimulationService(
            repository,
            settings,
            loaded_simulation,
            route_pool=AIRoutePool((route,), loaded_ai.configuration),
        )
        operation = await service.execute(
            SimulationOperationRequest(
                dependency=DependencyKind.OMC,
                operation="CREATE_RMA",
                sessionId="RET-TEST-AI-FAIL",
                idempotencyKey="RET-TEST-AI-FAIL:CREATE_RMA",
                payload={"items": [{"quantity": 1}]},
                useAiNarrative=True,
            )
        )
        assert operation.status.value == "CONFIRMED"
        assert operation.narrative.source == "DEFAULT_TEMPLATE"
        metrics = await repository.list_ai_metrics(session_id="RET-TEST-AI-FAIL")
        assert {item.status for item in metrics} == {"FAILED", "FALLBACK"}
        summary = await repository.ai_summary()
        assert summary.failureCount == 1
        assert summary.fallbackCount == 1

    asyncio.run(run())


def test_freight_booking_accepts_authoritative_bol_from_outbox_command() -> None:
    async def run() -> None:
        service, _ = _service()
        booking = await service.execute(
            SimulationOperationRequest(
                dependency=DependencyKind.FREIGHT,
                operation="CONFIRM_BOOKING",
                sessionId="RET-TEST-OUTBOX",
                idempotencyKey="RET-TEST-OUTBOX:BOOKING",
                payload={"bolReference": "BOL-SIM-EXISTING"},
                useAiNarrative=False,
            )
        )
        assert booking.status.value == "CONFIRMED"
        assert booking.simulatedState == "BOOKED"

    asyncio.run(run())


class _NarrativeProvider:
    def __init__(
        self,
        *,
        name: str,
        model: str,
        response: str | None = None,
        error_code: str | None = None,
    ) -> None:
        self.name = name
        self.model = model
        self.configured = True
        self._response = response
        self._error_code = error_code

    async def generate(self, request: object) -> ProviderResponse:
        del request
        if self._error_code is not None:
            raise ProviderError(self._error_code)
        assert self._response is not None
        return ProviderResponse(
            provider=self.name,
            model=self.model,
            text=self._response,
            input_tokens=37,
            output_tokens=19,
            total_tokens=56,
        )


def _simulation_route(
    *,
    provider: object,
    model: str,
    credential_id: str,
    model_priority: int,
    credential_priority: int = 0,
) -> AIRoute:
    return AIRoute(
        route_id=f"google/{model}/{credential_id}",
        provider_name="GOOGLE",
        model=model,
        credential_id=credential_id,
        credential_fingerprint="test",
        tier=ModelTier.LIGHTWEIGHT,
        provider=provider,  # type: ignore[arg-type]
        provider_priority=0,
        model_priority=model_priority,
        credential_priority=credential_priority,
    )


def test_simulator_ai_uses_lightweight_route_and_captures_usage() -> None:
    async def run() -> None:
        repository = MemorySimulationRepository()
        settings = Settings.model_construct(
            environment="test",
            google_api_key=None,
            nvidia_api_key=None,
            openai_api_key=None,
            anthropic_api_key=None,
            ollama_model=None,
            ai_gateway_configuration_path=(CONFIG.parent / "ai_gateway.yaml"),
            ai_timeout_seconds=1.0,
        )
        loaded_simulation = load_dependency_simulation_configuration(CONFIG)
        loaded_ai = load_ai_gateway_configuration(CONFIG.parent / "ai_gateway.yaml")
        provider = _NarrativeProvider(
            name="GOOGLE",
            model="light-model-a",
            response=(
                '{"message":"The simulated RMA was created.",'
                '"summary":"OMC simulation completed.",'
                '"nextAction":"Continue the configured return workflow."}'
            ),
        )
        route = _simulation_route(
            provider=provider,
            model="light-model-a",
            credential_id="google-key-1",
            model_priority=0,
        )
        service = DependencySimulationService(
            repository,
            settings,
            loaded_simulation,
            route_pool=AIRoutePool((route,), loaded_ai.configuration),
        )
        operation = await service.execute(
            SimulationOperationRequest(
                dependency=DependencyKind.OMC,
                operation="CREATE_RMA",
                sessionId="RET-TEST-AI-SUCCESS",
                idempotencyKey="RET-TEST-AI-SUCCESS:CREATE_RMA",
                payload={"items": [{"quantity": 1}]},
                useAiNarrative=True,
            )
        )
        assert operation.status.value == "CONFIRMED"
        assert operation.narrative.source == "LIGHTWEIGHT_AI"
        metrics = await repository.list_ai_metrics(session_id="RET-TEST-AI-SUCCESS")
        assert len(metrics) == 1
        metric = metrics[0]
        assert metric.status == "SUCCESS"
        assert metric.modelTier == "LIGHTWEIGHT"
        assert metric.model == "light-model-a"
        assert metric.credentialId == "google-key-1"
        assert metric.routeId == "google/light-model-a/google-key-1"
        assert metric.inputTokens == 37
        assert metric.outputTokens == 19
        assert metric.totalTokens == 56
        assert metric.fallbackUsed is False

    asyncio.run(run())


def test_simulator_ai_rotates_model_after_model_unavailable() -> None:
    async def run() -> None:
        repository = MemorySimulationRepository()
        settings = Settings.model_construct(
            environment="test",
            google_api_key=None,
            nvidia_api_key=None,
            openai_api_key=None,
            anthropic_api_key=None,
            ollama_model=None,
            ai_gateway_configuration_path=(CONFIG.parent / "ai_gateway.yaml"),
            ai_timeout_seconds=1.0,
        )
        loaded_simulation = load_dependency_simulation_configuration(CONFIG)
        loaded_ai = load_ai_gateway_configuration(CONFIG.parent / "ai_gateway.yaml")
        failed = _simulation_route(
            provider=_NarrativeProvider(
                name="GOOGLE",
                model="light-model-a",
                error_code="MODEL_UNAVAILABLE",
            ),
            model="light-model-a",
            credential_id="google-key-1",
            model_priority=0,
        )
        successful = _simulation_route(
            provider=_NarrativeProvider(
                name="GOOGLE",
                model="light-model-b",
                response=(
                    '{"message":"The label operation completed.",'
                    '"summary":"Parcel simulation completed.",'
                    '"nextAction":"Wait for package acceptance."}'
                ),
            ),
            model="light-model-b",
            credential_id="google-key-1",
            model_priority=1,
        )
        service = DependencySimulationService(
            repository,
            settings,
            loaded_simulation,
            route_pool=AIRoutePool((failed, successful), loaded_ai.configuration),
        )
        operation = await service.execute(
            SimulationOperationRequest(
                dependency=DependencyKind.PARCEL,
                operation="CREATE_RETURN_LABEL",
                sessionId="RET-TEST-AI-MODEL-ROTATION",
                idempotencyKey="RET-TEST-AI-MODEL-ROTATION:LABEL",
                payload={"handlingUnitId": "HU-001"},
                useAiNarrative=True,
            )
        )
        assert operation.status.value == "CONFIRMED"
        assert operation.narrative.source == "LIGHTWEIGHT_AI"
        metrics = await repository.list_ai_metrics(session_id="RET-TEST-AI-MODEL-ROTATION")
        by_attempt = sorted(metrics, key=lambda item: item.attempt)
        assert [item.status for item in by_attempt] == ["FAILED", "SUCCESS"]
        assert [item.model for item in by_attempt] == ["light-model-a", "light-model-b"]
        assert by_attempt[-1].fallbackUsed is False

    asyncio.run(run())


def test_simulator_ai_invalid_schema_falls_back_without_affecting_operation() -> None:
    async def run() -> None:
        repository = MemorySimulationRepository()
        settings = Settings.model_construct(
            environment="test",
            google_api_key=None,
            nvidia_api_key=None,
            openai_api_key=None,
            anthropic_api_key=None,
            ollama_model=None,
            ai_gateway_configuration_path=(CONFIG.parent / "ai_gateway.yaml"),
            ai_timeout_seconds=1.0,
        )
        loaded_simulation = load_dependency_simulation_configuration(CONFIG)
        loaded_ai = load_ai_gateway_configuration(CONFIG.parent / "ai_gateway.yaml")
        invalid = _simulation_route(
            provider=_NarrativeProvider(
                name="GOOGLE",
                model="light-model-invalid",
                response='{"message":"Created","extra":"not allowed"}',
            ),
            model="light-model-invalid",
            credential_id="google-key-1",
            model_priority=0,
        )
        service = DependencySimulationService(
            repository,
            settings,
            loaded_simulation,
            route_pool=AIRoutePool((invalid,), loaded_ai.configuration),
        )
        operation = await service.execute(
            SimulationOperationRequest(
                dependency=DependencyKind.OMC,
                operation="CREATE_RMA",
                sessionId="RET-TEST-AI-SCHEMA",
                idempotencyKey="RET-TEST-AI-SCHEMA:RMA",
                payload={"items": [{"quantity": 1}]},
                useAiNarrative=True,
            )
        )
        assert operation.status.value == "CONFIRMED"
        assert operation.externalReference is not None
        assert operation.narrative.source == "DEFAULT_TEMPLATE"
        metrics = await repository.list_ai_metrics(session_id="RET-TEST-AI-SCHEMA")
        assert {item.status for item in metrics} == {"FAILED", "FALLBACK"}
        fallback_metric = next(item for item in metrics if item.status == "FALLBACK")
        assert fallback_metric.fallbackUsed is True

    asyncio.run(run())
