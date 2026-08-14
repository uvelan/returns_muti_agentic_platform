# agents/registry

The single place that constructs all six agents. Replaces two prior,
never-reconciled registries:

- `agents.registry.ReturnAgentRegistry` — a frozen dataclass with no configuration
  metadata, no `implementation_id`, no resolution by `agent_id`.
- `dynamic_knowledge.agents.registry.IndependentAgentRegistry` — had the right identity
  fields (`agent_id`/`task_queue`/`state_namespace`/`prompt_ref`/`policy_ref`) but was
  descriptor-only: nothing ever constructed one, and it had no way to resolve a
  descriptor to something executable. Deleted; zero call sites referenced it.

`AgentRegistry.build(configuration)` constructs all six agents from one
`ReturnPlatformConfiguration`, exactly like `ReturnAgentRegistry` did. There is one
access pattern, by typed attribute:

```python
registry = AgentRegistry.build(configuration)
registry.order_discovery.assess(request)
```

`resolve(agent_id)`, `descriptor(agent_id)`, `all_descriptors()` and `UnknownAgentId`
were removed under AGT-02. They served an `agent_id`-keyed dispatch path that no
production caller ever used and that could not carry all six agents — the canonical
`ReturnCaseWorkflow` drives named Temporal activities, not agents by id. A caller that
genuinely has only an id has no agent to type-check against, which is the problem the
typed attributes exist to avoid.
