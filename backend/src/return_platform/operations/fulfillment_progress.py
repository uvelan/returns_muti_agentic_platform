"""What a shipment status means for the return record and its case.

The catalog decides everything: a status is terminal, an exception, or an
ordinary rung *because the release says so*, and this module only acts on that
classification -- no status code appears here.

On a terminal, non-exception status (the parcel or freight arrived):

* the return record is closed (`ReturnRecordStatus.CLOSED`, the vocabulary the
  SQL constraint admits) with a `fulfilledAt` stamp;
* a `return_record_fulfilled` fact lands on the case, which is how both the
  Operations screen and the associate's original conversation learn of it --
  the agent's turn context is built from the case fact projection, so no new
  chat, poll or join is needed;
* when every return record of the case is terminal, the case status moves to
  CLOSED. A multi-item return completes only when the last package lands.

On an exception-class status, an `fulfillment_exception` fact carries the
status and the note onto the case -- surfaced, never swallowed -- and clears
(`RESOLVED`) when the ladder resumes.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from return_platform.configuration.return_configuration import (
    ShipmentTrackingConfiguration,
)
from return_platform.operations.case_projection.vocabulary import ReturnRecordStatus
from return_platform.operations.models import CaseStatus, FactAcquisition, FactChannel
from return_platform.operations.repository import OperationalRepository

logger = logging.getLogger("return_platform.operations.fulfillment_progress")

__all__ = ["FulfillmentProgress"]

_AGENT = "fulfillment-tracking-agent"


class FulfillmentProgress:
    def __init__(
        self,
        repository: OperationalRepository,
        catalog: Callable[[], ShipmentTrackingConfiguration | None],
    ) -> None:
        self._repository = repository
        self._catalog = catalog

    def _classify(self, mode: str, code: str) -> tuple[bool, bool]:
        """(terminal-success, exception) per the release's catalog."""
        configuration = self._catalog()
        if configuration is None:
            return False, False
        for status in configuration.statuses:
            if status.ladder.strip().lower() == mode.strip().lower() and status.code == code:
                return (status.terminal and not status.exception_state), status.exception_state
        return False, False

    async def apply(
        self,
        *,
        case_id: str,
        return_reference: str,
        status_code: str,
        mode: str,
        note: str | None = None,
        actor: str = _AGENT,
    ) -> None:
        terminal, exception = self._classify(mode, status_code)
        if exception:
            await self._append_fact(
                fact_id=f"fulfillment-exception-{return_reference}-{status_code}",
                case_id=case_id,
                name="fulfillment_exception",
                value=f"{return_reference}: {status_code}" + (f" — {note}" if note else ""),
            )
            return
        if not terminal:
            # An ordinary rung leaving an exception behind resolves it on the
            # case, so Operations does not show a cleared exception forever.
            await self._append_fact(
                fact_id=f"fulfillment-exception-{return_reference}-resolved-{status_code}",
                case_id=case_id,
                name="fulfillment_exception",
                value=f"{return_reference}: RESOLVED ({status_code})",
            )
            return

        record = await self._record_for(case_id, return_reference)
        if record is None:
            logger.warning(
                "fulfillment_terminal_without_record",
                extra={"case_id": case_id, "return_reference": return_reference},
            )
            return
        if str(record.get("status") or "") != ReturnRecordStatus.CLOSED.value:
            await self._repository.update_return_record(
                str(record["returnRecordId"]),
                {
                    "status": ReturnRecordStatus.CLOSED.value,
                    "fulfilledAt": datetime.now(UTC),
                },
                expected_version=int(record.get("version") or 0),
            )
        await self._append_fact(
            fact_id=f"return-record-fulfilled-{return_reference}",
            case_id=case_id,
            name="return_record_fulfilled",
            value=return_reference,
        )
        await self._close_case_if_complete(case_id)

    async def _record_for(self, case_id: str, return_reference: str) -> dict[str, Any] | None:
        records = await self._repository.list_return_records(case_id)
        for record in records:
            if str(record.get("returnReference") or "") == return_reference:
                return record
        return None

    async def _close_case_if_complete(self, case_id: str) -> None:
        """The case completes only when every one of its records is terminal."""
        records = await self._repository.list_return_records(case_id)
        terminal = {ReturnRecordStatus.CLOSED.value, ReturnRecordStatus.CANCELLED.value}
        if not records or any(
            str(record.get("status") or "") not in terminal for record in records
        ):
            return
        case = await self._repository.get_case(case_id)
        if case is None:
            return
        if str(case.get("status") or "") != CaseStatus.CLOSED.value:
            await self._repository.update_case(
                case_id,
                {"status": CaseStatus.CLOSED.value},
                expected_version=int(case.get("version") or 0),
            )
        # Recorded even when the workflow already closed the case at
        # business-complete: paperwork-done and packages-landed are different
        # facts, and Operations reads this one to say fulfillment finished.
        await self._append_fact(
            fact_id=f"case-fulfilled-{case_id}",
            case_id=case_id,
            name="case_status",
            value="ALL_RETURNS_DELIVERED",
        )

    async def _append_fact(self, *, fact_id: str, case_id: str, name: str, value: Any) -> None:
        try:
            await self._repository.append_case_fact(
                fact_id=fact_id,
                case_id=case_id,
                fact_name=name,
                value=value,
                agent_id=_AGENT,
                channel=FactChannel.SYSTEM,
                acquisition_method=FactAcquisition.DERIVED,
                source_system="RETURN_SHIPMENT",
                source_path="SHIPMENT_STATUS_CONSOLE",
            )
        except Exception:  # noqa: BLE001 - a duplicate fact is an idempotent replay
            logger.debug(
                "fulfillment_fact_already_recorded",
                extra={"case_id": case_id, "fact_id": fact_id},
            )
