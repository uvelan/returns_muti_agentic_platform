"""Deterministic external-dependency simulator with optional AI narratives."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from return_platform.ai_gateway.routing import AIRoutePool
from return_platform.configuration.settings import Settings
from return_platform.dependency_simulation.ai import SimulationNarrativeService
from return_platform.dependency_simulation.configuration import (
    LoadedDependencySimulationConfiguration,
)
from return_platform.dependency_simulation.models import (
    DependencyKind,
    SimulationAdvanceRequest,
    SimulationOperationRequest,
    SimulationOperationStatus,
    SimulationOperationView,
    SimulationScenario,
)
from return_platform.dependency_simulation.repository import SimulationRepository
from return_platform.dependency_simulation.templates import default_narrative


class SimulationContractError(ValueError):
    pass


class DependencySimulationService:
    def __init__(
        self,
        repository: SimulationRepository,
        settings: Settings,
        loaded_configuration: LoadedDependencySimulationConfiguration,
        *,
        route_pool: AIRoutePool | None = None,
    ) -> None:
        self.repository = repository
        self.settings = settings
        self.loaded = loaded_configuration
        self.configuration = loaded_configuration.configuration
        self.ai = SimulationNarrativeService(
            repository,
            settings,
            self.configuration,
            route_pool=route_pool,
        )

    @staticmethod
    def _digits(seed: str, length: int = 8) -> str:
        number = int(hashlib.sha256(seed.encode()).hexdigest()[:16], 16)
        return str(number).zfill(length)[-length:]

    def _reference(self, prefix: str, key: str, digits: int = 8) -> str:
        return f"{prefix}{self._digits(key, digits)}"

    def _validate(self, request: SimulationOperationRequest) -> None:
        definition = self.configuration.dependencies[request.dependency.value]
        if request.operation not in definition.operations:
            raise SimulationContractError(
                f"{request.operation} is not allowed for {request.dependency.value}."
            )
        if self.settings.environment == "production":
            raise SimulationContractError("Dependency simulation is forbidden in production.")

    async def _has_confirmed(
        self,
        session_id: str,
        dependency: str,
        operations: tuple[str, ...],
    ) -> bool:
        return (
            await self.repository.latest_operation(session_id, dependency, operations) is not None
        )

    async def _require_confirmed(
        self,
        session_id: str,
        dependency: str,
        operations: tuple[str, ...],
        message: str,
    ) -> None:
        if not await self._has_confirmed(session_id, dependency, operations):
            raise SimulationContractError(message)

    async def _has_rtv(self, session_id: str) -> bool:
        for dependency in ("LSI", "OMC"):
            item = await self.repository.latest_operation(
                session_id,
                dependency,
                ("SET_PRODUCT_RESOLUTION",),
            )
            if item and str(item.responsePayload.get("productResolution", "")).upper() == "RTV":
                return True
        return False

    async def _omc(
        self, request: SimulationOperationRequest
    ) -> tuple[dict[str, Any], str | None, str | None]:
        key = request.idempotencyKey
        op = request.operation
        payload = request.payload
        if op == "CREATE_RMA":
            rma = self._reference("2SIM", key)
            result = {
                "externalReference": rma,
                "rmaId": rma,
                "returnVersion": "V2_SIMULATED",
                "cartId": self._reference("CART-SIM-", key),
                "cartItemIds": [
                    self._reference("CI-SIM-", f"{key}:{index}")
                    for index, _ in enumerate(payload.get("items", [{}]), start=1)
                ],
                "status": "ACTIVE",
                "returnMethod": payload.get("returnMethod", "PREPAID_PARCEL"),
                "simulation": True,
            }
            return result, rma, "ACTIVE"
        if op == "CREATE_LEGACY_RETURN":
            ref = self._reference("1SIM", key)
            return (
                {
                    "externalReference": ref,
                    "returnId": ref,
                    "returnVersion": "V1_SIMULATED",
                    "status": "ACTIVE",
                    "simulation": True,
                },
                ref,
                "ACTIVE",
            )
        if op == "GET_RETURN":
            latest = await self.repository.latest_operation(
                request.sessionId, "OMC", ("CREATE_RMA", "CREATE_LEGACY_RETURN")
            )
            if latest is None:
                raise SimulationContractError("No simulated OMC return exists for this session.")
            return (
                {**latest.responsePayload, "readback": True},
                latest.externalReference,
                latest.simulatedState,
            )
        if op == "SET_CUSTOMER_RESOLUTION":
            await self._require_confirmed(
                request.sessionId,
                "OMC",
                ("CREATE_RMA", "CREATE_LEGACY_RETURN"),
                "Customer resolution requires an authoritative simulated return.",
            )
            resolution = str(payload.get("customerResolution", "REFUNDED")).upper()
            ref = self._reference("OMC-CRES-", key)
            return (
                {
                    "externalReference": ref,
                    "customerResolution": resolution,
                    "status": "CUSTOMER_RESOLVED",
                    "simulation": True,
                },
                ref,
                "CUSTOMER_RESOLVED",
            )
        if op == "SET_PRODUCT_RESOLUTION":
            await self._require_confirmed(
                request.sessionId,
                "OMC",
                ("CREATE_RMA", "CREATE_LEGACY_RETURN"),
                "Product resolution requires an authoritative simulated return.",
            )
            resolution = str(payload.get("productResolution", "RTV")).upper()
            ref = self._reference("OMC-PRES-", key)
            return (
                {
                    "externalReference": ref,
                    "productResolution": resolution,
                    "status": "PRODUCT_RESOLVED",
                    "simulation": True,
                },
                ref,
                "PRODUCT_RESOLVED",
            )
        if op == "CREATE_RGA":
            if not await self._has_rtv(request.sessionId):
                raise SimulationContractError("A downstream RGA requires productResolution=RTV.")
            rga = self._reference("RGA-SIM-", key)
            return (
                {
                    "externalReference": rga,
                    "rgaId": rga,
                    "status": "AWAITING_VENDOR_CREDIT",
                    "simulation": True,
                },
                rga,
                "AWAITING_VENDOR_CREDIT",
            )
        if op == "RECORD_VENDOR_CREDIT":
            await self._require_confirmed(
                request.sessionId,
                "OMC",
                ("CREATE_RGA",),
                "Vendor credit requires a confirmed downstream RGA.",
            )
            ref = self._reference("CM-SIM-", key)
            return (
                {
                    "externalReference": ref,
                    "creditMemoId": ref,
                    "status": "VENDOR_CREDIT_CONFIRMED",
                    "simulation": True,
                },
                ref,
                "VENDOR_CREDIT_CONFIRMED",
            )
        state = str(payload.get("status", "UPDATED")).upper()
        ref = self._reference("OMC-SIM-", key)
        return {"externalReference": ref, "status": state, "simulation": True}, ref, state

    async def _parcel(
        self, request: SimulationOperationRequest
    ) -> tuple[dict[str, Any], str | None, str | None]:
        key, op, payload = request.idempotencyKey, request.operation, request.payload
        tracking = str(payload.get("trackingNumber") or self._reference("1ZSIM", key, 10))
        if op in {"CREATE_RETURN_LABEL", "REISSUE_LABEL"}:
            result = {
                "externalReference": tracking,
                "trackingNumber": tracking,
                "labelArtifactId": self._reference("ART-LABEL-SIM-", key),
                "carrier": "UPS_SIMULATED",
                "serviceLevel": payload.get("serviceLevel", "GROUND_RETURN"),
                "handlingUnitId": payload.get("handlingUnitId"),
                "status": "LABEL_CREATED",
                "simulation": True,
            }
            return result, tracking, "LABEL_CREATED"
        if op == "VOID_LABEL":
            return (
                {
                    "externalReference": tracking,
                    "trackingNumber": tracking,
                    "status": "VOIDED",
                    "simulation": True,
                },
                tracking,
                "VOIDED",
            )
        if op == "SIMULATE_EXCEPTION":
            status = str(payload.get("exceptionCode", "DELIVERY_EXCEPTION")).upper()
            return (
                {
                    "externalReference": tracking,
                    "trackingNumber": tracking,
                    "status": status,
                    "simulation": True,
                },
                tracking,
                status,
            )
        await self._require_confirmed(
            request.sessionId,
            "PARCEL",
            ("CREATE_RETURN_LABEL", "REISSUE_LABEL"),
            "Parcel tracking requires a confirmed simulated return label.",
        )
        latest = await self.repository.latest_operation(
            request.sessionId,
            "PARCEL",
            ("CREATE_RETURN_LABEL", "REISSUE_LABEL", "ADVANCE_TRACKING"),
        )
        current = latest.simulatedState if latest else "LABEL_CREATED"
        sequence = list(self.configuration.dependencies["PARCEL"].statusSequence)
        target = str(payload.get("targetStatus") or "").upper()
        if not target:
            target = (
                sequence[min(sequence.index(current) + 1, len(sequence) - 1)]
                if current in sequence
                else sequence[0]
            )
        if target not in sequence:
            raise SimulationContractError(f"Unsupported parcel state {target}.")
        return (
            {
                "externalReference": tracking,
                "trackingNumber": tracking,
                "status": target,
                "simulation": True,
            },
            tracking,
            target,
        )

    async def _freight(
        self, request: SimulationOperationRequest
    ) -> tuple[dict[str, Any], str | None, str | None]:
        key, op, payload = request.idempotencyKey, request.operation, request.payload
        if op == "REQUEST_QUOTES":
            quote_request = self._reference("FQ-SIM-", key)
            quotes = [
                {
                    "quoteId": self._reference("QUOTE-SIM-", f"{key}:1"),
                    "scac": "FXFE",
                    "carrierName": "FedEx Freight Simulator",
                    "estimatedCost": 425.50,
                    "estimatedTransitDays": 4,
                },
                {
                    "quoteId": self._reference("QUOTE-SIM-", f"{key}:2"),
                    "scac": "ODFL",
                    "carrierName": "Old Dominion Simulator",
                    "estimatedCost": 468.75,
                    "estimatedTransitDays": 3,
                },
                {
                    "quoteId": self._reference("QUOTE-SIM-", f"{key}:3"),
                    "scac": "SEFL",
                    "carrierName": "Southeastern Freight Simulator",
                    "estimatedCost": 451.20,
                    "estimatedTransitDays": 4,
                },
            ]
            return (
                {
                    "externalReference": quote_request,
                    "quoteRequestId": quote_request,
                    "quotes": quotes,
                    "status": "QUOTED",
                    "simulation": True,
                },
                quote_request,
                "QUOTED",
            )
        prerequisites: dict[str, tuple[tuple[str, ...], str]] = {
            "APPROVE_QUOTE": (
                ("REQUEST_QUOTES",),
                "Quote approval requires a simulated quote request.",
            ),
            "CREATE_BOL": (
                ("APPROVE_QUOTE",),
                "BOL creation requires an approved simulated quote.",
            ),
            "TENDER_SHIPMENT": (("CREATE_BOL",), "Freight tender requires a simulated BOL."),
            "CONFIRM_BOOKING": (
                ("TENDER_SHIPMENT",),
                "Booking confirmation requires a tendered simulated shipment.",
            ),
            "SCHEDULE_APPOINTMENT": (
                ("CONFIRM_BOOKING",),
                "Appointment scheduling requires a confirmed simulated booking.",
            ),
            "CONFIRM_CARRIER_ARRIVAL": (
                ("SCHEDULE_APPOINTMENT",),
                "Carrier arrival requires a scheduled simulated appointment.",
            ),
            "CONFIRM_PICKUP": (
                ("CONFIRM_BOOKING",),
                "Pickup confirmation requires a confirmed simulated booking.",
            ),
            "ADVANCE_FREIGHT_TRACKING": (
                ("CONFIRM_PICKUP",),
                "Freight tracking requires confirmed simulated pickup.",
            ),
            "FAIL_PICKUP": (
                ("CONFIRM_BOOKING",),
                "Pickup failure requires a confirmed simulated booking.",
            ),
            "RESCHEDULE_PICKUP": (
                ("FAIL_PICKUP",),
                "Rescheduling requires a prior simulated pickup failure.",
            ),
        }
        prerequisite = prerequisites.get(op)
        has_authoritative_bol = op == "CONFIRM_BOOKING" and bool(payload.get("bolReference"))
        if prerequisite is not None and not has_authoritative_bol:
            operations, message = prerequisite
            await self._require_confirmed(request.sessionId, "FREIGHT", operations, message)
        mappings = {
            "APPROVE_QUOTE": ("QUOTE-APPROVED-SIM-", "QUOTE_APPROVED"),
            "CREATE_BOL": ("BOL-SIM-", "BOL_CREATED"),
            "TENDER_SHIPMENT": ("TENDER-SIM-", "TENDERED"),
            "CONFIRM_BOOKING": ("BOOKING-SIM-", "BOOKED"),
            "SCHEDULE_APPOINTMENT": ("APPT-SIM-", "APPOINTMENT_SCHEDULED"),
            "CONFIRM_CARRIER_ARRIVAL": ("ARRIVAL-SIM-", "CARRIER_ARRIVED"),
            "CONFIRM_PICKUP": ("PICKUP-SIM-", "PICKED_UP"),
            "FAIL_PICKUP": (
                "PICKUP-SIM-",
                str(payload.get("failureCode", "CARRIER_NO_SHOW")).upper(),
            ),
            "RESCHEDULE_PICKUP": ("APPT-SIM-", "APPOINTMENT_SCHEDULED"),
            "ADVANCE_FREIGHT_TRACKING": (
                "FREIGHT-SIM-",
                str(payload.get("targetStatus", "IN_TRANSIT")).upper(),
            ),
        }
        prefix, state = mappings[op]
        ref = self._reference(prefix, key)
        return (
            {
                "externalReference": ref,
                "status": state,
                "bolReference": payload.get("bolReference")
                or (ref if op == "CREATE_BOL" else None),
                "carrier": payload.get("carrier", "FREIGHT_SIMULATED"),
                "simulation": True,
            },
            ref,
            state,
        )

    async def _lsi(
        self, request: SimulationOperationRequest
    ) -> tuple[dict[str, Any], str | None, str | None]:
        key, op, payload = request.idempotencyKey, request.operation, request.payload
        prerequisites: dict[str, tuple[tuple[str, ...], str]] = {
            "ASSIGN_LICENSE_PLATE": (
                ("RECORD_RECEIPT",),
                "License-plate assignment requires simulated LSI receipt.",
            ),
            "SET_PRODUCT_RESOLUTION": (
                ("ASSIGN_LICENSE_PLATE",),
                "Product resolution requires a simulated license plate.",
            ),
            "COMPLETE_WAREHOUSE_PROCESSING": (
                ("RECORD_RECEIPT",),
                "Warehouse completion requires simulated LSI receipt.",
            ),
            "CREATE_LOT": (
                ("SET_PRODUCT_RESOLUTION",),
                "Vendor lot creation requires simulated product resolution.",
            ),
            "RECORD_VENDOR_DEBIT": (
                ("CREATE_RGA",),
                "Vendor debit requires a confirmed simulated RGA.",
            ),
            "RECORD_VENDOR_CREDIT": (
                ("CREATE_RGA",),
                "Vendor credit requires a confirmed simulated RGA.",
            ),
            "CLOSE_VENDOR_RECOVERY": (
                ("RECORD_VENDOR_CREDIT",),
                "Vendor recovery closure requires simulated vendor credit.",
            ),
        }
        prerequisite = prerequisites.get(op)
        if prerequisite is not None:
            operations, message = prerequisite
            await self._require_confirmed(request.sessionId, "LSI", operations, message)
        mappings = {
            "GENERATE_RETURN_AUTHORIZATION_ACK": ("LSI-AUTH-SIM-", "AUTHORIZED"),
            "RECORD_RECEIPT": ("LSI-RECEIPT-SIM-", "RECEIVED"),
            "ASSIGN_LICENSE_PLATE": ("IT@SIM", "LICENSE_PLATE_ASSIGNED"),
            "SET_PRODUCT_RESOLUTION": ("LSI-DISP-SIM-", "PRODUCT_RESOLVED"),
            "COMPLETE_WAREHOUSE_PROCESSING": ("LSI-WH-SIM-", "WAREHOUSE_PROCESSING_COMPLETED"),
            "CREATE_LOT": ("LOT-SIM-", "LOT_CREATED"),
            "CREATE_RGA": ("RGA-SIM-", "AWAITING_VENDOR_CREDIT"),
            "RECORD_VENDOR_DEBIT": ("VD-SIM-", "VENDOR_DEBIT_RECORDED"),
            "RECORD_VENDOR_CREDIT": ("CM-SIM-", "VENDOR_CREDIT_CONFIRMED"),
            "CLOSE_VENDOR_RECOVERY": ("RECOVERY-SIM-", "VENDOR_RECOVERY_CLOSED"),
        }
        if op == "CREATE_RGA" and not await self._has_rtv(request.sessionId):
            raise SimulationContractError(
                "LSI cannot create an RGA before an RTV product resolution."
            )
        prefix, state = mappings[op]
        ref = self._reference(prefix, key)
        result: dict[str, Any] = {"externalReference": ref, "status": state, "simulation": True}
        if op == "RECORD_RECEIPT":
            result.update(
                {
                    "receiptId": ref,
                    "rmaId": payload.get("rmaId"),
                    "cartItemId": payload.get("cartItemId"),
                    "receivedAt": datetime.now(UTC).isoformat(),
                }
            )
        elif op == "ASSIGN_LICENSE_PLATE":
            result.update(
                {
                    "licensePlateId": ref,
                    "cartItemId": payload.get("cartItemId"),
                    "handlingUnitId": payload.get("handlingUnitId"),
                }
            )
        elif op == "SET_PRODUCT_RESOLUTION":
            result["productResolution"] = str(payload.get("productResolution", "RTV")).upper()
        elif op == "CREATE_RGA":
            result["rgaId"] = ref
        elif op == "RECORD_VENDOR_CREDIT":
            result["creditMemoId"] = ref
        return result, ref, state

    async def execute(self, request: SimulationOperationRequest) -> SimulationOperationView:
        self._validate(request)
        existing = await self.repository.get_by_idempotency_key(request.idempotencyKey)
        if existing is not None:
            return existing
        now = datetime.now(UTC)
        operation_id = f"SIM-OP-{uuid.uuid4()}"
        fallback = default_narrative(
            request.dependency,
            request.operation,
            {},
            template_version=self.configuration.templateVersion,
        )
        document: dict[str, Any] = {
            "_id": operation_id,
            "id": operation_id,
            "dependency": request.dependency.value,
            "operation": request.operation,
            "sessionId": request.sessionId,
            "idempotencyKey": request.idempotencyKey,
            "scenario": request.scenario.value,
            "status": SimulationOperationStatus.RECEIVED.value,
            "externalReference": None,
            "simulatedState": None,
            "requestPayload": request.payload,
            "responsePayload": {},
            "narrative": fallback.model_dump(mode="json"),
            "errorCode": None,
            "workflowEventType": None,
            "workflowSignalStatus": None,
            "createdAt": now,
            "updatedAt": now,
        }
        await self.repository.insert_operation(document)
        if request.scenario is not SimulationScenario.SUCCESS:
            status = (
                SimulationOperationStatus.RETRYABLE_FAILURE
                if request.scenario
                in {SimulationScenario.RETRYABLE_FAILURE, SimulationScenario.TIMEOUT}
                else SimulationOperationStatus.TERMINAL_FAILURE
            )
            narrative = await self.ai.generate(
                operation_id=operation_id,
                session_id=request.sessionId,
                dependency=request.dependency,
                operation=request.operation,
                result={"status": status.value, "scenario": request.scenario.value},
                enabled=request.useAiNarrative,
            )
            return await self.repository.update_operation(
                operation_id,
                {
                    "status": status.value,
                    "responsePayload": {"simulation": True, "scenario": request.scenario.value},
                    "narrative": narrative.narrative.model_dump(mode="json"),
                    "errorCode": request.scenario.value,
                },
            )
        try:
            if request.dependency is DependencyKind.OMC:
                result, reference, state = await self._omc(request)
            elif request.dependency is DependencyKind.PARCEL:
                result, reference, state = await self._parcel(request)
            elif request.dependency is DependencyKind.FREIGHT:
                result, reference, state = await self._freight(request)
            else:
                result, reference, state = await self._lsi(request)
        except SimulationContractError as error:
            narrative = await self.ai.generate(
                operation_id=operation_id,
                session_id=request.sessionId,
                dependency=request.dependency,
                operation=request.operation,
                result={"status": "MANUAL_REVIEW_REQUIRED", "error": str(error)},
                enabled=request.useAiNarrative,
            )
            return await self.repository.update_operation(
                operation_id,
                {
                    "status": SimulationOperationStatus.MANUAL_REVIEW_REQUIRED.value,
                    "responsePayload": {"simulation": True},
                    "narrative": narrative.narrative.model_dump(mode="json"),
                    "errorCode": "SIMULATION_CONTRACT_ERROR",
                },
            )
        response_digest = hashlib.sha256(
            json.dumps(result, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()
        result = {**result, "responseDigest": response_digest}
        narrative = await self.ai.generate(
            operation_id=operation_id,
            session_id=request.sessionId,
            dependency=request.dependency,
            operation=request.operation,
            result=result,
            enabled=request.useAiNarrative,
        )
        return await self.repository.update_operation(
            operation_id,
            {
                "status": SimulationOperationStatus.CONFIRMED.value,
                "externalReference": reference,
                "simulatedState": state,
                "responsePayload": result,
                "narrative": narrative.narrative.model_dump(mode="json"),
                "errorCode": None,
            },
        )

    async def advance(
        self, operation_id: str, request: SimulationAdvanceRequest
    ) -> SimulationOperationView:
        current = await self.repository.get_operation(operation_id)
        if current is None:
            raise KeyError(operation_id)
        operation = (
            "ADVANCE_TRACKING"
            if current.dependency is DependencyKind.PARCEL
            else "ADVANCE_FREIGHT_TRACKING"
        )
        payload = {**request.payload}
        if request.targetStatus:
            payload["targetStatus"] = request.targetStatus
        return await self.execute(
            SimulationOperationRequest(
                dependency=current.dependency,
                operation=operation,
                sessionId=current.sessionId,
                idempotencyKey=(
                    f"{current.idempotencyKey}:advance:{request.targetStatus or uuid.uuid4()}"
                ),
                scenario=request.scenario,
                payload={
                    **payload,
                    **(
                        {"trackingNumber": current.externalReference}
                        if current.dependency is DependencyKind.PARCEL
                        else {}
                    ),
                },
                useAiNarrative=request.useAiNarrative,
                signalWorkflow=request.signalWorkflow,
            )
        )
