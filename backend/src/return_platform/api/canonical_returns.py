"""The canonical Return Business Copilot API: `/api/returns`.

Phase 16. Versionless, matching `/api/graph-schema` and `/api/config`.

**One session aggregate, one surface.** Today the return domain is served by
nine routers across six prefixes, three of which (`api/returns.py`,
`api/physical_operations.py`, `api/return_artifacts.py`) already share
`/api/v1/returns` — so "which module owns this path" is not answerable from the
path alone. That fragmentation is what this consolidates.

**Reads first, then writes.** The plan's instruction is "resolve duplicate
current implementations before deleting anything", so this surface was read-only
until they were. It no longer is: the write surface is `POST ""` and
`POST "/{session_id}/events"`, and those two replace five legacy routes --
create and cancel on `api/returns.py`, start and events on
`api/production_workflow.py`.

**Two routes, not five, because three of the five should not exist.**
Cancellation is an event, not an endpoint: the legacy `/cancel` wrote the
session document and released the discovery lock without telling the workflow,
while the workflow's own CANCELLED event updated durable state but left the lock
held, so the two disagreed about what a cancelled return looked like.
`ProductionWorkflowCoordinator.record_event` now releases the lock, which makes
the event able to replace the endpoint rather than merely outrank it. And
`/start` only ever created a workflow with no event in it -- `record_event`
calls `ensure_started` itself.

**Correction to an earlier claim in this docstring.** It said the duplicates were
"two artifact endpoints on one prefix", on the write side. Neither part held.
`GET /{id}/artifacts` returns the document-artifact list; `GET
/{id}/production-artifacts` returns the return's entire evidence record, of
which document artifacts are one of eleven collections. They shared a word, not
an implementation, and both are reads. Both now have canonical homes here
(`/artifacts` and `/evidence`), which is what actually resolves them. The real
write-side overlap was that four routers recorded production workflow
transitions and one of them ran the authorization check -- closed separately in
`operations/production_event_authorization.py`.

**The two parked reads are here too.** `/support` looked like two competing
stores and is the same shared-word-over-different-things as artifacts/evidence:
a case is raised by the platform when a flow fails, a work item is opened by a
person. `/conversation` was genuinely unanswerable -- the session-to-conversation
direction had no accessor and no index -- and now is not.

Still open on the write side: the overlapping stage actions between
`production_workflow.py` and `physical_operations.py`. The associate flow is not
one of them; it is partitioned by channel, and
`tests/api/test_return_creation_is_single_sourced.py` holds that.

**No generic advance.** There is deliberately no `POST /{session_id}/advance`
here and there never will be: a stage completes because a specific,
evidence-carrying command was applied (`ReturnWorkflowAdvanceCommand`), and an
endpoint that took a target state as a parameter would let a caller move a
return without producing the evidence that justifies the move. A test in
`tests/platform/` enforces this for the canonical surface as well as the legacy
one.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from temporalio.client import WorkflowUpdateFailedError
from temporalio.exceptions import ApplicationError
from temporalio.service import RPCError

from return_platform.configuration.return_configuration import LoadedReturnConfiguration
from return_platform.operations.associate_service_factory import (
    build_associate_conversation_service,
)
from return_platform.operations.models import (
    ReturnCreateRequest,
    ReturnSessionView,
    TimelineEvent,
)
from return_platform.operations.production_event_authorization import (
    ProductionEventNotPermitted,
    authorize_production_event,
)
from return_platform.operations.production_workflow import resolve_production_coordinator
from return_platform.operations.repository import (
    OperationalRepository,
    resolve_operational_repository,
)
from return_platform.operations.return_support.service import ReturnSupportService
from return_platform.resources import RuntimeResources
from return_platform.security.authorization import (
    actor_roles,
    require_read_roles,
    require_write_roles,
)
from return_platform.shared.contracts import APIResponse, ResponseMeta
from return_platform.workflows.production_return_workflow import ProductionReturnEventType

router = APIRouter(prefix="/api/returns", tags=["Returns"])


class ReturnEventRequest(BaseModel):
    """One evidence-carrying production event.

    Mirrors the legacy `ProductionEventRequest` field for field. `evidence` is
    required and has a minimum length because it is the whole justification for
    the transition -- the stage-result binding, the audit record and the outbox
    event all hang off it. `extra="forbid"` so a caller who misspells
    `businessPayload` is told, rather than having their payload dropped.
    """

    model_config = ConfigDict(extra="forbid")

    eventId: str = Field(min_length=8, max_length=128)
    eventType: ProductionReturnEventType
    evidenceReference: str = Field(min_length=3, max_length=512)
    businessPayload: dict[str, object] = Field(default_factory=dict)


class ReturnEventResult(BaseModel):
    """Typed, unlike the legacy handler's `dict[str, object]`.

    A caller needs to know where the return ended up, and whether it is now
    terminal -- both of which decide what the next request may be.
    """

    model_config = ConfigDict(extra="forbid")

    stage: str
    caseFullyClosed: bool
    cancelled: bool


class ReturnSupport(BaseModel):
    """The two support records a return can have, and they are not duplicates.

    This pair is why `/support` was parked: two stores looked like competing
    implementations of one idea. Measured, they are the artifact/evidence
    situation again -- a shared word over different things.

    `case` is raised **by the platform**: `orchestrator._fail` creates one when a
    return flow fails, with a type, a priority and an SLA. Nothing human opens
    it.

    `workItem` is opened **by a person**, through the support workbench. It
    carries a message thread and an eleven-state lifecycle from NEW to
    COMPLETED.

    Both are at most one per return -- each collection has `sessionId` uniquely
    indexed -- and either can be absent. A return that never failed has no case;
    a return support never touched has no work item. `null` is a real answer for
    both, and collapsing them into one field would lose which of the two a
    caller is looking at.
    """

    model_config = ConfigDict(extra="forbid")

    case: dict[str, Any] | None = None
    workItem: dict[str, Any] | None = None


class ReturnEvidence(BaseModel):
    """Everything recorded *about* a return that is not the return itself.

    Typed rather than the legacy `dict[str, Any]`, so the contract names the
    collections instead of publishing an opaque object. The entries stay
    `dict[str, Any]`: they are projections of documents whose shape belongs to
    the physical-operations and integration modules, and inventing a model per
    collection here would put eleven schemas in the return domain that the
    return domain does not own.
    """

    model_config = ConfigDict(extra="forbid")

    returnItems: list[dict[str, Any]] = Field(default_factory=list)
    handlingUnits: list[dict[str, Any]] = Field(default_factory=list)
    pickup: dict[str, Any] | None = None
    branchStaging: list[dict[str, Any]] = Field(default_factory=list)
    documentArtifacts: list[dict[str, Any]] = Field(default_factory=list)
    shippingInstructions: list[dict[str, Any]] = Field(default_factory=list)
    shipmentEvents: list[dict[str, Any]] = Field(default_factory=list)
    omcCommands: list[dict[str, Any]] = Field(default_factory=list)
    integrationCommands: list[dict[str, Any]] = Field(default_factory=list)
    vendorReturnLinks: list[dict[str, Any]] = Field(default_factory=list)
    agentDecisions: list[dict[str, Any]] = Field(default_factory=list)


def _without_mongo_id(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop `_id`. The legacy handlers repeated this comprehension eleven times;
    one of eleven copies quietly not doing it is how a raw ObjectId reaches a
    client."""
    return [
        {key: value for key, value in document.items() if key != "_id"} for document in documents
    ]


def _optional_support_service(request: Request) -> ReturnSupportService | None:
    """The work-item service, or `None` when its dependencies are absent.

    `api/return_support.py::_service` raises 503 for the same condition, which is
    right for a router whose every endpoint is a work item. It is wrong here:
    `/support` also returns the platform-raised case, which needs only the
    operational repository, and failing the whole response because half of it is
    unavailable would hide the half that works.
    """
    resources = getattr(request.app.state, "resources", None)
    loaded = getattr(request.app.state, "return_configuration", None)
    if (
        not isinstance(resources, RuntimeResources)
        or resources.mongo is None
        or not isinstance(loaded, LoadedReturnConfiguration)
    ):
        return None
    return ReturnSupportService(
        client=resources.mongo,
        settings=resources.settings,
        configuration=loaded.configuration,
        operational_repository=OperationalRepository(
            resources.mongo, resources.settings, resources.source_mongo
        ),
    )


def _meta(request: Request) -> ResponseMeta:
    request_id = getattr(request.state, "correlation_id", "unknown")
    return ResponseMeta(request_id=request_id if isinstance(request_id, str) else "unknown")


async def _require_session(request: Request, session_id: str) -> Any:
    """404 before doing anything else.

    Sub-resources check the parent explicitly rather than returning an empty
    list for a session that does not exist -- an empty timeline and a
    nonexistent return are different answers, and a UI cannot tell them apart
    from `[]`.
    """
    repository = resolve_operational_repository(request)
    session = await repository.get_return(session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Return session not found"
        )
    return session


@router.get("", response_model=APIResponse[list[ReturnSessionView]])
async def list_returns(
    request: Request,
    return_status: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    _actor_id: str = Depends(require_read_roles),
) -> APIResponse[list[ReturnSessionView]]:
    repository = resolve_operational_repository(request)
    return APIResponse(
        data=await repository.list_returns(status=return_status, limit=limit),
        meta=_meta(request),
    )


@router.get("/{session_id}", response_model=APIResponse[ReturnSessionView])
async def get_return(
    request: Request,
    session_id: str,
    _actor_id: str = Depends(require_read_roles),
) -> APIResponse[ReturnSessionView]:
    return APIResponse(data=await _require_session(request, session_id), meta=_meta(request))


@router.get("/{session_id}/timeline", response_model=APIResponse[list[TimelineEvent]])
async def get_timeline(
    request: Request,
    session_id: str,
    after_sequence: int = Query(default=0, alias="after", ge=0),
    limit: int = Query(default=1_000, ge=1, le=10_000),
    _actor_id: str = Depends(require_read_roles),
) -> APIResponse[list[TimelineEvent]]:
    """Named `timeline`, not `events`.

    The legacy path is `/events`, but the aggregate this belongs to calls it a
    timeline and so does the plan's domain list. The canonical name should match
    the domain rather than inherit an implementation word; the legacy path keeps
    working until Wave F.
    """
    await _require_session(request, session_id)
    repository = resolve_operational_repository(request)
    return APIResponse(
        data=await repository.list_events(session_id, after_sequence=after_sequence, limit=limit),
        meta=_meta(request),
    )


@router.get("/{session_id}/artifacts", response_model=APIResponse[list[dict[str, Any]]])
async def get_artifacts(
    request: Request,
    session_id: str,
    _actor_id: str = Depends(require_read_roles),
) -> APIResponse[list[dict[str, Any]]]:
    """The return's document artifacts.

    Canonical home for `GET /api/v1/returns/{id}/artifacts`, which has no
    consumer today -- its name was being shadowed by the unrelated
    `production-artifacts` endpoint next to it on the same prefix.
    """
    await _require_session(request, session_id)
    repository = resolve_operational_repository(request)
    return APIResponse(
        data=_without_mongo_id(await repository.list_document_artifacts(session_id)),
        meta=_meta(request),
    )


@router.get("/{session_id}/evidence", response_model=APIResponse[ReturnEvidence])
async def get_evidence(
    request: Request,
    session_id: str,
    _actor_id: str = Depends(require_read_roles),
) -> APIResponse[ReturnEvidence]:
    """Named `evidence`, not `production-artifacts`.

    The legacy name was the reason `artifacts` and `production-artifacts` looked
    like a duplicate pair worth reconciling. They never were: `artifacts` is the
    document-artifact list, and `production-artifacts` is the return's entire
    evidence record, of which document artifacts are one of eleven collections.
    A shared word, not a shared implementation. The canonical name says what the
    payload is.

    **Narrower than the legacy endpoint, deliberately.** That one also embedded
    the session and the first thousand timeline events. Both now have canonical
    endpoints of their own, and including them here would make this a third way
    to read a session and a second way to read a timeline -- adding surface
    while claiming to consolidate it. A client that wants all three asks for all
    three; the legacy path keeps its combined shape until Wave F.
    """
    await _require_session(request, session_id)
    repository = resolve_operational_repository(request)
    return APIResponse(
        data=ReturnEvidence(
            returnItems=_without_mongo_id(await repository.list_return_items(session_id)),
            handlingUnits=_without_mongo_id(await repository.list_handling_units(session_id)),
            pickup=await repository.get_pickup_projection(session_id),
            branchStaging=_without_mongo_id(
                await repository.list_branch_staging_records(session_id)
            ),
            documentArtifacts=_without_mongo_id(
                await repository.list_document_artifacts(session_id)
            ),
            shippingInstructions=_without_mongo_id(
                await repository.list_shipping_instructions(session_id)
            ),
            shipmentEvents=_without_mongo_id(await repository.list_shipment_events(session_id)),
            omcCommands=_without_mongo_id(await repository.list_omc_commands(session_id)),
            integrationCommands=_without_mongo_id(
                await repository.list_integration_commands(session_id)
            ),
            vendorReturnLinks=_without_mongo_id(
                await repository.list_vendor_return_links(session_id)
            ),
            agentDecisions=_without_mongo_id(await repository.list_agent_decisions(session_id)),
        ),
        meta=_meta(request),
    )


@router.get("/{session_id}/support", response_model=APIResponse[ReturnSupport])
async def get_support(
    request: Request,
    session_id: str,
    _actor_id: str = Depends(require_read_roles),
) -> APIResponse[ReturnSupport]:
    """The return's support records: the platform-raised case and the human work item.

    Both are optional and independent. See `ReturnSupport` for why they are two
    fields rather than one -- they were parked as an unresolved duplicate and
    are not one.

    The work-item service is resolved leniently: a deployment without the
    support module still has cases, and 503-ing the whole endpoint because half
    of it is unavailable would hide the half that works.
    """
    await _require_session(request, session_id)
    repository = resolve_operational_repository(request)
    case = await repository.get_support_case_for_session(session_id)

    work_item = None
    service = _optional_support_service(request)
    if service is not None:
        work_item = await service.get_work_item_for_session(session_id)

    return APIResponse(
        data=ReturnSupport(
            case=case.model_dump(mode="json") if case is not None else None,
            workItem=work_item.model_dump(mode="json") if work_item is not None else None,
        ),
        meta=_meta(request),
    )


@router.get("/{session_id}/conversation", response_model=APIResponse[dict[str, Any] | None])
async def get_conversation(
    request: Request,
    session_id: str,
    _actor_id: str = Depends(require_read_roles),
) -> APIResponse[Any]:
    """The associate discovery conversation this return came out of, if any.

    **This direction did not previously exist.** `returnSessionId` is stamped on
    the conversation when `submit_details` creates the return, so the link was in
    the data -- but only conversation-to-session, with no accessor and no index
    for the reverse. Given a session there was no way to find its conversation,
    which is why this endpoint was parked as unresolved rather than merely
    unbuilt. `AssociateConversationService.get_for_session` and a sparse index
    are what closed it.

    **`null` is a successful answer, not a 404.** A SYSTEM-channel return has no
    conversation behind it and never will; in a batch-driven deployment most
    returns will not. 404 here would mean "no such return", which the parent
    check already covers, and a caller could not tell the two apart.
    """
    await _require_session(request, session_id)
    service = build_associate_conversation_service(request)
    conversation = await service.get_for_session(session_id)
    return APIResponse(
        data=conversation.model_dump(mode="json") if conversation is not None else None,
        meta=_meta(request),
    )


@router.post("", status_code=status.HTTP_201_CREATED, response_model=APIResponse[ReturnSessionView])
async def create_return(
    request: Request,
    payload: ReturnCreateRequest,
    actor_id: str = Depends(require_write_roles),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> APIResponse[ReturnSessionView]:
    """Create a SYSTEM-channel return.

    Interactive returns are refused here exactly as on the legacy path: they
    begin as a conversation, and a return created directly would have no
    discovery evidence behind it. The conversation surface is not yet canonical
    (`/support` and `/conversation` are parked), so the refusal names the legacy
    path -- pointing at an endpoint that does not exist would be worse.
    """
    if payload.channel != "SYSTEM":
        raise HTTPException(
            status_code=409,
            detail=(
                "Interactive returns must start through /api/v1/associate-returns/conversations."
            ),
        )
    if idempotency_key is not None:
        payload = payload.model_copy(update={"idempotencyKey": idempotency_key})
    repository = resolve_operational_repository(request)
    return APIResponse(
        data=await repository.create_return(
            payload,
            correlation_id=_meta(request).request_id,
            actor_id=actor_id,
        ),
        meta=_meta(request),
    )


@router.post("/{session_id}/events", response_model=APIResponse[ReturnEventResult])
async def record_event(
    request: Request,
    session_id: str,
    payload: ReturnEventRequest,
    actor: str = Depends(require_write_roles),
) -> APIResponse[ReturnEventResult]:
    """The canonical way to move a return, and the only one.

    **This is what replaces `POST /{id}/cancel`.** Cancellation is
    `eventType: CANCELLED` -- not a separate endpoint. The legacy pair were two
    ways to cancel that disagreed: `/cancel` wrote `status: CANCELLED` straight
    to Mongo and released the discovery lock but never told the workflow, while
    the workflow's CANCELLED event updated the durable state and the session
    document but left the discovery lock held. Whichever a caller used, one of
    the two records was wrong. `record_event` now releases the lock itself, so
    the single canonical path does everything both did.

    **It also replaces `POST /{id}/start`.** `record_event` calls
    `ensure_started` first, so a separate start endpoint only exists to create a
    workflow with no event in it. Callers that want the workflow running send
    the first event.

    Not a generic advance: the caller names an *event that happened* and the
    evidence for it, and the state machine decides which stage that implies.
    An endpoint taking a target stage would invert that.
    """
    # Refused before `ensure_started`, so a caller who may not record the event
    # does not leave a started workflow behind as the side effect of a 403.
    try:
        authorize_production_event(event_type=payload.eventType, actor_roles=actor_roles(request))
    except ProductionEventNotPermitted as error:
        raise HTTPException(status_code=403, detail=str(error)) from error

    session = await _require_session(request, session_id)
    coordinator = resolve_production_coordinator(request)
    try:
        await coordinator.ensure_started(session, actor_id=actor)
        state = await coordinator.record_event(
            session_id,
            event_id=payload.eventId,
            event_type=payload.eventType,
            evidence_reference=payload.evidenceReference,
            actor_id=actor,
            actor_roles=actor_roles(request),
            business_payload=dict(payload.businessPayload),
        )
    except ValueError as error:
        # Raised on this side of the boundary -- a business payload missing a
        # field the projection needs.
        raise HTTPException(status_code=409, detail=str(error)) from error
    except WorkflowUpdateFailedError as error:
        # The state machine refused: out of order, or already recorded. The
        # legacy handler flattened every one of these to "Production workflow
        # update failed or is not available", which tells a caller nothing about
        # whether to fix the request or retry it. The real reason is on the
        # wrapped `ApplicationError`, exactly as `orchestrator._failure_code`
        # reads it.
        cause = error.cause
        detail = str(cause) if isinstance(cause, ApplicationError) else str(error)
        raise HTTPException(status_code=409, detail=detail) from error
    except RPCError as error:
        # Temporal is unreachable or timed out. Not the caller's fault, and not
        # a conflict -- the legacy handler reported it as 409, which invites a
        # client to "fix" a request that was already correct.
        raise HTTPException(
            status_code=503, detail="The production workflow service is unavailable."
        ) from error
    return APIResponse(
        data=ReturnEventResult(
            stage=state.stage.value,
            caseFullyClosed=state.case_fully_closed,
            cancelled=state.cancelled,
        ),
        meta=_meta(request),
    )
