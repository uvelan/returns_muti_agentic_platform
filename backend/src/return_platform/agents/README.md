# agents

The six independent business agents and the one registry that constructs and
resolves them. See `contracts/README.md` for the shared plugin contract these agents
implement, and `registry/README.md` for how they're constructed and resolved.

## The six agents

There is no per-agent subdirectory (each agent is one file) — each gets a sibling
`<name>.md` next to its `.py` instead of a directory README:

```
Order Discovery      order_discovery.py     order_discovery.md
Order Analysis       order_analysis.py      order_analysis.md
Return Workflow      return_workflow.py     return_workflow.md
Return Fulfillment   fulfillment.py         fulfillment.md
Bay Assignment       bay_assignment.py      bay_assignment.md
Feedback Learning    feedback.py            feedback.md
```

Each covers responsibility, input, output, queue, state, prompt, policy, AI route,
knowledge access, side effects, failure semantics, configuration, and
extension/replacement.

Two things are true of every one of them and are not repeated per-README:

- **This agent does not directly invoke another agent.** No agent imports another
  agent's module. Sequencing across agents is the orchestrator's job (`operations/`
  today; Temporal-driven configuration in a later phase), never an agent's.
- **No agent imports `ai_gateway` or `dynamic_knowledge`/`data_platform.graph`
  directly.** The one agent that touches AI today (`OrderAnalysisAgent`) receives an
  `AIGatewayService` as an explicit method parameter from its caller — it does not
  reach for it itself. `tests/agents/test_no_cross_agent_imports.py` and
  `tests/agents/test_context_has_no_module_fields.py` keep both invariants enforced.

## What's not here yet

`AgentPlugin.execute()` exists on every agent (satisfying the plugin contract), but
`context.capabilities` has no published `AgentAiPort`/`KnowledgePort` provider yet — no
`bootstrap/adapters/` module binds one. `OrderAnalysisAgent.execute()` raises
`NotImplementedError` for this reason; every other agent's `execute()` works today
because it never needed a capability, just a thin async wrapper around its existing
`assess()`. Real callers keep using `.assess()`/`.analyze()`/`.disambiguate()` directly,
exactly as they did before this contract existed — `execute()` becomes load-bearing
once a later phase adds Temporal orchestration that resolves agents generically by
`agent_id` instead of by import.
