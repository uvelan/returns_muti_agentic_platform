"""`/api/graph-schema` draft editing: mutations, revisions, diffs, validation, approval.

The mutation endpoint accepts the typed command union directly, so FastAPI's own
request validation is what rejects a malformed or smuggled command -- before any
analyzer code runs. That is deliberate: the narrowest place to enforce "only
typed mutations" is the parser.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from return_platform.graph_schema_analyzer.api.analyses import _actor, resolve_persistence
from return_platform.graph_schema_analyzer.application.draft_service import (
    DraftService,
    NoSnapshotToValidateAgainst,
)
from return_platform.graph_schema_analyzer.application.mutation_service import MutationRejected
from return_platform.graph_schema_analyzer.application.validation_service import ValidationService
from return_platform.graph_schema_analyzer.domain.errors import (
    ConcurrentModification,
    InvalidSessionTransition,
    UnknownAnalysis,
)
from return_platform.graph_schema_analyzer.domain.mutation import MutationCommand
from return_platform.graph_schema_analyzer.domain.schema_draft import DraftStatus
from return_platform.graph_schema_analyzer.domain.schema_revision import SchemaDiff, diff_shapes
from return_platform.graph_schema_analyzer.domain.validation_result import (
    Severity,
    ValidationCheck,
)
from return_platform.graph_schema_analyzer.ports.graph_target_port import GraphTargetPort
from return_platform.graph_schema_analyzer.ports.system_store_port import PersistencePort

router = APIRouter(prefix="/api/graph-schema", tags=["Graph Schema Analyzer"])

_Persistence = Annotated[PersistencePort, Depends(resolve_persistence)]


def resolve_graph_target(request: Request) -> GraphTargetPort:
    """The graph target, attached at startup.

    503 rather than a degraded validation when absent: validation without the
    target silently skips CYPHER_COMPILES and QUERY_SAFETY_PASSES, and a partial
    validation that reports "passed" is worse than no validation at all.
    """
    target = getattr(request.app.state, "graph_schema_analyzer_graph_target", None)
    if not isinstance(target, GraphTargetPort):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "GRAPH_TARGET_UNAVAILABLE",
                "message": (
                    "The graph target is not available, so a schema cannot be fully "
                    "validated. Validation is refused rather than run partially."
                ),
            },
        )
    return target


_GraphTarget = Annotated[GraphTargetPort, Depends(resolve_graph_target)]


class ApplyMutationsRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    mutations: tuple[MutationCommand, ...] = Field(min_length=1)


class ApproveRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    note: str | None = Field(default=None, max_length=1000)


class PublishRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # Publishing records the release; activating points the runtime at it.
    # Separate because they are separate decisions -- a schema can be cut and
    # reviewed before anything starts reasoning over it.
    activate: bool = False


class PublishedReleaseView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    configurationReleaseId: str
    accepted: bool
    detail: str | None = None


class DraftView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    draft_id: str
    analysis_id: str
    status: DraftStatus
    current_revision: int
    version: int
    validation_result_id: str | None
    entity_count: int
    relationship_count: int


class PropertyShapeView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    type: str
    source_field: str
    transformation: str


class EntityShapeView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str
    source_dataset: str
    properties: Mapping[str, PropertyShapeView]
    identifier_properties: tuple[str, ...]
    ownership: str
    sync_mode: str


class RelationshipShapeView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    relationship_type: str
    from_label: str
    to_label: str
    cardinality: str


class GraphIndexShapeView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str
    properties: tuple[str, ...]


class GraphConstraintShapeView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str
    property_name: str
    unique: bool
    required: bool


class DraftShapeView(BaseModel):
    """The schema itself -- what a canvas draws.

    **Typed here, not in the domain.** `GraphSchemaShape` is deliberately plain
    `Mapping[str, Any]`: it is the *result* of applying typed mutation commands,
    never an editing surface, and its own docstring says so. Typing it at the
    domain would add a second place every command has to satisfy. Typing it at
    the boundary gives the contract real field names without touching that
    decision.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    entities: Mapping[str, EntityShapeView]
    relationships: tuple[RelationshipShapeView, ...]
    graph_indexes: tuple[GraphIndexShapeView, ...]
    graph_constraints: tuple[GraphConstraintShapeView, ...]


class RevisionView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    revision_id: str
    sequence: int
    author: str
    authored_by_model: bool
    mutation_count: int
    created_at: datetime


class ValidationFindingView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    check: ValidationCheck
    severity: Severity
    element: str
    message: str


class ValidationResultView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    result_id: str
    draft_id: str
    revision_id: str
    passed: bool
    findings: tuple[ValidationFindingView, ...]
    checks_run: tuple[ValidationCheck, ...]
    missing_checks: tuple[ValidationCheck, ...]


def _draft_view(draft: object) -> DraftView:
    from return_platform.graph_schema_analyzer.domain.schema_draft import GraphSchemaDraft

    assert isinstance(draft, GraphSchemaDraft)
    return DraftView(
        draft_id=draft.draft_id,
        analysis_id=draft.analysis_id,
        status=draft.status,
        current_revision=draft.current_revision,
        version=draft.version,
        validation_result_id=draft.validation_result_id,
        entity_count=len(draft.shape.entities),
        relationship_count=len(draft.shape.relationships),
    )


def _draft_shape_view(draft: object) -> DraftShapeView:
    """Validate the plain shape into the published models.

    Deliberately `model_validate` rather than field-by-field copying: the shape
    is untyped `Mapping[str, Any]`, so a mutation command that started writing a
    differently-named key would otherwise serialise as a silently missing field.
    `extra="forbid"` on each view turns that into a 500 the first time it
    happens, which is loud, findable, and far better than a canvas that quietly
    stops drawing an attribute.
    """
    from return_platform.graph_schema_analyzer.domain.schema_draft import GraphSchemaDraft

    assert isinstance(draft, GraphSchemaDraft)
    return DraftShapeView.model_validate(draft.shape.model_dump(mode="json"))


def _service(persistence: PersistencePort, target: GraphTargetPort) -> DraftService:
    return DraftService(persistence, ValidationService(target))


@router.post(
    "/analyses/{analysis_id}/drafts",
    response_model=DraftView,
    status_code=status.HTTP_201_CREATED,
)
async def create_draft(analysis_id: str, persistence: _Persistence) -> DraftView:
    try:
        await persistence.load_session(analysis_id)
    except UnknownAnalysis as exc:
        raise _not_found("analysis", analysis_id) from exc
    existing = await persistence.load_draft_for_analysis(analysis_id)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "DRAFT_ALREADY_EXISTS",
                "message": f"analysis {analysis_id} already has draft {existing.draft_id}.",
            },
        )
    draft = await DraftService(persistence).create_draft(
        analysis_id=analysis_id, occurred_at=datetime.now(UTC)
    )
    return _draft_view(draft)


@router.post("/drafts/{draft_id}/mutations", response_model=DraftView)
async def apply_draft_mutations(
    draft_id: str,
    payload: ApplyMutationsRequest,
    request: Request,
    persistence: _Persistence,
) -> DraftView:
    """Typed commands only -- the request model is the enforcement point."""
    try:
        draft, _ = await DraftService(persistence).apply(
            draft_id=draft_id,
            commands=payload.mutations,
            author=_actor(request),
            occurred_at=datetime.now(UTC),
        )
    except UnknownAnalysis as exc:
        raise _not_found("draft", draft_id) from exc
    except MutationRejected as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "MUTATION_REJECTED", "message": str(exc)},
        ) from exc
    except ConcurrentModification as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "CONCURRENT_MODIFICATION", "message": str(exc)},
        ) from exc
    return _draft_view(draft)


@router.get("/drafts/{draft_id}", response_model=DraftView)
async def get_draft(draft_id: str, persistence: _Persistence) -> DraftView:
    try:
        return _draft_view(await persistence.load_draft(draft_id))
    except UnknownAnalysis as exc:
        raise _not_found("draft", draft_id) from exc


@router.get("/drafts/{draft_id}/shape", response_model=DraftShapeView)
async def get_draft_shape(draft_id: str, persistence: _Persistence) -> DraftShapeView:
    """The draft's entities and relationships.

    **Separate from `GET /drafts/{id}` on purpose.** That view carries
    `entity_count` and `relationship_count`, which are O(1) and are what a list
    of drafts needs. The shape is unbounded -- a real source can produce a large
    schema -- and putting it inline would make every draft listing pay for a
    payload only the canvas reads.

    Until this existed the analyzer serialised counts and nothing else, so a
    consumer could learn that a draft had seven entities and never learn what
    they were. E4's canvas is what that blocked.
    """
    try:
        draft = await persistence.load_draft(draft_id)
    except UnknownAnalysis as exc:
        raise _not_found("draft", draft_id) from exc
    return _draft_shape_view(draft)


@router.get("/drafts/{draft_id}/revisions", response_model=list[RevisionView])
async def list_revisions(draft_id: str, persistence: _Persistence) -> Sequence[RevisionView]:
    revisions = await persistence.list_revisions(draft_id)
    return [
        RevisionView(
            revision_id=revision.revision_id,
            sequence=revision.sequence,
            author=revision.author,
            authored_by_model=revision.authored_by_model,
            mutation_count=len(revision.mutations),
            created_at=revision.created_at,
        )
        for revision in revisions
    ]


@router.get("/drafts/{draft_id}/revisions/{sequence}/diff", response_model=SchemaDiff)
async def get_revision_diff(draft_id: str, sequence: int, persistence: _Persistence) -> SchemaDiff:
    """Diff a revision against its predecessor.

    Reconstructed by replaying the recorded commands rather than by storing a
    shape snapshot per revision: the commands *are* the history, and a stored
    shape could drift from them.
    """
    from return_platform.graph_schema_analyzer.application.mutation_service import apply_mutations
    from return_platform.graph_schema_analyzer.domain.schema_draft import GraphSchemaShape

    revisions = await persistence.list_revisions(draft_id)
    if not any(revision.sequence == sequence for revision in revisions):
        raise _not_found("revision", f"{draft_id}#{sequence}")

    before = GraphSchemaShape()
    after = GraphSchemaShape()
    for revision in revisions:
        if revision.sequence > sequence:
            break
        before = after
        after = apply_mutations(after, revision.mutations)
    return diff_shapes(
        before.model_dump(mode="json"),
        after.model_dump(mode="json"),
        from_sequence=max(sequence - 1, 0),
        to_sequence=sequence,
    )


@router.post("/drafts/{draft_id}/validate", response_model=ValidationResultView)
async def validate_draft(
    draft_id: str, persistence: _Persistence, target: _GraphTarget
) -> ValidationResultView:
    try:
        _, result = await _service(persistence, target).validate(
            draft_id=draft_id, occurred_at=datetime.now(UTC)
        )
    except UnknownAnalysis as exc:
        raise _not_found("draft", draft_id) from exc
    except NoSnapshotToValidateAgainst as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "NO_SNAPSHOT", "message": str(exc)},
        ) from exc
    except InvalidSessionTransition as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "NOTHING_TO_VALIDATE", "message": str(exc)},
        ) from exc
    return ValidationResultView(
        result_id=result.result_id,
        draft_id=result.draft_id,
        revision_id=result.revision_id,
        passed=result.passed,
        findings=tuple(
            ValidationFindingView(
                check=f.check, severity=f.severity, element=f.element, message=f.message
            )
            for f in result.findings
        ),
        checks_run=tuple(sorted(result.checks_run)),
        missing_checks=tuple(sorted(result.missing_checks)),
    )


@router.post("/drafts/{draft_id}/approve", response_model=DraftView)
async def approve_draft(
    draft_id: str,
    payload: ApproveRequest,
    request: Request,
    persistence: _Persistence,
    target: _GraphTarget,
) -> DraftView:
    try:
        draft, _ = await _service(persistence, target).approve(
            draft_id=draft_id,
            approver=_actor(request),
            occurred_at=datetime.now(UTC),
            note=payload.note,
        )
    except UnknownAnalysis as exc:
        raise _not_found("draft", draft_id) from exc
    except InvalidSessionTransition as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "NOT_APPROVABLE", "message": str(exc)},
        ) from exc
    except ConcurrentModification as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "CONCURRENT_MODIFICATION", "message": str(exc)},
        ) from exc
    return _draft_view(draft)


@router.post("/drafts/{draft_id}/publish", response_model=PublishedReleaseView)
async def publish_draft(
    draft_id: str,
    payload: PublishRequest,
    request: Request,
    persistence: _Persistence,
    target: _GraphTarget,
) -> PublishedReleaseView:
    """Turn an approved draft into the schema the platform runs.

    The step that closes the analyzer's loop. Until this existed a draft could
    be discovered, edited, validated and approved, and the runtime went on
    reading a file from the repository -- so an approval changed a document and
    nothing else.

    APPROVED only. A validated draft is a shape someone might accept; a
    published release is one the platform will reason over, and the human act
    in between is the whole reason the state machine has three states.

    A shape that cannot compile comes back `accepted=false` with the element
    named, not as a 500: which entity was ambiguous is the only useful thing to
    say, and the analyst is the one who can fix it.
    """
    try:
        draft = await persistence.load_draft(draft_id)
    except UnknownAnalysis as exc:
        raise _not_found("draft", draft_id) from exc
    if draft.status is not DraftStatus.APPROVED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "NOT_APPROVED",
                "message": (
                    f"draft {draft_id} is {draft.status}; only an APPROVED draft can be "
                    "published to the runtime."
                ),
            },
        )
    try:
        handle = await target.publish_release(
            draft=draft.shape.model_dump(mode="json"),
            draft_id=draft_id,
            approver=_actor(request),
            activate=payload.activate,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "RELEASE_STORE_UNAVAILABLE", "message": str(exc)},
        ) from exc
    return PublishedReleaseView(
        configurationReleaseId=handle.generation_id,
        accepted=handle.accepted,
        detail=handle.detail,
    )


def _not_found(kind: str, identifier: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": f"UNKNOWN_{kind.upper()}", "message": f"no {kind} {identifier}."},
    )
