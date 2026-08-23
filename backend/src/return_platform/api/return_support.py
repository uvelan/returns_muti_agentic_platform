"""Production Returns Support workbench APIs."""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from return_platform.configuration.return_configuration import LoadedReturnConfiguration
from return_platform.operations.models import CaseStatus
from return_platform.operations.production_event_authorization import (
    unauthorized_events_for,
)
from return_platform.operations.production_workflow import ProductionWorkflowCoordinator
from return_platform.operations.repository import ConcurrencyConflictError, OperationalRepository
from return_platform.operations.return_support.service import (
    CreateSupportMessageRequest,
    CreateSupportWorkItemRequest,
    ReturnSupportService,
    SupportActionRequest,
    SupportMessageView,
    SupportWorkItemView,
    production_events_for_support_action,
)
from return_platform.operations.support_events import (
    DurableSupportEventStore,
    IdempotencyConflictError,
    support_return_record,
)
from return_platform.resources import RuntimeResources
from return_platform.security.authorization import (
    actor_roles,
    require_associate_roles,
    require_read_roles,
    require_return_collaboration_roles,
    require_support_roles,
)
from return_platform.shared.contracts import APIResponse, ResponseMeta
from return_platform.workflows.production_return_workflow import ProductionReturnEventType
from return_platform.workflows.return_case_workflow import return_case_workflow_id

router = APIRouter(prefix="/api/v1/return-support", tags=["Returns Support"])

#: Case states in which the workflow is gone for good, so an outcome sent to it
#: would be dead-lettered rather than applied. Refused here rather than queued,
#: because a refusal Support can read is worth more than a durable record of a
#: reply nobody will ever act on. Only these two: every other status is a case
#: whose execution is running or recoverable.
_TERMINAL_CASE_STATUSES: frozenset[str] = frozenset(
    {CaseStatus.CLOSED.value, CaseStatus.CANCELLED.value}
)


def _meta(request: Request) -> ResponseMeta:
    return ResponseMeta(request_id=cast(str, getattr(request.state, "correlation_id", "unknown")))


def _service(request: Request) -> ReturnSupportService:
    resources = getattr(request.app.state, "resources", None)
    loaded = getattr(request.app.state, "return_configuration", None)
    if (
        not isinstance(resources, RuntimeResources)
        or resources.mongo is None
        or not isinstance(loaded, LoadedReturnConfiguration)
    ):
        raise HTTPException(status_code=503, detail="Returns Support dependencies are unavailable.")
    repository = OperationalRepository(resources.mongo, resources.settings, resources.source_mongo)
    return ReturnSupportService(
        client=resources.mongo,
        settings=resources.settings,
        configuration=loaded.configuration,
        operational_repository=repository,
    )


@router.post(
    "/work-items",
    response_model=APIResponse[SupportWorkItemView],
    status_code=status.HTTP_201_CREATED,
)
async def create_work_item(
    payload: CreateSupportWorkItemRequest,
    request: Request,
    actor: str = Depends(require_associate_roles),
) -> APIResponse[SupportWorkItemView]:
    try:
        data = await _service(request).create_work_item(
            payload,
            actor_id=actor,
            correlation_id=_meta(request).request_id,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Return session not found.") from error
    return APIResponse(data=data, meta=_meta(request))


@router.get("/work-items", response_model=APIResponse[list[SupportWorkItemView]])
async def list_work_items(
    request: Request,
    work_item_status: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=200, ge=1, le=500),
    _actor: str = Depends(require_read_roles),
) -> APIResponse[list[SupportWorkItemView]]:
    return APIResponse(
        data=await _service(request).list_work_items(status=work_item_status, limit=limit),
        meta=_meta(request),
    )


@router.get("/work-items/{work_item_id}", response_model=APIResponse[SupportWorkItemView])
async def get_work_item(
    work_item_id: str,
    request: Request,
    _actor: str = Depends(require_read_roles),
) -> APIResponse[SupportWorkItemView]:
    data = await _service(request).get_work_item(work_item_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Support work item not found.")
    return APIResponse(data=data, meta=_meta(request))


@router.get(
    "/work-items/{work_item_id}/messages",
    response_model=APIResponse[list[SupportMessageView]],
)
async def list_messages(
    work_item_id: str,
    request: Request,
    _actor: str = Depends(require_read_roles),
) -> APIResponse[list[SupportMessageView]]:
    item = await _service(request).get_work_item(work_item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Support work item not found.")
    return APIResponse(
        data=await _service(request).list_messages(item.threadId), meta=_meta(request)
    )


@router.post(
    "/work-items/{work_item_id}/messages",
    response_model=APIResponse[dict[str, object]],
    status_code=status.HTTP_201_CREATED,
)
async def add_message(
    work_item_id: str,
    payload: CreateSupportMessageRequest,
    request: Request,
    actor: str = Depends(require_return_collaboration_roles),
) -> APIResponse[dict[str, object]]:
    principal = getattr(request.state, "principal", None)
    actor_role = (
        "RETURN_SUPPORT"
        if principal is not None and "return_support" in principal.roles
        else "ASSOCIATE"
    )
    try:
        item, message = await _service(request).add_message(
            work_item_id,
            payload,
            actor_id=actor,
            actor_role=actor_role,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Support work item not found.") from error
    except ConcurrencyConflictError as error:
        raise HTTPException(
            status_code=409, detail="Support work item version conflict."
        ) from error
    return APIResponse(
        data={"workItem": item.model_dump(mode="json"), "message": message.model_dump(mode="json")},
        meta=_meta(request),
    )


@router.post(
    "/work-items/{work_item_id}/actions",
    response_model=APIResponse[SupportWorkItemView],
)
async def apply_action(
    work_item_id: str,
    payload: SupportActionRequest,
    request: Request,
    actor: str = Depends(require_support_roles),
) -> APIResponse[SupportWorkItemView]:
    roles = actor_roles(request)
    #: Every workflow event this action will record, derived from the payload and
    #: the active policy so the whole set can be authorized before the work item
    #: is mutated. A 403 raised after `apply_action` would leave the support
    #: record advanced and the workflow un-signalled.
    #:
    #: Resolved once, here, and reused for the recording below -- the same
    #: `planned_events` tuple decides both, so the check and the act cannot read
    #: different policies even if a release lands mid-request. An absent
    #: configuration yields no BOL event *and* skips the whole recording block
    #: further down, so the two stay consistent rather than one half acting on a
    #: policy the other could not read.
    loaded = getattr(request.app.state, "return_configuration", None)
    planned_events = production_events_for_support_action(
        payload,
        bol_tendering_instruction_types=(
            loaded.configuration.return_policy.bol_tendering_instruction_types
            if isinstance(loaded, LoadedReturnConfiguration)
            else ()
        ),
    )
    refused = unauthorized_events_for(event_types=planned_events, actor_roles=roles)
    if refused:
        raise HTTPException(
            status_code=403,
            detail=(
                "Role cannot record "
                + ", ".join(event.value for event in refused)
                + ", which this action would produce."
            ),
        )
    try:
        data = await _service(request).apply_action(work_item_id, payload, actor_id=actor)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Support work item not found.") from error
    except ConcurrencyConflictError as error:
        raise HTTPException(
            status_code=409, detail="Support work item version conflict."
        ) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    event_type = planned_events[0] if planned_events else None
    resources = getattr(request.app.state, "resources", None)
    if (
        event_type is not None
        # A case thread has no session, so there is no session-scoped
        # production workflow to signal. Channel B outcomes on a case reach the
        # return through `ReturnCaseWorkflow`'s `support_response` signal
        # instead; bridging here as well would be two paths to the same event.
        and data.sessionId is not None
        and isinstance(resources, RuntimeResources)
        and resources.temporal is not None
        and resources.mongo is not None
        and isinstance(loaded, LoadedReturnConfiguration)
    ):
        repository = OperationalRepository(
            resources.mongo, resources.settings, resources.source_mongo
        )
        session = await repository.get_return(data.sessionId)
        if session is not None:
            coordinator = ProductionWorkflowCoordinator(
                temporal=resources.temporal,
                repository=repository,
                configuration=loaded.configuration,
                task_queue=resources.settings.return_workflow_task_queue,
            )
            try:
                await coordinator.ensure_started(session, actor_id=actor)
                business_payload: dict[str, object] = {
                    "supportWorkItemId": data.id,
                    "returnVersion": data.returnVersion,
                    "returnReference": data.returnReference,
                    "shippingInstructionReference": (data.shippingInstructionReference),
                    "instructionType": payload.shippingInstructionType,
                    "carrier": payload.carrier,
                    "trackingNumbers": list(payload.trackingNumbers),
                    "bolReference": payload.bolReference,
                    "customerResolution": data.customerResolutionStatus,
                }
                await coordinator.record_event(
                    data.sessionId,
                    event_id=f"support-action:{data.id}:{payload.action.value}:{data.version}",
                    event_type=event_type,
                    evidence_reference=f"SUPPORT_WORK_ITEM:{data.id}:v{data.version}",
                    actor_id=actor,
                    actor_roles=roles,
                    business_payload=business_payload,
                )
                # Same `planned_events` the authorization above was computed
                # from, rather than re-deriving the LTL condition here. Two
                # copies of "does this action tender a BOL?" is how the check
                # and the act drift apart.
                if ProductionReturnEventType.BOL_TENDERED in planned_events:
                    await coordinator.record_event(
                        data.sessionId,
                        event_id=(f"support-action:{data.id}:BOL_TENDERED:{data.version}"),
                        event_type=ProductionReturnEventType.BOL_TENDERED,
                        evidence_reference=(
                            payload.bolReference
                            or data.shippingInstructionReference
                            or f"SUPPORT_WORK_ITEM:{data.id}:v{data.version}"
                        ),
                        actor_id=actor,
                        actor_roles=roles,
                        business_payload={
                            "sourceSystem": "OMC_OR_SUPPORT_READBACK",
                            "sourceEventId": (f"{data.id}:BOL_TENDERED:{data.version}"),
                            "bolReference": payload.bolReference,
                            "carrier": payload.carrier,
                        },
                    )
            except Exception as error:
                await repository.append_event(
                    data.sessionId,
                    event_type="PRODUCTION_WORKFLOW_SIGNAL_DEFERRED",
                    actor_type="SYSTEM",
                    actor_id="return-support-api",
                    payload={
                        "supportAction": payload.action.value,
                        "errorType": type(error).__name__,
                    },
                    deduplication_key=(
                        f"support-signal-deferred:{data.id}:{payload.action.value}:{data.version}"
                    ),
                )
    return APIResponse(data=data, meta=_meta(request))


class ReturnOutcomeRecord(BaseModel):
    """One RMA as Support is issuing it."""

    model_config = ConfigDict(extra="forbid")

    returnReference: str = Field(min_length=1, max_length=128)
    trackingReference: str | None = Field(default=None, max_length=128)
    labelReference: str | None = Field(default=None, max_length=256)
    returnLocation: str | None = Field(default=None, max_length=128)
    shippingInstructionReference: str | None = Field(default=None, max_length=128)
    #: How the goods come back, as Support decided it (D23).
    #:
    #: **No `pattern=`.** The vocabulary is
    #: `return_policy.normalized_return_methods` in the active configuration, and
    #: it is operator-owned through the Control Centre. A regex here would refuse
    #: every method added after the day it was written and would advertise the
    #: stale set through OpenAPI as authoritative -- the defect
    #: `return_creation_policy.py` removed from `shippingPathExpectation` (CFG-03).
    #: `_reject_unconfigured_return_methods` below resolves the catalogue from the
    #: snapshot the process is serving, on the request path, so activating a
    #: release is enough to accept a new method.
    #:
    #: 64 characters to match `dbo.return_record.return_method`, so a value this
    #: model accepts is a value the authoritative store can hold.
    returnMethod: str | None = Field(default=None, min_length=1, max_length=64)
    #: Who is carrying the goods back, as Support arranged it (audit finding #9).
    #:
    #: The sender the case path never had. `SupportActionRequest.carrier` exists
    #: and reaches a production-workflow business payload, but only under
    #: `data.sessionId is not None`, and a Copilot case has no session -- so
    #: `ShipmentProjection.carrier` was `None` on every case and the Copilot's
    #: "Carrier & Service" tile was filled from `session.orderSource`, an order's
    #: source system rendered as a carrier. This field is the honest producer;
    #: nothing derives the value from anything else.
    #:
    #: **No `pattern=`, and no catalogue check either.** There is no
    #: operator-owned carrier vocabulary anywhere in the platform --
    #: `return_policy` declares none, and `dbo.return_tracking`'s `carrier_code`
    #: is a free column with no CHECK. A regex or an in-code list here would pin
    #: a runtime-owned vocabulary to the day it was written and advertise the
    #: stale set through OpenAPI as authoritative, which is the defect
    #: `return_creation_policy.py` removed from `shippingPathExpectation`
    #: (CFG-03). Carriers are also not a closed set the way return methods are:
    #: a method with no row in the requirement table makes a case permanently
    #: unresolvable, whereas an unrecognised carrier name is just a carrier
    #: nobody has heard of yet, and refusing it would lose Support's reply over
    #: a label on a screen.
    #:
    #: `min_length=1` because a blank string is not a statement. The merge
    #: treats `None` as "Support did not say" and leaves a value already present
    #: standing; `""` is not `None`, so it would sail through the merge and
    #: erase a carrier -- the exact deletion the merge exists to prevent.
    #: 64 characters to match `dbo.return_record.carrier`, so a value this model
    #: accepts is a value the authoritative store can hold.
    carrier: str | None = Field(default=None, min_length=1, max_length=64)
    #: The order lines this RMA covers. **At least one**, because a return that
    #: covers nothing cannot be received, credited or reconciled.
    #:
    #: This defaulted to `()` and nothing downstream objected: the activity builds
    #: the record's items by iterating this list, so an empty one produced a
    #: return record with zero items, and the console's submit gate asked only
    #: for a non-empty `returnReference`. The frozen return-truth decision -- a
    #: return cannot present as issued without durable items -- was declared and
    #: enforced in no layer at all. SQL cannot express "at least one child row"
    #: declaratively, so the contract is where it has to live.
    #:
    #: Five documents in Mongo `return_records` read `ISSUED` with no items, and
    #: this is the path that made them.
    orderLineReferences: tuple[str, ...] = Field(min_length=1, max_length=200)


class ReturnOutcomeRequest(BaseModel):
    """What Support is sending back to the case.

    A *list* of records, because one reply can issue several RMAs with
    different labels going to different places -- the shape the case model was
    changed to carry, and the one a single set of fields cannot express.
    """

    model_config = ConfigDict(extra="forbid")

    records: tuple[ReturnOutcomeRecord, ...] = Field(default=(), max_length=100)
    rejected: bool = False
    reason: str | None = Field(default=None, max_length=2_000)
    #: The identity of this Support mutation, supplied by the caller.
    #:
    #: Caller-supplied and not server-minted, because a server-minted id is a
    #: new id on every retry and therefore no idempotency at all -- the console
    #: that lost its response and pressed send again would issue a second RMA.
    #: `Idempotency-Key` is accepted as an equivalent, for callers that carry
    #: idempotency in a header across every endpoint they speak to.
    supportEventId: str | None = Field(default=None, min_length=1, max_length=128)


class ReturnOutcomeAcceptedView(BaseModel):
    """The receipt for a durably recorded Support event.

    Says what committed, and nothing about what the workflow has done with it.
    Delivery happens after this response is written, so any field here claiming
    the workflow had been told would be a claim the handler cannot make.
    """

    model_config = ConfigDict(extra="forbid")

    caseId: str
    supportEventId: str
    #: `RECORDED` the first time, `DUPLICATE` for a resend of the same event.
    #: Both are successes and both leave exactly one business mutation behind.
    disposition: str
    #: The outbox command that will carry it to the case workflow. Queued here;
    #: its state is readable at `GET /api/v1/integration-outbox`.
    outboxCommandId: str


def _support_event_id(payload: ReturnOutcomeRequest, header_value: str | None) -> str:
    """The body field, or the header, or a refusal.

    Required rather than defaulted. The whole mechanism below is keyed on this
    string, and a handler that invents one when the caller omits it would give
    every duplicate send a fresh identity and quietly restore the defect.
    """
    supplied = payload.supportEventId or header_value
    if supplied is None or not supplied.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "SUPPORT_EVENT_ID_REQUIRED",
                "message": (
                    "Supply supportEventId in the body or an Idempotency-Key header. "
                    "It is what makes a resent reply a no-op instead of a second RMA."
                ),
                "retryable": False,
            },
        )
    return supplied.strip()


def _reject_unconfigured_return_methods(request: Request, payload: ReturnOutcomeRequest) -> None:
    """Check every method named against the running configuration (D23).

    Resolved from `app.state.return_configuration` in the handler, after model
    validation, for the reason `return_creation_policy.apply_active_return_policy`
    documents at length: a pydantic validator has no request and no application
    state, so nothing it can read is runtime-owned -- which is the whole defect.
    `main.py`'s correlation middleware refreshes the activator before every
    handler, so the snapshot read here is the one this process is serving at that
    moment rather than one captured at import.

    Only reached when a method was actually named. A reply that says nothing
    about the method needs no catalogue to validate against, and demanding a
    loaded snapshot for it would make an unconfigured process refuse replies it
    is perfectly able to record. When one *is* named and no snapshot is loaded
    the answer is 503 and not 422: this process cannot say whether the method is
    valid, so it must not say it is not.
    """
    named = sorted(
        {record.returnMethod for record in payload.records if record.returnMethod is not None}
    )
    if not named:
        return
    loaded = getattr(request.app.state, "return_configuration", None)
    if not isinstance(loaded, LoadedReturnConfiguration):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "RETURN_CONFIGURATION_UNAVAILABLE",
                "message": (
                    "No return configuration is loaded, so a return method cannot be validated."
                ),
                "retryable": True,
            },
        )
    catalogue = loaded.configuration.return_policy.normalized_return_methods
    unknown = [method for method in named if method not in catalogue]
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "UNCONFIGURED_RETURN_METHOD",
                "message": (
                    f"returnMethod must be one of {sorted(catalogue)}; this reply named {unknown}"
                ),
                "retryable": False,
            },
        )


@router.post(
    "/work-items/{work_item_id}/return-outcome",
    response_model=APIResponse[ReturnOutcomeAcceptedView],
)
async def submit_return_outcome(
    work_item_id: str,
    payload: ReturnOutcomeRequest,
    request: Request,
    # Support roles only: issuing an RMA is Support's act, and the same gate
    # every other outcome-recording action on this router uses.
    actor_id: str = Depends(require_support_roles),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> APIResponse[ReturnOutcomeAcceptedView]:
    """Write Support's answer down, and let the outbox deliver it.

    This used to signal Temporal directly from the handler. When the case
    workflow had already closed, `handle.signal` raised a raw `RPCError`, the
    caller got an HTTP 500, and the RMA existed nowhere -- Support had no way to
    know their reply had evaporated. Worse, the shape was a dual write: persist,
    then signal, with a crash in between leaving a stored event that the retry
    recognised as a duplicate and reported as success while the workflow had
    never received it.

    So the handler no longer signals. One MongoDB transaction writes the Support
    event and the outbox command that delivers it, and the response is returned
    the moment that commits. `TemporalSignalDispatcher` picks the command up,
    retries a Temporal outage with bounded backoff, and dead-letters a workflow
    that is permanently gone into a state Phase 10 reconciles -- rather than
    losing the reply to a 500.

    The guarantee that comes out of this is stated the way it actually holds,
    and no stronger:

        transport (outbox -> Temporal):  at least once
        business processing:             effectively once, keyed on supportEventId

    A dispatcher that signals and then loses its process before it can
    acknowledge will signal again. What stops that from becoming a second RMA
    is the event id travelling with the notice, not the transport.

    `409 CASE_WORKFLOW_CLOSED` survives only where it is still reachable from
    here -- a case the platform already knows is terminal. A workflow that
    closed without the case knowing is no longer this handler's failure to
    detect; it is the dispatcher's, and it surfaces as a dead-lettered command.
    """
    item = await _service(request).get_work_item(work_item_id)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "WORK_ITEM_NOT_FOUND", "message": "No such work item."},
        )
    if item.caseId is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "WORK_ITEM_HAS_NO_CASE",
                "message": (
                    "This work item belongs to a return session rather than a case; "
                    "record the outcome through the support action instead."
                ),
                "retryable": False,
            },
        )

    support_event_id = _support_event_id(payload, idempotency_key)
    # Before anything is recorded. A method the operator has not configured
    # resolves to no row in the requirement table, so recording it would leave
    # the case permanently unresolvable with a durable event behind it.
    _reject_unconfigured_return_methods(request, payload)

    resources = getattr(request.app.state, "resources", None)
    if not isinstance(resources, RuntimeResources) or resources.mongo is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "SUPPORT_EVENT_STORE_UNAVAILABLE",
                "message": "The Support event cannot be recorded durably from this process.",
                "retryable": True,
            },
        )
    # Deliberately *not* `resources.temporal`. Recording the event no longer
    # touches Temporal, and requiring a Temporal connection to accept a write
    # that does not use one would reintroduce the coupling this phase removes:
    # Support could not file a reply during a Temporal outage, which is the
    # exact outage the outbox is here to absorb.

    repository = OperationalRepository(resources.mongo, resources.settings, resources.source_mongo)
    case = await repository.get_case(item.caseId)
    if case is not None and str(case.get("status")) in _TERMINAL_CASE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "CASE_WORKFLOW_CLOSED",
                "message": (
                    "This case is already closed; its workflow will not accept an outcome. "
                    "Reopen the case or raise a new one."
                ),
                "retryable": False,
            },
        )

    store = DurableSupportEventStore(resources.mongo, resources.settings)
    try:
        receipt = await store.record_support_response(
            case_id=item.caseId,
            work_item_id=work_item_id,
            support_event_id=support_event_id,
            records=[
                support_return_record(
                    return_reference=record.returnReference,
                    tracking_reference=record.trackingReference,
                    label_reference=record.labelReference,
                    return_location=record.returnLocation,
                    shipping_instruction_reference=record.shippingInstructionReference,
                    return_method=record.returnMethod,
                    carrier=record.carrier,
                    order_line_references=record.orderLineReferences,
                )
                for record in payload.records
            ],
            rejected=payload.rejected,
            reason=payload.reason,
            workflow_id=return_case_workflow_id(item.caseId),
            actor_id=actor_id,
            correlation_id=_meta(request).request_id,
        )
    except IdempotencyConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "IDEMPOTENCY_CONFLICT",
                "message": (
                    "This supportEventId was already recorded for this case with a different "
                    "payload. Use a new id for a new reply."
                ),
                "retryable": False,
            },
        ) from error

    return APIResponse(
        data=ReturnOutcomeAcceptedView(
            caseId=receipt.case_id,
            supportEventId=receipt.support_event_id,
            disposition="DUPLICATE" if receipt.duplicate else "RECORDED",
            outboxCommandId=receipt.outbox_command_id,
        ),
        meta=_meta(request),
    )
