"""The outbox adapter that delivers a Support event to its case workflow.

One `TopicDispatcher`, registered against one topic. Everything that makes
delivery durable -- the lease, the attempt count, the backoff, the terminal
state -- already exists in `outbox.py` and is not rebuilt here; this module is
only the part that knows how to talk to Temporal and how to tell a Temporal
outage apart from a workflow that is never coming back.

    Support API -> Mongo transaction { support event, outbox command } -> commit
                -> outbox worker -> TemporalSignalDispatcher -> signal -> DELIVERED

The guarantee:

    Transport (outbox -> Temporal):  AT LEAST ONCE
    Business processing:             EFFECTIVELY ONCE, keyed on supportEventId

Those are two different statements and the difference is load-bearing. A
dispatcher that signals successfully and then loses its process before it can
mark the command delivered *will* signal again on the next claim -- there is no
arrangement of two systems that prevents it. What prevents a second RMA is the
event id travelling with the notice and the receiving side keying on it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Final, cast

from temporalio.client import Client
from temporalio.service import RPCError, RPCStatusCode

from return_platform.operations.integrations.outbox import (
    DispatchResult,
    OutboxCommand,
    PermanentDeliveryFailure,
    TransientDeliveryFailure,
)

logger = logging.getLogger("return_platform.integration_outbox.temporal")

#: The signal this dispatcher sends. Fixed, not read from the command payload:
#: the topic *is* the signal, and a stored document that could name the method
#: to invoke on a running workflow is a stored document that can invoke any of
#: them.
SUPPORT_RESPONSE_SIGNAL: Final = "support_response"

#: gRPC statuses that mean this command can never succeed as written.
#:
#: `NOT_FOUND` is the one the runtime defect produced: the case workflow had
#: completed, so its id resolved to no open execution and `handle.signal` raised
#: straight through the API handler as an HTTP 500. Retrying it forever would
#: replace a visible 500 with an invisible loop, which is worse -- so it
#: dead-letters, and Phase 10 reconciles from there.
_PERMANENT_STATUSES: Final[frozenset[RPCStatusCode]] = frozenset(
    {
        # No open execution with this id: completed, terminated, or never started.
        RPCStatusCode.NOT_FOUND,
        # Cancelled or otherwise closed such that the server refuses the signal.
        RPCStatusCode.FAILED_PRECONDITION,
        # A malformed id or an argument the converter cannot encode. Redelivering
        # the same bytes produces the same rejection.
        RPCStatusCode.INVALID_ARGUMENT,
        RPCStatusCode.OUT_OF_RANGE,
        # Credentials and namespace problems. Real, but not fixed by waiting.
        RPCStatusCode.PERMISSION_DENIED,
        RPCStatusCode.UNAUTHENTICATED,
        RPCStatusCode.UNIMPLEMENTED,
        RPCStatusCode.ALREADY_EXISTS,
    }
)

#: Everything else -- `UNAVAILABLE`, `DEADLINE_EXCEEDED`, `RESOURCE_EXHAUSTED`,
#: `ABORTED`, `INTERNAL`, `UNKNOWN`, `CANCELLED`, `DATA_LOSS` -- is the cluster
#: being unreachable, busy or mid-failover, which is precisely the case the
#: outbox exists to survive. Classified by exclusion rather than by a second
#: list so a status added to a future Temporal release retries and stays
#: recoverable, instead of dead-lettering an event that would have delivered.


def classify_rpc_error(error: RPCError) -> bool:
    """True when this failure is permanent. Exposed so the table is testable."""
    return error.status in _PERMANENT_STATUSES


class TemporalSignalDispatcher:
    """Signal one case workflow with one durably recorded Support event.

    The client is resolved lazily through a factory rather than injected
    already-connected. The worker must start when Temporal is down -- that is
    the whole scenario -- and a constructor that connects would turn a Temporal
    outage into a crash loop of the process whose job is to outlive it.
    """

    def __init__(
        self,
        *,
        client_factory: Callable[[], Awaitable[Client]],
        signal_name: str = SUPPORT_RESPONSE_SIGNAL,
    ) -> None:
        self._client_factory = client_factory
        self._signal_name = signal_name
        self._client: Client | None = None
        self._connect_lock = asyncio.Lock()

    async def _client_or_transient(self) -> Client:
        if self._client is not None:
            return self._client
        async with self._connect_lock:
            if self._client is not None:
                return self._client
            try:
                self._client = await self._client_factory()
            except RPCError as error:
                raise self._classified(error, stage="CONNECT") from error
            except Exception as error:  # noqa: BLE001 - see below
                # Deliberately broad. Connecting can fail as a DNS error, a
                # refused socket, a TLS error or a timeout, and every one of
                # them means "not right now" rather than "not ever". A
                # connection failure that escaped uncaught would leave the
                # command leased and kill the loop that owns the lease.
                raise TransientDeliveryFailure(
                    f"TEMPORAL_CONNECT_{type(error).__name__}"
                ) from error
        return self._client

    @staticmethod
    def _classified(error: RPCError, *, stage: str) -> Exception:
        code = f"TEMPORAL_{stage}_{error.status.name}"
        if classify_rpc_error(error):
            return PermanentDeliveryFailure(code, error_code=code)
        return TransientDeliveryFailure(code)

    async def dispatch(self, command: OutboxCommand) -> DispatchResult:
        payload = command.payload
        workflow_id = payload.get("workflowId")
        notice = payload.get("notice")
        if not isinstance(workflow_id, str) or not workflow_id:
            raise PermanentDeliveryFailure(
                "SUPPORT_SIGNAL_COMMAND_HAS_NO_WORKFLOW_ID",
                error_code="SUPPORT_SIGNAL_COMMAND_HAS_NO_WORKFLOW_ID",
            )
        if not isinstance(notice, dict):
            raise PermanentDeliveryFailure(
                "SUPPORT_SIGNAL_COMMAND_HAS_NO_NOTICE",
                error_code="SUPPORT_SIGNAL_COMMAND_HAS_NO_NOTICE",
            )

        client = await self._client_or_transient()
        handle = client.get_workflow_handle(workflow_id)
        try:
            await handle.signal(self._signal_name, cast(Any, notice))
        except RPCError as error:
            classified = self._classified(error, stage="SIGNAL")
            logger.warning(
                "support_signal_dispatch_failed",
                extra={
                    "workflow_id": workflow_id,
                    "support_event_id": payload.get("supportEventId"),
                    "status": error.status.name,
                    "permanent": isinstance(classified, PermanentDeliveryFailure),
                },
            )
            raise classified from error
        except Exception as error:  # noqa: BLE001 - see `_client_or_transient`
            if isinstance(error, (PermanentDeliveryFailure, TransientDeliveryFailure)):
                raise
            raise TransientDeliveryFailure(f"TEMPORAL_SIGNAL_{type(error).__name__}") from error

        # `external_reference` is the workflow the signal reached; there is no
        # response body to digest, and inventing one would suggest the workflow
        # confirmed something it never sends back.
        return DispatchResult(external_reference=workflow_id, response_digest=None)
