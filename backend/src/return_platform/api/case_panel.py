"""`GET /api/v1/cases/{case_id}/panel` -- compose, serialize, hash (DR-10).

The mechanism the user chose, and its two halves are separable: **composition
always runs**, and the `ETag` decides only whether the bytes travel. A 304
saves bandwidth, never work, and pretending otherwise would mean serving a
cached panel over a review whose state had moved -- which is the one thing an
associate is watching this screen for.

Headers, and each is load-bearing:

* `Cache-Control: private, no-cache` -- cacheable by the browser, never by a
  shared proxy, and revalidated every time. `no-store` would defeat the ETag
  entirely; a plain `max-age` would let a stale panel render over a sent review.
* `Vary: Authorization` -- the body is principal-*independent*, but the
  **404** is not: a principal who may not read this case gets one, and a cache
  keyed without the credential could serve them somebody else's panel.

Volume (contracts.md sect. 2, investigation 5): ≤ 200 concurrent open cases at
`copilot.case_poll_interval_ms = 10000` is ≤ 20 compositions a second
platform-wide, each with one Temporal query. The shipped poll interval is gated
on ACC's load test at that volume; nothing here assumes a cache hit.
"""

from __future__ import annotations

import logging
from typing import Any, cast

from fastapi import APIRouter, Depends, Request, Response, status

# Module level, and safe: `case_reviews` imports this module's *operations*
# sibling, never this one. The lazy imports further down are for the request-time
# helpers, which is a different problem (they reach app state).
from return_platform.api.case_reviews import ReviewRefusal
from return_platform.operations.case_panel import (
    AcceptedCommandView,
    CasePanelView,
    PanelExecutionView,
    PanelSectionView,
    PanelTimersView,
    ReviewPanelView,
    panel_etag,
    panel_section_contributors,
    sorted_sections,
)
from return_platform.operations.repository import resolve_operational_repository
from return_platform.operations.review_aggregate import (
    TERMINAL_REVIEW_STATES,
    ReviewState,
)
from return_platform.operations.support_events import canonical_payload_digest
from return_platform.operations.support_template_gate import PAYLOAD_GAPS
from return_platform.security.authorization import require_capability
from return_platform.security.capabilities import RETURNS_SESSION_READ
from return_platform.shared.contracts import APIResponse, ResponseMeta

logger = logging.getLogger("return_platform.api.case_panel")

router = APIRouter(prefix="/api/v1/cases", tags=["Case panel"])

#: How long a terminal review stays on the panel. Contracts.md sect. 9 asks for
#: "recently-terminal for visibility" and does not fix a number, so this is a
#: **count**, not a duration: an associate who has just sent three requests
#: needs to see three confirmations, and a duration would make what they see
#: depend on how fast they work.
_RECENTLY_TERMINAL: int = 5


def _meta(request: Request) -> ResponseMeta:
    request_id = getattr(request.state, "correlation_id", "unknown")
    return ResponseMeta(request_id=request_id if isinstance(request_id, str) else "unknown")


async def compose_case_panel(request: Request, case_id: str) -> CasePanelView:
    """Everything the panel shows, composed once.

    Order of assembly is the order of the DTO, and every part that can fail
    transiently degrades on its own rather than taking the panel with it. The
    reviews are the exception and are deliberately *not* degradable: they are
    what an associate is blocked on, and a panel that rendered "degraded" over
    them would be a screen that looks alive and tells you nothing.
    """
    from return_platform.api.case_reviews import panel_dependencies

    dependencies = panel_dependencies(request)
    reviews = await dependencies.reviews.list_reviews(case_id)
    marker = await dependencies.reviews.conflict_marker(case_id)
    flagged = set(cast(list[str], marker.get("reviewIds") or []))

    execution, timers = await _execution(request, case_id)
    commands = await _accepted_commands(dependencies, case_id)
    sections = await _sections(request, case_id)
    records = await _return_records(request, case_id)

    return CasePanelView(
        case_id=case_id,
        execution=execution,
        reviews=tuple(_review_view(review, flagged) for review in _visible(reviews)),
        return_records=records,
        # `support_digest`, `clarifications` and `parked_messages` are declared
        # here and left empty **by ownership, not by omission**. The thread
        # digest and the parked count are the ingress surface's (V2) and the
        # clarifications are the resolver's (V3); each arrives through
        # `register_panel_section` in its own slice's file, which is what the
        # section seam is for. The fields stay on the frozen DTO so the shapes
        # are settled before those slices need them.
        support_digest=(),
        clarifications=(),
        timers=timers,
        parked_messages=0,
        accepted_commands=commands,
        sections=sections,
    )


async def _return_records(request: Request, case_id: str) -> tuple[dict[str, Any], ...]:
    """The case's RMAs, in creation order (contracts.md sect. 9).

    Case-scoped and principal-independent like everything else in the shared
    body, and deliberately a **narrow projection** rather than the stored
    documents: a record carries `updatedAt`, which moves on writes the panel
    does not care about, and putting it in the hash would invalidate every
    cached panel on the estate for a reason nobody could see. Identity, the
    reference Support issued, and the two attributes the review sections are
    grouped by -- nothing that ticks.

    Not degradable. It is the same Mongo the reviews are read from, so a
    failure here is a failure that has already taken the reviews with it.
    """
    repository = resolve_operational_repository(request)
    records = await repository.list_return_records(case_id)
    return tuple(
        {
            "return_record_id": str(record.get("returnRecordId", "")),
            "return_reference": _text(record.get("returnReference")),
            "status": _text(record.get("status")),
            "return_method": _text(record.get("returnMethod")),
        }
        for record in records
    )


def _visible(reviews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every non-terminal review, plus the last few terminal ones.

    Sorted by `createdAt` already (the store's read does it), so "the last few"
    is a tail rather than a re-sort -- which keeps the order stable and
    therefore keeps the ETag stable across two composes of an unchanged case.
    """
    live = [
        review
        for review in reviews
        if ReviewState(str(review["state"])) not in TERMINAL_REVIEW_STATES
    ]
    terminal = [
        review for review in reviews if ReviewState(str(review["state"])) in TERMINAL_REVIEW_STATES
    ]
    return live + terminal[-_RECENTLY_TERMINAL:]


def _review_view(review: dict[str, Any], flagged: set[str]) -> ReviewPanelView:
    payload = cast(dict[str, Any], review.get("draftPayload") or {})
    state = str(review["state"])
    return ReviewPanelView(
        review_id=str(review["_id"]),
        review_kind=str(review["reviewKind"]),
        scope_id=str(review["scopeId"]),
        request_id=str(review["requestId"]),
        state=state,
        draft_version=int(review.get("draftVersion", 0)),
        canonical_edit_version=int(review.get("canonicalEditVersion", 0)),
        # The **marker's** answer, not the review's own flag. They agree in
        # every state the store can reach; reading the case-scoped marker is
        # what makes the panel and the approval endpoint disagree about a
        # conflict impossible rather than merely unlikely.
        conflict_present=str(review["_id"]) in flagged,
        draft=payload,
        gaps=tuple(cast(list[dict[str, Any]], payload.get(PAYLOAD_GAPS) or ())),
        approved_by=_text(review.get("approvedBy")),
        approved_at_iso=_instant(review.get("approvedAt")),
        recovery_status=state if state in _RECOVERABLE else None,
        last_delivery_error_code=_text(review.get("lastDeliveryErrorCode")),
        hold_reason=_text(review.get("holdReason")),
        abandon_audit=_audit(review.get("abandonAudit")),
    )


_RECOVERABLE: frozenset[str] = frozenset(
    {ReviewState.DELIVERY_FAILED.value, ReviewState.HELD_FOR_OPERATIONS.value}
)


async def _execution(request: Request, case_id: str) -> tuple[PanelExecutionView, PanelTimersView]:
    """The workflow's own state and its timers, or a degraded answer.

    The two travel together because they come from one query, and the timers
    are **empty** whenever the execution could not be read: a deadline the
    panel invented would be a countdown to a moment nothing is going to happen
    at, which is worse than no countdown.

    Expected-transient only. A Temporal outage is exactly the case this exists
    for and the rest of the panel -- read from Mongo -- is still true. Anything
    that is not a connection problem propagates, because a panel that renders
    "degraded" over a contract violation hides it.
    """
    from return_platform.api.case_reviews import case_execution_state

    try:
        answered = await case_execution_state(request, case_id)
    except TimeoutError:
        return (
            PanelExecutionView(status="degraded", reason="EXECUTION_QUERY_TIMEOUT"),
            PanelTimersView(),
        )
    except ConnectionError:
        return (
            PanelExecutionView(status="degraded", reason="EXECUTION_HOST_UNREACHABLE"),
            PanelTimersView(),
        )
    if answered is None:
        return (
            PanelExecutionView(status="degraded", reason="EXECUTION_NOT_AVAILABLE"),
            PanelTimersView(),
        )
    return answered


async def _accepted_commands(dependencies: Any, case_id: str) -> tuple[AcceptedCommandView, ...]:
    """Commands the platform accepted, unfiltered by actor.

    This is what answers "I pressed Send and nothing has happened": the command
    is durable, the signal has not landed, and the panel says so rather than
    showing an unchanged review and letting the associate press it again.
    """
    records = await dependencies.commands.list_commands(case_id)
    return tuple(
        AcceptedCommandView(
            signal_id=str(record.get("signalId", "")),
            kind=str(record.get("kind", "")),
            actor_id=str(record.get("actorId", "")),
            review_id=_text(record.get("reviewId")),
            recorded_at_iso=_instant(record.get("createdAt")),
            applied=bool(record.get("applied", False)),
        )
        for record in records
    )


async def _sections(request: Request, case_id: str) -> tuple[PanelSectionView, ...]:
    """V2's and V3's sections, each isolated from the others and from the panel.

    A contributor that raises becomes a degraded section rather than a 500. It
    is somebody else's slice, it is additive by construction, and the reviews
    are the part an associate is blocked on -- so one contributor's bad day
    must not be able to blank the screen.
    """
    principal = getattr(request.state, "principal", None)
    context = {
        "case_id": case_id,
        "tenant_id": str(getattr(request.state, "tenant_id", "default")),
        "principal_id": str(getattr(principal, "subject", "")),
        "request": request,
    }
    produced: list[PanelSectionView] = []
    for section_id, contributor in panel_section_contributors():
        try:
            section = await contributor(context)
        except Exception:  # noqa: BLE001 - see the docstring
            logger.warning(
                "panel_section_failed",
                extra={"case_id": case_id, "section_id": section_id},
                exc_info=True,
            )
            produced.append(
                PanelSectionView(
                    section_id=section_id, status="degraded", reason="SECTION_UNAVAILABLE"
                )
            )
            continue
        if section is not None:
            produced.append(section)
    return sorted_sections(produced)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _instant(value: Any) -> str | None:
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if callable(isoformat) else str(value)


def _audit(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        "actor_id": _text(value.get("actorId")),
        "reason": _text(value.get("reason")),
        "at_iso": _instant(value.get("at")),
    }


@router.get(
    "/{case_id}/panel",
    # **Declared, because a client has to be written against them.** FastAPI
    # documents what a handler returns and knows nothing about what it raises,
    # so without this the conditional read -- the half of DR-10's mechanism a
    # poll spends most of its life in -- appears nowhere in the contract, and
    # the console's ETag branch would be written against an undocumented
    # response. The 304 declares no model because it has no body; that is the
    # point of it.
    responses={
        status.HTTP_304_NOT_MODIFIED: {
            "description": (
                "The panel is unchanged since the supplied `If-None-Match`. No body; "
                "composition ran anyway (DR-10 trades bandwidth, not work)."
            ),
        },
        status.HTTP_404_NOT_FOUND: {
            "model": ReviewRefusal,
            "description": "No such case, or not the caller's. Absent, never forbidden.",
        },
    },
    response_model=APIResponse[CasePanelView],
)
async def read_case_panel(
    case_id: str,
    request: Request,
    response: Response,
    _actor: str = Depends(require_capability(RETURNS_SESSION_READ)),
) -> Any:
    """The panel, with its `ETag`.

    A matching `If-None-Match` answers 304 with the same headers and no body.
    Composition ran anyway -- see the module docstring -- and that is the whole
    trade DR-10 makes: bandwidth, not work.
    """
    from return_platform.api.case_reviews import require_case_access

    await require_case_access(request, case_id)
    view = await compose_case_panel(request, case_id)
    etag = panel_etag(view, digest=canonical_payload_digest)

    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "private, no-cache"
    response.headers["Vary"] = "Authorization"

    if request.headers.get("if-none-match") == etag:
        return Response(
            status_code=status.HTTP_304_NOT_MODIFIED,
            headers={
                "ETag": etag,
                "Cache-Control": "private, no-cache",
                "Vary": "Authorization",
            },
        )
    return APIResponse(data=view, meta=_meta(request))
