"""Deterministic sandbox Return Support adapter backed by authoritative SQL state."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from return_platform.operations.models import ReturnSessionView

from return_platform.operations.return_support.providers.contracts import (
    ReturnSupportRepository,
    ReturnSupportResult,
)


class SandboxReturnSupportProvider:
    def __init__(self, repository: ReturnSupportRepository) -> None:
        self._repository = repository

    async def submit(
        self,
        session: ReturnSessionView,
        *,
        decision: str,
        request_digest: str,
    ) -> ReturnSupportResult:
        token = hashlib.sha256(session.id.encode("utf-8")).hexdigest()[:12].upper()
        approved = decision == "APPROVE"
        result = ReturnSupportResult(
            ticket_id=f"TKT-{token}",
            ticket_status="RETURN_CREATED" if approved else "REJECTED",
            external_reference=f"SUP-{token}",
            return_reference=f"RMA-{token}" if approved else None,
            fulfillment_reference=f"FUL-{token}" if approved else None,
            tracking_reference=(
                f"TRK-{token}"
                if approved and session.shippingPathExpectation in {"PPL", "BOL", "CUSTOMER_SHIP"}
                else None
            ),
            shipping_path=session.shippingPathExpectation if approved else None,
        )
        await self._repository.persist_support_result(
            session,
            decision=decision,
            request_digest=request_digest,
            result=result,
        )
        return result
