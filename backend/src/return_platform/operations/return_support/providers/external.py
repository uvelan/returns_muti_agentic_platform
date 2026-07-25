"""HTTP Return Support adapter with idempotent submit and bounded status follow-up."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import httpx

from return_platform.configuration.settings import Settings

if TYPE_CHECKING:
    from return_platform.operations.models import ReturnSessionView
from return_platform.operations.return_support.providers.contracts import (
    ReturnSupportRepository,
    ReturnSupportResult,
)

_TERMINAL_STATUSES = frozenset({"RETURN_CREATED", "REJECTED", "CANCELLED", "FAILED"})


def _required_text(payload: dict[str, Any], *names: str) -> str:
    for name in names:
        value = payload.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ValueError(f"Return Support response is missing {names[0]}")


def _optional_text(payload: dict[str, Any], *names: str) -> str | None:
    for name in names:
        value = payload.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _result(payload: dict[str, Any]) -> ReturnSupportResult:
    status = _required_text(payload, "status", "ticketStatus").upper()
    return ReturnSupportResult(
        ticket_id=_required_text(payload, "ticketId", "ticket_id", "id"),
        ticket_status=status,
        external_reference=_required_text(
            payload, "externalReference", "external_reference", "ticketReference"
        ),
        return_reference=_optional_text(payload, "returnReference", "return_reference"),
        fulfillment_reference=_optional_text(
            payload, "fulfillmentReference", "fulfillment_reference"
        ),
        tracking_reference=_optional_text(payload, "trackingReference", "tracking_reference"),
        shipping_path=_optional_text(payload, "shippingPath", "shipping_path"),
    )


class ExternalReturnSupportProvider:
    """Submit a support ticket, follow it to a terminal state, then persist authoritative facts."""

    def __init__(
        self,
        *,
        repository: ReturnSupportRepository,
        http_client: httpx.AsyncClient,
        settings: Settings,
    ) -> None:
        if not settings.support_ticket_base_url:
            raise ValueError(
                "PLATFORM_SUPPORT_TICKET_BASE_URL is required for EXTERNAL support mode"
            )
        self._settings = settings
        self._repository = repository
        self._http_client = http_client
        self._base_url = settings.support_ticket_base_url.rstrip("/")

    def _headers(self, request_digest: str, session_id: str) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Idempotency-Key": f"{session_id}:{request_digest}",
        }
        if self._settings.support_ticket_api_key is not None:
            headers["Authorization"] = (
                "Bearer " + self._settings.support_ticket_api_key.get_secret_value()
            )
        return headers

    async def submit(
        self,
        session: ReturnSessionView,
        *,
        decision: str,
        request_digest: str,
    ) -> ReturnSupportResult:
        payload = {
            "sessionId": session.id,
            "correlationId": session.correlationId,
            "customerReference": session.customerReference,
            "salesOrderNumber": session.orderReference,
            "orderLineIds": session.itemReferences,
            "productReferences": session.productReferences,
            "returnReason": session.reasonCode,
            "returnQuantity": session.returnQuantity,
            "packageCount": session.packageCount,
            "shippingPathExpectation": session.shippingPathExpectation,
            "notes": session.notes,
            "eligibilityDecision": decision,
            "requestDigest": request_digest,
        }
        timeout = httpx.Timeout(self._settings.support_ticket_timeout_seconds)
        headers = self._headers(request_digest, session.id)
        response = await self._http_client.post(
            f"{self._base_url}/tickets", headers=headers, json=payload, timeout=timeout
        )
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            raise ValueError("Return Support submit response must be a JSON object")
        result = _result(body)
        for _ in range(self._settings.support_ticket_max_polls):
            if result.ticket_status in _TERMINAL_STATUSES:
                break
            await asyncio.sleep(self._settings.support_ticket_poll_seconds)
            status_response = await self._http_client.get(
                f"{self._base_url}/tickets/{result.ticket_id}", headers=headers, timeout=timeout
            )
            status_response.raise_for_status()
            status_body = status_response.json()
            if not isinstance(status_body, dict):
                raise ValueError("Return Support status response must be a JSON object")
            result = _result(status_body)
        else:
            raise TimeoutError("Return Support ticket did not reach a terminal state")

        await self._repository.persist_support_result(
            session, decision=decision, request_digest=request_digest, result=result
        )
        return result
