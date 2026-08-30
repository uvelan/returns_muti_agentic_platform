"""The review endpoints (contracts.md sect. 9), and the panel's dependencies.

Every mutation here does the same four things in the same order, and the order
is the point:

1. **capability** -- `RETURNS_SUPPORT_ACT` to decide what Support is told,
   `RETURNS_REVIEW_RECOVERY` to re-drive or abandon a send, `RETURNS_SESSION_READ`
   to look;
2. **case access** -- 404 rather than 403 for a case that is not the caller's,
   so the endpoint is not an existence oracle;
3. **server-stamped actor** -- the authenticated principal, never a body field.
   `SYSTEM` is refused by S2 before the store is touched, so no client can
   approve as the platform;
4. **through S2's interfaces**, never around them.

**Every one of S2's six error types is a 409**, and the body carries the
review's *state* rather than a bare message, because contracts.md sect. 6 asks
the UI to surface the transition -- "this review is already approving" -- and a
UI that only has "409" can only say "something went wrong".

**V1's approval goes through the gate service, not the store.** That is
carry-forward conditions 5a and 8: `SupportTemplateGateService.approve`
recomputes the editing actor set from the edit rows before delegating, and it is
the only approval path V1 has, so both this endpoint and `auto_send` are
covered. See `operations/support_template_gate.py`.

---

**Two endpoints beyond sect. 9's list, both on a resource it names.**

Sect. 9 enumerates `GET .../reviews/{id}/edit-state` and does not enumerate a
write. Autosave has to land somewhere, so it lands on the same resource as
`PUT`, and conflict resolution -- which is V1's to own by sect. 10 -- lands as
`POST .../edit-state/resolve`. Recorded here rather than treated as obvious: a
new *path* would have been a new endpoint, and these are the read and write
halves of one.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Annotated, Any, Final, cast

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from temporalio.service import RPCError

from return_platform.configuration.return_configuration import LoadedReturnConfiguration
from return_platform.operations.case_commands import (
    CaseCommandKind,
    CaseCommandReceipt,
    DurableCaseCommandStore,
    StaleReviewVersionError,
)
from return_platform.operations.case_panel import PanelExecutionView, PanelTimersView
from return_platform.operations.repository import resolve_operational_repository
from return_platform.operations.review_aggregate import (
    ApprovedPayloadHashMismatchError,
    PendingRevisionError,
    ReservedActorError,
    ReviewAggregateStore,
    ReviewConflictError,
    ReviewKind,
    ReviewNotFoundError,
    ReviewState,
    ReviewStateError,
    ReviewVersionMismatchError,
)
from return_platform.operations.support_template_gate import (
    MongoDraftEditRows,
    SupportTemplateGateService,
    neutralise_field_edits,
)
from return_platform.resources import RuntimeResources
from return_platform.security.authorization import require_capability
from return_platform.security.capabilities import (
    RETURNS_REVIEW_RECOVERY,
    RETURNS_SESSION_READ,
    RETURNS_SUPPORT_ACT,
)
from return_platform.security.principal import Principal
from return_platform.shared.contracts import APIResponse, ResponseMeta
from return_platform.workflows.return_case_workflow import return_case_workflow_id

logger = logging.getLogger("return_platform.api.case_reviews")

router = APIRouter(prefix="/api/v1/cases", tags=["Case reviews"])


# --------------------------------------------------------------------------- #
# Wiring
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ReviewDependencies:
    reviews: ReviewAggregateStore
    commands: DurableCaseCommandStore
    gate: SupportTemplateGateService


def panel_dependencies(request: Request) -> ReviewDependencies:
    """The three stores this surface drives, or a clear 503.

    Built per request rather than held on app state, because the stores are
    thin wrappers over one `AsyncMongoClient` that the process already holds --
    and a cached instance would outlive a configuration reload that the gate
    reads through a callable.
    """
    resources = getattr(request.app.state, "resources", None)
    if not isinstance(resources, RuntimeResources) or resources.mongo is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "REVIEW_STORE_UNAVAILABLE",
                "message": "The review aggregate is not available in this process.",
                "retryable": True,
            },
        )
    settings = resources.settings
    mongo = resources.mongo
    commands = DurableCaseCommandStore(mongo, settings)
    reviews = ReviewAggregateStore(mongo, settings, command_store=commands)
    loaded = getattr(request.app.state, "return_configuration", None)
    configuration = loaded.configuration if isinstance(loaded, LoadedReturnConfiguration) else None
    gate = SupportTemplateGateService(
        reviews=reviews,
        # Required, never defaulted: an approval guard that is present and inert
        # is worse than one that refuses to build.
        edit_rows=MongoDraftEditRows(mongo[settings.mongo_database]),
        support_service=None,
        configuration=lambda: configuration,
        # This surface opens no reviews and sends nothing, so it writes no
        # facts. A callable that raised would be the honest shape if it ever
        # did; today nothing here reaches it.
        append_fact=_no_facts,
    )
    return ReviewDependencies(reviews=reviews, commands=commands, gate=gate)


async def _no_facts(**fact: Any) -> bool:
    raise RuntimeError(
        "the review endpoints write no case facts; the gate's fact writer is the "
        "activity's. If this raised, a code path moved and needs its own writer."
    )


async def require_case_access(request: Request, case_id: str) -> dict[str, Any]:
    """The case, or a 404 that tells a guesser nothing.

    **Tenant-scoped, not principal-scoped**, and deliberately: the associate who
    raised a return is frequently not the person reviewing the message about
    it, and a shift handover must not make a draft unreachable.
    `api/cases.py:_tenant_scoped_case` draws the same line for the recovery
    routes for the same reason.
    """
    repository = resolve_operational_repository(request)
    case = await repository.get_case(case_id)
    tenant_id = str(getattr(request.state, "tenant_id", "default"))
    if case is None or case.get("tenantId") != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "CASE_NOT_FOUND",
                "message": f"Case {case_id} does not exist.",
                "retryable": False,
            },
        )
    return dict(case)


async def case_execution_state(
    request: Request, case_id: str
) -> tuple[PanelExecutionView, PanelTimersView] | None:
    """The workflow's `execution_state` query.

    **The workflow id is derived, never read off the case** (carry-forward
    condition 5). `cases.workflowId` is a link written after the start and
    swallowed on failure, so it is legitimately null on a perfectly healthy
    case -- S2's review round 1 found the recovery sweep starving on exactly
    that assumption. `return_case_workflow_id(case_id)` is the id, and it is
    the id whether or not anybody managed to record it.
    """
    resources = getattr(request.app.state, "resources", None)
    temporal = getattr(resources, "temporal", None) if resources is not None else None
    if temporal is None:
        return None
    handle = temporal.get_workflow_handle(return_case_workflow_id(case_id))
    state = await handle.query("execution_state")
    return (
        PanelExecutionView(
            case_status=_attribute(state, "status"),
            work_item_id=_attribute(state, "work_item_id"),
            awaiting=tuple(getattr(state, "awaiting", ()) or ()),
            business_complete=bool(getattr(state, "business_complete", False)),
            parked_reason=_attribute(state, "parked_reason"),
        ),
        PanelTimersView(
            template_review_deadline_iso=_attribute(state, "template_review_deadline_iso"),
            template_review_reminders_sent=int(
                getattr(state, "template_review_reminders_sent", 0) or 0
            ),
        ),
    )


async def execution_holds_review(request: Request, case_id: str, review_id: str) -> bool | None:
    """Whether the running execution is still holding this review.

    **The liveness question AMENDMENT-5 rule 1 asks**, and it is asked of the
    *execution* rather than of the review aggregate on purpose: the aggregate
    says what state the review is in, but only the execution knows whether
    anything is still listening for a decision about it. A retry that satisfies
    the aggregate and not the execution is exactly the one that strands the
    review.

    `execution_state` already carries `template_reviews` -- the
    `(request_id, review_id)` pairs this run holds -- because the panel composes
    from it. No new query and no new field.

    `None` means **could not determine**: no Temporal client in this process, or
    the host would not answer. The caller refuses on `None`, and 503 rather than
    409, because "the gate has closed" and "we cannot tell whether it has" are
    different answers and only one of them is retryable.
    """
    resources = getattr(request.app.state, "resources", None)
    temporal = getattr(resources, "temporal", None) if resources is not None else None
    if temporal is None:
        return None
    try:
        handle = temporal.get_workflow_handle(return_case_workflow_id(case_id))
        state = await handle.query("execution_state")
    except (TimeoutError, ConnectionError, RPCError):
        return None
    held = getattr(state, "template_reviews", ()) or ()
    return any(str(pair[1]) == review_id for pair in held if len(tuple(pair)) == 2)


def _attribute(state: Any, name: str) -> str | None:
    value = getattr(state, name, None)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _meta(request: Request) -> ResponseMeta:
    request_id = getattr(request.state, "correlation_id", "unknown")
    return ResponseMeta(request_id=request_id if isinstance(request_id, str) else "unknown")


def _actor_of(request: Request) -> str:
    """The authenticated principal. **Never a body field.**

    `PolicyOverrideNotice` states the rule for the same reason: a
    client-supplied actor is not audit, and here it would also be the one field
    standing between a client and approving as `SYSTEM`.
    """
    principal = cast(Principal, request.state.principal)
    return str(principal.subject)


# --------------------------------------------------------------------------- #
# Telling the execution
# --------------------------------------------------------------------------- #

#: review kind -> (revised, cancelled) command kind. Approval's pair lives in
#: the review aggregate, which does its own branching inside the approving
#: transaction; these two are the ones this surface records.
_DECISION_KINDS: dict[str, tuple[CaseCommandKind, CaseCommandKind]] = {
    ReviewKind.TEMPLATE.value: (
        CaseCommandKind.TEMPLATE_REVISED,
        CaseCommandKind.TEMPLATE_CANCELLED,
    ),
    ReviewKind.SUPPORT_REPLY.value: (
        CaseCommandKind.REPLY_REVISED,
        CaseCommandKind.REPLY_CANCELLED,
    ),
}


async def _tell_the_execution(
    dependencies: ReviewDependencies,
    *,
    case_id: str,
    review: dict[str, Any],
    kind: CaseCommandKind,
    actor_id: str,
    signal_id: str,
    correlation_id: str | None,
    note: str | None = None,
    supersedes: str | None = None,
) -> CaseCommandReceipt:
    """Record the durable command whose dispatch is the workflow's notice.

    **This is what was missing, and its absence was silent.** `cancel`,
    `request_revision` and `redraft` move the review in Mongo and record no
    command -- only `approve` and `retry_delivery` do, because those two need
    the command *inside* their own transaction for the frozen CAS. So a review
    a person cancelled stayed `OPEN` in the workflow's wait map, a revision was
    never re-rendered, and a redraft's new attempt was invisible to the
    execution that owns the request. Every one of those ends the same way: the
    case waits to its deadline and parks `TEMPLATE_REVIEW_UNANSWERED` for a
    decision somebody made ten minutes earlier.

    Contracts.md sect. 7 fixes the shape -- REST never signals Temporal
    directly; a command record and its outbox row are the only path -- and
    `DurableCaseCommandStore.record_command` is the standalone form for a caller
    that does not own a larger transaction.

    **The ordering, and the window it leaves.** The state change commits first
    and this second. They are two transactions, because the review aggregate's
    state moves go through `_guarded_update`, which owns its own. If this write
    fails, the review is decided in the store and the execution has not heard:
    the case waits to its deadline and parks as unanswered, with the review's
    real state on the panel the whole time. The other ordering fails worse --
    the execution acts on a decision the store then refuses to record -- and for
    `cancel` specifically it would leave the workflow still holding a review it
    would happily send. Recovering the window is what makes `signal_id`
    deterministic: retrying the endpoint recognises the command as a duplicate
    rather than minting a second one.
    """
    review_id = str(review["_id"])
    payload: dict[str, Any] = {
        "review_id": review_id,
        # Sect. 7 requires both of these on a review-scoped command, and
        # `plan_command` refuses the write without them.
        "scope_id": str(review["scopeId"]),
        "signal_id": signal_id,
        "request_id": str(review["requestId"]),
        "actor": actor_id,
        "note": note,
        "draft_version": int(review.get("draftVersion", 0)),
        "canonical_edit_version": int(review.get("canonicalEditVersion", 0)),
        "supersedes": supersedes,
    }
    return await dependencies.commands.record_command(
        case_id=case_id,
        workflow_id=return_case_workflow_id(case_id),
        kind=kind,
        signal_id=signal_id,
        actor_id=actor_id,
        payload=payload,
        review_id=review_id,
        correlation_id=correlation_id,
    )


def _decision_kinds(review: dict[str, Any]) -> tuple[CaseCommandKind, CaseCommandKind]:
    kinds = _DECISION_KINDS.get(str(review["reviewKind"]))
    if kinds is None:  # pragma: no cover - the enum is closed
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "UNKNOWN_REVIEW_KIND",
                "message": f"No command kinds are mapped for {review['reviewKind']!r}.",
                "retryable": False,
            },
        )
    return kinds


# --------------------------------------------------------------------------- #
# 409, with the transition in it
# --------------------------------------------------------------------------- #

#: S2's six, plus the command store's stale-version error. Every one of them is
#: "the store's truth moved under you", which is one HTTP answer.
_CONFLICTS: tuple[type[Exception], ...] = (
    ReviewStateError,
    ReviewVersionMismatchError,
    ReviewConflictError,
    PendingRevisionError,
    ApprovedPayloadHashMismatchError,
    ReservedActorError,
    StaleReviewVersionError,
)


def _conflict(error: Exception) -> HTTPException:
    """One 409, carrying enough for the UI to say what happened.

    `state` is present whenever the store knew it, because contracts.md sect. 6
    asks the UI to surface *the transition*: "this review is already approving,
    and here is what it is doing" is actionable, and "409 Conflict" is not.
    `code` is the exception's own name so the UI can branch on the *kind* --
    an unresolved conflict offers Resolve, a stale version offers Reload -- and
    a new S2 error type shows up as an unknown code rather than as silence.
    """
    detail: dict[str, Any] = {
        "code": type(error).__name__,
        "message": str(error),
        "retryable": False,
    }
    state = getattr(error, "state", None)
    if state is not None:
        detail["state"] = getattr(state, "value", str(state))
    for field in ("field", "expected", "actual", "review_id"):
        value = getattr(error, field, None)
        if value is not None:
            detail[field] = value
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


def _missing(error: ReviewNotFoundError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "code": "REVIEW_NOT_FOUND",
            "message": str(error),
            "retryable": False,
        },
    )


# --------------------------------------------------------------------------- #
# Bodies
# --------------------------------------------------------------------------- #


#: A draft payload's top-level keys. The shipped template renders six; a
#: hundred is room for every variant an operator could publish and a refusal for
#: anything that is not a draft.
_MAX_PAYLOAD_KEYS: Final = 100


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReviewRefusalDetail(BaseModel):
    """The `detail` a refusal from this surface carries.

    **Declared, because the console branches on it.** FastAPI documents the
    bodies a handler *returns* and knows nothing about the ones it raises, so
    every 409 and 404 here was absent from the OpenAPI document -- and a
    response shape a client reads and the contract does not mention is drift
    with nothing watching it. The console's MSW conformance test found this by
    refusing to validate a mocked 409 against a contract that declared none.

    `state` is the field contracts.md sect. 6 is really asking for: the UI
    surfaces *the transition*, and "this review is already approving" is
    actionable where "409 Conflict" is not. `code` is the exception's own class
    name so a client can branch on the kind -- an unresolved conflict offers
    Resolve, a stale version offers Reload -- and a new error type shows up as
    an unknown code rather than as silence.
    """

    model_config = ConfigDict(extra="allow")

    code: str
    message: str
    retryable: bool = False
    #: Present whenever the store knew it. `None` on a 404, where there is no
    #: review to have a state.
    state: str | None = None
    #: Which version moved, on a compare-and-set refusal.
    field: str | None = None
    expected: int | None = None
    actual: int | None = None


class ReviewRefusal(BaseModel):
    """The envelope FastAPI wraps a raised `HTTPException` in."""

    model_config = ConfigDict(extra="forbid")

    detail: ReviewRefusalDetail


#: Attached to every mutation on this surface. Both statuses, because they are
#: different answers to different questions and the console renders them
#: differently: a 409 offers the action that would resolve it, a 404 says the
#: review is gone and reloads the panel.
_REFUSALS: dict[int | str, dict[str, Any]] = {
    status.HTTP_409_CONFLICT: {
        "model": ReviewRefusal,
        "description": "The store's truth moved under this request.",
    },
    status.HTTP_404_NOT_FOUND: {
        "model": ReviewRefusal,
        "description": "No such case, or no such review on it.",
    },
}

#: The recovery retry's extra answer (AMENDMENT-5, rule 1). Declared for the
#: reason every refusal on this surface is: FastAPI documents what a handler
#: returns and nothing about what it raises, and a client that must tell
#: "final" from "ask again shortly" is written against this shape.
_RETRY_REFUSALS: dict[int | str, dict[str, Any]] = {
    **_REFUSALS,
    status.HTTP_503_SERVICE_UNAVAILABLE: {
        "model": ReviewRefusal,
        "description": (
            "This process cannot reach the workflow host, so it cannot tell whether a "
            "redelivery would be applied. Nothing was changed; retryable."
        ),
    },
}


class ApproveReviewRequest(_Body):
    """**The exact field names contracts.md sect. 6 fixes.**

    All three are required and none is optional-with-a-default. A default would
    make "approve whatever is there now" expressible, and the whole purpose of
    the CAS is that the associate approves the bytes they read -- a draft that
    was re-rendered under them must fail, not silently go out.
    """

    draft_version: int = Field(ge=0)
    canonical_edit_version: int = Field(ge=0)
    canonical_approved_payload_hash: str = Field(min_length=8, max_length=128)


class ReviseReviewRequest(_Body):
    #: A person's free text. Neutralised on the way onto the fact log -- it
    #: enters no outbound message today, and the cheapest moment to make that
    #: safe is before anybody decides it should.
    note: str | None = Field(default=None, max_length=4_000)


class CancelReviewRequest(_Body):
    reason: str = Field(min_length=1, max_length=2_000)


class RecoveryRequest(_Body):
    reason: str = Field(default="", max_length=2_000)


class EditStateRequest(_Body):
    """One coalesced autosave.

    `client_edit_id` is what makes a retried save a no-op rather than a version
    bump -- the browser sends the same id for the same keystroke batch, so a
    flaky connection costs nothing.

    `base_draft_version` is the version the edit was made *against*. A mismatch
    is a 409 rather than a merge: the draft was re-rendered under the editor and
    silently rebasing their words onto different facts is how a message comes to
    say something nobody wrote.
    """

    client_edit_id: str = Field(min_length=1, max_length=128)
    base_draft_version: int = Field(ge=0)
    #: **Bounded**, because an autosave is the one write on this surface a
    #: client controls the size of and it fires every 800 ms. Unbounded, a
    #: single tab could grow one review's edit row without limit under a
    #: capability meant for editing a message -- which is the shape of RV's
    #: phase-1 advisory A4 about the preview endpoint, on a write path.
    #: 256 KB is roughly two orders of magnitude above the largest draft the
    #: shipped template can render, so it bounds abuse without bounding use.
    payload: dict[str, Any] = Field(max_length=_MAX_PAYLOAD_KEYS)


class ResolveEditRequest(_Body):
    """Select, merge or discard, resolved to one canonical payload.

    The *choice* is the UI's; what arrives here is its result, plus the edit row
    ids it was resolved from, so the audit says which drafts the canonical one
    came out of rather than only what it says.
    """

    canonical_payload: dict[str, Any] = Field(max_length=_MAX_PAYLOAD_KEYS)
    resolved_from_actor_edit_ids: list[str] = Field(default_factory=list, max_length=50)


class ReviewActionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_id: str
    state: str
    draft_version: int
    canonical_edit_version: int
    #: Present on the transitions that create a command. The panel shows it in
    #: `accepted_commands[]`, which is what answers "I pressed Send and nothing
    #: happened".
    signal_id: str | None = None
    duplicate: bool = False


class EditStateResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_id: str
    actor_id: str
    #: `None` when this actor holds no edit row. Not an error and not an empty
    #: payload: "you have not edited this" and "you edited it to nothing" are
    #: different answers and the restore path depends on telling them apart.
    edit_version: int | None = None
    base_draft_version: int | None = None
    client_edit_id: str | None = None
    payload: dict[str, Any] | None = None


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #


@router.get(
    "/{case_id}/reviews/{review_id}/edit-state",
    responses=_REFUSALS,
    response_model=APIResponse[EditStateResult],
)
async def read_edit_state(
    case_id: str,
    review_id: str,
    request: Request,
    response: Response,
    _actor: str = Depends(require_capability(RETURNS_SESSION_READ)),
) -> APIResponse[EditStateResult]:
    """**This actor's** private edit row (contracts.md sect. 9).

    `private, no-store` and nothing else. An autosaved draft is one person's
    unfinished thinking about a message to a customer's supplier; it is not in
    the shared panel body, it is not in the panel hash, and it is not written to
    disk by a cache. `no-cache` would still permit storage; `no-store` is the
    one that does not.
    """
    await require_case_access(request, case_id)
    actor_id = _actor_of(request)
    dependencies = panel_dependencies(request)
    row = await dependencies.reviews.get_edit_state(
        case_id=case_id, review_id=review_id, actor_id=actor_id
    )
    response.headers["Cache-Control"] = "private, no-store"
    if row is None:
        return APIResponse(
            data=EditStateResult(review_id=review_id, actor_id=actor_id),
            meta=_meta(request),
        )
    return APIResponse(
        data=EditStateResult(
            review_id=review_id,
            actor_id=actor_id,
            edit_version=int(row.get("editVersion", 0)),
            base_draft_version=int(row.get("baseDraftVersion", 0)),
            client_edit_id=str(row.get("clientEditId", "")),
            payload=cast(dict[str, Any], row.get("payload") or {}),
        ),
        meta=_meta(request),
    )


# --------------------------------------------------------------------------- #
# Editing
# --------------------------------------------------------------------------- #


@router.put(
    "/{case_id}/reviews/{review_id}/edit-state",
    responses=_REFUSALS,
    response_model=APIResponse[EditStateResult],
)
async def write_edit_state(
    case_id: str,
    review_id: str,
    request: Request,
    response: Response,
    body: Annotated[EditStateRequest, Body()],
    _actor: str = Depends(require_capability(RETURNS_SUPPORT_ACT)),
) -> APIResponse[EditStateResult]:
    """Autosave. **Field values are neutralised here** (carry-forward condition 7).

    A field edit replaces one value inside an agent-authored frame, which is
    structurally the `associate_notes` finding: the reader cannot tell the frame
    from the value. So the payload is put through composition's own `_safe`
    before it is stored -- at write time rather than at send time, so what the
    associate sees on re-render is what Support will receive, and the diff
    against the agent's draft is honest.

    A whole-body override is *not* neutralised, and that distinction is
    explained where it is implemented, in `operations/support_template_gate.py`.
    """
    await require_case_access(request, case_id)
    actor_id = _actor_of(request)
    dependencies = panel_dependencies(request)
    try:
        row = await dependencies.reviews.upsert_draft_edit(
            case_id=case_id,
            review_id=review_id,
            actor_id=actor_id,
            client_edit_id=body.client_edit_id,
            base_draft_version=body.base_draft_version,
            payload=neutralise_field_edits(body.payload),
        )
    except ReviewNotFoundError as error:
        raise _missing(error) from None
    except _CONFLICTS as error:
        raise _conflict(error) from None
    response.headers["Cache-Control"] = "private, no-store"
    return APIResponse(
        data=EditStateResult(
            review_id=review_id,
            actor_id=actor_id,
            edit_version=int(row.get("editVersion", 0)),
            base_draft_version=int(row.get("baseDraftVersion", 0)),
            client_edit_id=str(row.get("clientEditId", "")),
            payload=cast(dict[str, Any], row.get("payload") or {}),
        ),
        meta=_meta(request),
    )


@router.post(
    "/{case_id}/reviews/{review_id}/edit-state/resolve",
    responses=_REFUSALS,
    response_model=APIResponse[ReviewActionResult],
)
async def resolve_edit_state(
    case_id: str,
    review_id: str,
    request: Request,
    body: Annotated[ResolveEditRequest, Body()],
    _actor: str = Depends(require_capability(RETURNS_SUPPORT_ACT)),
) -> APIResponse[ReviewActionResult]:
    """Resolve several actors' edits into the canonical one.

    The conflict marker clears in the same transaction as the write -- that is
    S2's, and it is why this endpoint does not clear anything itself. An empty
    `resolved_from_actor_edit_ids` is legal and means *discard*: the canonical
    edit came from nobody's row, which is a real resolution and a different one
    from selecting a row that happens to match.
    """
    await require_case_access(request, case_id)
    dependencies = panel_dependencies(request)
    try:
        review = await dependencies.reviews.resolve_canonical_edit(
            case_id=case_id,
            review_id=review_id,
            resolved_by=_actor_of(request),
            canonical_payload=neutralise_field_edits(body.canonical_payload),
            resolved_from_actor_edit_ids=tuple(body.resolved_from_actor_edit_ids),
        )
    except ReviewNotFoundError as error:
        raise _missing(error) from None
    except _CONFLICTS as error:
        raise _conflict(error) from None
    return APIResponse(data=_result(review), meta=_meta(request))


# --------------------------------------------------------------------------- #
# The decisions
# --------------------------------------------------------------------------- #


@router.post(
    "/{case_id}/reviews/{review_id}/approve",
    responses=_REFUSALS,
    response_model=APIResponse[ReviewActionResult],
)
async def approve_review(
    case_id: str,
    review_id: str,
    request: Request,
    body: Annotated[ApproveReviewRequest, Body()],
    _actor: str = Depends(require_capability(RETURNS_SUPPORT_ACT)),
) -> APIResponse[ReviewActionResult]:
    """`OPEN -> APPROVING`, atomically, with the command and its outbox row.

    Through `SupportTemplateGateService.approve` rather than the store, which
    is carry-forward conditions 5a and 8: the editing actor set is recomputed
    from the edit rows first, so a torn conflict flag cannot let this endpoint
    freeze the agent's draft while two associates' edits are silently
    discarded. That is one read on a path that already does several, and it
    cannot be retrofitted once this endpoint has shipped.

    The three CAS values are the request's, not the store's, so approving a
    draft that moved under the associate is a 409 rather than a send.
    """
    await require_case_access(request, case_id)
    dependencies = panel_dependencies(request)
    signal_id = str(uuid.uuid4())
    try:
        review, receipt = await dependencies.gate.approve(
            case_id=case_id,
            review_id=review_id,
            actor_id=_actor_of(request),
            expected_draft_version=body.draft_version,
            expected_canonical_edit_version=body.canonical_edit_version,
            canonical_approved_payload_hash=body.canonical_approved_payload_hash,
            workflow_id=return_case_workflow_id(case_id),
            signal_id=signal_id,
            correlation_id=str(getattr(request.state, "correlation_id", "")) or None,
        )
    except ReviewNotFoundError as error:
        raise _missing(error) from None
    except _CONFLICTS as error:
        raise _conflict(error) from None
    logger.info(
        "review_approved",
        extra={"case_id": case_id, "review_id": review_id, "signal_id": signal_id},
    )
    return APIResponse(data=_result(review, receipt), meta=_meta(request))


@router.post(
    "/{case_id}/reviews/{review_id}/revise",
    responses=_REFUSALS,
    response_model=APIResponse[ReviewActionResult],
)
async def revise_review(
    case_id: str,
    review_id: str,
    request: Request,
    body: Annotated[ReviseReviewRequest, Body()],
    _actor: str = Depends(require_capability(RETURNS_SUPPORT_ACT)),
) -> APIResponse[ReviewActionResult]:
    """Ask for the draft again. Approval refuses until the re-render lands.

    The re-render itself is the workflow's -- `rerender_template_draft` reads
    the case's facts, and an endpoint that rendered would be a second renderer
    with a second idea of what the case says. So the flag is only half of this
    endpoint: without the command that follows it, `pendingRevision` would block
    approval forever and nothing would ever produce the draft it is waiting for.

    `note` travels on the command and is neutralised onto the fact log by
    `record_template_revision`, which is where the activity already expected to
    find it.

    The signal id is `(review_id, draft_version)` rather than a fresh uuid: one
    revision per version of a draft is exactly the rule -- you cannot ask twice
    for the same bytes to be re-rendered -- and it makes a retried request a
    recognised duplicate instead of a second re-render.
    """
    actor_id = _actor_of(request)
    await require_case_access(request, case_id)
    dependencies = panel_dependencies(request)
    try:
        review = await dependencies.reviews.request_revision(
            case_id=case_id, review_id=review_id, actor_id=actor_id
        )
        revised, _cancelled = _decision_kinds(review)
        receipt = await _tell_the_execution(
            dependencies,
            case_id=case_id,
            review=review,
            kind=revised,
            actor_id=actor_id,
            signal_id=f"{revised.value}:{review_id}:{int(review.get('draftVersion', 0))}",
            correlation_id=str(getattr(request.state, "correlation_id", "")) or None,
            note=body.note,
        )
    except ReviewNotFoundError as error:
        raise _missing(error) from None
    except _CONFLICTS as error:
        raise _conflict(error) from None
    return APIResponse(data=_result(review, receipt), meta=_meta(request))


@router.post(
    "/{case_id}/reviews/{review_id}/cancel",
    responses=_REFUSALS,
    response_model=APIResponse[ReviewActionResult],
)
async def cancel_review(
    case_id: str,
    review_id: str,
    request: Request,
    body: Annotated[CancelReviewRequest, Body()],
    _actor: str = Depends(require_capability(RETURNS_SUPPORT_ACT)),
) -> APIResponse[ReviewActionResult]:
    """`OPEN -> CANCELLED`. Terminal, and the case parks on it.

    "The case parks on it" is the command's doing, not the store's: without the
    notice the execution keeps waiting on a review nobody is ever going to
    answer and parks `TEMPLATE_REVIEW_UNANSWERED` at the deadline instead of
    `TEMPLATE_REVIEW_CANCELLED` immediately -- a different reason, on a
    different day, for a decision a person made now.

    Re-entrant: cancelling an already-cancelled review records no second state
    move and re-records the same deterministic command, so a client that lost
    the first answer gets the same one rather than a 409 over its own success.
    """
    actor_id = _actor_of(request)
    await require_case_access(request, case_id)
    dependencies = panel_dependencies(request)
    try:
        review = await dependencies.reviews.get_review(case_id=case_id, review_id=review_id)
        if str(review["state"]) != ReviewState.CANCELLED.value:
            review = await dependencies.reviews.cancel(
                case_id=case_id,
                review_id=review_id,
                actor_id=actor_id,
                reason=body.reason,
            )
        _revised, cancelled = _decision_kinds(review)
        receipt = await _tell_the_execution(
            dependencies,
            case_id=case_id,
            review=review,
            kind=cancelled,
            actor_id=actor_id,
            signal_id=f"{cancelled.value}:{review_id}",
            correlation_id=str(getattr(request.state, "correlation_id", "")) or None,
        )
    except ReviewNotFoundError as error:
        raise _missing(error) from None
    except _CONFLICTS as error:
        raise _conflict(error) from None
    return APIResponse(data=_result(review, receipt), meta=_meta(request))


@router.post(
    "/{case_id}/reviews/{review_id}/template-review/redraft",
    responses=_REFUSALS,
    response_model=APIResponse[ReviewActionResult],
)
async def redraft_review(
    case_id: str,
    review_id: str,
    request: Request,
    _actor: str = Depends(require_capability(RETURNS_SUPPORT_ACT)),
) -> APIResponse[ReviewActionResult]:
    """Mint a new attempt under the same `(case_id, request_id)` scope.

    The new attempt carries the **current** draft payload, not a fresh render:
    rendering is the workflow's, and an endpoint that rendered would be reading
    the case behind the execution that owns it. The workflow's re-render lands
    through `rerender_template_draft` when the revision signal arrives.

    **`supersedes` is what makes a redraft reach the execution at all.** The new
    attempt has a new `review_id`, and the workflow's wait map holds the old
    one; a plain revision notice naming the new id would be discarded as "not
    this case's attempt", and the request would sit unanswerable behind a review
    that had already been cancelled. Naming the attempt being replaced lets the
    workflow re-point its map -- and keeps that a privilege of a notice that
    matches what the workflow is actually holding, rather than of any notice
    that disagrees with it.
    """
    actor_id = _actor_of(request)
    await require_case_access(request, case_id)
    dependencies = panel_dependencies(request)
    try:
        current = await dependencies.reviews.get_review(case_id=case_id, review_id=review_id)
        review = await dependencies.reviews.redraft(
            case_id=case_id,
            review_id=review_id,
            actor_id=actor_id,
            draft_payload=cast(dict[str, Any], current.get("draftPayload") or {}),
        )
        revised, _cancelled = _decision_kinds(review)
        new_id = str(review["_id"])
        receipt = await _tell_the_execution(
            dependencies,
            case_id=case_id,
            review=review,
            kind=revised,
            actor_id=actor_id,
            signal_id=f"{revised.value}:{new_id}:{int(review.get('draftVersion', 0))}",
            correlation_id=str(getattr(request.state, "correlation_id", "")) or None,
            supersedes=review_id,
        )
    except ReviewNotFoundError as error:
        raise _missing(error) from None
    except _CONFLICTS as error:
        raise _conflict(error) from None
    return APIResponse(data=_result(review, receipt), meta=_meta(request))


# --------------------------------------------------------------------------- #
# Recovery
# --------------------------------------------------------------------------- #


@router.post(
    "/{case_id}/reviews/{review_id}/recovery/retry",
    responses=_RETRY_REFUSALS,
    response_model=APIResponse[ReviewActionResult],
)
async def retry_review_delivery(
    case_id: str,
    review_id: str,
    request: Request,
    body: Annotated[RecoveryRequest, Body()],
    _actor: str = Depends(require_capability(RETURNS_REVIEW_RECOVERY)),
) -> APIResponse[ReviewActionResult]:
    """`DELIVERY_FAILED -> APPROVING`, with the **same** delivery identity.

    A redelivery of one message, never a second message that happens to say the
    same thing: the stored `logical_operation_id`, `delivery_id` and frozen
    payload ride the command, and a retry the receiver already holds comes back
    absorbed -- **which still reaches `SENT`** (contracts.md sect. 7).

    Its own capability, not `RETURNS_SUPPORT_ACT`: re-driving a send the
    platform already committed to is not the act of the person who wrote the
    message. See `security/capabilities.py`.
    """
    del body
    actor_id = _actor_of(request)
    await require_case_access(request, case_id)

    # **AMENDMENT-5, rule 1 -- and this check must come before the CAS, not
    # after it.** `retry_delivery` moves `DELIVERY_FAILED -> APPROVING` and
    # records the command in one transaction, and it always succeeded. If the
    # gate has already closed, the workflow discards the notice, the redelivery
    # never happens, and the review sits in `APPROVING` -- whose only three
    # exits are workflow-driven -- while `abandon` is refused from there. The
    # operator's own recovery action built the trap.
    holds = await execution_holds_review(request, case_id, review_id)
    if holds is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "EXECUTION_LIVENESS_UNKNOWN",
                "message": (
                    "This process cannot reach the workflow host, so it cannot tell whether "
                    "a redelivery would be applied. Nothing was changed; try again shortly."
                ),
                "retryable": True,
            },
        )
    if not holds:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "ExecutionNoLongerHoldingReview",
                # Names the legal action, which is the whole point of refusing
                # here rather than succeeding into a discard: rule 2 has already
                # moved this review to `HELD_FOR_OPERATIONS`, from which
                # reopening and abandoning are both legal.
                "message": (
                    "This return is no longer waiting on that message, so it cannot be "
                    "re-sent. Reopen the review to send it again, or stop trying and record "
                    "why."
                ),
                "retryable": False,
                "state": ReviewState.HELD_FOR_OPERATIONS.value,
            },
        )

    dependencies = panel_dependencies(request)
    # Deterministic, like every other decision on this surface (RV advisory A1).
    # A random id made a client that lost the response record a *second* command
    # for one decision, which is the dedupe the command store already provides
    # being thrown away at the one endpoint an operator retries by hand.
    signal_id = f"{CaseCommandKind.REVIEW_DELIVERY_RETRY.value}:{review_id}"
    try:
        review, receipt = await dependencies.reviews.retry_delivery(
            case_id=case_id,
            review_id=review_id,
            actor_id=actor_id,
            workflow_id=return_case_workflow_id(case_id),
            signal_id=signal_id,
            correlation_id=str(getattr(request.state, "correlation_id", "")) or None,
        )
    except ReviewNotFoundError as error:
        raise _missing(error) from None
    except _CONFLICTS as error:
        raise _conflict(error) from None
    return APIResponse(data=_result(review, receipt), meta=_meta(request))


@router.post(
    "/{case_id}/reviews/{review_id}/recovery/abandon",
    responses=_REFUSALS,
    response_model=APIResponse[ReviewActionResult],
)
async def abandon_review(
    case_id: str,
    review_id: str,
    request: Request,
    body: Annotated[CancelReviewRequest, Body()],
    _actor: str = Depends(require_capability(RETURNS_REVIEW_RECOVERY)),
) -> APIResponse[ReviewActionResult]:
    """Terminal, audited, and still on the panel (contracts.md sect. 6).

    The reason is required -- there is no default and no empty string -- because
    "this message is never going out" is a decision somebody has to own, and an
    unattributed one is the unaudited close in another shape.
    """
    await require_case_access(request, case_id)
    dependencies = panel_dependencies(request)
    try:
        review = await dependencies.reviews.abandon(
            case_id=case_id,
            review_id=review_id,
            actor_id=_actor_of(request),
            reason=body.reason,
        )
    except ReviewNotFoundError as error:
        raise _missing(error) from None
    except _CONFLICTS as error:
        raise _conflict(error) from None
    return APIResponse(data=_result(review), meta=_meta(request))


def _result(review: dict[str, Any], receipt: Any = None) -> ReviewActionResult:
    return ReviewActionResult(
        review_id=str(review["_id"]),
        state=str(review["state"]),
        draft_version=int(review.get("draftVersion", 0)),
        canonical_edit_version=int(review.get("canonicalEditVersion", 0)),
        signal_id=None if receipt is None else str(getattr(receipt, "signal_id", "")) or None,
        duplicate=bool(getattr(receipt, "duplicate", False)) if receipt is not None else False,
    )
