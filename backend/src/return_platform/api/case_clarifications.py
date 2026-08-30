"""The clarification answer endpoint (contracts.md sect. 9's endpoint list).

`POST /api/v1/cases/{case_id}/clarifications/{clarification_id}/answer`,
capability `RETURNS_SUPPORT_ACT` plus the case-access check every case route
here applies.

The handler does exactly one durable thing: it records a command and queues its
delivery, in one transaction, through `DurableCaseCommandStore`. It does **not**
write the fact, does not compose the relay, and does not signal Temporal --
sect. 7 is explicit that *REST never signals Temporal directly*, and the reason
is that a handler which signalled would have to choose between reporting success
before the signal landed and holding an HTTP connection open across a workflow
round trip. The command record is the durable promise; the dispatcher keeps it.

**404, never 403**, for a case the principal may not see: the pattern every case
route in this tree follows, because a 403 confirms the case exists to somebody
who should not know that.

**The answer text is data.** Nothing here branches on it, and the only thing
derived from it is a length. It reaches Support later through
`compose_clarification_relay`, which neutralises and bounds it; it reaches the
fact log verbatim, which is the audit record.
"""

from __future__ import annotations

from typing import Annotated, Final, cast

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, StringConstraints

from return_platform.operations.case_commands import (
    CaseCommandKind,
    CommandIdempotencyConflictError,
    DurableCaseCommandStore,
)
from return_platform.operations.repository import OperationalRepository
from return_platform.operations.return_support.clarification import (
    clarification_answer_signal_id,
)
from return_platform.resources import RuntimeResources
from return_platform.security.authorization import require_capability
from return_platform.security.capabilities import RETURNS_SUPPORT_ACT
from return_platform.shared.contracts import APIResponse, ResponseMeta
from return_platform.workflows.return_case_workflow import return_case_workflow_id

router = APIRouter(prefix="/api/v1/cases", tags=["Returns Support"])

require_support_act = require_capability(RETURNS_SUPPORT_ACT)

#: The two things an associate may do with an unmatched artifact (sect. 9's
#: "map-or-reject choice"). A closed set on the *request model*, so an
#: unrecognised choice is a 422 from the schema rather than a branch that has to
#: decide what an unknown choice means.
MAP_CHOICE: Final = "map"
REJECT_CHOICE: Final = "reject"

#: Longest answer accepted. Refused rather than truncated, for the reason
#: `support_ingress.enforce_limits` gives about message bodies: the cut half of
#: a truncated answer may be the part that identified the record.
MAX_ANSWER_CHARACTERS: Final = 4_000


class ClarificationAnswerRequest(BaseModel):
    """What the associate submits. Deliberately small.

    `extra="forbid"`: a field this endpoint does not know about is a client
    believing in behaviour that does not exist, and accepting it silently is how
    that belief survives to a release where it matters.

    There is no `actorId` and no `caseId` here. The actor comes from the
    capability check and the case from the path -- a body that could name either
    would be a body that could answer somebody else's clarification.
    """

    model_config = ConfigDict(extra="forbid")

    answerText: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_ANSWER_CHARACTERS),
    ]
    #: `map` binds the artifact to `returnRecordId`; `reject` says it belongs to
    #: none of the case's records. Absent for a plain question.
    resolutionChoice: Annotated[str | None, StringConstraints(pattern=r"^(map|reject)$")] = None
    returnRecordId: str | None = None


class ClarificationAnswerAcceptedView(BaseModel):
    """The receipt for a durably recorded answer.

    Says what committed. There is no `relayed` field and there will not be one:
    the relay happens in an activity after the signal lands, and a field here
    claiming it would be this handler reporting work it did not wait for.
    """

    model_config = ConfigDict(extra="forbid")

    caseId: str
    clarificationId: str
    commandId: str
    signalId: str
    outboxCommandId: str
    #: True when this exact answer was already recorded -- a double-submitted
    #: form, or a client retry. Still a 202: one answer is on file either way.
    duplicate: bool


def _meta(request: Request) -> ResponseMeta:
    return ResponseMeta(request_id=cast(str, getattr(request.state, "correlation_id", "unknown")))


def _resources(request: Request) -> RuntimeResources:
    resources = getattr(request.app.state, "resources", None)
    if not isinstance(resources, RuntimeResources) or resources.mongo is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "CASE_COMMAND_STORE_UNAVAILABLE",
                "message": "The answer cannot be recorded durably from this process.",
                "retryable": True,
            },
        )
    return resources


def _repository(request: Request) -> OperationalRepository:
    """The case read. One seam, so a test can substitute the store layer --
    the same shape `api/support_ingress.py` uses, and for the same reason."""
    resources = _resources(request)
    return OperationalRepository(resources.mongo, resources.settings, resources.source_mongo)


def _command_store(request: Request) -> DurableCaseCommandStore:
    resources = _resources(request)
    return DurableCaseCommandStore(resources.mongo, resources.settings)


def _not_found() -> HTTPException:
    """One answer for "no such case" and "not your case".

    Telling a caller which of the two they hit tells them the case exists.
    """
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "code": "CASE_CLARIFICATION_NOT_FOUND",
            "message": "No such clarification on a case you can act on.",
        },
    )


@router.post(
    "/{case_id}/clarifications/{clarification_id}/answer",
    response_model=APIResponse[ClarificationAnswerAcceptedView],
    status_code=status.HTTP_202_ACCEPTED,
)
async def answer_clarification(
    case_id: str,
    clarification_id: str,
    payload: ClarificationAnswerRequest,
    request: Request,
    actor_id: str = Depends(require_support_act),
) -> APIResponse[ClarificationAnswerAcceptedView]:
    """Record one answer to one clarification.

    `202`, never `200`: when this returns, a command is on file and a delivery
    row is queued. The fact, the relay to Support and the deadline reset all
    happen after the signal reaches the workflow.

    A `map` choice must name the record it maps to. Refused here rather than
    resolved later, because "map this artifact to nothing" is not a decision the
    associate can have meant, and a later step inventing a record for it is
    exactly the create-a-record-from-a-loose-artifact behaviour sect. 4 forbids.
    """
    if payload.resolutionChoice == MAP_CHOICE and not payload.returnRecordId:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "CLARIFICATION_MAP_WITHOUT_RECORD",
                "message": (
                    "A 'map' answer must name the returnRecordId it maps to. Refused "
                    "rather than resolved later: a loose artifact never creates a record."
                ),
                "retryable": False,
            },
        )

    case = await _repository(request).get_case(case_id)
    tenant_id = str(getattr(request.state, "tenant_id", "default"))
    if case is None or case.get("tenantId") != tenant_id:
        raise _not_found()

    store = _command_store(request)
    signal_id = clarification_answer_signal_id(
        case_id=case_id, clarification_id=clarification_id
    )
    try:
        receipt = await store.record_command(
            case_id=case_id,
            workflow_id=return_case_workflow_id(case_id),
            kind=CaseCommandKind.CLARIFICATION_ANSWERED,
            signal_id=signal_id,
            # Server-stamped (sect. 4). The body cannot carry one.
            actor_id=actor_id,
            payload={
                "clarification_id": clarification_id,
                "case_id": case_id,
                "answer_text": payload.answerText,
                "resolution_choice": payload.resolutionChoice,
                "return_record_id": payload.returnRecordId,
                "answered_by": actor_id,
            },
            return_record_id=payload.returnRecordId,
            correlation_id=_meta(request).request_id,
        )
    except CommandIdempotencyConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "CLARIFICATION_ALREADY_ANSWERED",
                "message": (
                    "This clarification was already answered with different words. "
                    "The first answer stands; it is on the case and has been relayed."
                ),
                "retryable": False,
            },
        ) from error

    return APIResponse(
        data=ClarificationAnswerAcceptedView(
            caseId=case_id,
            clarificationId=clarification_id,
            commandId=receipt.command_id,
            signalId=receipt.signal_id,
            outboxCommandId=receipt.outbox_command_id,
            duplicate=receipt.duplicate,
        ),
        meta=_meta(request),
    )
