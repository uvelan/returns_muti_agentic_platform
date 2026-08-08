# agents/registry

The single place that constructs and resolves all six agents. Replaces two prior,
never-reconciled registries:

- `agents.registry.ReturnAgentRegistry` — a frozen dataclass with no configuration
  metadata, no `implementation_id`, no resolution by `agent_id`.
- `dynamic_knowledge.agents.registry.IndependentAgentRegistry` — had the right identity
  fields (`agent_id`/`task_queue`/`state_namespace`/`prompt_ref`/`policy_ref`) but was
  descriptor-only: nothing ever constructed one, and it had no way to resolve a
  descriptor to something executable. Deleted; zero call sites referenced it.

`AgentRegistry.build(configuration)` constructs all six agents from one
`ReturnPlatformConfiguration`, exactly like `ReturnAgentRegistry` did. Two access
patterns are both supported, deliberately:

```python
registry = AgentRegistry.build(configuration)

# A caller that already knows which agent it needs keeps full static typing:
registry.order_discovery.assess(request)

# A caller that only has an agent_id (Temporal orchestration, in a later phase)
# resolves dynamically instead:
registry.resolve("order_discovery")          # -> AgentPlugin[Any, Any]
registry.descriptor("order_discovery")        # -> AgentDescriptor
registry.all_descriptors()                    # -> tuple[AgentDescriptor, ...]
```

`resolve()` raises `UnknownAgentId` for anything outside the six configured agents —
there is no silent fallback.
