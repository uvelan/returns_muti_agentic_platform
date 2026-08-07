"""Semantic and cross-reference configuration validator.

Validation runs before a release transitions from DRAFT → VALIDATED.
Every failure identifies: source domain, source identifier, referenced
identifier, and target domain.

Rules implemented per the Phase 2 plan:
  §3.1  Manifest/module consistency (via loaded_modules from loader)
  §3.2  Agent → manifest module normalization (agent_id → agent.<id>)
  §3.3  Agent implementation non-empty if set
  §3.4  Agent AI route refs → ai.routes; route.task_id → ai.tasks; provider refs
  §3.5  Agent prompt_ref deferred (reliable resolution requires task/prompt registry)
  §3.6  Workflow stages are business state names — NOT validated as agent IDs
        Validates: stage IDs non-empty, no duplicates within a workflow
        Structured handler refs (type=AGENT → agent_id) validated only if present
  §3.7  Graph → source logical refs validated against SourcesConfig
  §3.8  Source connector_type non-empty; access_mode must be read-only if set
  §3.9  SystemStore structure physical_name non-empty; no duplicate physical names
  §3.10 Duplicate IDs rejected within same namespace only
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from return_platform.configuration.domain.errors import ConfigurationValidationError
from return_platform.configuration.domain.release_model import RuntimeSnapshot

# Read-only access modes per the platform contract
_READ_ONLY_ACCESS_MODES = {"READ_ONLY", "read_only", "readonly"}


class ConfigurationValidator:
    """Validates a RuntimeSnapshot for semantic correctness."""

    def validate_snapshot(self, snapshot: RuntimeSnapshot) -> None:
        """Run all validation rules.  Raises ConfigurationValidationError on failure."""
        errors: list[str] = []

        self._validate_modules_non_empty(snapshot, errors)
        self._validate_agents(snapshot, errors)
        self._validate_workflows(snapshot, errors)
        self._validate_graph_source_refs(snapshot, errors)
        self._validate_sources(snapshot, errors)
        self._validate_system_store(snapshot, errors)

        if errors:
            summary = "; ".join(errors)
            raise ConfigurationValidationError(
                f"Snapshot failed semantic validation ({len(errors)} error(s)): {summary}"
            )

    # ------------------------------------------------------------------
    # §3.1 — Modules presence
    # ------------------------------------------------------------------

    def _validate_modules_non_empty(
        self,
        snapshot: RuntimeSnapshot,
        errors: list[str],
    ) -> None:
        if not snapshot.modules or not snapshot.modules.modules:
            errors.append(
                "modules: snapshot must declare at least one module in ModulesConfig"
            )

    # ------------------------------------------------------------------
    # §3.2 / §3.3 / §3.4 — Agent validation
    # ------------------------------------------------------------------

    def _validate_agents(
        self,
        snapshot: RuntimeSnapshot,
        errors: list[str],
    ) -> None:
        modules = snapshot.modules.modules if snapshot.modules else {}
        ai_routes: Mapping[str, Any] = (
            snapshot.ai.routes if snapshot.ai and snapshot.ai.routes else {}
        ) or {}
        ai_tasks: Mapping[str, Any] = (
            snapshot.ai.tasks if snapshot.ai and snapshot.ai.tasks else {}
        ) or {}
        ai_providers: Mapping[str, Any] = (
            snapshot.ai.providers if snapshot.ai and snapshot.ai.providers else {}
        ) or {}

        for agent_id, agent in (snapshot.agents.agents or {}).items():
            # §3.2 — Normalize: agent.{agent_id} must appear in modules
            manifest_module_id = f"agent.{agent_id}"
            if manifest_module_id not in modules:
                errors.append(
                    f"agents: agent '{agent_id}' not declared as module "
                    f"'{manifest_module_id}' in ModulesConfig"
                )

            # §3.3 — implementation must be non-empty if present
            impl = agent.implementation
            if impl is not None:
                if not impl.strip():
                    errors.append(
                        f"agents: agent '{agent_id}' has whitespace-only implementation"
                    )

            # §3.4 — ai_route_ref must resolve
            route_ref = agent.ai_route_ref
            if route_ref:
                if not ai_routes:
                    errors.append(
                        f"agents: agent '{agent_id}' references AI route "
                        f"'{route_ref}' but ai.routes is empty/absent"
                    )
                elif route_ref not in ai_routes:
                    errors.append(
                        f"agents: agent '{agent_id}' references unknown AI route "
                        f"'{route_ref}' — not found in ai.routes"
                    )
                else:
                    # If the route declares a task_id, validate it too
                    route_doc = ai_routes[route_ref]
                    if isinstance(route_doc, dict):
                        task_id = route_doc.get("task_id") or route_doc.get("task")
                        if task_id and ai_tasks and task_id not in ai_tasks:
                            errors.append(
                                f"agents: agent '{agent_id}' → route '{route_ref}' "
                                f"references unknown AI task '{task_id}'"
                            )
                        # Provider ref in route
                        provider_ref = route_doc.get("provider")
                        if provider_ref and ai_providers and provider_ref not in ai_providers:
                            errors.append(
                                f"agents: agent '{agent_id}' → route '{route_ref}' "
                                f"references unknown AI provider '{provider_ref}'"
                            )

    # ------------------------------------------------------------------
    # §3.6 — Workflow validation (stages are business state names)
    # ------------------------------------------------------------------

    def _validate_workflows(
        self,
        snapshot: RuntimeSnapshot,
        errors: list[str],
    ) -> None:
        agent_ids = set(snapshot.agents.agents.keys()) if snapshot.agents.agents else set()

        wf_ids: set[str] = set()
        for wf_id, workflow in (snapshot.workflow.workflow or {}).items():
            # Duplicate workflow IDs
            if wf_id in wf_ids:
                errors.append(f"workflow: duplicate workflow ID '{wf_id}'")
            wf_ids.add(wf_id)

            # Stage IDs must be non-empty and unique within the workflow
            stage_ids: set[str] = set()
            for stage in (workflow.stages or []):
                if not isinstance(stage, str) or not stage.strip():
                    errors.append(
                        f"workflow: workflow '{wf_id}' contains an empty stage ID"
                    )
                    continue

                # §3.6 — Do NOT treat stage name as agent ID.
                # Only structured handlers trigger agent resolution.
                if stage in stage_ids:
                    errors.append(
                        f"workflow: workflow '{wf_id}' has duplicate stage ID '{stage}'"
                    )
                stage_ids.add(stage)

            # §3.6 — Structured handler validation (future extension point)
            # If a stage is a dict with handler.type == AGENT, validate agent ref.
            for stage in (workflow.stages or []):
                if isinstance(stage, dict):
                    handler = stage.get("handler", {})
                    if isinstance(handler, dict) and handler.get("type") == "AGENT":
                        handler_agent = handler.get("agent")
                        if handler_agent and handler_agent not in agent_ids:
                            errors.append(
                                f"workflow: workflow '{wf_id}' stage handler "
                                f"references unknown agent '{handler_agent}'"
                            )

    # ------------------------------------------------------------------
    # §3.7 — Graph → source references
    # ------------------------------------------------------------------

    def _validate_graph_source_refs(
        self,
        snapshot: RuntimeSnapshot,
        errors: list[str],
    ) -> None:
        source_ids = set(snapshot.sources.sources.keys()) if snapshot.sources.sources else set()

        for graph_id, graph_schema in (snapshot.graph.graphs or {}).items():
            if not graph_schema.sources:
                continue
            for source_ref, source_val in graph_schema.sources.items():
                # Dynamic Knowledge graph schemas embed full source definitions
                # (with connector_type, connection_ref, etc.) rather than
                # cross-referencing SourcesConfig keys.  Only validate keys
                # whose value is a plain string ID or None — i.e. a logical
                # reference into SourcesConfig, not an embedded definition.
                if isinstance(source_val, dict):
                    # Embedded inline source definition — not a SourcesConfig ref
                    continue

                # It is a logical reference; validate it resolves
                normalized = (
                    source_ref.removeprefix("source.")
                    if source_ref.startswith("source.")
                    else source_ref
                )
                if normalized not in source_ids and source_ref not in source_ids:
                    errors.append(
                        f"graph: graph schema '{graph_id}' references unknown source "
                        f"'{source_ref}' — not found in SourcesConfig"
                    )

    # ------------------------------------------------------------------
    # §3.8 — Source validation
    # ------------------------------------------------------------------

    def _validate_sources(
        self,
        snapshot: RuntimeSnapshot,
        errors: list[str],
    ) -> None:
        for src_id, source in (snapshot.sources.sources or {}).items():
            # connector_type must be non-empty
            if not source.connector_type or not source.connector_type.strip():
                errors.append(
                    f"sources: source '{src_id}' has missing or empty connector_type"
                )

            # access_mode must be read-only if declared
            access_mode = source.access_mode
            if access_mode is not None and access_mode not in _READ_ONLY_ACCESS_MODES:
                errors.append(
                    f"sources: source '{src_id}' access_mode is '{access_mode}' "
                    f"— only read-only sources are permitted; "
                    f"acceptable values: {sorted(_READ_ONLY_ACCESS_MODES)}"
                )

    # ------------------------------------------------------------------
    # §3.9 — SystemStore validation
    # ------------------------------------------------------------------

    def _validate_system_store(
        self,
        snapshot: RuntimeSnapshot,
        errors: list[str],
    ) -> None:
        if not snapshot.system_store:
            return

        physical_names_seen: set[str] = set()
        for struct_id, structure in (snapshot.system_store.structures or {}).items():
            if not struct_id.strip():
                errors.append(
                    "system_store: logical structure ID must be non-empty"
                )

            phys = structure.physical_name
            if not phys or not phys.strip():
                errors.append(
                    f"system_store: structure '{struct_id}' has missing or empty "
                    f"physical_name"
                )
                continue

            if phys in physical_names_seen:
                errors.append(
                    f"system_store: duplicate physical_name '{phys}' — "
                    f"two logical structures alias the same collection"
                )
            physical_names_seen.add(phys)
