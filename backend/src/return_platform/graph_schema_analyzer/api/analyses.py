"""`/api/graph-schema/analyses` — the analysis-session surface.

Versionless by design (design doc section 9.3): the analyzer's API is reached
through the module, and its contract evolves with the module rather than through
a `/v2/`-style prefix. That is a deliberate departure from `/api/v2/order-agent`,
which predates the module architecture.

This slice serves session lifecycle, snapshot inspection, and clarifications.
Discovery, AI proposal, mutations, validation, and approval arrive in C3.2/C3.3;
their routes are absent rather than stubbed, so the OpenAPI schema never advertises
an endpoint that does nothing.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from return_platform.graph_schema_analyzer.api.schemas import (
    AnalysisSessionView,
    AnswerClarificationRequest,
    ClarificationView,
    CreateAnalysisRequest,
    SnapshotView,
)
from return_platform.graph_schema_analyzer.domain.analysis_session import (
    AnalysisSession,
    SessionStatus,
)
from return_platform.graph_schema_analyzer.domain.errors import (
    ConcurrentModification,
    InvalidSessionTransition,
    UnknownAnalysis,
)
from return_platform.graph_schema_analyzer.ports.system_store_port import PersistencePort
from return_platform.security.principal import Principal

router = APIRouter(prefix="/api/graph-schema", tags=["Graph Schema Analyzer"])


def resolve_persistence(request: Request) -> PersistencePort:
    """The analyzer's persistence, attached to app.state during startup.

    Typed as the port, not the concrete `SystemStorePersistence`: the API layer
    must not reach past the contract into repository internals, or swapping the
    backing store later becomes an API-layer change.
    """
    persistence = getattr(request.app.state, "graph_schema_analyzer_persistence", None)
    if not isinstance(persistence, PersistencePort):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "GRAPH_SCHEMA_ANALYZER_UNAVAILABLE",
                "message": "The Graph Schema Analyzer is not initialized.",
            },
        )
    return persistence


def _actor(request: Request) -> str:
    """Who to attribute a write to. Falls back to a literal rather than raising:
    authentication is enforced by middleware, so a missing principal here means a
    deployment served this route unauthenticated, and recording that fact in
    `created_by` is more useful than a 500 that loses the audit trail."""
    principal: Principal | None = getattr(request.state, "principal", None)
    return principal.subject if principal is not None else "anonymous"


_Persistence = Annotated[PersistencePort, Depends(resolve_persistence)]


@router.post(
    "/analyses",
    response_model=AnalysisSessionView,
    status_code=status.HTTP_201_CREATED,
)
async def create_analysis(
    payload: CreateAnalysisRequest, request: Request, persistence: _Persistence
) -> AnalysisSessionView:
    now = datetime.now(UTC)
    session = AnalysisSession(
        analysis_id=str(uuid4()),
        status=SessionStatus.DRAFT,
        source_refs=payload.source_refs,
        created_by=_actor(request),
        created_at=now,
        updated_at=now,
    )
    await persistence.create_session(session)
    return AnalysisSessionView.of(session)


@router.get("/analyses", response_model=list[AnalysisSessionView])
async def list_analyses(
    persistence: _Persistence,
    session_status: Annotated[SessionStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Sequence[AnalysisSessionView]:
    sessions = await persistence.list_sessions(status=session_status, limit=limit)
    return [AnalysisSessionView.of(session) for session in sessions]


@router.get("/analyses/{analysis_id}", response_model=AnalysisSessionView)
async def get_analysis(analysis_id: str, persistence: _Persistence) -> AnalysisSessionView:
    try:
        session = await persistence.load_session(analysis_id)
    except UnknownAnalysis as exc:
        raise _not_found(analysis_id) from exc
    return AnalysisSessionView.of(session)


@router.post("/analyses/{analysis_id}/abandon", response_model=AnalysisSessionView)
async def abandon_analysis(analysis_id: str, persistence: _Persistence) -> AnalysisSessionView:
    """Abandonment is the one transition available before discovery exists, and it
    is what keeps a DRAFT from living forever if the analyst walks away."""
    try:
        session = await persistence.load_session(analysis_id)
    except UnknownAnalysis as exc:
        raise _not_found(analysis_id) from exc
    expected_version = session.version
    try:
        abandoned = session.transitioned_to(SessionStatus.ABANDONED, occurred_at=datetime.now(UTC))
    except InvalidSessionTransition as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "INVALID_SESSION_TRANSITION", "message": str(exc)},
        ) from exc
    try:
        await persistence.save_session(abandoned, expected_version=expected_version)
    except ConcurrentModification as exc:
        raise _conflict(exc) from exc
    return AnalysisSessionView.of(abandoned)


@router.get("/analyses/{analysis_id}/snapshot", response_model=SnapshotView)
async def get_snapshot(analysis_id: str, persistence: _Persistence) -> SnapshotView:
    try:
        session = await persistence.load_session(analysis_id)
    except UnknownAnalysis as exc:
        raise _not_found(analysis_id) from exc
    if session.snapshot_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "NO_SNAPSHOT",
                "message": f"analysis {analysis_id} has not captured a source snapshot yet.",
            },
        )
    snapshot = await persistence.load_snapshot(session.snapshot_id)
    return SnapshotView.of(snapshot)


@router.get("/analyses/{analysis_id}/clarifications", response_model=list[ClarificationView])
async def list_clarifications(
    analysis_id: str, persistence: _Persistence
) -> Sequence[ClarificationView]:
    clarifications = await persistence.list_clarifications(analysis_id)
    return [ClarificationView.of(item) for item in clarifications]


@router.post(
    "/analyses/{analysis_id}/clarifications/{clarification_id}/answer",
    response_model=ClarificationView,
)
async def answer_clarification(
    analysis_id: str,
    clarification_id: str,
    payload: AnswerClarificationRequest,
    request: Request,
    persistence: _Persistence,
) -> ClarificationView:
    try:
        clarification = await persistence.load_clarification(clarification_id)
    except UnknownAnalysis as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "UNKNOWN_CLARIFICATION",
                "message": f"no clarification {clarification_id}.",
            },
        ) from exc
    # A clarification id is globally unique, so this guards against answering one
    # analysis's question through another analysis's URL -- which would otherwise
    # succeed and silently attribute the answer to the wrong session.
    if clarification.analysis_id != analysis_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "UNKNOWN_CLARIFICATION",
                "message": f"clarification {clarification_id} does not belong to {analysis_id}.",
            },
        )
    try:
        answered = clarification.answered(
            payload.answer, by=_actor(request), occurred_at=datetime.now(UTC)
        )
    except InvalidSessionTransition as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "CLARIFICATION_NOT_OPEN", "message": str(exc)},
        ) from exc
    await persistence.save_clarification(answered)
    return ClarificationView.of(answered)


def _not_found(analysis_id: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "UNKNOWN_ANALYSIS", "message": f"no analysis session {analysis_id}."},
    )


def _conflict(exc: ConcurrentModification) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": "CONCURRENT_MODIFICATION", "message": str(exc)},
    )
