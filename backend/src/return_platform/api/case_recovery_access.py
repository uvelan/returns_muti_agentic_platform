"""The case recovery service, as a request handler reaches it.

Two callers need the same object: the operator's `POST /recovery/relaunch`,
which insists on it and answers 503 when the process cannot build one, and a
write path that has just learned the execution it signalled is gone, which
must carry on either way. Assembled in one place so both are demonstrably the
same service with the same duplicate-execution guard.
"""

from __future__ import annotations

import logging

from fastapi import Request
from temporalio.service import RPCError, RPCStatusCode

from return_platform.configuration.return_configuration import LoadedReturnConfiguration
from return_platform.operations.repository import resolve_operational_repository
from return_platform.resources import RuntimeResources
from return_platform.workflows.return_case_recovery import (
    RecoveryAction,
    ReturnCaseRecoveryService,
    build_case_recovery_service,
)

logger = logging.getLogger("return_platform.api.case_recovery")


def recovery_service_from(request: Request) -> ReturnCaseRecoveryService | None:
    """The service this process can assemble, or None when it cannot.

    `app.state.case_recovery_service` wins when set -- a process that built one
    at startup, or a test standing one in -- otherwise it is assembled from the
    Temporal client and the active return configuration. None means "this
    process has no Temporal client or no configuration", and the caller says
    what that means for it.
    """
    preassembled: ReturnCaseRecoveryService | None = getattr(
        request.app.state, "case_recovery_service", None
    )
    if preassembled is not None:
        return preassembled
    resources = getattr(request.app.state, "resources", None)
    if not isinstance(resources, RuntimeResources) or resources.temporal is None:
        return None
    loaded = getattr(request.app.state, "return_configuration", None)
    if not isinstance(loaded, LoadedReturnConfiguration):
        return None
    settings = resources.settings
    mongo = resources.mongo
    return build_case_recovery_service(
        temporal=resources.temporal,
        repository=resolve_operational_repository(request),
        database=None if mongo is None else mongo[settings.mongo_database],
        timings=loaded.configuration.return_case,
        gate=loaded.configuration.support_gate,
        task_queue=settings.return_workflow_task_queue,
    )


def execution_is_gone(error: BaseException) -> bool:
    """Whether a signal failed because there is no execution to receive it.

    Temporal answers NOT_FOUND for both a closed execution ("workflow
    execution already completed") and one that never existed. Either way the
    signal cannot be retried into it -- the only thing that helps is a new
    execution -- which is what separates this from every other RPC failure,
    where retrying, or leaving the case to its own timeout, is right.
    """
    return isinstance(error, RPCError) and error.status is RPCStatusCode.NOT_FOUND


async def recover_after_late_event(
    request: Request, *, case_id: str, event: str
) -> RecoveryAction | None:
    """The event that ends a park has arrived; give the case its execution back.

    A case parked for `RETURN_DETAILS_NOT_RECORDED` completed its execution
    waiting for exactly the write that just committed. Signalling the closed
    execution cannot deliver it, and leaving the case for an operator to
    relaunch turns "the associate answered late" into a ticket. So the write
    drives recovery itself, through the same service the operator route uses:
    classify first, relaunch only when the execution is closed or absent and
    the case is not terminal, refuse otherwise. The relaunched execution reads
    the case, finds the details recorded, and carries on -- no re-signal is
    needed, and none is sent.

    Returns what recovery did, or None when this process could not ask.
    """
    service = recovery_service_from(request)
    if service is None:
        logger.warning(
            "case_recovery_unavailable_after_late_event",
            extra={"case_id": case_id, "event": event},
        )
        return None
    try:
        outcome = await service.reconcile_case(case_id)
    except Exception:  # noqa: BLE001 - best-effort, the case keeps its parked status
        logger.warning(
            "case_recovery_failed_after_late_event",
            extra={"case_id": case_id, "event": event},
            exc_info=True,
        )
        return None
    logger.info(
        "case_recovery_driven_by_late_event",
        extra={"case_id": case_id, "event": event, "action": outcome.action.value},
    )
    return outcome.action
