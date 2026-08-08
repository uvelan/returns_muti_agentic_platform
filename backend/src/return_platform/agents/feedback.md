# Feedback Learning Agent

`feedback.py` — `FeedbackLearningAgent`

**Responsibility.** Turn aggregate operational metrics into policy-improvement
recommendations for human review — never alters production policy itself.

**Input.** `FeedbackAssessmentRequest` — session id, aggregate metrics, and evidence
references.

**Output.** `FeedbackAssessment` — recommendations, whether human review is required,
and a decision record.

**Queue / state.** `task_queue: returns.feedback-learning`,
`state_namespace: feedback_learning` (declarative today).

**Prompt / policy / AI route.** `ai_assisted: true` in
`config/returns/production.yaml`, and unlike Order Discovery/Return Workflow this one
has a real, dedicated, wired-but-unconsumed AI Gateway route already configured for it:
`ai_route_ref: FEEDBACK_RECOMMENDATION_V1` (`config/ai_gateway.yaml`). This class does
not call it yet — today's `assess()` is deterministic, config-driven aggregation.
Declaring the route now records where AI-assisted recommendation generation will plug
in without fabricating a call this class doesn't make.

**Knowledge access.** None. No database or graph read — metrics are supplied by the
caller.

**Side effects.** None — pure function of its request and configuration.
`reviewRequired` is always advisory; this agent never applies a policy change itself.

**Failure semantics.** Never raises for a normal request.

**Configuration.** `agents.feedback_learning` in `config/returns/production.yaml`.

**Extension/replacement.** Resolve via
`AgentRegistry.build(configuration).feedback_learning` or
`AgentRegistry.resolve("feedback_learning")`.

**This agent does not directly invoke another agent.**
