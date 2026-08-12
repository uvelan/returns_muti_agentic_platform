"""The case read surface: one return, its RMAs, and which items each covers.

Nesting items *inside* their return record is the point of the shape. A flat
`{records: [...], items: [...]}` would leave the client to join them, and the
console's previous join -- matching an order reference across two collections in
the browser -- is exactly the defect this replaces. A response that cannot
express "label LBL-1 belongs to RMA-2" cannot be misread as saying it does.

Unassigned items are returned separately rather than folded into the first
record. The two are learned at different times: the associate names the lines
before Support has said how many RMAs those lines become, and putting them
somewhere plausible in the meantime would be a guess the UI would render as
fact.
"""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict

from return_platform.operations.models import CaseFactView, CaseView, ReturnRecordView
from return_platform.operations.repository import resolve_operational_repository
from return_platform.security.authorization import require_read_roles
from return_platform.security.principal import Principal
from return_platform.shared.contracts import APIResponse, ResponseMeta

router = APIRouter(prefix="/api/cases", tags=["Cases"])


class CaseReturnItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    returnItemId: str
    orderLineReference: str
    productReference: str | None = None
    quantity: int = 1
    reason: str | None = None
    condition: str | None = None
    packageReference: str | None = None


class CaseReturnRecord(BaseModel):
    """One RMA and everything that belongs to it rather than to the case."""

    model_config = ConfigDict(extra="forbid")

    record: ReturnRecordView
    items: tuple[CaseReturnItem, ...]


class CaseDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case: CaseView
    returnRecords: tuple[CaseReturnRecord, ...]
    # Items the associate has named that no RMA covers yet.
    unassignedItems: tuple[CaseReturnItem, ...]
    facts: tuple[CaseFactView, ...]


class CaseSummary(BaseModel):
    """A row in the associate's case list.

    Carries the counts rather than the records: a list of twenty cases must not
    pull every RMA and item to render twenty lines.
    """

    model_config = ConfigDict(extra="forbid")

    caseId: str
    status: str
    confirmedOrderReference: str | None = None
    channelAConversationId: str | None = None
    returnRecordCount: int
    updatedAt: str


def _meta(request: Request) -> ResponseMeta:
    request_id = getattr(request.state, "correlation_id", "unknown")
    return ResponseMeta(request_id=request_id if isinstance(request_id, str) else "unknown")


def _stored(document: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in document.items() if key != "_id"}


def _item(document: dict[str, Any]) -> CaseReturnItem:
    return CaseReturnItem(
        returnItemId=str(document["returnItemId"]),
        orderLineReference=str(document.get("orderLineId", "")),
        productReference=document.get("productReference"),
        quantity=int(document.get("quantity", 1)),
        reason=document.get("reason"),
        condition=document.get("condition"),
        packageReference=document.get("packageReference"),
    )


@router.get("", response_model=APIResponse[list[CaseSummary]])
async def list_cases(
    request: Request,
    _actor_id: str = Depends(require_read_roles),
) -> APIResponse[list[CaseSummary]]:
    """The caller's own cases, newest first.

    Scoped by tenant and principal in the query, for the same reason the
    conversation history is: a case carries the customer's order and whatever
    the associate typed about them, and "list every case" is not a read anyone
    should be able to make by accident.
    """
    repository = resolve_operational_repository(request)
    principal = cast(Principal, request.state.principal)
    cases = await repository.list_cases_for_principal(
        tenant_id=str(getattr(request.state, "tenant_id", "default")),
        principal_id=principal.subject,
    )
    summaries = [
        CaseSummary(
            caseId=str(case["caseId"]),
            status=str(case["status"]),
            confirmedOrderReference=case.get("confirmedOrderReference"),
            channelAConversationId=case.get("channelAConversationId"),
            returnRecordCount=len(await repository.list_return_records(str(case["caseId"]))),
            updatedAt=str(case["updatedAt"]),
        )
        for case in cases
    ]
    return APIResponse(data=summaries, meta=_meta(request))


@router.get("/{case_id}", response_model=APIResponse[CaseDetail])
async def get_case(
    case_id: str,
    request: Request,
    _actor_id: str = Depends(require_read_roles),
) -> APIResponse[CaseDetail]:
    """One case, with each RMA carrying the items it covers."""
    repository = resolve_operational_repository(request)
    case = await repository.get_case(case_id)
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "CASE_NOT_FOUND",
                "message": f"Case {case_id} does not exist.",
                "retryable": False,
            },
        )

    items = await repository.list_case_return_items(case_id)
    by_record: dict[str, list[dict[str, Any]]] = {}
    unassigned: list[dict[str, Any]] = []
    for document in items:
        record_id = document.get("returnRecordId")
        if isinstance(record_id, str) and record_id:
            by_record.setdefault(record_id, []).append(document)
        else:
            unassigned.append(document)

    records = tuple(
        CaseReturnRecord(
            record=ReturnRecordView.model_validate(_stored(record)),
            # Only this record's items. The grouping is done here, once, rather
            # than left to every consumer to get right.
            items=tuple(_item(item) for item in by_record.get(str(record["returnRecordId"]), ())),
        )
        for record in await repository.list_return_records(case_id)
    )

    return APIResponse(
        data=CaseDetail(
            case=CaseView.model_validate(_stored(case)),
            returnRecords=records,
            unassignedItems=tuple(_item(item) for item in unassigned),
            facts=tuple(
                CaseFactView.model_validate(_stored(fact))
                for fact in await repository.list_case_facts(case_id)
            ),
        ),
        meta=_meta(request),
    )
