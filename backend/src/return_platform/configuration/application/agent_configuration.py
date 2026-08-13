"""Read and edit one agent's configuration.

Each agent already has its own file under `config/agents/`, declared in
`manifest.yaml` -- the separation the operator surface needs exists, and
nothing served it. This is the read/write seam for that, and nothing more: it
does not decide what an agent's configuration means, it moves a document
between the configuration the platform runs and the console that edits it.

**The packaged file is seed, and the release is truth (W4.2).** This used to
write the YAML back to disk. Three things were wrong with that and none of them
were visible from the console: section 8 forbids a runtime change writing
packaged configuration, an edit was lost on the next redeploy, another replica
never saw it, and the audit trail had no record it happened. The file is now
read only as the baseline for an agent the release has never carried; where the
active release carries an `AGENT_MODULES` document for a module, that document
is what `read` and `list_agents` return.

**Writes go through the loader's own validation, not a second copy of it.**
`ConfigurationLoader` already enforces the rules that matter -- no absolute
paths, no escaping the config directory, `module_id` matching the manifest key,
`module_type` matching the id's prefix. Re-implementing any of that here would
create two definitions of a valid module that could disagree, and the one an
editor goes through is the one that would drift.

**Validate by reload survived the change of sink.** The old docstring's argument
was that checking a candidate in memory tests a different thing from what the
platform reads at start, so the file is what gets validated. That is still the
argument: the candidate is written into a throwaway directory with its real
relative path and loaded through the same `ConfigurationLoader`, so the same
parser, the same containment check and the same id/type agreement decide. What
changed is that the directory is disposable and the packaged one is never
touched.
"""

from __future__ import annotations

import hashlib
import tempfile
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from return_platform.configuration.application.loader import (
    ConfigurationLoader,
    LoadedManifestModule,
)
from return_platform.configuration.domain.agents import AgentConfigNode
from return_platform.platform.governance.kernel import ProposalKernel
from return_platform.platform.governance.proposal import Proposal, ProposalType

__all__ = [
    "AGENT_MODULE_KEY_ROOT",
    "AgentConfigurationService",
    "AgentConfigurationView",
    "AgentModuleOverlay",
    "AgentSummary",
]


class _IndentedDumper(yaml.SafeDumper):
    """Indent sequences under their key, the way these files are already written.

    PyYAML writes a list flush with its parent key. Semantically identical, and
    it turns a one-field edit into a whole-file diff -- which buries the change
    somebody actually needs to review. Matching the existing style keeps the
    candidate that goes through the loader byte-comparable with the packaged
    file it is derived from.
    """

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        super().increase_indent(flow=flow, indentless=False)


#: Only AGENT modules are editable here. The manifest also carries policies,
#: workflows and sync definitions, and an "agent configuration" screen that
#: silently let you rewrite a sync definition would be mislabelled.
_AGENT_MODULE_TYPE = "AGENT"

#: The root every proposed agent key hangs from, so the permitted-key policy sees
#: `agent.payload.enabled` rather than a bare `payload.enabled`. The forbidden
#: patterns in plan section 7 are matched at every segment offset, so a
#: `credentials` or `secrets` block anywhere inside the document is caught
#: regardless of how deeply an editor nests it.
AGENT_MODULE_KEY_ROOT = "agent"

#: A snapshot of the active release's agent-module documents, keyed by manifest
#: id. Supplied as a callable rather than a value because the service outlives
#: any one release: it is constructed during startup and the release moves
#: underneath it every time one is activated.
type AgentModuleOverlay = Callable[[], Mapping[str, Any]]


class AgentSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    manifestId: str
    moduleId: str
    name: str
    enabled: bool
    status: str
    configurationVersion: str
    #: Whether this document came from the active release or is still the
    #: packaged seed. An operator looking at a screen that cannot tell them
    #: which cannot tell whether their last edit took effect.
    source: str


class AgentConfigurationView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    manifestId: str
    moduleId: str
    path: str
    document: dict[str, Any]
    source: str


class AgentConfigurationService:
    def __init__(
        self,
        config_dir: Path,
        *,
        overlay: AgentModuleOverlay | None = None,
    ) -> None:
        """`overlay` is the active release's agent documents.

        Absent, every read answers from the packaged seed -- which is the honest
        answer in a process that has no release yet (bootstrap, the OpenAPI
        exporter) and is never mistaken for one, because the view says which
        source it came from.
        """
        self._config_dir = config_dir
        self._loader = ConfigurationLoader(config_dir)
        self._overlay = overlay

    # --- reads --------------------------------------------------------------

    def _packaged(self) -> dict[str, LoadedManifestModule]:
        manifest = self._loader.load_manifest()
        return {
            manifest_id: module
            for manifest_id, module in self._loader.load_manifest_entries(manifest).items()
            if module.module_type == _AGENT_MODULE_TYPE
        }

    def _released(self) -> Mapping[str, Any]:
        if self._overlay is None:
            return {}
        overlay = self._overlay()
        return overlay if isinstance(overlay, Mapping) else {}

    def _effective(self, manifest_id: str) -> tuple[LoadedManifestModule, dict[str, Any], str]:
        packaged = self._packaged().get(manifest_id)
        if packaged is None:
            raise ValueError(f"{manifest_id} is not an agent module")
        released = self._released().get(manifest_id)
        if isinstance(released, Mapping):
            return packaged, dict(released), "RELEASE"
        return packaged, dict(packaged.document), "PACKAGED_BASELINE"

    def list_agents(self) -> list[AgentSummary]:
        summaries = []
        for manifest_id in sorted(self._packaged()):
            module, document, source = self._effective(manifest_id)
            payload = document.get("payload")
            payload = payload if isinstance(payload, dict) else {}
            summaries.append(
                AgentSummary(
                    manifestId=manifest_id,
                    moduleId=module.module_id,
                    # The declared name, falling back to the id. An agent whose
                    # payload has no name is still an agent worth listing --
                    # dropping it would hide the one that most needs editing.
                    name=str(payload.get("name") or module.module_id),
                    enabled=bool(payload.get("enabled", False)),
                    status=str(document.get("status", "UNKNOWN")),
                    configurationVersion=str(document.get("configuration_version", "")),
                    source=source,
                )
            )
        return summaries

    def read(self, manifest_id: str) -> AgentConfigurationView | None:
        try:
            module, document, source = self._effective(manifest_id)
        except ValueError:
            return None
        return AgentConfigurationView(
            manifestId=manifest_id,
            moduleId=module.module_id,
            path=module.path,
            document=document,
            source=source,
        )

    def released_documents(self) -> dict[str, Any]:
        """Every agent module as the next release should carry it.

        The activator needs the *whole* domain, not the one module that changed:
        a release is an immutable document set, so publishing only the edited
        module would drop every other agent's configuration from it.
        """
        return {manifest_id: self._effective(manifest_id)[1] for manifest_id in self._packaged()}

    # --- writes -------------------------------------------------------------

    def validate_candidate(self, manifest_id: str, document: dict[str, Any]) -> str:
        """Refuse the document, or return a receipt. Raises ValueError with the reason.

        The reason is the loader's own message wherever the loader is what
        refused: "invalid configuration" gives an operator nothing to correct,
        and the loader's message names the field.

        The receipt is the SHA-256 of the exact serialization that went through
        the loader. A `validation_receipt` has to name what was validated, and
        for a document with no id of its own the only honest name is its content
        -- which also means a proposal whose document was altered afterwards
        cannot keep the receipt that blessed it.
        """
        module, _, _ = self._effective(manifest_id)
        serialized = _dump(document)
        with tempfile.TemporaryDirectory(prefix="agent-config-candidate-") as raw:
            candidate_root = Path(raw)
            target = candidate_root / module.path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(serialized, encoding="utf-8")
            manifest = self._loader.load_manifest()
            (candidate_root / "manifest.yaml").write_text(
                _dump(
                    {
                        "schema_version": manifest.schema_version,
                        "release_id": manifest.release_id,
                        "status": manifest.status.value,
                        "modules": {manifest_id: {"path": module.path}},
                    }
                ),
                encoding="utf-8",
            )
            candidate_loader = ConfigurationLoader(candidate_root)
            loaded = candidate_loader.load_manifest_entries(candidate_loader.load_manifest())
            written = loaded.get(manifest_id)
            if written is None or written.module_type != _AGENT_MODULE_TYPE:
                raise ValueError(f"{manifest_id} no longer loads as an agent module after the edit")

        # The loader proves the envelope; this proves the payload. The runtime
        # snapshot builds `AgentConfigNode` from it (`compatibility.py`), so a
        # payload that fails here is one the platform would refuse at boot --
        # and refusing it now is the difference between a corrected form field
        # and a release nothing can start on.
        payload = document.get("payload")
        if not isinstance(payload, dict):
            raise ValueError(f"{manifest_id} payload is not a mapping")
        try:
            AgentConfigNode(**payload)
        except (ValidationError, TypeError) as exc:
            raise ValueError(f"{manifest_id} payload failed validation: {exc}") from exc

        return f"agent-module-reload:{hashlib.sha256(serialized.encode('utf-8')).hexdigest()}"

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

        Validate, then propose, then put it in front of a reviewer -- which is
        the step order W4.2 asks for and the reason the proposal comes back
        already REVIEW_PENDING rather than sitting in DRAFT where nothing would
        ever look at it. The old sink replaced a file and the change was live;
        this one produces a record a human approves and a release activates,
        which is what makes an agent edit auditable, durable across a redeploy
        and visible to every replica.
        """
        receipt = self.validate_candidate(manifest_id, document)
        module, current, _ = self._effective(manifest_id)
        proposal = await kernel.submit(
            proposal_type=ProposalType.CONFIGURATION,
            subject_id=manifest_id,
            title=f"Agent configuration {manifest_id}",
            before={AGENT_MODULE_KEY_ROOT: current},
            after={AGENT_MODULE_KEY_ROOT: document},
            evidence=(f"module_path:{module.path}",),
            proposed_by=actor,
            occurred_at=occurred_at,
        )
        proposal = await kernel.validate(
            proposal.proposal_id, receipt=receipt, actor=actor, occurred_at=occurred_at
        )
        return await kernel.submit_for_review(
            proposal.proposal_id, actor=actor, occurred_at=occurred_at
        )


def _dump(document: Mapping[str, Any]) -> str:
    return yaml.dump(
        dict(document),
        Dumper=_IndentedDumper,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
