"""The case read surface: one return, projected.

`GET /api/cases/{case_id}` serves `CaseProjection` -- the read contract of plan
sect. 6.3 -- and it is the **only** shape this resource has. The earlier
`CaseDetail` is gone rather than kept beside it: two shapes for one case is the
drift this programme removes, and a console reading one while the Copilot reads
the other is how "what does the platform believe about this case" acquires two
answers.

Nesting is still the point of the shape. `returnRecords[] -> shipments[]` and
`returnRecords[] -> artifacts[]` mean a response cannot express "label LBL-1
belongs to RMA-2" unless it does, and the console's previous join -- matching an
order reference across two collections in the browser -- is unsayable here.

One artifact hangs off that shape rather than beside it:
`GET /api/cases/{caseId}/returns/{returnRecordId}/artifacts/{artifactId}` is the
opaque authenticated endpoint plan sect. 11 requires, and its path *is* its
authorization -- case, then record, then artifact, so an artifact can only be
reached through the return that owns it. `read_return_artifact` says what it can
and cannot serve, and why the answer today is a reference rather than a file.

**Nothing in this module derives anything.** `project_case` computes `stage`,
`awaiting`, `businessComplete` and `isTerminal`, and plan sect. 6.6 is explicit
that no stage logic lives in an API handler. The handler's whole job is: load
the state, check who is asking, hand the state and the operator's requirement
table to `project_case`, and serialize what comes back.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from return_platform.configuration.return_configuration import (
    LoadedReturnConfiguration,
    build_return_method_requirement_table,
)
from return_platform.operations.case_projection import (
    CaseProjection,
    CaseProjectionState,
    CopilotStage,
    ReturnCaseStatus,
    ReturnMethodRequirementTable,
    project_case,
)
from return_platform.operations.case_projection.contract import (
    ReturnArtifactProjection,
    ReturnRecordProjection,
)
from return_platform.operations.case_projection.vocabulary import ReturnArtifactType
from return_platform.operations.errors import ConcurrencyConflictError
from return_platform.operations.models import FactAcquisition, FactChannel
from return_platform.operations.repository import resolve_operational_repository
from return_platform.resources import RuntimeResources
from return_platform.security.authorization import (
    require_admin_roles,
    require_audit_roles,
    require_capability,
    require_read_roles,
)
from return_platform.security.capabilities import (
    RETURNS_LOGISTICS_ACT,
    RETURNS_POLICY_OVERRIDE,
)
from return_platform.security.principal import Principal
from return_platform.shared.contracts import APIResponse, ResponseMeta
from return_platform.workflows.case_divergence import (
    CaseDivergence,
    CaseDivergenceAssessment,
    CaseExecutionState,
    DivergenceReason,
    LateEventDisposition,
)
from return_platform.workflows.return_case_recovery import (
    CaseRecoveryOutcome,
    RecoveryAction,
    ReturnCaseRecoveryService,
    build_case_recovery_service,
)
from return_platform.workflows.return_case_workflow import (
    PolicyOverrideNotice,
    return_case_workflow_id,
)

logger = logging.getLogger("return_platform.api.cases")

router = APIRouter(prefix="/api/cases", tags=["Cases"])

#: The signal an override sends. Fixed here rather than read from the payload,
#: exactly as `TemporalSignalDispatcher` fixes its own: a request body that could
#: name the method to invoke on a running workflow is a request body that can
#: invoke any of them.
_POLICY_OVERRIDE_SIGNAL = "policy_override"


class CaseSummary(BaseModel):
    """A row in the associate's case list.

    Carries the counts rather than the records: a list of twenty cases must not
    pull every RMA and item to render twenty lines.

    **`stage` and `isTerminal` travel with the row** so that a list can show the
    polling stop without reading each case in full. `isTerminal` is the same
    value `caseRefetchInterval` reads on the detail, derived by the same
    function, so a row that says "finished" and a detail that says "still
    running" cannot disagree.

    **`status` is the projected `ReturnCaseStatus`, not the persisted
    `CaseStatus`.** The detail serves the projected vocabulary, and a list that
    served the persisted one would show `CLOSED` beside a detail saying
    `COMPLETED_EXTERNAL_SETTLEMENT` for the same case -- two names for one state,
    which is the drift `status_mapping` exists to end rather than to relocate.
    """

    model_config = ConfigDict(extra="forbid")

    caseId: str
    status: ReturnCaseStatus
    stage: CopilotStage
    isTerminal: bool
    confirmedOrderReference: str | None = None
    channelAConversationId: str | None = None
    returnRecordCount: int
    updatedAt: str


class PolicyOverrideRequest(BaseModel):
    """What a supervisor supplies. Everything else is derived by the server.

    `extra="ignore"`, alone among the models in this module, and deliberately.
    The audit fields a caller might try to set -- `actor`, a timestamp, the
    original decision, a tenant -- are server-derived, and dropping them is what
    "the server derives it" means. Refusing the request instead would tell the
    caller the field exists and is merely mis-typed, which invites the next
    attempt to guess the right spelling.
    """

    model_config = ConfigDict(extra="ignore")

    #: The `case.revision` the supervisor was looking at. A case that moved
    #: underneath them is a 409, not a silent override of a newer state.
    expectedRevision: int = Field(ge=0)
    #: Exactly the two decisions that resolve a review. `REVIEW_REQUIRED` is what
    #: the case is already in, so it is not an override of anything.
    overrideDecision: Literal["APPROVE", "REJECT"]
    reasonCode: str = Field(min_length=1, max_length=128)
    reason: str | None = Field(default=None, max_length=2_000)
    idempotencyKey: str = Field(min_length=8, max_length=128)


class PolicyOverrideDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overrideDecision: str
    reasonCode: str
    reason: str | None = None
    #: The authenticated principal, never the request body.
    actor: str
    #: The server clock, never the request body.
    overriddenAt: str


class PolicyOverrideResult(BaseModel):
    """Both readings of the decision, because the original is never overwritten."""

    model_config = ConfigDict(extra="forbid")

    caseId: str
    revision: int
    originalDecision: str
    effectiveDecision: str
    override: PolicyOverrideDetail
    #: Whether the running workflow was told. `False` means the override is
    #: recorded and the case has not moved -- which is a state an operator can
    #: see and retry, and is reported rather than hidden behind a 200.
    signalDelivered: bool


def _meta(request: Request) -> ResponseMeta:
    request_id = getattr(request.state, "correlation_id", "unknown")
    return ResponseMeta(request_id=request_id if isinstance(request_id, str) else "unknown")


def _belongs_to(state: CaseProjectionState, *, tenant_id: str, principal_id: str) -> bool:
    """Whose case this is.

    Both, not either: a principal id repeated in a second tenant would
    otherwise read across the boundary, which is the failure the conversation
    store was already fixed for.

    Checked here rather than pushed into `load_case_projection_state`, and
    deliberately. That loader mirrors `get_case`: it takes an id and answers
    about the case with that id, and half a dozen callers -- the workflow, the
    backfill, the recovery sweep -- have no principal to scope by. Scoping it
    would either refuse those callers or grow a "no really, load it anyway"
    parameter, and a scope you can opt out of is not a scope. The route knows
    who is asking, so the route is where the question is answered.
    """
    return state.tenantId == tenant_id and state.principalId == principal_id


def _not_found(case_id: str) -> HTTPException:
    """Absent, not forbidden.

    A 403 on a guessed id confirms the id exists, which is most of what an
    attacker enumerating cases wants to learn. The list is scoped for the same
    reason, so no caller ever hears of a case that is not theirs.
    """
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "code": "CASE_NOT_FOUND",
            "message": f"Case {case_id} does not exist.",
            "retryable": False,
        },
    )


def _requirement_table(request: Request) -> ReturnMethodRequirementTable:
    """The operator's return-method requirement table, from the active release.

    **Passing this is not optional and there is no fallback.**
    `resolve_completion(..., requirements=)` and `project_case(..., requirements=)`
    have no default -- omitting the table is a `TypeError` rather than an answer
    computed from `DEFAULT_RETURN_METHOD_REQUIREMENTS` -- because the table is
    operator-owned in `return_policy.return_method_requirements`. While a default
    existed, a handler that omitted the argument still answered, so the release's
    table, including the four rows it flags for operator review, could become
    decorative and nothing would fail to say so.

    Refusing with 503 rather than falling back for the same reason
    `validate_return_eligibility_policy` refuses: an unconfigured platform that
    quietly answers from a constant looks exactly like a configured one, and the
    difference only surfaces as a return that completed without the paperwork.
    """
    loaded = getattr(request.app.state, "return_configuration", None)
    if not isinstance(loaded, LoadedReturnConfiguration):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "RETURN_CONFIGURATION_UNAVAILABLE",
                "message": (
                    "No return configuration is active, so the return-method requirement "
                    "table cannot be resolved and no case can be projected."
                ),
                "retryable": True,
            },
        )
    return build_return_method_requirement_table(loaded.configuration)


@router.get("", response_model=APIResponse[list[CaseSummary]])
async def list_cases(
    request: Request,
    conversationId: str | None = Query(  # noqa: N803 - the wire name, not a Python one
        default=None,
        max_length=200,
        description="Only the case this Channel A conversation raised, if it raised one.",
    ),
    _actor_id: str = Depends(require_read_roles),
) -> APIResponse[list[CaseSummary]]:
    """The caller's own cases, newest first.

    Scoped by tenant and principal in the query, for the same reason the
    conversation history is: a case carries the customer's order and whatever
    the associate typed about them, and "list every case" is not a read anyone
    should be able to make by accident.

    `conversationId` narrows to one case rather than being a second endpoint.
    It is what makes resuming a conversation restore the return with it: the
    copilot learns the case id from the turn that confirmed, and after a reload
    there is no such turn to learn it from.
    """
    repository = resolve_operational_repository(request)
    principal = cast(Principal, request.state.principal)
    tenant_id = str(getattr(request.state, "tenant_id", "default"))
    requirements = _requirement_table(request)
    if conversationId is not None:
        found = await repository.get_case_by_conversation(
            conversationId, tenant_id=tenant_id, principal_id=principal.subject
        )
        cases = [] if found is None else [found]
    else:
        cases = await repository.list_cases_for_principal(
            tenant_id=tenant_id,
            principal_id=principal.subject,
        )

    summaries: list[CaseSummary] = []
    for case in cases:
        case_id = str(case["caseId"])
        state = await repository.load_case_projection_state(case_id)
        if state is None:
            # Deleted between the scan above and the read here. A row for a case
            # that no longer exists is a row whose link 404s.
            continue
        # The same function the detail calls. A list that derived its own stage
        # would be the second place stage logic lives, which sect. 6.6 forbids
        # precisely so the two cannot drift.
        projection = project_case(state, requirements=requirements)
        summaries.append(
            CaseSummary(
                caseId=projection.caseId,
                status=projection.status,
                stage=projection.stage,
                isTerminal=projection.isTerminal,
                confirmedOrderReference=(
                    None
                    if projection.confirmedOrder is None
                    else projection.confirmedOrder.orderReference
                ),
                channelAConversationId=projection.conversationId,
                returnRecordCount=len(projection.records()),
                updatedAt=projection.updatedAt.isoformat(),
            )
        )
    return APIResponse(data=summaries, meta=_meta(request))


@router.get("/{case_id}", response_model=APIResponse[CaseProjection])
async def get_case(
    case_id: str,
    request: Request,
    _actor_id: str = Depends(require_read_roles),
) -> APIResponse[CaseProjection]:
    """One case as the Copilot read contract sees it (plan sect. 6.3).

    Four steps and no fifth. The state comes from the one sanctioned assembler,
    ownership is checked here because the assembler mirrors `get_case` and takes
    no principal, the operator's requirement table comes from the active
    release, and `project_case` derives everything derived. Every block it
    carries is **absent rather than defaulted** when the platform has not
    computed it: `artifacts: null` is "nobody has asked", `artifacts: []` is
    "asked, and there are none", and collapsing the two is how a pane renders
    "no label" for a case whose label nobody looked for.

    Someone else's case is reported as absent, not as forbidden.
    """
    repository = resolve_operational_repository(request)
    principal = cast(Principal, request.state.principal)
    requirements = _requirement_table(request)

    state = await repository.load_case_projection_state(case_id)
    if state is None:
        raise _not_found(case_id)
    if not _belongs_to(
        state,
        tenant_id=str(getattr(request.state, "tenant_id", "default")),
        principal_id=principal.subject,
    ):
        raise _not_found(case_id)

    return APIResponse(
        data=project_case(state, requirements=requirements),
        meta=_meta(request),
    )


# ---------------------------------------------------------------------------
# Return artifacts (plan sect. 11)
# ---------------------------------------------------------------------------


class ArtifactContentState(StrEnum):
    """What this platform can actually hand over for one artifact.

    **One member, because one is the truth.** Nothing in this platform stores a
    label document. `ReturnRecordView.labelReference` is a bare string --
    `LBL-OPS01` on the real record `4e372a39...` -- written by Support through
    `ReturnOutcomeRecord.labelReference`, and there is no object store, no
    GridFS bucket, no provider URL and no bytes anywhere behind it. So the only
    thing this route can serve is the reference itself and the metadata the
    projection holds about it.

    `REFERENCE_ONLY` says exactly that, positively, in the same spirit as
    `SettlementStatus.NOT_INTEGRATED`: not "the document is missing", which
    would read as a failure somebody should retry, but "this platform holds a
    reference and no document", which is a fact about the system. A route that
    404'd instead would be indistinguishable from a wrong artifact id, and one
    that returned an empty PDF would be the audit's fabrications in a new
    costume.

    Members land here when producers do, and the handler's branches are already
    written against them -- stored bytes become a `Response` with a media type
    and a `Content-Disposition`, and a provider-owned file becomes a short-lived
    redirect. Neither can be built today: there is no byte source, and
    `ReturnArtifactProjection` deliberately carries no `url` field for a
    redirect target to hide in.
    """

    REFERENCE_ONLY = "REFERENCE_ONLY"


class ReturnArtifactView(BaseModel):
    """One document belonging to one RMA, as much of it as exists.

    The response of the artifact route. Deliberately **not**
    `ReturnArtifactProjection` re-served: the projection is what the case read
    carries, and this adds the three things a retrieval answer needs and the
    projection has no business holding -- the scope that was validated
    (`caseId`, `returnRecordId`), the backend's own verdict on whether this is
    the live artifact (`isActive`), and what can be handed over
    (`contentState`).

    **`isActive` is computed here and never by the client.** Plan sect. 11 is
    explicit that the single label action resolves to the backend-declared
    active artifact -- `active == true AND supersededBy == null`, never
    `labels[0]` -- so the pair of clauses is evaluated once, on the server, by
    `ReturnArtifactProjection.is_active`. `supersededBy` travels beside it so an
    auditor reading a superseded artifact can follow it to its replacement
    without a second request.
    """

    model_config = ConfigDict(extra="forbid")

    caseId: str
    returnRecordId: str
    artifactId: str
    artifactType: ReturnArtifactType
    #: The package this document is attributed to, or `null` for "this RMA has
    #: it and no package is known yet" -- the shape of the real stuck record.
    shipmentId: str | None = None
    fileName: str | None = None
    mediaType: str | None = None
    version: int | None = None
    #: The backend's verdict, not a field for the client to recompute.
    isActive: bool
    supersededBy: str | None = None
    expiresAt: str | None = None
    createdAt: str | None = None
    contentState: ArtifactContentState


def _artifact_not_found(case_id: str, return_record_id: str, artifact_id: str) -> HTTPException:
    """404 for an unknown record and for an artifact on a different return alike.

    **One answer for both, and 404 rather than 403.** Plan sect. 11 requires the
    artifact to belong to *that* return and never to be authorized on artifact
    id alone; the corollary is that a caller who names a real artifact under the
    wrong record must learn nothing from the refusal. A 403 there would confirm
    the artifact exists somewhere, and a distinct "no such return record" code
    would confirm which half of the path was wrong -- both of which turn this
    route into an oracle for enumerating another return's paperwork.
    """
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "code": "RETURN_ARTIFACT_NOT_FOUND",
            "message": (
                f"Case {case_id} has no artifact {artifact_id} on return record {return_record_id}."
            ),
            "retryable": False,
        },
    )


def _find_record(
    state: CaseProjectionState, return_record_id: str
) -> ReturnRecordProjection | None:
    return next(
        (record for record in state.records() if record.returnRecordId == return_record_id),
        None,
    )


def _find_artifact(
    record: ReturnRecordProjection, artifact_id: str
) -> ReturnArtifactProjection | None:
    """Every artifact on the record, superseded ones included.

    Not `active_artifacts`. A replaced label stays auditable -- that is the
    whole reason `supersededBy` exists instead of a delete -- and an audit that
    cannot fetch the document it is auditing is not an audit. The response says
    `isActive: false` and the caller decides; the route does not decide for it
    by pretending the artifact is gone.
    """
    if record.artifacts is None:
        return None
    return next(
        (artifact for artifact in record.artifacts if artifact.artifactId == artifact_id),
        None,
    )


@router.get(
    "/{case_id}/returns/{return_record_id}/artifacts/{artifact_id}",
    response_model=APIResponse[ReturnArtifactView],
)
async def read_return_artifact(
    case_id: str,
    return_record_id: str,
    artifact_id: str,
    request: Request,
    _actor_id: str = Depends(require_read_roles),
) -> APIResponse[ReturnArtifactView]:
    """One RMA's document, through an opaque authenticated endpoint (plan sect. 11).

    The route the label action needs. Before it existed the Copilot's print
    button called `window.print()` -- it printed the web page, because there was
    nowhere to ask for the label.

    **Authorization is four checks and the fourth is the point.** Tenant and
    principal, exactly as `get_case` does them and through the same
    `_belongs_to`; then that the return record is *on this case*; then that the
    artifact is *on that record*. Never on artifact id alone: an id is a
    guessable string and an artifact validated on its own id is an artifact
    servable to whoever guesses it. Because the lookup walks the projection --
    case, then its records, then that record's artifacts -- the containment is
    structural rather than a comparison somebody has to remember to write.

    **Every refusal is a 404.** Someone else's case, a record on another case,
    an artifact on another record and an artifact that does not exist are one
    answer, matching `get_case`: a 403 on a guessed id confirms the id exists,
    which is most of what an enumerator wants.

    **What comes back, honestly.** There is no document. `labelReference` is a
    string Support typed into `ReturnOutcomeRecord`, and this platform has no
    object store, no bucket and no provider URL behind it; see
    `ArtifactContentState`. So the response is the reference and the metadata
    the projection holds, marked `REFERENCE_ONLY` -- which is what an associate
    can act on today, because the reference is what a carrier desk and a
    warehouse ticket are keyed by. No file is invented, and the two branches a
    real document would take are written below rather than left to be
    rediscovered:

    ```text
    stored bytes      -> Response(content, media_type=artifact.mediaType,
                         headers={"Content-Disposition":
                                  f'attachment; filename="{artifact.fileName}"'})
    provider-owned    -> 307 to a short-lived provider URL
    expired provider  -> 409 RETURN_ARTIFACT_EXPIRED, never a broken link
    ```

    The expiry branch is live now, because `expiresAt` is on the contract even
    though nothing sets it yet: an artifact past its expiry is refused with a
    re-issue hint rather than served as a reference an operator would take to a
    counter and be turned away from.
    """
    repository = resolve_operational_repository(request)
    principal = cast(Principal, request.state.principal)

    state = await repository.load_case_projection_state(case_id)
    if state is None:
        raise _not_found(case_id)
    if not _belongs_to(
        state,
        tenant_id=str(getattr(request.state, "tenant_id", "default")),
        principal_id=principal.subject,
    ):
        raise _not_found(case_id)

    record = _find_record(state, return_record_id)
    if record is None:
        raise _artifact_not_found(case_id, return_record_id, artifact_id)
    artifact = _find_artifact(record, artifact_id)
    if artifact is None:
        raise _artifact_not_found(case_id, return_record_id, artifact_id)

    if artifact.expiresAt is not None and artifact.expiresAt <= datetime.now(UTC):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "RETURN_ARTIFACT_EXPIRED",
                "message": (
                    f"Artifact {artifact_id} expired at "
                    f"{artifact.expiresAt.isoformat()}. Ask Support to re-issue it on "
                    f"return record {return_record_id}; the replacement supersedes this "
                    "one and this artifact stays readable for audit."
                ),
                "retryable": False,
            },
        )

    return APIResponse(
        data=ReturnArtifactView(
            caseId=state.caseId,
            returnRecordId=record.returnRecordId,
            artifactId=artifact.artifactId,
            artifactType=artifact.artifactType,
            shipmentId=artifact.shipmentId,
            fileName=artifact.fileName,
            mediaType=artifact.mediaType,
            version=artifact.version,
            # Both clauses, resolved server-side. Never `labels[0]`.
            isActive=artifact.is_active,
            supersededBy=artifact.supersededBy,
            expiresAt=None if artifact.expiresAt is None else artifact.expiresAt.isoformat(),
            createdAt=None if artifact.createdAt is None else artifact.createdAt.isoformat(),
            # The only state this platform can report, and it reports it rather
            # than manufacturing a file to satisfy the shape of a download.
            contentState=ArtifactContentState.REFERENCE_ONLY,
        ),
        meta=_meta(request),
    )


# ---------------------------------------------------------------------------
# Recovery (plan sect. 13, Phase 10)
# ---------------------------------------------------------------------------


class CaseRecoveryView(BaseModel):
    """Why a case is stuck, in terms an operator can act on.

    A separate resource from `CaseProjection` rather than a block on it, and
    that is the boundary the whole read contract rests on: **the case read never
    calls Temporal.** `project_case` derives `status`, `stage`, `awaiting` and
    `isTerminal` from persisted state alone, and adding an execution probe to it
    would make the workflow host a synchronous dependency of every Copilot poll
    -- the exact coupling `case_projection/vocabulary.py` refuses. The
    projection already says *that* a case is stuck, through
    `status: RECOVERY_REQUIRED` and `awaiting: [RECOVERY]`. This says *why*, on
    a route an operator opens deliberately.

    Nothing here is derived in this module. Every field comes off
    `CaseDivergenceAssessment`, which is pure and tested on its own.
    """

    model_config = ConfigDict(extra="forbid")

    caseId: str
    #: The projected status -- the same vocabulary the detail and the list serve.
    status: ReturnCaseStatus
    #: The persisted status, verbatim. Present because `CLOSED` and `CANCELLED`
    #: are one word apart on the projection and an operator reconciling against
    #: the collection needs the value that is actually in it.
    persistedStatus: str
    executionState: CaseExecutionState
    #: The workflow host's own status name, when it had one to give.
    executionStatus: str | None = None
    workflowId: str
    divergence: CaseDivergence
    reason: DivergenceReason
    #: Whether relaunching is the correct repair. `false` for a healthy case, for
    #: a legitimately terminal one, and while the workflow host is unreachable.
    isRecoverable: bool
    #: What a durable Support event whose delivery permanently failed is owed.
    lateEventDisposition: LateEventDisposition


class CaseRecoveryResult(BaseModel):
    """What a relaunch actually did. Never a bare 200.

    `action` rather than a success flag, because "did not relaunch" has four
    meanings -- already running, legitimately terminal, host unreachable, start
    refused -- and an operator who cannot tell them apart cannot act on any of
    them.
    """

    model_config = ConfigDict(extra="forbid")

    caseId: str
    action: RecoveryAction
    workflowId: str | None = None
    #: Dead-lettered Support events put back on the delivery queue by this call.
    #: They were durable in Mongo the whole time; recovery re-drives them rather
    #: than asking Support to send the reply again.
    requeuedSupportEvents: int
    #: Dead-lettered Support events recorded as never-to-be-applied, because the
    #: case is legitimately terminal. Retained for audit, not deleted.
    rejectedSupportEvents: int
    recovery: CaseRecoveryView | None = None


def _recovery_service(request: Request) -> ReturnCaseRecoveryService:
    """The one service, assembled from what this process already holds.

    Built by `build_case_recovery_service` rather than here, so the route and
    the background sweep are provably the same object with the same guards -- a
    handler that assembled its own launcher would be a second place the
    duplicate-execution rule lives.
    """
    resources = getattr(request.app.state, "resources", None)
    temporal = resources.temporal if isinstance(resources, RuntimeResources) else None
    if not isinstance(resources, RuntimeResources) or temporal is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "WORKFLOW_HOST_UNAVAILABLE",
                "message": (
                    "This process has no Temporal client, so it cannot tell a case that "
                    "lost its execution from one that is running normally."
                ),
                "retryable": True,
            },
        )
    loaded = getattr(request.app.state, "return_configuration", None)
    if not isinstance(loaded, LoadedReturnConfiguration):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "RETURN_CONFIGURATION_UNAVAILABLE",
                "message": (
                    "No return configuration is active, so a recovered case has no "
                    "timings to be restarted with."
                ),
                "retryable": True,
            },
        )
    settings = resources.settings
    mongo = resources.mongo
    return build_case_recovery_service(
        temporal=temporal,
        repository=resolve_operational_repository(request),
        database=None if mongo is None else mongo[settings.mongo_database],
        timings=loaded.configuration.return_case,
        gate=loaded.configuration.support_gate,
        task_queue=settings.return_workflow_task_queue,
    )


async def _tenant_scoped_case(request: Request, case_id: str) -> dict[str, Any]:
    """The case, or a 404 that tells a guesser nothing.

    Tenant only, deliberately -- not `_belongs_to`. The associate who raised a
    return is rarely the person who recovers it, and scoping by principal would
    refuse the only callers these two routes exist for.
    """
    repository = resolve_operational_repository(request)
    case = await repository.get_case(case_id)
    tenant_id = str(getattr(request.state, "tenant_id", "default"))
    if case is None or case.get("tenantId") != tenant_id:
        raise _not_found(case_id)
    return case


def _recovery_view(assessment: CaseDivergenceAssessment) -> CaseRecoveryView:
    return CaseRecoveryView(
        caseId=assessment.case_id,
        status=assessment.projected_status,
        persistedStatus=assessment.persisted_status.value,
        executionState=assessment.execution,
        executionStatus=assessment.execution_detail,
        workflowId=return_case_workflow_id(assessment.case_id),
        divergence=assessment.divergence,
        reason=assessment.reason,
        isRecoverable=assessment.is_recoverable,
        lateEventDisposition=assessment.late_event,
    )


@router.get("/{case_id}/recovery", response_model=APIResponse[CaseRecoveryView])
async def read_case_recovery(
    case_id: str,
    request: Request,
    _actor: str = Depends(require_audit_roles),
) -> APIResponse[CaseRecoveryView]:
    """Read one case against the execution that is supposed to own it.

    **Reads, and writes nothing.** Looking at a stuck case must never be the
    thing that restarts it, which is why this and the relaunch below are two
    routes rather than one that repairs as a side effect of being asked.

    Same gate as the integration-outbox listing: this is operational visibility
    over a delivery failure, and it is the other half of the
    `reconciliationState` that surface already reports.
    """
    await _tenant_scoped_case(request, case_id)
    assessment = await _recovery_service(request).assess(case_id)
    if assessment is None:  # pragma: no cover - the case existed a line ago
        raise _not_found(case_id)
    return APIResponse(data=_recovery_view(assessment), meta=_meta(request))


@router.post("/{case_id}/recovery/relaunch", response_model=APIResponse[CaseRecoveryResult])
async def relaunch_case_workflow(
    case_id: str,
    request: Request,
    _actor: str = Depends(require_admin_roles),
) -> APIResponse[CaseRecoveryResult]:
    """Start the execution an orphaned case is owed, and re-drive what was lost.

    **Refuses far more often than it acts, and that is the feature.** The
    service probes the derived execution id first and relaunches only when the
    execution is closed or absent *and* the case is not terminal. A live
    execution answers `ALREADY_RUNNING`, a finished case answers
    `REFUSED_TERMINAL` and stays finished, and an unreachable Temporal answers
    `DEFERRED_UNKNOWN` without touching anything. None of those are errors --
    they are the answer -- so all of them are a 200 carrying the action.

    Idempotent by consequence rather than by a key: the second call finds a
    running execution and reports `ALREADY_RUNNING` with nothing requeued.
    """
    await _tenant_scoped_case(request, case_id)
    outcome: CaseRecoveryOutcome = await _recovery_service(request).reconcile_case(case_id)
    if outcome.action is RecoveryAction.CASE_NOT_FOUND:  # pragma: no cover - checked above
        raise _not_found(case_id)
    logger.info(
        "case_recovery_requested",
        extra={"case_id": case_id, "action": outcome.action.value, "actor": _actor},
    )
    return APIResponse(
        data=CaseRecoveryResult(
            caseId=case_id,
            action=outcome.action,
            workflowId=outcome.workflow_id,
            requeuedSupportEvents=outcome.requeued_commands,
            rejectedSupportEvents=outcome.rejected_commands,
            recovery=None if outcome.assessment is None else _recovery_view(outcome.assessment),
        ),
        meta=_meta(request),
    )


def _conflict(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": code, "message": message, "retryable": False},
    )


def _fact_value(facts: dict[str, dict[str, Any]], name: str) -> str | None:
    fact = facts.get(name)
    if fact is None:
        return None
    value = fact.get("value")
    return None if value is None else str(value)


def _temporal_client(request: Request) -> Any | None:
    """The Temporal client, if this process has one.

    Read off `app.state.resources` rather than injected so that a deployment
    without Temporal -- and every test of the recording half of this endpoint --
    still records the override. The override is durable before anything is
    signalled, which is the ordering that survives a Temporal outage.
    """
    resources = getattr(request.app.state, "resources", None)
    return getattr(resources, "temporal", None)


class CaseReceiptRequest(BaseModel):
    """Goods booked in at the warehouse. What a receiver states, and no more.

    `receivedAt` is the server clock and cannot be supplied, for the reason the
    policy override derives its own timestamp: a caller-supplied audit field is
    not audit. A receipt backdated by the client is exactly the value a dispute
    would turn on.
    """

    model_config = ConfigDict(extra="forbid")

    #: How many units actually arrived, which is frequently not how many the
    #: associate said were coming. Zero is a real answer -- a consignment that
    #: arrived empty is a receipt, and a different problem from one that never
    #: arrived at all.
    receivedQuantity: int = Field(ge=0, le=100_000)
    #: The receiver's own finding, in the deployment's inspection vocabulary.
    #: Distinct from the condition the associate claimed at selection: this one
    #: is somebody looking at the goods.
    inspectionStatus: str | None = Field(default=None, min_length=1, max_length=128)
    condition: str | None = Field(default=None, min_length=1, max_length=128)
    #: Where the goods now are in the receiving process. Free text against the
    #: deployment's own ladder rather than an enum here, because the platform
    #: publishes no warehouse-status catalogue to validate against and a
    #: `Literal` would be this module inventing one.
    warehouseStatus: str | None = Field(default=None, min_length=1, max_length=128)
    #: Idempotency, the same shape the override uses: a retried request writes
    #: the same fact ids rather than a second arrival.
    idempotencyKey: str = Field(min_length=1, max_length=128)


@router.post("/{case_id}/receipt", response_model=APIResponse[CaseProjection])
async def record_case_receipt(
    case_id: str,
    payload: CaseReceiptRequest,
    request: Request,
    actor: str = Depends(require_capability(RETURNS_LOGISTICS_ACT)),
) -> APIResponse[CaseProjection]:
    """The goods arrived. The producer four projection fields were waiting for.

    Until this existed the case path had no concept of arrival at all.
    `WarehouseProjection.receivedAt`, `receivedQuantity`, `inspectionStatus` and
    `warehouseStatus` were declared, documented as having no producer, and
    always `None` -- so the lifecycle's "Reached warehouse" could never be true
    and a physically completed return could not be shown on any screen.

    **A recommended bay is not goods booked in**, which is why this is a
    separate act from placement: `CaseBayPlacement` runs pre-arrival by design
    and typically predates the goods by days. **Nor is a carrier's `delivered`
    scan**: that is the carrier saying it handed the parcel over, not the
    warehouse saying it has the item and counted it. Deriving a receipt from
    either would be the invention this endpoint exists to replace.

    **Written as facts, not a new collection.** The case log is append-only and
    already carries who recorded what and when, which is what a receipt is. A
    corrected count is a second receipt superseding the first under the log's
    newest-per-name rule, so the correction is visible rather than destructive.

    **Accepted on a closed case.** Goods arriving after closure is the normal
    path for a prepaid parcel -- the customer's obligation ended when they
    handed it over, and the credit is issued externally days before the dock
    sees anything. Recording the arrival does not reopen the case; refusing it
    would leave the platform unable to say where the goods went.
    """
    repository = resolve_operational_repository(request)
    tenant_id = str(getattr(request.state, "tenant_id", "default"))
    case = await repository.get_case(case_id)
    if case is None or case.get("tenantId") != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "CASE_NOT_FOUND",
                "message": f"Case {case_id} does not exist.",
                "retryable": False,
            },
        )

    received_at = datetime.now(UTC)
    values: list[tuple[str, Any]] = [
        ("warehouse_received_at", received_at.isoformat()),
        ("warehouse_received_quantity", payload.receivedQuantity),
    ]
    if payload.inspectionStatus is not None:
        values.append(("warehouse_inspection_status", payload.inspectionStatus))
    if payload.condition is not None:
        values.append(("warehouse_received_condition", payload.condition))
    if payload.warehouseStatus is not None:
        values.append(("warehouse_status", payload.warehouseStatus))

    for name, value in values:
        await repository.append_case_fact(
            # Deterministic in the key, so a retried request re-writes the same
            # ids and the append is idempotent rather than a second arrival.
            fact_id=f"{name}-{payload.idempotencyKey}",
            case_id=case_id,
            fact_name=name,
            value=value,
            agent_id=actor,
            channel=FactChannel.SYSTEM,
            acquisition_method=FactAcquisition.OBSERVED,
            source_system="RETURN_PLATFORM_WAREHOUSE",
            source_path="CASE_RECEIPT",
            observed_at=received_at,
        )

    # The freshly assembled case, so the caller sees the receipt it just
    # recorded rather than having to poll for it.
    return await get_case(case_id, request)


@router.post(
    "/{case_id}/policy-override",
    response_model=APIResponse[PolicyOverrideResult],
)
async def override_case_policy(
    case_id: str,
    payload: PolicyOverrideRequest,
    request: Request,
    actor: str = Depends(require_capability(RETURNS_POLICY_OVERRIDE)),
) -> APIResponse[PolicyOverrideResult]:
    """A supervisor's decision on a case the evaluator sent to review (3A.8).

    **Append-only.** The evaluator's `policy_decision` is never rewritten. The
    override is appended beside it and a later `policy_effective_decision`
    supersedes the earlier one in the fact projection, so "what the policy said"
    and "what stands now" are both recoverable afterwards -- which is the whole
    difference between an override and an edit.

    **Server-derived attribution.** `actor` is the authenticated principal and
    `overriddenAt` is the server clock. Neither can be supplied, for the same
    reason `order_agent.py` derives `correlation_id`: a client-supplied audit
    field is not audit.

    **Scoped by tenant, gated by capability, and deliberately not by principal.**
    The associate who raised the return is exactly the person who must not be
    able to overrule the policy on it, so `_belongs_to`'s principal check would
    refuse the only caller this endpoint is for. `returns.policy.override` is the
    gate, and it is held by supervisors and by no service account.

    Records the override before it signals. A Temporal outage then leaves an
    audited decision that has not yet moved the case, which an operator can see
    and retry; signalling first would leave a case that moved with nothing
    written down.
    """
    repository = resolve_operational_repository(request)
    tenant_id = str(getattr(request.state, "tenant_id", "default"))
    case = await repository.get_case(case_id)
    if case is None or case.get("tenantId") != tenant_id:
        # Absent, not forbidden -- matching `get_case`, so a guessed id confirms
        # nothing.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "CASE_NOT_FOUND",
                "message": f"Case {case_id} does not exist.",
                "retryable": False,
            },
        )

    facts = await repository.latest_case_facts(case_id)
    if _fact_value(facts, "policy_override_idempotency_key") == payload.idempotencyKey:
        # The same override arriving twice -- a retried request, a double click.
        # Answered from what was written rather than written again, and
        # deliberately before the revision check: a retry whose first attempt
        # already bumped the revision must not read as a conflict.
        return APIResponse(data=_recorded_override(case_id, case, facts), meta=_meta(request))

    route = _fact_value(facts, "policy_route")
    original = _fact_value(facts, "policy_decision")
    if route is None or original is None:
        raise _conflict(
            "POLICY_NOT_EVALUATED",
            f"Case {case_id} has no policy evaluation to override.",
        )
    if route != "STANDARD_RETURN":
        # Warranty and delivery claims are verified by Support, and an override
        # here would be a supervisor approving the claim the verification exists
        # to test. `PolicyEvaluationProjection` refuses the same combination.
        raise _conflict(
            "POLICY_ROUTE_NOT_OVERRIDABLE",
            f"Route {route} is verified by Support and carries no decision to override.",
        )

    try:
        updated = await repository.update_case(
            case_id,
            # No field is set. The revision bump *is* the write: the projection
            # changed because a fact was appended, and `CaseView` forbids extra
            # keys, so recording the override on the case document would break
            # every read of it.
            {},
            expected_version=payload.expectedRevision,
        )
    except ConcurrencyConflictError as error:
        raise _conflict(
            "CASE_REVISION_CONFLICT",
            f"Case {case_id} has moved since revision {payload.expectedRevision}.",
        ) from error

    overridden_at = datetime.now(UTC)
    await _append_override_facts(
        repository,
        case_id=case_id,
        payload=payload,
        actor=actor,
        overridden_at=overridden_at,
        original_decision=original,
    )

    notice = PolicyOverrideNotice(
        override_decision=payload.overrideDecision,
        reason_code=payload.reasonCode,
        actor=actor,
        overridden_at_iso=overridden_at.isoformat(),
        reason=payload.reason,
        idempotency_key=payload.idempotencyKey,
    )
    return APIResponse(
        data=PolicyOverrideResult(
            caseId=case_id,
            revision=int(updated["version"]),
            originalDecision=original,
            effectiveDecision=payload.overrideDecision,
            override=PolicyOverrideDetail(
                overrideDecision=payload.overrideDecision,
                reasonCode=payload.reasonCode,
                reason=payload.reason,
                actor=actor,
                overriddenAt=overridden_at.isoformat(),
            ),
            signalDelivered=await _signal_override(request, case_id, notice),
        ),
        meta=_meta(request),
    )


async def _append_override_facts(
    repository: Any,
    *,
    case_id: str,
    payload: PolicyOverrideRequest,
    actor: str,
    overridden_at: datetime,
    original_decision: str,
) -> None:
    """One fact per field, under ids the idempotency key derives.

    Derived ids rather than fresh uuids so that a retry of the same override
    lands on the insert-only log as the same entry. The original decision is
    copied here as well: the log already holds it, and copying it into the
    override is what keeps the pairing legible after a later re-evaluation
    appends a second `policy_decision`.
    """
    values: tuple[tuple[str, Any], ...] = (
        ("policy_override_decision", payload.overrideDecision),
        ("policy_override_reason_code", payload.reasonCode),
        ("policy_override_reason", payload.reason),
        ("policy_override_actor", actor),
        ("policy_override_at", overridden_at.isoformat()),
        ("policy_override_original_decision", original_decision),
        ("policy_override_idempotency_key", payload.idempotencyKey),
        # Appended over the evaluator's own, so the projection resolves the
        # override without anything having overwritten the original.
        ("policy_effective_decision", payload.overrideDecision),
    )
    for name, value in values:
        if value is None:
            continue
        await repository.append_case_fact(
            fact_id=f"{name}-{payload.idempotencyKey}",
            case_id=case_id,
            fact_name=name,
            value=value,
            agent_id="policy-override",
            channel=FactChannel.SYSTEM,
            acquisition_method=FactAcquisition.STATED,
            source_system="RETURN_PLATFORM_CONSOLE",
            source_path="POLICY_OVERRIDE",
            observed_at=overridden_at,
        )


def _recorded_override(
    case_id: str, case: dict[str, Any], facts: dict[str, dict[str, Any]]
) -> PolicyOverrideResult:
    """The override already on the log, for an idempotent replay."""
    decision = _fact_value(facts, "policy_override_decision") or ""
    return PolicyOverrideResult(
        caseId=case_id,
        revision=int(case.get("version", 0)),
        originalDecision=(
            _fact_value(facts, "policy_override_original_decision")
            or _fact_value(facts, "policy_decision")
            or ""
        ),
        effectiveDecision=_fact_value(facts, "policy_effective_decision") or decision,
        override=PolicyOverrideDetail(
            overrideDecision=decision,
            reasonCode=_fact_value(facts, "policy_override_reason_code") or "",
            reason=_fact_value(facts, "policy_override_reason"),
            actor=_fact_value(facts, "policy_override_actor") or "",
            overriddenAt=_fact_value(facts, "policy_override_at") or "",
        ),
        signalDelivered=True,
    )


async def _signal_override(request: Request, case_id: str, notice: PolicyOverrideNotice) -> bool:
    """Tell the running workflow, and say whether it heard.

    Never raises. The override is already durable by the time this runs, and a
    Temporal outage must not turn an audited supervisor decision into a 500 the
    caller reads as "nothing happened".
    """
    client = _temporal_client(request)
    if client is None:
        return False
    try:
        handle = client.get_workflow_handle(return_case_workflow_id(case_id))
        await handle.signal(_POLICY_OVERRIDE_SIGNAL, notice)
    except Exception:  # noqa: BLE001 - see the docstring
        logger.warning(
            "policy_override_signal_undelivered", extra={"case_id": case_id}, exc_info=True
        )
        return False
    return True
