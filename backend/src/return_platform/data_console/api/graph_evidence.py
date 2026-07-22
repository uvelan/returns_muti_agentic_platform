"""Read-only Data Console APIs for Customer graph evidence."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated, Final, Protocol
from uuid import UUID

from fastapi import APIRouter, Path, Query, Request, status
from fastapi.responses import JSONResponse

from return_platform.configuration.settings import Settings
from return_platform.data_platform.graph.evidence_query import (
    CustomerGraphEvidenceFullView,
    CustomerGraphEvidenceInspectionPage,
    CustomerGraphEvidenceQueryError,
    CustomerGraphEvidenceQueryErrorCode,
    CustomerGraphEvidenceQueryRepository,
    CustomerGraphEvidenceSummary,
)
from return_platform.resources import RuntimeResources
from return_platform.security.principal import Principal
from return_platform.shared.contracts import (
    APIResponse,
    PageMeta,
    ResponseMeta,
    WarningMeta,
)

__all__ = [
    "CustomerGraphEvidenceInspectionService",
    "CustomerGraphEvidenceInspectionServicePort",
    "resolve_customer_graph_evidence_inspection_service",
    "router",
]

router = APIRouter(
    prefix="/data-console/v1/graph-evidence",
    tags=["Graph Evidence"],
)

_READ_ROLES: Final = frozenset({"console_admin", "console_viewer"})
_FULL_READ_ROLES: Final = frozenset({"console_admin"})
_SOURCE: Final = "GRAPH_EVIDENCE"
_STATUS_BY_QUERY_CODE: Final = {
    CustomerGraphEvidenceQueryErrorCode.INVALID_INPUT: (status.HTTP_400_BAD_REQUEST),
    CustomerGraphEvidenceQueryErrorCode.CURSOR_INVALID: (status.HTTP_400_BAD_REQUEST),
    CustomerGraphEvidenceQueryErrorCode.AUTH_FAILED: (status.HTTP_503_SERVICE_UNAVAILABLE),
    CustomerGraphEvidenceQueryErrorCode.TIMEOUT: (status.HTTP_504_GATEWAY_TIMEOUT),
    CustomerGraphEvidenceQueryErrorCode.QUERY_FAILED: (status.HTTP_503_SERVICE_UNAVAILABLE),
    CustomerGraphEvidenceQueryErrorCode.EVIDENCE_INVALID: (status.HTTP_500_INTERNAL_SERVER_ERROR),
}


class _GraphEvidenceResolutionError(RuntimeError):
    """Raised when request-scoped graph-evidence resources are unavailable."""


class _GraphEvidenceForbiddenError(RuntimeError):
    """Raised when the principal lacks a code-owned graph-evidence role."""


class CustomerGraphEvidenceInspectionServicePort(Protocol):
    """Read-only application-service boundary used by the API."""

    async def list_summaries(
        self,
        *,
        page_size: int,
        cursor: str | None,
    ) -> CustomerGraphEvidenceInspectionPage:
        """Return one bounded summary page."""
        ...

    async def latest_summary(self) -> CustomerGraphEvidenceSummary | None:
        """Return the latest validated graph-evidence summary."""
        ...

    async def summary_by_document_id(
        self,
        document_id: str,
    ) -> CustomerGraphEvidenceSummary | None:
        """Return one summary by canonical document identity."""
        ...

    async def summary_by_sync_run_id(
        self,
        sync_run_id: UUID,
    ) -> CustomerGraphEvidenceSummary | None:
        """Return one summary by sync-run identity."""
        ...

    async def summary_by_report_digest(
        self,
        report_digest: str,
    ) -> CustomerGraphEvidenceSummary | None:
        """Return one summary by report digest."""
        ...

    async def full_by_document_id(
        self,
        document_id: str,
    ) -> CustomerGraphEvidenceFullView | None:
        """Return one complete validated evidence view."""
        ...


class CustomerGraphEvidenceInspectionService:
    """Read-only application service for graph-evidence inspection."""

    def __init__(
        self,
        repository: CustomerGraphEvidenceQueryRepository,
    ) -> None:
        """Store the fixed-query repository."""
        self._repository = repository

    async def list_summaries(
        self,
        *,
        page_size: int,
        cursor: str | None,
    ) -> CustomerGraphEvidenceInspectionPage:
        """Return one bounded summary page."""
        return await self._repository.list_summaries(
            page_size=page_size,
            cursor=cursor,
        )

    async def latest_summary(self) -> CustomerGraphEvidenceSummary | None:
        """Return the latest successful validation summary."""
        page = await self._repository.list_summaries(
            page_size=1,
            cursor=None,
        )
        return page.items[0] if page.items else None

    async def summary_by_document_id(
        self,
        document_id: str,
    ) -> CustomerGraphEvidenceSummary | None:
        """Return one summary by canonical document identity."""
        document = await self._repository.get_by_document_id(document_id)
        if document is None:
            return None
        return CustomerGraphEvidenceSummary.from_document(document)

    async def summary_by_sync_run_id(
        self,
        sync_run_id: UUID,
    ) -> CustomerGraphEvidenceSummary | None:
        """Return one summary by sync-run identity."""
        document = await self._repository.get_by_sync_run_id(sync_run_id)
        if document is None:
            return None
        return CustomerGraphEvidenceSummary.from_document(document)

    async def summary_by_report_digest(
        self,
        report_digest: str,
    ) -> CustomerGraphEvidenceSummary | None:
        """Return one summary by report digest."""
        document = await self._repository.get_by_report_digest(report_digest)
        if document is None:
            return None
        return CustomerGraphEvidenceSummary.from_document(document)

    async def full_by_document_id(
        self,
        document_id: str,
    ) -> CustomerGraphEvidenceFullView | None:
        """Return one complete developer inspection view."""
        document = await self._repository.get_by_document_id(document_id)
        if document is None:
            return None
        return CustomerGraphEvidenceFullView.from_document(document)


def resolve_customer_graph_evidence_inspection_service(
    request: Request,
) -> CustomerGraphEvidenceInspectionServicePort:
    """Resolve one request-scoped read-only inspection service."""
    resources_value: object = getattr(request.app.state, "resources", None)
    settings_value: object = getattr(request.app.state, "settings", None)
    if not isinstance(resources_value, RuntimeResources):
        raise _GraphEvidenceResolutionError
    if not isinstance(settings_value, Settings):
        raise _GraphEvidenceResolutionError
    if resources_value.mongo is None:
        raise _GraphEvidenceResolutionError
    repository = CustomerGraphEvidenceQueryRepository.from_client(
        resources_value.mongo,
        database=settings_value.mongo_database,
        collection=settings_value.graph_evidence_collection,
        operation_timeout_seconds=(settings_value.graph_evidence_query_timeout_seconds),
    )
    return CustomerGraphEvidenceInspectionService(repository)


def _request_id(request: Request) -> str:
    """Return the correlation ID without accepting arbitrary state types."""
    value: object = getattr(request.state, "correlation_id", None)
    return value if isinstance(value, str) and value else "unknown"


def _require_role(request: Request, allowed_roles: frozenset[str]) -> None:
    """Enforce one code-owned Data Console role boundary."""
    value: object = getattr(request.state, "principal", None)
    if not isinstance(value, Principal) or not value.roles.intersection(allowed_roles):
        raise _GraphEvidenceForbiddenError


def _response_meta(request: Request) -> ResponseMeta:
    """Build successful live response metadata."""
    return ResponseMeta(request_id=_request_id(request))


def _error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
) -> JSONResponse:
    """Return one safe standard error envelope."""
    payload = APIResponse[None](
        data=None,
        meta=ResponseMeta(
            request_id=_request_id(request),
            partial=True,
            warnings=(
                WarningMeta(
                    source=_SOURCE,
                    code=code,
                    message=message,
                ),
            ),
        ),
    )
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(mode="json"),
    )


def _query_error_response(
    request: Request,
    error: CustomerGraphEvidenceQueryError,
) -> JSONResponse:
    """Map one safe repository error to an HTTP envelope."""
    return _error_response(
        request,
        status_code=_STATUS_BY_QUERY_CODE[error.code],
        code=error.code.value,
        message=error.safe_message,
    )


def _not_found_response(request: Request) -> JSONResponse:
    """Return one safe evidence-not-found envelope."""
    return _error_response(
        request,
        status_code=status.HTTP_404_NOT_FOUND,
        code="EVIDENCE_NOT_FOUND",
        message="The requested graph evidence was not found.",
    )


def _forbidden_response(request: Request) -> JSONResponse:
    """Return one safe role-denied envelope."""
    return _error_response(
        request,
        status_code=status.HTTP_403_FORBIDDEN,
        code="FORBIDDEN",
        message="The principal is not authorized to inspect graph evidence.",
    )


def _unavailable_response(request: Request) -> JSONResponse:
    """Return one safe resource-resolution failure envelope."""
    return _error_response(
        request,
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        code="DEPENDENCY_UNAVAILABLE",
        message="Platform graph-evidence storage is unavailable.",
    )


async def _execute_summary_lookup(
    request: Request,
    operation: Callable[[], Awaitable[CustomerGraphEvidenceSummary | None]],
) -> APIResponse[CustomerGraphEvidenceSummary] | JSONResponse:
    """Execute one summary lookup with safe boundary mapping."""
    try:
        summary = await operation()
    except CustomerGraphEvidenceQueryError as error:
        return _query_error_response(request, error)
    if summary is None:
        return _not_found_response(request)
    return APIResponse(data=summary, meta=_response_meta(request))


@router.get(
    "",
    response_model=APIResponse[tuple[CustomerGraphEvidenceSummary, ...]],
)
async def list_customer_graph_evidence(
    request: Request,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    cursor: Annotated[str | None, Query(min_length=1, max_length=512)] = None,
) -> APIResponse[tuple[CustomerGraphEvidenceSummary, ...]] | JSONResponse:
    """List newest Customer graph evidence through bounded seek pagination."""
    try:
        _require_role(request, _READ_ROLES)
        service = resolve_customer_graph_evidence_inspection_service(request)
        page = await service.list_summaries(
            page_size=page_size,
            cursor=cursor,
        )
    except _GraphEvidenceForbiddenError:
        return _forbidden_response(request)
    except _GraphEvidenceResolutionError:
        return _unavailable_response(request)
    except CustomerGraphEvidenceQueryError as error:
        return _query_error_response(request, error)
    return APIResponse(
        data=page.items,
        page=PageMeta(
            next_cursor=page.next_cursor,
            has_more=page.has_more,
            page_size=page.page_size,
        ),
        meta=_response_meta(request),
    )


@router.get(
    "/validation/latest",
    response_model=APIResponse[CustomerGraphEvidenceSummary],
)
async def get_latest_customer_graph_validation(
    request: Request,
) -> APIResponse[CustomerGraphEvidenceSummary] | JSONResponse:
    """Return the latest successful Customer graph validation evidence."""
    try:
        _require_role(request, _READ_ROLES)
        service = resolve_customer_graph_evidence_inspection_service(request)
    except _GraphEvidenceForbiddenError:
        return _forbidden_response(request)
    except _GraphEvidenceResolutionError:
        return _unavailable_response(request)
    return await _execute_summary_lookup(request, service.latest_summary)


@router.get(
    "/documents/{document_id}/full",
    response_model=APIResponse[CustomerGraphEvidenceFullView],
)
async def get_full_customer_graph_evidence(
    request: Request,
    document_id: Annotated[str, Path(min_length=59, max_length=59)],
) -> APIResponse[CustomerGraphEvidenceFullView] | JSONResponse:
    """Return complete evidence for an authorized developer inspection."""
    try:
        _require_role(request, _FULL_READ_ROLES)
        service = resolve_customer_graph_evidence_inspection_service(request)
        evidence = await service.full_by_document_id(document_id)
    except _GraphEvidenceForbiddenError:
        return _forbidden_response(request)
    except _GraphEvidenceResolutionError:
        return _unavailable_response(request)
    except CustomerGraphEvidenceQueryError as error:
        return _query_error_response(request, error)
    if evidence is None:
        return _not_found_response(request)
    return APIResponse(data=evidence, meta=_response_meta(request))


@router.get(
    "/documents/{document_id}",
    response_model=APIResponse[CustomerGraphEvidenceSummary],
)
async def get_customer_graph_evidence_by_document_id(
    request: Request,
    document_id: Annotated[str, Path(min_length=59, max_length=59)],
) -> APIResponse[CustomerGraphEvidenceSummary] | JSONResponse:
    """Return one graph-evidence summary by canonical document ID."""
    try:
        _require_role(request, _READ_ROLES)
        service = resolve_customer_graph_evidence_inspection_service(request)
    except _GraphEvidenceForbiddenError:
        return _forbidden_response(request)
    except _GraphEvidenceResolutionError:
        return _unavailable_response(request)
    return await _execute_summary_lookup(
        request,
        lambda: service.summary_by_document_id(document_id),
    )


@router.get(
    "/sync-runs/{sync_run_id}",
    response_model=APIResponse[CustomerGraphEvidenceSummary],
)
async def get_customer_graph_evidence_by_sync_run_id(
    request: Request,
    sync_run_id: UUID,
) -> APIResponse[CustomerGraphEvidenceSummary] | JSONResponse:
    """Return one graph-evidence summary by sync-run identity."""
    try:
        _require_role(request, _READ_ROLES)
        service = resolve_customer_graph_evidence_inspection_service(request)
    except _GraphEvidenceForbiddenError:
        return _forbidden_response(request)
    except _GraphEvidenceResolutionError:
        return _unavailable_response(request)
    return await _execute_summary_lookup(
        request,
        lambda: service.summary_by_sync_run_id(sync_run_id),
    )


@router.get(
    "/reports/{report_digest}",
    response_model=APIResponse[CustomerGraphEvidenceSummary],
)
async def get_customer_graph_evidence_by_report_digest(
    request: Request,
    report_digest: Annotated[
        str,
        Path(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"),
    ],
) -> APIResponse[CustomerGraphEvidenceSummary] | JSONResponse:
    """Return one graph-evidence summary by immutable report digest."""
    try:
        _require_role(request, _READ_ROLES)
        service = resolve_customer_graph_evidence_inspection_service(request)
    except _GraphEvidenceForbiddenError:
        return _forbidden_response(request)
    except _GraphEvidenceResolutionError:
        return _unavailable_response(request)
    return await _execute_summary_lookup(
        request,
        lambda: service.summary_by_report_digest(report_digest),
    )
