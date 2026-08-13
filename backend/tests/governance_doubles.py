"""In-memory doubles for the proposal kernel, shared by every suite that needs one.

One double, not one per suite. The kernel is reached from three surfaces now --
the analyzer's draft routes, `/api/proposals`, and the agent configuration
surface -- and a per-suite store would let each of them be tested against a
slightly different idea of what the store guarantees.

Structural conformance, not mocks: `InMemoryProposalStore` satisfies
`ProposalStorePort` and the compare-and-set contract it declares, so a test that
passes here is a test the real Mongo-backed store has to keep passing.
"""

from __future__ import annotations

import copy
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from fastapi import FastAPI, Request, Response

from return_platform.platform.governance.errors import (
    ProposalConcurrentModification,
    UnknownProposal,
)
from return_platform.platform.governance.kernel import ProposalKernel
from return_platform.platform.governance.ports import ProposalActivationPort
from return_platform.platform.governance.proposal import (
    LIVE_STATUSES,
    Proposal,
    ProposalStatus,
    ProposalType,
)

__all__ = [
    "InMemoryProposalStore",
    "RecordingAudit",
    "attach_governance",
    "build_test_kernel",
]


class InMemoryProposalStore:
    def __init__(self) -> None:
        self.proposals: dict[str, Proposal] = {}
        self._sequence = 0
        self._order: dict[str, int] = {}

    async def create(self, proposal: Proposal) -> None:
        if proposal.proposal_id in self.proposals:
            raise ProposalConcurrentModification(f"{proposal.proposal_id} already exists")
        self._sequence += 1
        self._order[proposal.proposal_id] = self._sequence
        self.proposals[proposal.proposal_id] = proposal

    async def save(self, proposal: Proposal, *, expected_version: int) -> None:
        stored = self.proposals.get(proposal.proposal_id)
        if stored is None or stored.version != expected_version:
            raise ProposalConcurrentModification(
                f"proposal {proposal.proposal_id} was not at version {expected_version}"
            )
        self.proposals[proposal.proposal_id] = proposal

    async def load(self, proposal_id: str) -> Proposal:
        try:
            return self.proposals[proposal_id]
        except KeyError as exc:
            raise UnknownProposal(f"no proposal with id {proposal_id!r}") from exc

    async def list(
        self,
        *,
        proposal_type: ProposalType | None = None,
        status: ProposalStatus | None = None,
        subject_id: str | None = None,
        limit: int = 50,
    ) -> Sequence[Proposal]:
        found = [
            proposal
            for proposal in self.proposals.values()
            if (proposal_type is None or proposal.proposal_type is proposal_type)
            and (status is None or proposal.status is status)
            and (subject_id is None or proposal.subject_id == subject_id)
        ]
        # Newest first, matching the Mongo store's `created_at` sort. Insertion
        # order stands in for it because a test can create two proposals inside
        # one clock tick and `created_at` alone would order them arbitrarily.
        found.sort(key=lambda proposal: self._order[proposal.proposal_id], reverse=True)
        return found[:limit]

    async def live_for_subject(
        self, proposal_type: ProposalType, subject_id: str
    ) -> Sequence[Proposal]:
        return [
            proposal
            for proposal in self.proposals.values()
            if proposal.proposal_type is proposal_type
            and proposal.subject_id == subject_id
            and proposal.status in LIVE_STATUSES
        ]

    async def last_activated(self, proposal_type: ProposalType, subject_id: str) -> Proposal | None:
        activated = [
            proposal
            for proposal in self.proposals.values()
            if proposal.proposal_type is proposal_type
            and proposal.subject_id == subject_id
            and proposal.status is ProposalStatus.ACTIVATED
        ]
        if not activated:
            return None
        return max(
            activated, key=lambda proposal: (proposal.updated_at, self._order[proposal.proposal_id])
        )


class RecordingAudit:
    """Satisfies `GovernanceAuditPort` and keeps what it was told.

    The audit trail is part of W4.2's Validation clause, so a test has to be able
    to assert on it; a sink that swallowed the record would let "audit" pass by
    doing nothing.
    """

    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    async def append_audit(
        self, *, action: str, actor: str, target: str, details: dict[str, Any]
    ) -> None:
        self.entries.append(
            {
                "action": action,
                "actor": actor,
                "target": target,
                "details": copy.deepcopy(details),
            }
        )

    def actions(self) -> list[str]:
        return [entry["action"] for entry in self.entries]


def build_test_kernel(
    activators: Mapping[ProposalType, ProposalActivationPort] | None = None,
) -> tuple[ProposalKernel, InMemoryProposalStore, RecordingAudit]:
    store = InMemoryProposalStore()
    audit = RecordingAudit()
    return (
        ProposalKernel(store, activators=dict(activators or {}), audit=audit),
        store,
        audit,
    )


def attach_governance(app: FastAPI, target: object | None = None) -> None:
    """Give a bare test app the kernel and the principal the routes now need.

    Both are wiring `main.py` supplies in production: the kernel comes from
    startup, the principal from authentication middleware. Approval and
    publication carry capability dependencies as of W4.3, so a test app with no
    principal is answered 401 -- which is correct, and not the question these
    suites are asking.

    The kernel, store and audit are left on `app.state` so a test can assert
    against the proposal that was written rather than only against the HTTP
    response.
    """
    from return_platform.bootstrap.adapters.governance_graph_schema import (
        GraphSchemaProposalActivator,
    )
    from return_platform.security import roles as r
    from return_platform.security.principal import Principal

    activators: dict[ProposalType, ProposalActivationPort] = {}
    if target is not None:
        activators[ProposalType.GRAPH_SCHEMA] = GraphSchemaProposalActivator(
            app.state.graph_schema_analyzer_persistence,
            target,  # type: ignore[arg-type]
        )
    kernel, store, audit = build_test_kernel(activators)
    app.state.proposal_kernel = kernel
    app.state.governance_store = store
    app.state.governance_audit = audit

    @app.middleware("http")
    async def _principal(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request.state.principal = Principal(subject="analyst", roles=frozenset({r.CONSOLE_ADMIN}))
        return await call_next(request)
