"""The durable retry path for a case whose workflow start did not land.

Confirmation commits the case to Mongo and then starts its workflow. Those are
two operations against two systems, so there is a window -- a Temporal outage,
a killed worker, a network partition -- in which the case is durable and the
execution that owns it is not. The confirmation *fails* in that window and the
associate is told so, but a failed turn is not a repair: nothing about an
associate walking away makes the case reachable again, and a case with no
workflow is invisible to Support, to Bay and to every later agent turn.

This sweep is the repair. It is deliberately not a second reliability
framework:

* **No new collection and no new record.** The case document already carries
  `workflowId` -- a field created for exactly this link, with a unique partial
  index behind it -- and it is null on a case whose workflow never started. The
  queue is therefore the cases themselves, and it cannot drift out of step with
  them the way a parallel outbox row can.
* **No new idempotency rule.** Recovery calls the same
  `TemporalCaseWorkflowLauncher` the confirmation node calls, so a case whose
  workflow is in fact running (the start succeeded and only the link write
  failed) converges through `WorkflowAlreadyStartedError` and simply has its
  link rewritten.
* **No delivery guarantee of its own.** A failed pass logs and leaves the case
  exactly as it found it, so the next pass retries. There is no lease, no
  attempt counter and no dead-letter state, because the terminal condition is
  observable from the case itself.

`grace_seconds` is what keeps this from racing the confirmation that is still
in flight: a case created a moment ago is being started right now by the node
that created it, and starting it here as well would be harmless but noisy.

The `integration_outbox` was considered and rejected for this. It is scoped to
*external* dependency commands: its topics are enumerated in
`IntegrationConfiguration`, its dispatchers are HTTP adapters, its terminal
failure state is `BLOCKED_EXTERNAL_DEPENDENCY`, and a topic with no registered
adapter is marked non-retryable and abandoned -- which is precisely the outcome
a case must never reach.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from return_platform.workflows.return_case_launcher import StartedCaseWorkflow

__all__ = ["RecoverableCaseRepositoryPort", "ReturnCaseWorkflowRecovery"]

logger = logging.getLogger("return_platform.workflows.return_case_recovery")


class RecoverableCaseRepositoryPort(Protocol):
    async def list_cases_without_workflow(
        self, *, created_before: datetime, limit: int
    ) -> list[dict[str, Any]]: ...


class CaseWorkflowLauncherPort(Protocol):
    async def ensure_case_workflow(
        self,
        *,
        case_id: str,
        tenant_id: str,
        principal_id: str,
        conversation_id: str,
        configuration_release_id: str,
    ) -> StartedCaseWorkflow: ...


class ReturnCaseWorkflowRecovery:
    """Finds confirmed cases with no workflow and starts the one they are owed."""

    def __init__(
        self,
        *,
        launcher: CaseWorkflowLauncherPort,
        repository: RecoverableCaseRepositoryPort,
        grace_seconds: float = 120.0,
        batch_size: int = 100,
        interval_seconds: float = 30.0,
    ) -> None:
        if grace_seconds < 0:
            raise ValueError("grace_seconds must not be negative")
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        self._launcher = launcher
        self._repository = repository
        self._grace_seconds = grace_seconds
        self._batch_size = batch_size
        self._interval_seconds = interval_seconds

    async def recover_once(self) -> int:
        """One pass. Returns how many cases now have a workflow because of it.

        One failing case never stops the pass: the cases in a batch are
        independent returns belonging to different associates, and letting the
        first Temporal error abandon the rest would turn one stuck case into a
        stuck queue.
        """
        cutoff = datetime.now(UTC) - timedelta(seconds=self._grace_seconds)
        pending = await self._repository.list_cases_without_workflow(
            created_before=cutoff, limit=self._batch_size
        )
        recovered = 0
        for case in pending:
            case_id = str(case.get("caseId") or "")
            conversation_id = case.get("channelAConversationId")
            if not case_id or not isinstance(conversation_id, str) or not conversation_id:
                # Not a confirmed Channel A case. `ReturnCaseWorkflow` is the
                # owner of a *confirmation*; giving one to a case that reached
                # the collection some other way would start a support
                # conversation nobody asked for.
                continue
            try:
                started = await self._launcher.ensure_case_workflow(
                    case_id=case_id,
                    tenant_id=str(case.get("tenantId") or ""),
                    principal_id=str(case.get("principalId") or ""),
                    conversation_id=conversation_id,
                    configuration_release_id=str(case.get("configurationReleaseId") or ""),
                )
            except Exception:  # noqa: BLE001 - the next pass retries this case
                logger.warning(
                    "case_workflow_recovery_failed",
                    extra={"case_id": case_id},
                    exc_info=True,
                )
                continue
            recovered += 1
            logger.info(
                "case_workflow_recovered",
                extra={
                    "case_id": case_id,
                    "workflow_id": started.workflow_id,
                    "already_running": started.already_running,
                },
            )
        return recovered

    async def run_forever(self) -> None:
        while True:
            try:
                await self.recover_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a sweep that dies stops repairing
                logger.error("case_workflow_recovery_pass_failed", exc_info=True)
            await asyncio.sleep(self._interval_seconds)
