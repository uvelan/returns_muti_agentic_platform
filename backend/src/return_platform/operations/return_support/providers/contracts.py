"""Return Support integration contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from return_platform.operations.models import ReturnSessionView


@dataclass(frozen=True, slots=True)
class ReturnSupportResult:
    ticket_id: str
    ticket_status: str
    external_reference: str
    return_reference: str | None
    fulfillment_reference: str | None
    tracking_reference: str | None
    shipping_path: str | None


class ReturnSupportProvider(Protocol):
    async def submit(
        self,
        session: ReturnSessionView,
        *,
        decision: str,
        request_digest: str,
    ) -> ReturnSupportResult: ...


class ReturnSupportRepository(Protocol):
    async def persist_support_result(
        self,
        session: ReturnSessionView,
        *,
        decision: str,
        request_digest: str,
        result: ReturnSupportResult,
    ) -> None: ...
