"""Carries an approved `GRAPH_SCHEMA` proposal into a runtime schema release.

Binds the proposal kernel (platform) to the analyzer's `GraphTargetPort` (a
business module). `bootstrap/adapters/` is the only place permitted to see both,
which is why this lives here rather than inside either.

**The publish already existed** -- `POST /drafts/{id}/publish` called
`publish_release` directly. What changed is that the call now happens under the
kernel's activation step, so a published schema and an activated proposal are the
same event rather than two records that can disagree about whether a change
went live.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from return_platform.graph_schema_analyzer.domain.schema_draft import DraftStatus
from return_platform.graph_schema_analyzer.ports.graph_target_port import GraphTargetPort
from return_platform.graph_schema_analyzer.ports.system_store_port import PersistencePort
from return_platform.platform.governance.errors import ActivationRefused, GovernanceError
from return_platform.platform.governance.ports import ActivationReceipt
from return_platform.platform.governance.proposal import Proposal

__all__ = ["GraphSchemaProposalActivator"]


class GraphSchemaProposalActivator:
    def __init__(self, persistence: PersistencePort, target: GraphTargetPort) -> None:
        self._persistence = persistence
        self._target = target

    async def activate(
        self,
        proposal: Proposal,
        *,
        actor: str,
        occurred_at: datetime,
        parameters: Mapping[str, Any],
    ) -> ActivationReceipt:
        del occurred_at
        draft = await self._persistence.load_draft(proposal.subject_id)
        if draft.status is not DraftStatus.APPROVED:
            # The kernel already refused anything but an APPROVED proposal. This
            # checks the *draft*, which is a separate record and can have been
            # edited since -- and an edit returns it to DRAFT.
            raise GovernanceError(
                f"draft {draft.draft_id} is {draft.status}; only an APPROVED draft can be "
                "published to the runtime."
            )
        # The shape the reviewer approved, not the draft as it stands now. If the
        # two have diverged the draft is no longer APPROVED and the check above
        # has already refused; publishing `proposal.after` is what makes that
        # guarantee mean something rather than relying on it.
        handle = await self._target.publish_release(
            draft=dict(proposal.after),
            draft_id=draft.draft_id,
            approver=actor,
            activate=bool(parameters.get("activate", False)),
        )
        if not handle.accepted:
            raise ActivationRefused(
                handle.detail or "the graph target refused the schema",
                reference=handle.generation_id,
            )
        return ActivationReceipt(reference=handle.generation_id, detail=handle.detail)
