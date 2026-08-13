"""`governance_proposals`, backed by the platform system store.

One collection for all three types, because the inbox is one query. Splitting by
type would put the "show me everything awaiting a decision" screen back where it
started -- reading two collections and hoping neither was forgotten.

Writes are compare-and-set on `version`, the same discipline the analyzer's
drafts use: two reviewers opening the same proposal is the ordinary case, not the
exotic one.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from return_platform.platform.governance.errors import (
    ProposalConcurrentModification,
    UnknownProposal,
)
from return_platform.platform.governance.proposal import (
    LIVE_STATUSES,
    Proposal,
    ProposalStatus,
    ProposalType,
)
from return_platform.platform.system_store.repository import SystemStore

__all__ = ["GOVERNANCE_PROPOSALS", "SystemStoreProposalStore"]

GOVERNANCE_PROPOSALS = "governance_proposals"


class SystemStoreProposalStore:
    def __init__(self, system_store: SystemStore) -> None:
        self._store = system_store

    async def create(self, proposal: Proposal) -> None:
        await self._store.insert_one(GOVERNANCE_PROPOSALS, dict(proposal.to_document()))

    async def save(self, proposal: Proposal, *, expected_version: int) -> None:
        result = await self._store.replace_one(
            GOVERNANCE_PROPOSALS,
            {"proposal_id": proposal.proposal_id, "version": expected_version},
            dict(proposal.to_document()),
            upsert=False,
        )
        if result.matched_count != 1:
            raise ProposalConcurrentModification(
                f"proposal {proposal.proposal_id} was not at version {expected_version}; "
                "re-read it and re-decide."
            )

    async def load(self, proposal_id: str) -> Proposal:
        document = await self._store.read_only(GOVERNANCE_PROPOSALS).find_one(
            {"proposal_id": proposal_id}, {"_id": 0}
        )
        if document is None:
            raise UnknownProposal(f"no proposal with id {proposal_id!r}")
        return Proposal.model_validate(document)

    async def list(
        self,
        *,
        proposal_type: ProposalType | None = None,
        status: ProposalStatus | None = None,
        subject_id: str | None = None,
        limit: int = 50,
    ) -> Sequence[Proposal]:
        query: dict[str, Any] = {}
        if proposal_type is not None:
            query["proposal_type"] = proposal_type.value
        if status is not None:
            query["status"] = status.value
        if subject_id is not None:
            query["subject_id"] = subject_id
        cursor = (
            self._store.read_only(GOVERNANCE_PROPOSALS)
            .find(query, {"_id": 0})
            .sort("created_at", -1)
            .limit(limit)
        )
        return [Proposal.model_validate(document) async for document in cursor]

    async def live_for_subject(
        self, proposal_type: ProposalType, subject_id: str
    ) -> Sequence[Proposal]:
        cursor = self._store.read_only(GOVERNANCE_PROPOSALS).find(
            {
                "proposal_type": proposal_type.value,
                "subject_id": subject_id,
                "status": {"$in": sorted(status.value for status in LIVE_STATUSES)},
            },
            {"_id": 0},
        )
        return [Proposal.model_validate(document) async for document in cursor]

    async def last_activated(self, proposal_type: ProposalType, subject_id: str) -> Proposal | None:
        cursor = (
            self._store.read_only(GOVERNANCE_PROPOSALS)
            .find(
                {
                    "proposal_type": proposal_type.value,
                    "subject_id": subject_id,
                    "status": ProposalStatus.ACTIVATED.value,
                },
                {"_id": 0},
            )
            .sort("updated_at", -1)
            .limit(1)
        )
        documents = [document async for document in cursor]
        return Proposal.model_validate(documents[0]) if documents else None
