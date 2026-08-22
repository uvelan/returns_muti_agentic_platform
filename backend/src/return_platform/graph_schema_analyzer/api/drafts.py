"""`/api/graph-schema` draft editing: mutations, revisions, diffs, validation, approval.

The mutation endpoint accepts the typed command union directly, so FastAPI's own
request validation is what rejects a malformed or smuggled command -- before any
analyzer code runs. That is deliberate: the narrowest place to enforce "only
typed mutations" is the parser.

**Approval and publication carry capability dependencies.** They previously
carried none: `POST /drafts/{id}/approve` accepted whichever actor the
authentication middleware had let through, so "an explicit human act" was
enforced by nothing but the shape of the URL. Both now require the same
governance capability the shared proposal inbox does -- one grant, because a
schema approval and a configuration approval are the same decision made about
different subjects.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from return_platform.graph_schema_analyzer.api.analyses import _actor, resolve_persistence
from return_platform.graph_schema_analyzer.application.discovery_service import DiscoveryService
from return_platform.graph_schema_analyzer.application.draft_service import (
    DraftService,
    NoSnapshotToValidateAgainst,
)
from return_platform.graph_schema_analyzer.application.mutation_service import MutationRejected
from return_platform.graph_schema_analyzer.application.reanalysis_service import (
    ReanalysisProposal,
    propose_reanalysis,
)
from return_platform.graph_schema_analyzer.application.validation_service import ValidationService
from return_platform.graph_schema_analyzer.domain.errors import (
    ConcurrentModification,
    InvalidSessionTransition,
    UnknownAnalysis,
)
from return_platform.graph_schema_analyzer.domain.mutation import MutationCommand
from return_platform.graph_schema_analyzer.domain.sampling_policy import SamplingPolicy
from return_platform.graph_schema_analyzer.domain.schema_draft import DraftStatus
from return_platform.graph_schema_analyzer.domain.schema_revision import SchemaDiff, diff_shapes
from return_platform.graph_schema_analyzer.domain.validation_result import (
    Severity,
    ValidationCheck,
)
from return_platform.graph_schema_analyzer.ports.graph_target_port import GraphTargetPort
from return_platform.graph_schema_analyzer.ports.source_port import SourceDiscoveryPort
from return_platform.graph_schema_analyzer.ports.system_store_port import PersistencePort
from return_platform.platform.governance.errors import ActivationRefused, GovernanceError
from return_platform.platform.governance.kernel import NoActivatorRegistered, ProposalKernel
from return_platform.platform.governance.proposal import ProposalStatus, ProposalType
from return_platform.security.authorization import require_capability
from return_platform.security.capabilities import (
    GOVERNANCE_PROPOSAL_ACTIVATE,
    GOVERNANCE_PROPOSAL_APPROVE,
    GRAPH_SCHEMA_DRAFT_READ,
    GRAPH_SCHEMA_DRAFT_WRITE,
)

router = APIRouter(prefix="/api/graph-schema", tags=["Graph Schema Analyzer"])

_Persistence = Annotated[PersistencePort, Depends(resolve_persistence)]


def resolve_proposal_kernel(request: Request) -> ProposalKernel:
    """The shared proposal kernel, attached at startup.

    503 rather than editing a draft with no governance record: a draft that can
    be mutated, validated and approved while nothing lands in the review queue
    is precisely the state this module was in before the kernel existed, and it
    is invisible from the outside.
    """
    kernel = getattr(request.app.state, "proposal_kernel", None)
    if not isinstance(kernel, ProposalKernel):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "GOVERNANCE_UNAVAILABLE",
                "message": (
                    "The proposal kernel is not available, so a schema change cannot be "
                    "recorded for review. Editing is refused rather than done ungoverned."
                ),
            },
        )
    return kernel


_Kernel = Annotated[ProposalKernel, Depends(resolve_proposal_kernel)]


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


def resolve_source_discovery(request: Request) -> SourceDiscoveryPort:
    """The source discovery port, attached at startup.

    503 rather than a proposal built from no new evidence: a re-analysis that
    could not read the source would report "nothing drifted", which is the one
    answer that must never be guessed.
    """
    sources = getattr(request.app.state, "graph_schema_analyzer_source_discovery", None)
    if not isinstance(sources, SourceDiscoveryPort):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "SOURCE_DISCOVERY_UNAVAILABLE",
                "message": (
                    "Source discovery is not available, so the sources cannot be re-read. "
                    "Re-analysis is refused rather than reporting no drift it did not look for."
                ),
            },
        )
    return sources


_Sources = Annotated[SourceDiscoveryPort, Depends(resolve_source_discovery)]

#: Declare one of these ahead of `_Persistence`, `_Kernel` and `_GraphTarget`,
#: never after them. FastAPI resolves a handler's dependencies in parameter
#: order, and every one of those three answers 503 when its collaborator is not
#: composed -- so a grant declared last let an unauthorized caller read the
#: composition state of this process off the status code. `Annotated` rather
#: than `= Depends(...)` because a defaulted parameter cannot come first.
_Decider = Annotated[str, Depends(require_capability(GOVERNANCE_PROPOSAL_APPROVE))]
_Publisher = Annotated[str, Depends(require_capability(GOVERNANCE_PROPOSAL_ACTIVATE))]

#: Everything else on this router. Only approve, reject and publish carried a
#: grant; the eight handlers below declared none at all and the router adds no
#: `dependencies=`, so creating a draft, rewriting its schema through typed
#: mutations, reading it, diffing its revisions, re-running analysis and
#: validating it were all reachable by any authenticated caller. The sibling
#: analyzer at `/api/graph-analyzer/v1` has required these two capabilities for
#: the same operations all along.
_Reader = Annotated[str, Depends(require_capability(GRAPH_SCHEMA_DRAFT_READ))]
_Writer = Annotated[str, Depends(require_capability(GRAPH_SCHEMA_DRAFT_WRITE))]


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


def _service(
    persistence: PersistencePort, kernel: ProposalKernel, target: GraphTargetPort
) -> DraftService:
    return DraftService(persistence, kernel, ValidationService(target))


@router.post(
    "/analyses/{analysis_id}/drafts",
    response_model=DraftView,
    status_code=status.HTTP_201_CREATED,
)
async def create_draft(
    analysis_id: str,
    author: _Writer,
    persistence: _Persistence,
    kernel: _Kernel,
) -> DraftView:
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
    draft = await DraftService(persistence, kernel).create_draft(
        analysis_id=analysis_id, occurred_at=datetime.now(UTC)
    )
    return _draft_view(draft)


@router.post("/drafts/{draft_id}/mutations", response_model=DraftView)
async def apply_draft_mutations(
    draft_id: str,
    payload: ApplyMutationsRequest,
    request: Request,
    author: _Writer,
    persistence: _Persistence,
    kernel: _Kernel,
) -> DraftView:
    """Typed commands only -- the request model is the enforcement point."""
    try:
        draft, _ = await DraftService(persistence, kernel).apply(
            draft_id=draft_id,
            commands=payload.mutations,
            author=_actor(request),
            occurred_at=datetime.now(UTC),
        )
    except UnknownAnalysis as exc:
        raise _not_found("draft", draft_id) from exc
    except MutationRejected as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "MUTATION_REJECTED", "message": str(exc)},
        ) from exc
    except ConcurrentModification as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "CONCURRENT_MODIFICATION", "message": str(exc)},
        ) from exc
    return _draft_view(draft)


@router.get("/drafts/{draft_id}", response_model=DraftView)
async def get_draft(
    draft_id: str,
    reader: _Reader,
    persistence: _Persistence,
) -> DraftView:
    try:
        return _draft_view(await persistence.load_draft(draft_id))
    except UnknownAnalysis as exc:
        raise _not_found("draft", draft_id) from exc


@router.get("/drafts/{draft_id}/shape", response_model=DraftShapeView)
async def get_draft_shape(
    draft_id: str,
    reader: _Reader,
    persistence: _Persistence,
) -> DraftShapeView:
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
async def list_revisions(
    draft_id: str,
    reader: _Reader,
    persistence: _Persistence,
) -> Sequence[RevisionView]:
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
async def get_revision_diff(
    draft_id: str,
    sequence: int,
    reader: _Reader,
    persistence: _Persistence,
) -> SchemaDiff:
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


@router.post("/drafts/{draft_id}/reanalysis", response_model=ReanalysisProposal)
async def reanalyze_draft(
    author: _Writer,
    draft_id: str, persistence: _Persistence, sources: _Sources
) -> ReanalysisProposal:
    """Re-read the sources and say what the draft would have to change.

    **Proposes; never applies.** The commands come back typed, and the analyst
    accepts them by sending them to `POST /drafts/{id}/mutations` -- the same
    path a hand-written change takes, which is why there is no second one here.
    Rejecting is simply not sending them.

    **The evidence is refreshed; the design is not.** A new snapshot is captured
    and the analysis is re-grounded on it, because from now on "does this draft
    match the source" has to be answered against what the source actually looks
    like -- validation should start failing on the drift, not keep passing
    against a reading from last month. The draft's shape is untouched.

    **Metadata only.** Drift is a question about shape, so nothing here reads a
    sample row, and no sample-retention decision is made or needed.

    A run that finds nothing writes nothing: two captures of the same shape have
    the same content address, and storing a second copy of a snapshot under a
    new id would grow the collection with every poll.
    """
    try:
        draft = await persistence.load_draft(draft_id)
        session = await persistence.load_session(draft.analysis_id)
    except UnknownAnalysis as exc:
        raise _not_found("draft", draft_id) from exc
    if session.snapshot_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "NO_SNAPSHOT",
                "message": (
                    f"analysis {draft.analysis_id} has captured no source snapshot, so there "
                    "is nothing to re-analyse against; run discovery first."
                ),
            },
        )
    before = await persistence.load_snapshot(session.snapshot_id)

    now = datetime.now(UTC)
    try:
        outcome = await DiscoveryService(sources).discover(
            analysis_id=draft.analysis_id,
            # Metadata-only, deliberately, and not the policy the first
            # discovery ran under: re-reading to compare shapes never needs a
            # row, so a re-analysis cannot become a way to sample a source the
            # original analysis was not permitted to sample.
            policies=[SamplingPolicy.metadata_only(ref) for ref in session.source_refs],
            captured_at=now,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "SOURCE_NOT_DISCOVERABLE", "message": str(exc)},
        ) from exc

    after = outcome.snapshot
    if not before.describes_same_shape_as(after):
        await persistence.save_snapshot(after)
        try:
            await persistence.save_session(
                session.with_snapshot(after.snapshot_id, occurred_at=now),
                expected_version=session.version,
            )
        except ConcurrentModification as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "CONCURRENT_MODIFICATION", "message": str(exc)},
            ) from exc

    return propose_reanalysis(
        draft_id=draft_id,
        shape=draft.shape,
        before=before,
        after=after,
        from_sequence=draft.current_revision,
    )


@router.post("/drafts/{draft_id}/validate", response_model=ValidationResultView)
async def validate_draft(
    draft_id: str,
    request: Request,
    author: _Writer,
    persistence: _Persistence,
    kernel: _Kernel,
    target: _GraphTarget,
) -> ValidationResultView:
    """Check the draft, and -- when it passes -- put it in front of a reviewer.

    A passing validation now also submits a `GRAPH_SCHEMA` proposal and moves it
    to REVIEW_PENDING, so a validated schema appears in the one governance inbox
    alongside configuration and improvement changes. The response is unchanged:
    the findings are what the analyst came for, and the proposal is visible on
    `/api/proposals`.
    """
    try:
        _, result, _ = await _service(persistence, kernel, target).validate(
            draft_id=draft_id, actor=_actor(request), occurred_at=datetime.now(UTC)
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
    approver: _Decider,
    persistence: _Persistence,
    kernel: _Kernel,
    target: _GraphTarget,
) -> DraftView:
    """Accept a validated schema.

    The dependency is the point of this route's existence in W4.3: it had none,
    so any authenticated caller could approve any draft. The subject comes from
    the capability check rather than from `_actor`, which falls back to a literal
    when no principal is present -- a fallback that is right for attributing a
    write and wrong for recording who signed something off.
    """
    try:
        draft, _ = await _service(persistence, kernel, target).approve(
            draft_id=draft_id,
            approver=approver,
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
    except GovernanceError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "NOT_APPROVABLE", "message": str(exc)},
        ) from exc
    return _draft_view(draft)


@router.post("/drafts/{draft_id}/reject", response_model=DraftView)
async def reject_draft(
    draft_id: str,
    payload: ApproveRequest,
    approver: _Decider,
    persistence: _Persistence,
    kernel: _Kernel,
    target: _GraphTarget,
) -> DraftView:
    """Refuse a validated schema, on the record.

    There was no way to say no. `Approval` carried a `REJECTED` status that
    nothing ever set, so a reviewer who did not want a draft left it VALIDATED
    and the queue could not tell that from one nobody had looked at yet.

    The draft itself stays VALIDATED: rejecting the *proposal* is a statement
    about this shape, and the analyst edits and re-validates to make a new one.
    """
    try:
        await _service(persistence, kernel, target).reject(
            draft_id=draft_id,
            approver=approver,
            occurred_at=datetime.now(UTC),
            note=payload.note,
        )
        draft = await persistence.load_draft(draft_id)
    except UnknownAnalysis as exc:
        raise _not_found("draft", draft_id) from exc
    except GovernanceError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "NOT_REJECTABLE", "message": str(exc)},
        ) from exc
    return _draft_view(draft)


@router.post("/drafts/{draft_id}/publish", response_model=PublishedReleaseView)
async def publish_draft(
    draft_id: str,
    payload: PublishRequest,
    approver: _Publisher,
    persistence: _Persistence,
    kernel: _Kernel,
) -> PublishedReleaseView:
    """Turn an approved draft into the schema the platform runs.

    The step that closes the analyzer's loop. Until this existed a draft could
    be discovered, edited, validated and approved, and the runtime went on
    reading a file from the repository -- so an approval changed a document and
    nothing else.

    **It goes through the kernel's activation step, not around it.** The publish
    itself is unchanged and still performed by the graph target; what the kernel
    adds is the re-check before it -- the recorded diff is re-derived from the
    before/after documents, and the proposal is refused if the two disagree --
    and the ACTIVATED transition after it, so "this change is live" is recorded
    in the same place every other governed change records it.

    A shape that cannot compile comes back `accepted=false` with the element
    named, not as a 500: which entity was ambiguous is the only useful thing to
    say, and the analyst is the one who can fix it. The proposal stays APPROVED
    in that case, because nothing was activated.
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
    approved = [
        proposal
        for proposal in await kernel.list(
            proposal_type=ProposalType.GRAPH_SCHEMA, subject_id=draft_id, limit=50
        )
        if proposal.status is ProposalStatus.APPROVED
    ]
    if not approved:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "NOT_APPROVED",
                "message": (
                    f"draft {draft_id} has no approved proposal; the schema it would publish "
                    "was never signed off."
                ),
            },
        )
    try:
        _, receipt = await kernel.activate(
            approved[0].proposal_id,
            actor=approver,
            occurred_at=datetime.now(UTC),
            parameters={"activate": payload.activate},
        )
    except ActivationRefused as exc:
        return PublishedReleaseView(
            configurationReleaseId=exc.reference, accepted=False, detail=exc.detail
        )
    except NoActivatorRegistered as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "GRAPH_TARGET_UNAVAILABLE", "message": str(exc)},
        ) from exc
    except GovernanceError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "NOT_ACTIVATABLE", "message": str(exc)},
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "RELEASE_STORE_UNAVAILABLE", "message": str(exc)},
        ) from exc
    return PublishedReleaseView(
        configurationReleaseId=receipt.reference, accepted=True, detail=receipt.detail
    )


def _not_found(kind: str, identifier: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": f"UNKNOWN_{kind.upper()}", "message": f"no {kind} {identifier}."},
    )
