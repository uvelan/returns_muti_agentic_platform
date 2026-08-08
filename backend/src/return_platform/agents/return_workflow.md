# Return Workflow Agent

`return_workflow.py` — `ReturnWorkflowAgent`

**Responsibility.** Determine whether a return request is complete: identify missing
required fields, decide whether photo evidence is required, recommend a physical
return method, and draft a support-request summary when needed.

**Input.** `ReturnWorkflowAssessmentRequest` — session id, order source, product
presence, proposed return method, branch/associate ids, line items, optional pickup
assessment.

**Output.** `ReturnWorkflowAssessment` — completeness, missing fields, photo evidence
requirement, recommended return method, a drafted support summary, and a decision
record.

**Queue / state.** `task_queue: returns.return-workflow`,
`state_namespace: return_workflow` (declarative today).

**Prompt / policy / AI route.** None today, despite `ai_assisted: true` in
`config/returns/production.yaml` — this agent is pure, deterministic, config-driven
logic (`ReturnPlatformConfiguration.return_policy`/`.omc`). The flag is aspirational,
matching the same pattern as Order Discovery; no `ai_route_ref` is set for this agent
because there is no dedicated, currently-unused AI Gateway route to declare honestly.

**Knowledge access.** None. No database or graph read.

**Side effects.** None — pure function of its request and configuration.

**Failure semantics.** Never raises for a normal request; an incomplete return is a
valid, non-error outcome (`complete=false`, populated `missingFields`).

**Configuration.** `agents.return_workflow` in `config/returns/production.yaml` plus
`return_policy.*` and `omc.*`.

**Extension/replacement.** Resolve via
`AgentRegistry.build(configuration).return_workflow` or
`AgentRegistry.resolve("return_workflow")`.

**This agent does not directly invoke another agent.**
