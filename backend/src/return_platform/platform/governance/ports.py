"""What the kernel needs from outside itself.

Three narrow contracts, because the kernel is platform infrastructure and may not
import a domain module (`tests/platform/test_layering.py`). The concrete
activators live in `bootstrap/adapters/`, the only place permitted to see both
the kernel and the module whose change it carries.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from return_platform.platform.governance.proposal import Proposal, ProposalStatus, ProposalType

__all__ = [
    "ActivationReceipt",
    "GovernanceAuditPort",
    "ProposalActivationPort",
    "ProposalStorePort",
]


class ActivationReceipt(BaseModel):
    """What an activator produced, named so it can be found again.

    `reference` is the durable artifact -- a configuration release id, a graph
    generation id. A receipt with no reference would leave "this proposal is
    ACTIVATED" as a claim with nothing behind it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    reference: str
    detail: str | None = None


@runtime_checkable
class ProposalStorePort(Protocol):
    async def create(self, proposal: Proposal) -> None:
        """Insert. Fails on a duplicate id rather than adopting the stored one."""

    async def save(self, proposal: Proposal, *, expected_version: int) -> None:
        """Compare-and-set on `version`; raises ProposalConcurrentModification."""

    async def load(self, proposal_id: str) -> Proposal:
        """Raises UnknownProposal when absent -- never returns None, so no caller
        can forget to check."""

    async def list(
        self,
        *,
        proposal_type: ProposalType | None = None,
        status: ProposalStatus | None = None,
        subject_id: str | None = None,
        limit: int = 50,
    ) -> Sequence[Proposal]:
        """Newest first. This is the inbox query."""

    async def live_for_subject(
        self, proposal_type: ProposalType, subject_id: str
    ) -> Sequence[Proposal]:
        """Every proposal for this subject that can still change state."""

    async def last_activated(self, proposal_type: ProposalType, subject_id: str) -> Proposal | None:
        """The most recently activated proposal for this subject.

        Its `after` is the honest `before` for the next one: a diff against what
        is actually running, rather than against a document assembled for the
        occasion.
        """


@runtime_checkable
class ProposalActivationPort(Protocol):
    """Carries an approved proposal into the thing that runs.

    One per proposal type, registered with the kernel. The kernel decides
    *whether* a proposal may be activated; this decides *how*, and is the only
    side of the pair that knows what a graph release or a configuration domain
    is.
    """

    async def activate(
        self,
        proposal: Proposal,
        *,
        actor: str,
        occurred_at: datetime,
        parameters: Mapping[str, Any],
    ) -> ActivationReceipt: ...


@runtime_checkable
class GovernanceAuditPort(Protocol):
    """The queryable audit trail -- the one `/api/config/audit` serves.

    Deliberately this signature and not `platform.contracts.audit.AuditSink`:
    the only sink implementing that contract writes to the log pipeline, and the
    defect this kernel exists to close is that configuration decisions were
    "absent from the audit trail" an operator can actually query.
    `OperationalRepository.append_audit` satisfies this structurally.
    """

    async def append_audit(
        self, *, action: str, actor: str, target: str, details: dict[str, Any]
    ) -> None: ...
