"""Add entities to a live descriptor through the analyzer, not through an editor.

W2.1 gave the analyzer a way to *replace* the runtime schema: an approved shape
compiles to a whole release. That is the right shape for a schema being designed
and the wrong one for a schema being extended, because the authoring form is
strictly poorer than `ActiveSchema` -- it has no vocabulary for `record_path`,
`explode`, `where`, `key_resolution`, `ownership_policy`, or the distinction
between an entity id and a graph label. Recompiling the shipped descriptor from
its own authoring projection would therefore lose all of that from eleven
entities that were correct, to add two that were missing. W2.4 was left with a
hand edit as the only remaining option, which is exactly what it forbids.

**An addition is compiled on its own and merged.** The new entities go through
the full analyzer path -- typed mutation commands, `GraphSchemaShape`,
`compile_active_schema` against the real source's observation -- and the result
is grafted onto the baseline. Nothing already in the descriptor is recompiled,
so nothing already in it can be silently degraded.

**Strictly additive, and it routes otherwise.** An id that already exists is an
error *here*, never an overwrite -- but the error is no longer a dead end. When
this was written the migration path did not exist, so "this belongs on the
migration path" meant "this cannot be done". It exists now:
`dynamic_knowledge.release_migration.plan_migration` classifies a change as
ADDITIVE, COMPATIBLE or DESTRUCTIVE, and
`data_platform.graph.sync_service.GraphSyncService.apply_migration_plan` carries
out the strategy each class earns -- including the generation cutover a
destructive change needs.

So the refusal below is a statement about *this function's* contract, not about
the platform's capability: `compile_addition` compiles a fragment and grafts it
on, and a graft cannot express a replacement. A caller holding a full candidate
release publishes it and activates it, which plans and performs the migration.
The message names that route, because an error that does not say what to do
instead is how a capability that exists goes unused.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime

from return_platform.bootstrap.adapters.analyzer_release_compiler import (
    ReleaseCompilationError,
    compile_active_schema,
)
from return_platform.bootstrap.adapters.analyzer_source_observation import SourceObservation
from return_platform.dynamic_knowledge.schema import (
    ActiveSchema,
    SourceAssetDefinition,
)
from return_platform.dynamic_knowledge.source_bindings import (
    SourceBinding,
    binding_names,
    catalogue_from,
)
from return_platform.graph_schema_analyzer.application.mutation_service import apply_mutations
from return_platform.graph_schema_analyzer.domain.mutation import MutationCommand
from return_platform.graph_schema_analyzer.domain.schema_draft import GraphSchemaShape

__all__ = ["compile_addition"]


def compile_addition(
    *,
    baseline: ActiveSchema,
    commands: Sequence[MutationCommand],
    new_sources: Mapping[str, SourceAssetDefinition] | None = None,
    observations: Mapping[str, SourceObservation] | None = None,
    grant_to_agents: Sequence[str] = (),
    configuration_release_id: str,
    schema_version: str,
    approved_by: str,
    approved_at: datetime,
) -> ActiveSchema:
    """Apply a mutation batch to an empty draft, compile it, and graft it on.

    `new_sources` binds dataset names the batch cites that the baseline has never
    heard of -- a SQL Server table, where every source it already knows is a
    Mongo collection. They are bound under both the asset id and the object name,
    the same two names `catalogue_from` gives a baseline's own sources, so a
    command may cite either.

    `grant_to_agents` names the agents that may see the result. Omitting it
    produces entities the graph holds and no agent can query: `SchemaQueryGuard`
    refuses a plan whose entity is not in the policy, and `compact_schema` builds
    what the model sees from the same list, so an absent grant reads as "the
    platform cannot answer that" rather than as a policy decision.

    The compiled release's own `agent_policies`, `graph.database` and version
    fields are discarded -- `compile_active_schema` narrows policies to the
    entities *it* compiled, which for a two-entity addition is a policy that
    revokes everything else.
    """
    if not commands:
        raise ReleaseCompilationError("an addition with no commands changes nothing")

    shape = apply_mutations(GraphSchemaShape(), tuple(commands))
    catalogue = catalogue_from(
        baseline,
        tuple(
            SourceBinding(dataset=name, asset=asset)
            for asset in (new_sources or {}).values()
            for name in binding_names(asset)
        ),
    )
    fragment = compile_active_schema(
        shape.model_dump(mode="json"),
        baseline=baseline,
        bindings=catalogue,
        observations=observations or {},
        configuration_release_id=configuration_release_id,
        schema_version=schema_version,
        approved_by=approved_by,
        approved_at=approved_at,
    )

    _refuse_collisions(baseline, fragment)

    policies = dict(baseline.agent_policies)
    for agent_id in grant_to_agents:
        policy = policies.get(agent_id)
        if policy is None:
            raise ReleaseCompilationError(
                f"cannot grant the addition to unknown agent {agent_id!r}; "
                f"known: {', '.join(sorted(policies)) or 'none'}"
            )
        policies[agent_id] = policy.model_copy(
            update={"allowed_entity_ids": policy.allowed_entity_ids | frozenset(fragment.entities)}
        )

    return baseline.model_copy(
        update={
            "sources": {**baseline.sources, **fragment.sources},
            "entities": {**baseline.entities, **fragment.entities},
            "graph": baseline.graph.model_copy(
                update={
                    "nodes": {**baseline.graph.nodes, **fragment.graph.nodes},
                    "relationships": {
                        **baseline.graph.relationships,
                        **fragment.graph.relationships,
                    },
                }
            ),
            "agent_policies": policies,
        }
    )


def _refuse_collisions(baseline: ActiveSchema, fragment: ActiveSchema) -> None:
    """Every id the fragment introduces must be new.

    Source assets are the one exception: an addition legitimately reads a source
    the baseline already declares, and the fragment's copy of it is compiled from
    the same binding, so it is the same asset rather than a redefinition. It is
    still checked, because a *differing* asset under a known id would rebind a
    live entity as a side effect of adding an unrelated one.
    """
    for label, description in (
        ("entity", set(fragment.entities) & set(baseline.entities)),
        ("node projection", set(fragment.graph.nodes) & set(baseline.graph.nodes)),
        (
            "relationship",
            set(fragment.graph.relationships) & set(baseline.graph.relationships),
        ),
    ):
        if description:
            raise ReleaseCompilationError(
                f"this addition redefines existing {label}(s) {sorted(description)}; "
                "an additive compile grafts a fragment on and cannot express a "
                "replacement. Publish the full candidate release and activate it: "
                "plan_migration classifies the change and apply_migration_plan runs "
                "the strategy it earns, up to a generation cutover"
            )
    for source_id, asset in fragment.sources.items():
        existing = baseline.sources.get(source_id)
        if existing is not None and existing != asset:
            raise ReleaseCompilationError(
                f"this addition redefines source {source_id!r}; rebinding a live source is a "
                "binding change and goes through the source-binding catalogue"
            )
