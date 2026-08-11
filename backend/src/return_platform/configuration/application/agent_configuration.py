"""Read and edit one agent's configuration.

Each agent already has its own file under `config/agents/`, declared in
`manifest.yaml` -- the separation the operator surface needs exists, and
nothing served it. This is the read/write seam for that, and nothing more: it
does not decide what an agent's configuration means, it moves a document
between the file the loader reads and the console that edits it.

**Writes go through the loader's own validation, not a second copy of it.**
`ConfigurationLoader` already enforces the rules that matter -- no absolute
paths, no escaping the config directory, `module_id` matching the manifest key,
`module_type` matching the id's prefix. Re-implementing any of that here would
create two definitions of a valid module that could disagree, and the one an
editor goes through is the one that would drift.

**Written atomically.** A half-written module file is one the loader refuses at
the next start, which turns a typo in a form field into a service that will not
boot. The replacement is written beside the target and moved over it, so a
reader sees either the old document or the new one.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict

from return_platform.configuration.application.loader import (
    ConfigurationLoader,
    LoadedManifestModule,
)

__all__ = [
    "AgentConfigurationService",
    "AgentConfigurationView",
    "AgentSummary",
]


class _IndentedDumper(yaml.SafeDumper):
    """Indent sequences under their key, the way these files are already written.

    PyYAML writes a list flush with its parent key. Semantically identical, and
    it turns a one-field edit into a whole-file diff -- which buries the change
    somebody actually needs to review. Matching the existing style keeps an edit
    through the console reviewable as an edit.
    """

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        super().increase_indent(flow=flow, indentless=False)


#: Only AGENT modules are editable here. The manifest also carries policies,
#: workflows and sync definitions, and an "agent configuration" screen that
#: silently let you rewrite a sync definition would be mislabelled.
_AGENT_MODULE_TYPE = "AGENT"


class AgentSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    manifestId: str
    moduleId: str
    name: str
    enabled: bool
    status: str
    configurationVersion: str


class AgentConfigurationView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    manifestId: str
    moduleId: str
    path: str
    document: dict[str, Any]


class AgentConfigurationService:
    def __init__(self, config_dir: Path) -> None:
        self._config_dir = config_dir
        self._loader = ConfigurationLoader(config_dir)

    def _agents(self) -> dict[str, LoadedManifestModule]:
        manifest = self._loader.load_manifest()
        return {
            manifest_id: module
            for manifest_id, module in self._loader.load_manifest_entries(manifest).items()
            if module.module_type == _AGENT_MODULE_TYPE
        }

    def list_agents(self) -> list[AgentSummary]:
        summaries = []
        for manifest_id, module in sorted(self._agents().items()):
            payload = module.document.get("payload")
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
                    status=str(module.document.get("status", "UNKNOWN")),
                    configurationVersion=str(module.document.get("configuration_version", "")),
                )
            )
        return summaries

    def read(self, manifest_id: str) -> AgentConfigurationView | None:
        module = self._agents().get(manifest_id)
        if module is None:
            return None
        return AgentConfigurationView(
            manifestId=manifest_id,
            moduleId=module.module_id,
            path=module.path,
            document=dict(module.document),
        )

    def write(self, manifest_id: str, document: dict[str, Any]) -> AgentConfigurationView:
        """Replace one agent's document, or raise ValueError with the reason.

        Validated by reloading through the loader after the write and rolling
        back if it refuses. Checking a candidate in memory would test a
        different thing from what the platform will actually read at start --
        the file is the artifact, so the file is what gets validated.
        """
        existing = self._agents().get(manifest_id)
        if existing is None:
            raise ValueError(f"{manifest_id} is not an agent module")

        target = (self._config_dir / existing.path).resolve()
        original = target.read_text(encoding="utf-8")
        self._write_atomically(
            target,
            yaml.dump(
                document,
                Dumper=_IndentedDumper,
                sort_keys=False,
                allow_unicode=True,
                default_flow_style=False,
            ),
        )
        try:
            written = self._agents().get(manifest_id)
            if written is None:
                raise ValueError(f"{manifest_id} no longer loads as an agent module after the edit")
        except Exception:
            self._write_atomically(target, original)
            raise
        return AgentConfigurationView(
            manifestId=manifest_id,
            moduleId=written.module_id,
            path=written.path,
            document=dict(written.document),
        )

    @staticmethod
    def _write_atomically(target: Path, content: str) -> None:
        temporary = target.with_suffix(f"{target.suffix}.tmp")
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, target)
