# Order Analysis Agent

`order_analysis.py` — `OrderAnalysisAgent`

**Responsibility.** Analyze source-backed order candidates with AI assistance:
explain conflicts/uncertainty and draft a natural-language clarification question for
an associate to ask a customer. Never chooses or confirms an order, never creates a
return, never exposes hidden customer data. `disambiguate()` handles the follow-up
turn: given the customer's free-text answer, either identify a single candidate or
propose a better question.

**Input.** `analyze()`: `OrderAnalysisRequest` (session id, candidates, supplied
evidence). `disambiguate()`: candidate list, the user's response, the allowed
disambiguating fields, and the session id.

**Output.** `analyze()`: `OrderAnalysisAssessment` (smart question, explanation,
decision). `disambiguate()`: `(candidate_id | None, smart_question | None)`.

**Queue / state.** `task_queue: returns.order-analysis`,
`state_namespace: order_analysis` (declarative today, consumed by a later phase's
Temporal orchestration).

**Prompt / policy / AI route.** The only one of the six agents that calls AI today.
`analyze()` calls `AI_ROUTE: ORDER_CANDIDATE_ANALYSIS_V1`; `disambiguate()` calls a
second, related route, `ORDER_CANDIDATE_DISAMBIGUATION_V1` — not yet expressible as a
second `ai_route_ref`, since the config shape carries one primary
route per agent (`ai_route_ref: ORDER_CANDIDATE_ANALYSIS_V1` in
`config/returns/production.yaml`). Both routes are real, wired entries in
`config/ai_gateway.yaml`.

**Knowledge access.** None directly — candidates are supplied by the caller.

**Side effects.** None from this class itself; the AI call goes through
`AIGatewayService`, which the caller passes in explicitly (see "Not yet wired" below).

**Failure semantics.** Falls back to a deterministic, no-AI response
(`decision="DETERMINISTIC_FALLBACK"`) when `ai_assisted` is false in config. Does not
retry or fail closed on a malformed AI response — treats unparsable JSON as plain
explanation text and continues.

**Configuration.** `agents.order_analysis` in `config/returns/production.yaml`.

**Extension/replacement.** Reach it via
`AgentRegistry.build(configuration).order_analysis`, then call
`analyze()`/`disambiguate()` with an `AIGatewayService`, exactly as its only caller
(`operations/associate_flow.py`) does. This agent is why the generic
`AgentPlugin.execute()` path was removed rather than completed (AGT-02): it needs an
`AgentAiPort` resolved from `AgentExecutionContext.capabilities`, no adapter under
`bootstrap/adapters/` publishes `CapabilityName.AI_INVOCATION`, and passing the gateway
as an explicit parameter is what the callers already did and what keeps the agent from
reaching for the gateway itself.

**This agent does not directly invoke another agent.**
