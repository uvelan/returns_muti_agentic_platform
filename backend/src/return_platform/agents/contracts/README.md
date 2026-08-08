# agents/contracts

The one shape every agent implements, and the shapes it may reach for.

- `descriptor.py` — `AgentDescriptor`: agent_id, implementation_id, task_queue,
  state_namespace, prompt_ref, policy_ref, ai_route_ref, enabled, timeout, retry, rate/
  concurrency, circuit breaker. Built from `AgentConfiguration`
  (`configuration/return_configuration.py`) via `AgentDescriptor.from_configuration()`.
- `context.py` — `AgentExecutionContext`: everything an agent's `execute()` may reach
  for. Deliberately platform-neutral — no `.ai`, no `.knowledge`, no other agent, no
  domain type. `configuration` is a pinned `RuntimeConfigurationView`, never a
  `RuntimeConfigurationHandle` — an agent execution is always scoped to the release its
  session was pinned to when it started.
- `plugin.py` — `AgentPlugin[RequestT, ResultT]`: `descriptor` property +
  `async execute(request, context) -> result`. Generic over each agent's own
  strongly-typed request/response pair rather than one shared envelope, so a call site
  that already knows which agent it's invoking keeps full static type checking; only
  `agent_id`-keyed dynamic dispatch (a later phase's Temporal orchestration) narrows to
  `AgentPlugin[Any, Any]`.
- `ports.py` — `AgentAiPort`, `KnowledgePort`: shapes an agent resolves from
  `context.capabilities` rather than importing `ai_gateway`/`dynamic_knowledge`
  directly. Declared now; not yet bound to a provider (see `agents/README.md`).
- `dto.py` — the per-agent request/response pydantic models. Unchanged content from
  the former flat `agents/contracts.py`; split out so the package could grow the four
  files above without one file mixing DTOs, the descriptor, the context, and the
  plugin protocol together.

Everything above stays importable as `return_platform.agents.contracts.<name>` — the
package's `__init__.py` re-exports all of it, so this split needed no caller changes.

## Order Discovery's narrower port

Order Discovery is the only agent expected to eventually need generation-aware reads.
When it does, it declares that narrower shape in its own `agents/order_discovery/
ports.py` (e.g. `KnowledgeConsistencyPort`) rather than widening the shared
`KnowledgePort` here — generation semantics stay entirely out of the contract every
other agent also depends on.
