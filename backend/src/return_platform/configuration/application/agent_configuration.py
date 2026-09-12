"""Read and edit one agent's live configuration.

**CFG-5b: the live `agents:` section is the only source (D-CFG-1).** Before
this, an agent's "configuration" meant its own file under
`backend/config/agents/`, declared in `manifest.yaml` and edited through
`ConfigurationLoader`. CFG-5's own research (`.plan/tracks/CFG.ledger.md`,
step:05) traced every runtime consumer and found none of that read at
request time: `AgentRegistry.build()` and every agent class read only
`ReturnPlatformConfiguration.agents["<id>"]` -- the small model declared in
`configuration/return_configuration.py` (`name`, `version`, `enabled`,
`ai_assisted`, `ai_route_ref`, plus a handful of dead knobs kept only so
already-published releases still parse). The manifest-driven module system
was the console's own invention, editing a document nothing ran.

**The document IS the release's `agents.<id>` entry, not a wrapper around
it.** There is no more `module_id`/`module_type`/`payload` envelope: a read
returns exactly what `AgentConfiguration` validates, and a write proposes
exactly that shape back.

**The release is the only source, so `source` is always `"RELEASE"`.**
`agents` is a required key of `ReturnPlatformConfiguration` (`return_
configuration.py`) -- the release validator already refuses a release
without one -- so there is no longer a state where an agent has no release
value to fall back to. The field is kept (the frontend still shows it) but
it no longer distinguishes two possible sources, because there is only one.

**Writes are validated against `AgentConfiguration` directly, not through a
disposable-directory loader round-trip.** The manifest loader's containment
and id/type-agreement checks existed to protect a filesystem this file no
longer touches; the only thing left to check is that the submitted document
is one `AgentRegistry.build()` could actually use, which is exactly what
`AgentConfiguration(**document)` decides. The receipt is the SHA-256 of the
canonical `model_dump(mode="json")` of the *validated* document -- not the
submitted one -- so a document that omitted a defaulted field still records a
receipt over the same materialized shape the release will actually carry
(the class docstring on `AgentConfiguration` explains why those fields
default rather than disappear).

**`active()` is a read path only (RV F1).** It is a closure over this
process's own runtime snapshot, refreshed behind a debounce window and, on a
refresh failure, kept indefinitely stale -- an honest source for `GET`/
`list_agents`, which only ever need to answer with what this process is
currently serving. Publishing a release from it would be a different claim
entirely ("this is what the graph holds right now"), which it cannot make
without asking the graph. `bootstrap/adapters/governance_agent_configuration.
py`'s activator does exactly that: it reads the active release from
`ConfigurationGraphRepository` directly, never through this service's
`active()`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from return_platform.configuration.return_configuration import AgentConfiguration
from return_platform.platform.governance.kernel import ProposalKernel
from return_platform.platform.governance.proposal import Proposal, ProposalType

__all__ = [
    "AGENT_MODULE_KEY_ROOT",
    "AgentConfigurationService",
    "AgentConfigurationView",
    "AgentSummary",
    "ReturnPlatformDocument",
]

#: The root every proposed agent document hangs from in a proposal's
#: `before`/`after`, so the permitted-key policy sees `agent.enabled` rather
#: than a bare `enabled`. Unrelated to the release *domain* key
#: (`RETURN_PLATFORM_DOMAIN_KEY`) this document ends up patched into --
#: this root is governance's own view of the edit, not the release's.
AGENT_MODULE_KEY_ROOT = "agent"

#: A snapshot of the active release's whole `RETURN_PLATFORM` document.
#: Supplied as a callable rather than a value because the service outlives any
#: one release: it is constructed during startup and the release moves
#: underneath it every time one is activated. Returns `{}` pre-bootstrap, when
#: no release has been activated in this process yet.
type ReturnPlatformDocument = Callable[[], Mapping[str, Any]]


class AgentSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    manifestId: str
    name: str
    version: str
    enabled: bool
    aiAssisted: bool
    aiRouteRef: str | None
    #: Always `"RELEASE"` (see module docstring). Kept rather than removed so
    #: the frontend's existing source badge keeps rendering something true.
    source: str


class AgentConfigurationView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    manifestId: str
    #: A descriptive locator, not a filesystem path -- there is no file behind
    #: this document any more. Names exactly the release key an edit patches.
    path: str
    document: dict[str, Any]
    source: str


class AgentConfigurationService:
    def __init__(self, active: ReturnPlatformDocument) -> None:
        self._active = active

    # --- reads --------------------------------------------------------------

    def _return_platform(self) -> Mapping[str, Any]:
        document = self._active()
        return document if isinstance(document, Mapping) else {}

    def _agents(self) -> dict[str, Any]:
        agents = self._return_platform().get("agents")
        return dict(agents) if isinstance(agents, Mapping) else {}

    def list_agents(self) -> list[AgentSummary]:
        summaries = []
        for manifest_id, raw in sorted(self._agents().items()):
            payload = raw if isinstance(raw, Mapping) else {}
            route_ref = payload.get("ai_route_ref")
            summaries.append(
                AgentSummary(
                    manifestId=manifest_id,
                    name=str(payload.get("name") or manifest_id),
                    version=str(payload.get("version", "")),
                    enabled=bool(payload.get("enabled", False)),
                    aiAssisted=bool(payload.get("ai_assisted", False)),
                    aiRouteRef=route_ref if isinstance(route_ref, str) else None,
                    source="RELEASE",
                )
            )
        return summaries

    def read(self, manifest_id: str) -> AgentConfigurationView | None:
        raw = self._agents().get(manifest_id)
        if not isinstance(raw, Mapping):
            return None
        return AgentConfigurationView(
            manifestId=manifest_id,
            path=f"RETURN_PLATFORM.agents.{manifest_id}",
            document=dict(raw),
            source="RELEASE",
        )

    # --- writes -------------------------------------------------------------

    def validate_candidate(
        self, manifest_id: str, document: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        """Refuse the document, or return `(receipt, canonical_document)`.

        Raises `ValueError` with the reason -- `AgentConfiguration`'s own
        message, which names the offending field, since "invalid
        configuration" gives an operator nothing to correct.

        `canonical_document` is `AgentConfiguration(**document).model_dump
        (mode="json")`: every dead knob materialized with its default, exactly
        the shape every other agent in the release already carries. The
        receipt is the SHA-256 of that canonical serialization, not of
        whatever the caller submitted -- two submissions that validate to the
        same document get the same receipt, and a document altered after
        validation cannot keep a receipt that blessed a different one.
        """
        try:
            validated = AgentConfiguration(**document)
        except (ValidationError, TypeError) as exc:
            raise ValueError(f"{manifest_id} failed validation: {exc}") from exc
        canonical = validated.model_dump(mode="json")
        serialized = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
        receipt = f"agent-configuration:{hashlib.sha256(serialized.encode('utf-8')).hexdigest()}"
        return receipt, canonical

    async def propose(
        self,
        manifest_id: str,
        document: dict[str, Any],
        *,
        kernel: ProposalKernel,
        actor: str,
        occurred_at: datetime,
    ) -> Proposal:
        """Turn an edit into a reviewable proposal.

        Validate, then propose, then put it in front of a reviewer -- the
        proposal comes back already REVIEW_PENDING rather than sitting in
        DRAFT where nothing would ever look at it. Activation (bootstrap/
        adapters/governance_agent_configuration.py) is what turns an approved
        proposal into a release; nothing here writes anything durable.
        """
        receipt, canonical = self.validate_candidate(manifest_id, document)
        current = self._agents().get(manifest_id, {})
        proposal = await kernel.submit(
            proposal_type=ProposalType.CONFIGURATION,
            subject_id=manifest_id,
            title=f"Agent configuration {manifest_id}",
            before={AGENT_MODULE_KEY_ROOT: dict(current) if isinstance(current, Mapping) else {}},
            after={AGENT_MODULE_KEY_ROOT: canonical},
            evidence=(f"return_platform_path:RETURN_PLATFORM.agents.{manifest_id}",),
            proposed_by=actor,
            occurred_at=occurred_at,
        )
        proposal = await kernel.validate(
            proposal.proposal_id, receipt=receipt, actor=actor, occurred_at=occurred_at
        )
        return await kernel.submit_for_review(
            proposal.proposal_id, actor=actor, occurred_at=occurred_at
        )
