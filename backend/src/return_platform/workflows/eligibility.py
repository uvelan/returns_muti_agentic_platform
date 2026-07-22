"""Provider-neutral eligibility gateway and deterministic fallback boundary."""

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final, Protocol
from uuid import UUID

from temporalio import activity

from return_platform.canonical.operations import ContextSnapshot, WorkflowStage
from return_platform.workflows.stage_results import (
    EligibilityActivityResult,
    EligibilityDecision,
    StageResultValidationError,
    bind_stage_activity_result,
)

__all__ = [
    "EligibilityEvaluationInput",
    "EligibilityGatewayError",
    "EligibilityGatewayErrorCode",
    "EligibilityGatewayPort",
    "EligibilityGatewayService",
    "build_eligibility_input",
    "deterministic_eligibility_fallback",
]

_REFERENCE_PATTERN: Final = re.compile(r"^[^\s\x00-\x1f\x7f]{1,512}$")


class EligibilityGatewayErrorCode(StrEnum):
    """Stable safe failures returned by any gateway adapter."""

    AUTH_FAILED = "ELIGIBILITY_GATEWAY_AUTH_FAILED"
    RATE_LIMITED = "ELIGIBILITY_GATEWAY_RATE_LIMITED"
    TIMEOUT = "ELIGIBILITY_GATEWAY_TIMEOUT"
    UNAVAILABLE = "ELIGIBILITY_GATEWAY_UNAVAILABLE"
    RESPONSE_INVALID = "ELIGIBILITY_GATEWAY_RESPONSE_INVALID"


class EligibilityGatewayError(RuntimeError):
    """Sanitized provider-neutral gateway failure."""

    def __init__(self, code: EligibilityGatewayErrorCode) -> None:
        self.code = code
        super().__init__("The eligibility gateway could not produce a trusted decision.")


@dataclass(frozen=True, slots=True)
class EligibilityEvaluationInput:
    """Redacted bounded input derived only from persisted context snapshots."""

    session_id: str
    request_reference: str
    customer_reference: str
    order_references: tuple[str, ...]
    evidence_references: tuple[str, ...]
    configuration_version: str
    requested_at: datetime


class EligibilityGatewayPort(Protocol):
    """One-attempt provider-neutral AI Gateway contract."""

    async def evaluate(self, request: EligibilityEvaluationInput) -> EligibilityActivityResult: ...


def _payload(snapshot: ContextSnapshot, schema_version: str) -> dict[str, object]:
    if snapshot.schema_version != schema_version:
        raise ValueError("The eligibility source context is invalid.")
    value = json.loads(snapshot.payload_json)
    if not isinstance(value, dict):
        raise ValueError("The eligibility source context is invalid.")
    return value


def _reference(value: object) -> str:
    if not isinstance(value, str) or _REFERENCE_PATTERN.fullmatch(value) is None:
        raise ValueError("The eligibility source context is invalid.")
    return value


def _references(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or len(value) > 128:
        raise ValueError("The eligibility source context is invalid.")
    checked = tuple(_reference(item) for item in value)
    if len(set(checked)) != len(checked):
        raise ValueError("The eligibility source context is invalid.")
    return checked


def build_eligibility_input(
    *,
    session_id: UUID,
    intake: ContextSnapshot,
    discovery: ContextSnapshot,
    configuration_version: str,
    requested_at: datetime,
) -> EligibilityEvaluationInput:
    """Build redacted input exclusively from authoritative persisted snapshots."""
    intake_payload = _payload(intake, "intake-v1")
    discovery_payload = _payload(discovery, "order-discovery-v1")
    request_reference = _reference(intake_payload.get("request_reference"))
    customer_reference = _reference(intake_payload.get("customer_reference"))
    if (
        discovery_payload.get("request_reference") != request_reference
        or discovery_payload.get("customer_reference") != customer_reference
    ):
        raise ValueError("The eligibility source contexts do not agree.")
    evidence = (
        *_references(intake_payload.get("evidence_references")),
        *_references(discovery_payload.get("evidence_references")),
        f"CONTEXT_SHA256:{intake.payload_digest}",
        f"CONTEXT_SHA256:{discovery.payload_digest}",
    )
    return EligibilityEvaluationInput(
        session_id=str(session_id),
        request_reference=request_reference,
        customer_reference=customer_reference,
        order_references=_references(discovery_payload.get("order_references")),
        evidence_references=evidence,
        configuration_version=_reference(configuration_version),
        requested_at=requested_at,
    )


def deterministic_eligibility_fallback(
    request: EligibilityEvaluationInput,
    code: EligibilityGatewayErrorCode,
) -> EligibilityActivityResult:
    """Fail safe to human review without inventing approval or rejection facts."""
    return EligibilityActivityResult(
        schema_version="eligibility-v1",
        decision=EligibilityDecision.REVIEW_REQUIRED,
        explanation="Automated eligibility was unavailable; manual review is required.",
        confidence_millionths=0,
        evidence_references=(*request.evidence_references, f"SAFE_ERROR:{code.value}"),
        model_provider="DETERMINISTIC",
        model_name="eligibility-fallback-v1",
        configuration_version=request.configuration_version,
        observed_at=request.requested_at,
    )


class EligibilityGatewayService:
    """Execute exactly one bounded gateway attempt and apply fail-safe fallback."""

    def __init__(self, gateway: EligibilityGatewayPort, *, timeout_seconds: float) -> None:
        if not isinstance(timeout_seconds, float) or not 0.05 <= timeout_seconds <= 30.0:
            raise ValueError("timeout_seconds is invalid")
        self._gateway = gateway
        self._timeout_seconds = timeout_seconds

    @activity.defn(name="evaluate_return_eligibility")
    async def evaluate_return_eligibility(
        self, request: EligibilityEvaluationInput
    ) -> EligibilityActivityResult:
        """Return a validated result or deterministic review-required fallback."""
        try:
            async with asyncio.timeout(self._timeout_seconds):
                result = await self._gateway.evaluate(request)
            bind_stage_activity_result(WorkflowStage.ELIGIBILITY_EVALUATION, result)
            return result
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            return deterministic_eligibility_fallback(request, EligibilityGatewayErrorCode.TIMEOUT)
        except EligibilityGatewayError as error:
            return deterministic_eligibility_fallback(request, error.code)
        except StageResultValidationError:
            return deterministic_eligibility_fallback(
                request, EligibilityGatewayErrorCode.RESPONSE_INVALID
            )
