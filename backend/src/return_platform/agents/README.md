# agents

The seven independent business agents and the one registry that constructs them.
See `contracts/README.md` for the shapes they share, and `registry/README.md` for
how they're constructed.

## The seven agents

There is no per-agent subdirectory (each agent is one file) — each gets a sibling
`<name>.md` next to its `.py` instead of a directory README:

```
Order Discovery      order_discovery.py     order_discovery.md
Order Analysis       order_analysis.py      order_analysis.md
Return Workflow      return_workflow.py     return_workflow.md
Return Fulfillment   fulfillment.py         fulfillment.md
Bay Assignment       bay_assignment.py      bay_assignment.md
Feedback Learning    feedback.py            feedback.md
Support Response     support_response.py    support_response.md
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

## How an agent is called

By name, on a concrete instance: `.assess()`, or `.analyze()`/`.disambiguate()` for
Order Analysis. Each takes and returns that agent's own strongly-typed request and
result, so a caller is type-checked against the agent it actually invokes.

There is deliberately no generic `execute(request, context)` and no
`registry.resolve(agent_id)` (AGT-02). Both existed, and neither was ever used: every
production caller reached for a concrete agent, `execute()` was a thin async wrapper
around `assess()` on five of the six, and on the sixth it could not be written at all
— `OrderAnalysisAgent` needs an `AIGatewayService`, `AgentExecutionContext` carries no
`.ai` by design, and no `bootstrap/adapters/` module publishes an `AgentAiPort` under
`CapabilityName.AI_INVOCATION` for it to resolve one from. It raised
`NotImplementedError` instead. A dispatch path that cannot carry the one agent that
most needs it is not a dispatch path, so it is gone rather than left as a promise.

The same removal took `WorkflowStageHandlerType.AGENT` with it: a configured stage can
only be `ACTIVITY`, because that is the only kind anything can execute.
