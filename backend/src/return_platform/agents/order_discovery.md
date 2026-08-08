# Order Discovery Agent

`order_discovery.py` — `OrderDiscoveryAgent`

**Responsibility.** Rank source-backed order candidates against supplied evidence
(matched/conflicting anchors) and classify the order source. Never confirms or selects
a candidate on its own — always defers to human confirmation.

**Input.** `DiscoveryAssessmentRequest` — supplied evidence plus a list of candidates,
each with matched/conflicting evidence anchors.

**Output.** `DiscoveryAssessment` — ranked candidates with scores/explanations, the
classified order source, whether the result is ambiguous, and an optional next
clarification question.

**Queue / state.** `task_queue: returns.order-discovery`,
`state_namespace: order_discovery` (declarative today — nothing consumes them until
Temporal orchestration lands in a later phase).

**Prompt / policy / AI route.** None today. `ai_assisted: true` in
`config/returns/production.yaml` is aspirational — this agent is pure, deterministic,
config-driven scoring (anchor weights, conflict penalty, ambiguity gap, all from
`ReturnPlatformConfiguration.discovery`) with no AI call anywhere in it. AI-assisted
discovery reasoning belongs to Order Discovery's own future LangGraph decomposition
(Phase 5A+), not to this class as it stands.

**Knowledge access.** None. No database or graph read.

**Side effects.** None — pure function of its request and configuration.

**Failure semantics.** Never raises for a normal request; an empty/ambiguous candidate
list is a valid, non-error outcome (`ambiguous=true`, a clarification question
returned).

**Configuration.** `agents.order_discovery` in `config/returns/production.yaml` (name,
version, capabilities) plus `discovery.*` (anchor weights, conflict penalty, ambiguity
gap, web order pattern) and `clarification_policy.*` (which fields to ask about).

**Extension/replacement.** Resolve via `AgentRegistry.build(configuration).order_discovery`
or `AgentRegistry.resolve("order_discovery")`; a different implementation only needs to
satisfy `AgentPlugin[DiscoveryAssessmentRequest, DiscoveryAssessment]`.

**This agent does not directly invoke another agent.**
