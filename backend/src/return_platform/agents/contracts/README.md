# agents/contracts

The shapes the agents share, and the shapes they may reach for.

- `dto.py` — the per-agent request/response pydantic models. This is the contract that
  matters: each agent's own strongly-typed pair, which is what its callers are checked
  against.
- `context.py` — `AgentExecutionContext`: what an agent may reach for besides its
  request. Deliberately platform-neutral — no `.ai`, no `.knowledge`, no other agent, no
  domain type. `configuration` is a pinned `RuntimeConfigurationView`, never a
  `RuntimeConfigurationHandle` — an agent execution is always scoped to the release its
  session was pinned to when it started.
- `ports.py` — `AgentAiPort`, `KnowledgePort`: shapes an agent would resolve from
  `context.capabilities` rather than importing `ai_gateway`/`dynamic_knowledge`
  directly. Declared; no provider is bound (see `agents/README.md`).

Everything above stays importable as `return_platform.agents.contracts.<name>` — the
package's `__init__.py` re-exports all of it.

## Removed: `plugin.py` and `descriptor.py` (AGT-02)

`AgentPlugin[RequestT, ResultT]` (a `descriptor` property plus
`async execute(request, context)`) and `AgentDescriptor` are gone. They described a
generic execution path nothing ever executed: no production caller dispatched by
`agent_id`, `AgentDescriptor` was a field-for-field copy of `AgentConfiguration` that
only a test read back, and `OrderAnalysisAgent.execute()` could not be implemented at
all. Callers hold a concrete agent and call its own method; agent identity and limits
are read from `AgentConfiguration` directly, where they were always coming from.

## Order Discovery's narrower port

Order Discovery is the only agent expected to eventually need generation-aware reads.
When it does, it declares that narrower shape in its own `agents/order_discovery/
ports.py` (e.g. `KnowledgeConsistencyPort`) rather than widening the shared
`KnowledgePort` here — generation semantics stay entirely out of the shapes every
other agent also depends on.
