"""The natural-language door (contracts.md sect. 5, sect. 9's endpoint list).

`POST /api/v1/return-support/work-items/{id}/inbound-messages` (AMENDMENT-3).
One route, and almost all of it is refusals -- which is the right proportion for
an endpoint whose job is to accept untrusted text from outside the platform and
commit it.

**Why `inbound-messages` and not `messages`.** T0 froze `.../messages`, which was
already `return_support.add_message` -- the associate's outbound composer. Two
handlers on one path is not a merge: mounted second this one is unreachable,
mounted first it retires a live endpoint, and either way the OpenAPI document
(keyed by path) advertises one surface while the other answers. The name is also
the better one: this is inbound *from* Support, transport-agnostic, as against an
associate composing outbound. `test_no_two_routers_in_this_application_declare_the
_same_endpoint` is the check that T0 did not run.

The order of the checks is the design. Capability, then case access, then size,
then rate, then the commit. Everything that can be refused without touching the
database is refused without touching the database: a size limit that recorded
having rejected a 40MB body would be a size limit that had already read it.

**A shut door is not an error.** `support_ingress.nl_enabled=false` parks the
message and answers `202` with `disposition: PARKED`. Answering `409` or `503`
would put an operator's switch inside the transport's error budget, and a
transport that retries a `503` would deliver the same message over and over
against a door that is not going to open until somebody decides it should.

**The body is data.** It reaches a model later, under a task whose prompt
sections say so; nothing on this path interprets it, branches on it, or lets it
name anything. What is stored is the text and who sent it, and the only thing
derived from it here is a length.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict

from return_platform.configuration.return_configuration import LoadedReturnConfiguration
from return_platform.configuration.support_ingress_configuration import (
    SupportIngressConfiguration,
)
from return_platform.operations.repository import OperationalRepository
from return_platform.operations.return_support.ingress import (
    SupportEventStatus,
    SupportInboundMessage,
    normalize_inbound_message,
)
from return_platform.operations.return_support.ingress_store import (
    DurableSupportIngressStore,
)
from return_platform.operations.return_support.service import ReturnSupportService
from return_platform.operations.support_events import IdempotencyConflictError
from return_platform.resources import RuntimeResources
from return_platform.security.authorization import require_capability
from return_platform.security.capabilities import RETURNS_SUPPORT_ACT
from return_platform.shared.contracts import APIResponse, ResponseMeta
from return_platform.workflows.return_case_workflow import return_case_workflow_id

router = APIRouter(prefix="/api/v1/return-support", tags=["Returns Support"])

#: The one capability this route needs (contracts.md sect. 2, investigation 3:
#: reuse, one addition across the whole programme and it is not this one).
require_support_act = require_capability(RETURNS_SUPPORT_ACT)


class SupportMessageAcceptedView(BaseModel):
    """The receipt for a durably recorded inbound message.

    Says what committed and nothing about what has been made of it. There is no
    `intent` here and there will not be one: the classification is a model's
    answer, it is pinned and accepted asynchronously under S2's analysis record,
    and a field here carrying it would be this handler claiming an analysis it
    did not wait for.
    """

    model_config = ConfigDict(extra="forbid")

    caseId: str
    supportEventId: str
    #: `ACCEPTED`, `PARKED`, or `DUPLICATE` for a redelivery. All three are
    #: successes; all three leave exactly one message on file.
    disposition: str
    #: The classify command queued for this message, when one was. `None` for a
    #: parked message -- there is nothing to classify until the door opens.
    outboxCommandId: str | None
    #: Parked messages on this case. The panel's degraded entry reads this
    #: (contracts.md sect. 5).
    parkedCount: int


def _meta(request: Request) -> ResponseMeta:
    return ResponseMeta(request_id=cast(str, getattr(request.state, "correlation_id", "unknown")))


def _resources(request: Request) -> RuntimeResources:
    resources = getattr(request.app.state, "resources", None)
    if not isinstance(resources, RuntimeResources) or resources.mongo is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "SUPPORT_INGRESS_STORE_UNAVAILABLE",
                "message": "The inbound message cannot be recorded durably from this process.",
                "retryable": True,
            },
        )
    return resources


def _ingress_configuration(request: Request) -> SupportIngressConfiguration:
    """The released ingress policy, resolved per request.

    Per request, not at wiring time, for the reason
    `_reject_unconfigured_return_methods` documents at length: activating a
    release must be enough to change behaviour, and a value captured at startup
    is a value the Control Centre cannot move. `nl_enabled` in particular is
    meant to be flipped by an operator and take effect on the next message.
    """
    loaded = getattr(request.app.state, "return_configuration", None)
    if not isinstance(loaded, LoadedReturnConfiguration):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "RETURN_CONFIGURATION_UNAVAILABLE",
                "message": (
                    "No return configuration is loaded, so the ingress policy is unknown. "
                    "Refused rather than defaulted: defaulting would decide whether the "
                    "natural-language door is open, which is an operator's decision."
                ),
                "retryable": True,
            },
        )
    return loaded.configuration.support_ingress


def _support_service(request: Request) -> ReturnSupportService:
    """The work-item read. One seam, so a test can substitute the store layer.

    Constructed per request rather than held on app state, exactly as
    `api/return_support.py::_service` does it: the service closes over the
    active configuration, and a long-lived instance would be a release pinned to
    process start.
    """
    resources = _resources(request)
    loaded = getattr(request.app.state, "return_configuration", None)
    if not isinstance(loaded, LoadedReturnConfiguration):  # pragma: no cover - guarded above
        raise HTTPException(status_code=503, detail="Returns Support dependencies are unavailable.")
    return ReturnSupportService(
        client=resources.mongo,
        settings=resources.settings,
        configuration=loaded.configuration,
        operational_repository=_repository(request),
    )


def _repository(request: Request) -> OperationalRepository:
    resources = _resources(request)
    return OperationalRepository(resources.mongo, resources.settings, resources.source_mongo)


def _ingress_store(
    request: Request, configuration: SupportIngressConfiguration
) -> DurableSupportIngressStore:
    resources = _resources(request)
    return DurableSupportIngressStore(resources.mongo, resources.settings, configuration)


def enforce_limits(
    payload: SupportInboundMessage, configuration: SupportIngressConfiguration
) -> None:
    """Size checks, before anything durable happens.

    A module-level function rather than an inline block so the rules are
    testable without a running application -- the limits are released
    configuration, and a test that could only reach them through HTTP would be a
    test of FastAPI.
    """
    limits = configuration.limits
    if len(payload.body_text) > limits.max_body_characters:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail={
                "code": "SUPPORT_MESSAGE_TOO_LARGE",
                "message": (
                    f"The message body exceeds {limits.max_body_characters} characters. "
                    "Refused rather than truncated: the cut half of a truncated support "
                    "message may be the tracking number."
                ),
                "retryable": False,
            },
        )
    for name, value in (
        ("externalMessageId", payload.external_message_id),
        ("channelHint", payload.channel_hint),
    ):
        if len(value) > limits.max_identifier_characters:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "code": "SUPPORT_IDENTIFIER_TOO_LONG",
                    "message": (
                        f"{name} exceeds {limits.max_identifier_characters} characters; "
                        "it is half of the dedupe identity and therefore an index key."
                    ),
                    "retryable": False,
                },
            )


@router.post(
    # AMENDMENT-3. `/messages` on this prefix belongs to
    # `return_support.add_message` and has since before this slice existed.
    "/work-items/{work_item_id}/inbound-messages",
    response_model=APIResponse[SupportMessageAcceptedView],
    status_code=status.HTTP_202_ACCEPTED,
)
async def receive_support_message(
    work_item_id: str,
    payload: SupportInboundMessage,
    request: Request,
    actor_id: str = Depends(require_support_act),
) -> APIResponse[SupportMessageAcceptedView]:
    """Accept one natural-language message from Support, or park it.

    `202`, never `201`: nothing about this message has been acted on when the
    response is written. The analysis is queued, the relay to Channel A happens
    after the analysis commits, and a `201 Created` would be a claim about a
    resource whose meaning does not exist yet.

    Three refusals, and each is a different fact:

        413  the body is larger than the released limit
        429  this case has taken more messages than the released window allows
        409  this exact identity already carries different words

    and one non-refusal that is easy to get wrong: a message arriving while
    `nl_enabled` is false comes back `202 PARKED`. It is on file, it is counted,
    and it will be analysed in stream order when the switch flips.
    """
    configuration = _ingress_configuration(request)
    enforce_limits(payload, configuration)

    item = await _support_service(request).get_work_item(work_item_id)
    if item is None or item.caseId is None:
        # One answer for both. A work item on a return session rather than a
        # case has no case to record against, and telling a caller which of the
        # two it hit is telling them a work item exists.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "SUPPORT_WORK_ITEM_NOT_FOUND",
                "message": "No such support work item on a case.",
            },
        )

    case = await _repository(request).get_case(item.caseId)
    tenant_id = str(getattr(request.state, "tenant_id", "default"))
    if case is None or case.get("tenantId") != tenant_id:
        # 404 rather than 403, the pattern every case route here follows: a 403
        # confirms the case exists to somebody who should not know that.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "SUPPORT_WORK_ITEM_NOT_FOUND",
                "message": "No such support work item on a case.",
            },
        )

    store = _ingress_store(request, configuration)
    await _enforce_rate(store, case_id=item.caseId, configuration=configuration)

    event = normalize_inbound_message(
        payload, case_id=item.caseId, work_item_id=work_item_id
    )
    try:
        receipt = await store.record_inbound_message(
            event=event,
            workflow_id=return_case_workflow_id(item.caseId),
            actor_id=actor_id,
            nl_enabled=configuration.nl_enabled,
            correlation_id=_meta(request).request_id,
        )
    except IdempotencyConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "IDEMPOTENCY_CONFLICT",
                "message": (
                    "This transport and externalMessageId were already recorded on this "
                    "case carrying different words. Use a new id for a new message."
                ),
                "retryable": False,
            },
        ) from error

    disposition = "DUPLICATE" if receipt.duplicate else receipt.status
    return APIResponse(
        data=SupportMessageAcceptedView(
            caseId=receipt.case_id,
            supportEventId=receipt.support_event_id,
            disposition=disposition,
            outboxCommandId=receipt.outbox_command_id,
            parkedCount=receipt.parked_count,
        ),
        meta=_meta(request),
    )


async def _enforce_rate(
    store: DurableSupportIngressStore,
    *,
    case_id: str,
    configuration: SupportIngressConfiguration,
) -> None:
    """Per case, per window (contracts.md sect. 5's ingress limits).

    Per *case* rather than per principal: the resource being protected is the
    case's analysis pipeline, and one transport speaking on behalf of many
    support agents is the ordinary shape -- a per-principal budget would let a
    single case be flooded through a handful of senders while each stayed under
    its own ceiling.

    Counted over persisted messages rather than in an in-process counter, so the
    limit survives a restart and holds across replicas. It costs one indexed
    count per accepted message, which at this platform's stated volume (<= 200
    concurrent cases) is not the thing worth optimising.
    """
    limits = configuration.limits
    since = datetime.now(UTC) - timedelta(seconds=limits.rate_window_seconds)
    recent = await store.recent_messages_in_window(case_id, since=since)
    if recent >= limits.max_messages_per_case_per_window:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "SUPPORT_INGRESS_RATE_LIMITED",
                "message": (
                    f"This case has taken {recent} messages in the last "
                    f"{limits.rate_window_seconds}s, at or above the configured ceiling "
                    f"of {limits.max_messages_per_case_per_window}."
                ),
                "retryable": True,
            },
        )


__all__ = [
    "SupportEventStatus",
    "SupportMessageAcceptedView",
    "enforce_limits",
    "receive_support_message",
    "router",
]
