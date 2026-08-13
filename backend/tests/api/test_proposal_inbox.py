"""`/api/proposals` — the one inbox, and the grants that gate it.

The point of the surface is that a schema change, a configuration change and an
improvement all arrive in the same queue, so that is what is asserted: three
types listed together, filterable, and decidable through one set of handlers.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient

from return_platform.api.proposals import router
from return_platform.platform.governance.ports import ActivationReceipt
from return_platform.platform.governance.proposal import Proposal, ProposalStatus, ProposalType
from return_platform.security import roles as r
from return_platform.security.principal import Principal
from tests.governance_doubles import build_test_kernel

NOW = datetime(2026, 8, 13, 10, 0, tzinfo=UTC)


class StubActivator:
    def __init__(self) -> None:
        self.calls: list[Mapping[str, Any]] = []

    async def activate(
        self,
        proposal: Proposal,
        *,
        actor: str,
        occurred_at: datetime,
        parameters: Mapping[str, Any],
    ) -> ActivationReceipt:
        del proposal, actor, occurred_at
        self.calls.append(dict(parameters))
        return ActivationReceipt(reference="release-9", detail="applied")


def _app(roles: frozenset[str]) -> tuple[FastAPI, StubActivator]:
    app = FastAPI()
    app.include_router(router)
    activator = StubActivator()
    kernel, store, audit = build_test_kernel(
        {
            ProposalType.IMPROVEMENT: activator,
            ProposalType.CONFIGURATION: activator,
        }
    )
    app.state.proposal_kernel = kernel
    app.state.governance_store = store
    app.state.governance_audit = audit

    @app.middleware("http")
    async def _principal(request: Request, call_next: Any) -> Response:
        request.state.principal = Principal(subject="operator", roles=roles)
        response: Response = await call_next(request)
        return response

    return app, activator


@pytest.fixture
def admin_client() -> TestClient:
    app, _ = _app(frozenset({r.CONSOLE_ADMIN}))
    return TestClient(app)


async def _seed(app: FastAPI) -> dict[ProposalType, str]:
    kernel = app.state.proposal_kernel
    ids: dict[ProposalType, str] = {}
    for proposal_type, subject, before, after in (
        (ProposalType.GRAPH_SCHEMA, "draft-1", {}, {"entities": {"Order": {"label": "Order"}}}),
        (
            ProposalType.CONFIGURATION,
            "agent.learning",
            {"agent": {"enabled": True}},
            {"agent": {"enabled": False}},
        ),
        (
            ProposalType.IMPROVEMENT,
            "FDB-1",
            {"returns": {"reminders": {"max_reminders": 3}}},
            {"returns": {"reminders": {"max_reminders": 2}}},
        ),
    ):
        proposal = await kernel.submit(
            proposal_type=proposal_type,
            subject_id=subject,
            title=f"{proposal_type} change",
            before=before,
            after=after,
            proposed_by="proposer",
            occurred_at=NOW,
        )
        proposal = await kernel.validate(
            proposal.proposal_id, receipt="receipt", actor="proposer", occurred_at=NOW
        )
        proposal = await kernel.submit_for_review(
            proposal.proposal_id, actor="proposer", occurred_at=NOW
        )
        ids[proposal_type] = proposal.proposal_id
    return ids


@pytest.mark.asyncio
async def test_all_three_kinds_of_change_arrive_in_one_queue(admin_client: TestClient) -> None:
    await _seed(admin_client.app)  # type: ignore[arg-type]
    listed = admin_client.get("/api/proposals?status=REVIEW_PENDING")
    assert listed.status_code == 200, listed.text
    types = {row["proposalType"] for row in listed.json()["data"]}
    assert types == {"GRAPH_SCHEMA", "CONFIGURATION", "IMPROVEMENT"}


@pytest.mark.asyncio
async def test_the_queue_filters_by_type(admin_client: TestClient) -> None:
    await _seed(admin_client.app)  # type: ignore[arg-type]
    rows = admin_client.get("/api/proposals?type=IMPROVEMENT").json()["data"]
    assert [row["subjectId"] for row in rows] == ["FDB-1"]


@pytest.mark.asyncio
async def test_the_row_carries_what_a_reviewer_decides_on(admin_client: TestClient) -> None:
    ids = await _seed(admin_client.app)  # type: ignore[arg-type]
    detail = admin_client.get(f"/api/proposals/{ids[ProposalType.IMPROVEMENT]}").json()["data"]
    assert detail["before"] == {"returns": {"reminders": {"max_reminders": 3}}}
    assert detail["after"] == {"returns": {"reminders": {"max_reminders": 2}}}
    assert detail["affectedKeys"] == ["returns.reminders.max_reminders"]
    assert detail["risk"] == "MEDIUM"
    assert detail["validationReceipt"] == "receipt"
    assert detail["evidenceDigest"]
    assert [entry["status"] for entry in detail["history"]] == [
        "VALIDATED",
        "REVIEW_PENDING",
    ]


@pytest.mark.asyncio
async def test_approving_then_activating_records_both(admin_client: TestClient) -> None:
    ids = await _seed(admin_client.app)  # type: ignore[arg-type]
    proposal_id = ids[ProposalType.IMPROVEMENT]

    approved = admin_client.post(f"/api/proposals/{proposal_id}/approve", json={"note": "ok"})
    assert approved.status_code == 200, approved.text
    assert approved.json()["data"]["status"] == ProposalStatus.APPROVED
    assert approved.json()["data"]["decidedBy"] == "operator"

    activated = admin_client.post(f"/api/proposals/{proposal_id}/activate", json={})
    assert activated.status_code == 200, activated.text
    assert activated.json()["data"]["status"] == ProposalStatus.ACTIVATED
    assert activated.json()["data"]["activationReference"] == "release-9"


@pytest.mark.asyncio
async def test_rejecting_is_final(admin_client: TestClient) -> None:
    ids = await _seed(admin_client.app)  # type: ignore[arg-type]
    proposal_id = ids[ProposalType.CONFIGURATION]
    assert admin_client.post(f"/api/proposals/{proposal_id}/reject", json={}).status_code == 200
    second = admin_client.post(f"/api/proposals/{proposal_id}/approve", json={})
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "INVALID_TRANSITION"


@pytest.mark.asyncio
async def test_an_editor_may_propose_and_may_not_decide() -> None:
    """The split the approval capability exists for."""
    app, _ = _app(frozenset({r.WORKSPACE_EDITOR}))
    ids = await _seed(app)
    client = TestClient(app)
    assert client.get("/api/proposals").status_code == 200
    refused = client.post(f"/api/proposals/{ids[ProposalType.IMPROVEMENT]}/approve", json={})
    assert refused.status_code == 403


@pytest.mark.asyncio
async def test_a_viewer_cannot_activate() -> None:
    app, activator = _app(frozenset({r.CONSOLE_VIEWER}))
    ids = await _seed(app)
    client = TestClient(app)
    refused = client.post(f"/api/proposals/{ids[ProposalType.IMPROVEMENT]}/activate", json={})
    assert refused.status_code == 403
    assert activator.calls == []


def test_the_inbox_reports_its_own_absence_rather_than_500(admin_client: TestClient) -> None:
    app = FastAPI()
    app.include_router(router)

    @app.middleware("http")
    async def _principal(request: Request, call_next: Any) -> Response:
        request.state.principal = Principal(subject="operator", roles=frozenset({r.CONSOLE_ADMIN}))
        response: Response = await call_next(request)
        return response

    response = TestClient(app).get("/api/proposals")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "GOVERNANCE_UNAVAILABLE"


def test_an_unknown_proposal_is_a_404(admin_client: TestClient) -> None:
    response = admin_client.get("/api/proposals/proposal-nope")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "UNKNOWN_PROPOSAL"
