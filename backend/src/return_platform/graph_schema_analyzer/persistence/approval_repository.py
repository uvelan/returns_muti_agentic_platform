"""`schema_approvals`.

Approvals are effectively append-only: the domain refuses to re-decide a decided
approval, so the only legitimate write after creation is the single
PENDING -> APPROVED/REJECTED transition.
"""

from __future__ import annotations

from collections.abc import Sequence

from return_platform.graph_schema_analyzer.domain.approval import Approval
from return_platform.graph_schema_analyzer.domain.errors import UnknownAnalysis
from return_platform.platform.system_store.repository import SystemStore

__all__ = ["SCHEMA_APPROVALS", "ApprovalRepository"]

SCHEMA_APPROVALS = "schema_approvals"


class ApprovalRepository:
    def __init__(self, system_store: SystemStore) -> None:
        self._store = system_store

    async def save(self, approval: Approval) -> None:
        await self._store.replace_one(
            SCHEMA_APPROVALS,
            {"approval_id": approval.approval_id},
            dict(approval.to_document()),
            upsert=True,
        )

    async def load(self, approval_id: str) -> Approval:
        document = await self._store.read_only(SCHEMA_APPROVALS).find_one(
            {"approval_id": approval_id}, {"_id": 0}
        )
        if document is None:
            raise UnknownAnalysis(f"no approval with id {approval_id!r}")
        return Approval.model_validate(document)

    async def list_for_draft(self, draft_id: str) -> Sequence[Approval]:
        cursor = self._store.read_only(SCHEMA_APPROVALS).find({"draft_id": draft_id}, {"_id": 0})
        return [Approval.model_validate(document) async for document in cursor]
