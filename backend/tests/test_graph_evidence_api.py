"""API contract tests for read-only Customer graph-evidence inspection."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from fastapi import FastAPI, Request, Response, status
from fastapi.testclient import TestClient

from return_platform.data_console.api import graph_evidence
from return_platform.data_platform.graph.evidence_query import (
    CustomerGraphEvidenceFullView,
    CustomerGraphEvidenceInspectionPage,
    CustomerGraphEvidenceQueryError,
    CustomerGraphEvidenceQueryErrorCode,
    CustomerGraphEvidenceSummary,
)
from return_platform.security.principal import Principal

_DOCUMENT_ID = "CUSTOMER_GRAPH_SANDBOX:d084d10c-5bdf-4002-befb-8ccb9948f9e7"
_SYNC_RUN_ID = UUID("d084d10c-5bdf-4002-befb-8ccb9948f9e7")
_REPORT_DIGEST = "75b63cf87a1742e93dd05eb2542d6bfe17f3b345ffe3542d73fac32d664b33c8"
_DOCUMENT_DIGEST = "6ce23e2568171b3f53827dfb8b822f4c4cd2cec60080a6c959326136bdb81f5b"
_REQUEST_ID = "11111111-1111-4111-8111-111111111111"


def _summary() -> CustomerGraphEvidenceSummary:
    """Return one strict controlled graph-evidence summary."""
    return CustomerGraphEvidenceSummary(
        schema_version="1.0",
        evidence_type="CUSTOMER_GRAPH_SANDBOX_RUN",
        document_id=_DOCUMENT_ID,
        report_digest=_REPORT_DIGEST,
        document_digest=_DOCUMENT_DIGEST,
        sync_run_id=_SYNC_RUN_ID,
        executed_at=datetime(2026, 7, 22, 11, 0, tzinfo=UTC),
        executed_at_epoch_microseconds=1_784_718_000_000_000,
        source_document_id="P100",
        source_hash="a" * 64,
        configuration_digest="b" * 64,
        execution_plan_digest="c" * 64,
        command_batch_digest="d" * 64,
        evidence_classification="SANDBOX_VALIDATED",
        expected_customer_count=1,
        expected_customer_account_count=2,
        expected_relationship_count=2,
        idempotent=True,
    )


def _full_view() -> CustomerGraphEvidenceFullView:
    """Return one complete controlled developer inspection view."""
    return CustomerGraphEvidenceFullView(
        summary=_summary(),
        schema_evidence_digest="e" * 64,
        first_write_evidence_digest="1" * 64,
        second_write_evidence_digest="2" * 64,
        first_readback_evidence_digest="3" * 64,
        second_readback_evidence_digest="4" * 64,
        idempotency_evidence_digest="5" * 64,
        report_payload={
            "evidence_classification": "SANDBOX_VALIDATED",
            "process_exit_code": 0,
        },
    )


class _FakeInspectionService:
    """Controlled service implementing the API inspection protocol."""

    def __init__(
        self,
        *,
        found: bool = True,
        query_error: CustomerGraphEvidenceQueryErrorCode | None = None,
    ) -> None:
        """Store the controlled lookup behavior."""
        self.found = found
        self.query_error = query_error
        self.list_calls: list[tuple[int, str | None]] = []

    def _maybe_raise(self) -> None:
        """Raise the configured safe query error."""
        if self.query_error is not None:
            raise CustomerGraphEvidenceQueryError(self.query_error)

    async def list_summaries(
        self,
        *,
        page_size: int,
        cursor: str | None,
    ) -> CustomerGraphEvidenceInspectionPage:
        """Return one controlled summary page."""
        self._maybe_raise()
        self.list_calls.append((page_size, cursor))
        return CustomerGraphEvidenceInspectionPage(
            items=(_summary(),),
            next_cursor="next-page-cursor",
            has_more=True,
            page_size=page_size,
        )

    async def latest_summary(self) -> CustomerGraphEvidenceSummary | None:
        """Return the controlled latest summary."""
        self._maybe_raise()
        return _summary() if self.found else None

    async def summary_by_document_id(
        self,
        document_id: str,
    ) -> CustomerGraphEvidenceSummary | None:
        """Return one controlled document summary."""
        self._maybe_raise()
        assert document_id == _DOCUMENT_ID
        return _summary() if self.found else None

    async def summary_by_sync_run_id(
        self,
        sync_run_id: UUID,
    ) -> CustomerGraphEvidenceSummary | None:
        """Return one controlled sync-run summary."""
        self._maybe_raise()
        assert sync_run_id == _SYNC_RUN_ID
        return _summary() if self.found else None

    async def summary_by_report_digest(
        self,
        report_digest: str,
    ) -> CustomerGraphEvidenceSummary | None:
        """Return one controlled report summary."""
        self._maybe_raise()
        assert report_digest == _REPORT_DIGEST
        return _summary() if self.found else None

    async def full_by_document_id(
        self,
        document_id: str,
    ) -> CustomerGraphEvidenceFullView | None:
        """Return one controlled full evidence view."""
        self._maybe_raise()
        assert document_id == _DOCUMENT_ID
        return _full_view() if self.found else None


def _app(
    monkeypatch: pytest.MonkeyPatch,
    *,
    role: str,
    service: _FakeInspectionService,
) -> FastAPI:
    """Build one isolated API app with controlled principal and service."""
    app = FastAPI()

    @app.middleware("http")
    async def inject_request_context(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request.state.correlation_id = _REQUEST_ID
        request.state.principal = Principal(
            subject="test-principal",
            roles=frozenset({role}),
        )
        return await call_next(request)

    app.include_router(graph_evidence.router)
    monkeypatch.setattr(
        graph_evidence,
        "resolve_customer_graph_evidence_inspection_service",
        lambda _request: service,
    )
    return app


def test_lists_bounded_graph_evidence_in_standard_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return summary-only evidence with pagination and correlation metadata."""
    service = _FakeInspectionService()
    client = TestClient(_app(monkeypatch, role="console_viewer", service=service))

    response = client.get(
        "/data-console/v1/graph-evidence",
        params={"page_size": 25},
    )

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()
    assert payload["data"][0]["document_id"] == _DOCUMENT_ID
    assert "report_payload" not in payload["data"][0]
    assert payload["page"] == {
        "next_cursor": "next-page-cursor",
        "has_more": True,
        "page_size": 25,
    }
    assert payload["meta"]["request_id"] == _REQUEST_ID
    assert service.list_calls == [(25, None)]


def test_latest_validation_and_exact_lookup_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Expose latest, document, run, and report lookups as GET-only routes."""
    service = _FakeInspectionService()
    client = TestClient(_app(monkeypatch, role="console_viewer", service=service))

    paths = (
        "/data-console/v1/graph-evidence/validation/latest",
        f"/data-console/v1/graph-evidence/documents/{_DOCUMENT_ID}",
        f"/data-console/v1/graph-evidence/sync-runs/{_SYNC_RUN_ID}",
        f"/data-console/v1/graph-evidence/reports/{_REPORT_DIGEST}",
    )

    for path in paths:
        response = client.get(path)
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["data"]["report_digest"] == _REPORT_DIGEST

    assert client.post(paths[0]).status_code == status.HTTP_405_METHOD_NOT_ALLOWED


def test_full_evidence_requires_console_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject full report disclosure to summary-only console viewers."""
    service = _FakeInspectionService()
    client = TestClient(_app(monkeypatch, role="console_viewer", service=service))

    response = client.get(f"/data-console/v1/graph-evidence/documents/{_DOCUMENT_ID}/full")

    assert response.status_code == status.HTTP_403_FORBIDDEN
    payload = response.json()
    assert payload["data"] is None
    assert payload["meta"]["warnings"][0]["code"] == "FORBIDDEN"


def test_console_admin_can_read_full_digest_bound_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return full evidence only to the code-owned administrator role."""
    service = _FakeInspectionService()
    client = TestClient(_app(monkeypatch, role="console_admin", service=service))

    response = client.get(f"/data-console/v1/graph-evidence/documents/{_DOCUMENT_ID}/full")

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()["data"]
    assert payload["summary"]["document_id"] == _DOCUMENT_ID
    assert payload["report_payload"]["process_exit_code"] == 0


def test_missing_and_timeout_failures_are_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Map absent evidence and bounded query timeout without raw errors."""
    missing_client = TestClient(
        _app(
            monkeypatch,
            role="console_viewer",
            service=_FakeInspectionService(found=False),
        )
    )
    missing = missing_client.get(f"/data-console/v1/graph-evidence/documents/{_DOCUMENT_ID}")
    assert missing.status_code == status.HTTP_404_NOT_FOUND
    assert missing.json()["meta"]["warnings"][0]["code"] == ("EVIDENCE_NOT_FOUND")

    timeout_client = TestClient(
        _app(
            monkeypatch,
            role="console_viewer",
            service=_FakeInspectionService(query_error=CustomerGraphEvidenceQueryErrorCode.TIMEOUT),
        )
    )
    timeout = timeout_client.get("/data-console/v1/graph-evidence/validation/latest")
    assert timeout.status_code == status.HTTP_504_GATEWAY_TIMEOUT
    assert timeout.json()["meta"]["warnings"][0]["code"] == "TIMEOUT"


def test_router_exposes_no_mutation_routes() -> None:
    """Expose read-only routes and define no mutation handler."""
    source_path = (
        Path(__file__).parents[1] / "src/return_platform/data_console/api/graph_evidence.py"
    )
    source = source_path.read_text(encoding="utf-8")

    assert "@router.post" not in source
    assert "@router.put" not in source
    assert "@router.patch" not in source
    assert "@router.delete" not in source


def _summary_projection() -> dict[str, object]:
    """Return one fixed MongoDB summary projection document."""
    summary = _summary()
    executed_at = summary.executed_at.isoformat(timespec="microseconds").replace("+00:00", "Z")
    return {
        "_id": summary.document_id,
        "schema_version": summary.schema_version,
        "evidence_type": summary.evidence_type,
        "report_digest": summary.report_digest,
        "document_digest": summary.document_digest,
        "sync_run_id": str(summary.sync_run_id),
        "executed_at": executed_at,
        "executed_at_epoch_microseconds": (summary.executed_at_epoch_microseconds),
        "source_document_id": summary.source_document_id,
        "source_hash": summary.source_hash,
        "configuration_digest": summary.configuration_digest,
        "execution_plan_digest": summary.execution_plan_digest,
        "command_batch_digest": summary.command_batch_digest,
        "report_payload": {
            "evidence_classification": summary.evidence_classification,
            "expected_customer_count": summary.expected_customer_count,
            "expected_customer_account_count": (summary.expected_customer_account_count),
            "expected_relationship_count": (summary.expected_relationship_count),
            "execution": {
                "idempotency": {
                    "idempotent": summary.idempotent,
                }
            },
        },
    }


@pytest.mark.parametrize(
    ("field_path", "invalid_value"),
    (
        (("evidence_type",), "UNAPPROVED_EVIDENCE_TYPE"),
        (
            ("report_payload", "evidence_classification"),
            "PRODUCTION_VALIDATED",
        ),
    ),
)
def test_projection_rejects_unapproved_literal_values(
    field_path: tuple[str, ...],
    invalid_value: str,
) -> None:
    """Reject stored literals outside the code-owned evidence contract."""
    projection = _summary_projection()
    if len(field_path) == 1:
        projection[field_path[0]] = invalid_value
    else:
        report_payload = projection["report_payload"]
        assert isinstance(report_payload, dict)
        report_payload[field_path[1]] = invalid_value

    with pytest.raises(CustomerGraphEvidenceQueryError) as exc_info:
        CustomerGraphEvidenceSummary.from_projection(projection)

    assert exc_info.value.code is (CustomerGraphEvidenceQueryErrorCode.EVIDENCE_INVALID)
