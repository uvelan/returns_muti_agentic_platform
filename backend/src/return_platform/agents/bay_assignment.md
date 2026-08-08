# Bay Assignment Agent

`bay_assignment.py` — `BayAssignmentAgent`

**Responsibility.** Filter and rank eligible warehouse bays for a return, given its
required capacity, hazardous/oversized flags, and the return method, and explain any
exclusions.

**Input.** `BayAssessmentRequest` — physical status, return method, required capacity,
hazardous/oversized flags, and the candidate bay list.

**Output.** `BayAssessment` — a recommended bay id (or none), the full eligible-bay
list, per-exclusion reasons, and a decision record.

**Queue / state.** `task_queue: returns.bay-assignment`,
`state_namespace: bay_assignment` (declarative today).

**Prompt / policy / AI route.** None. `ai_assisted: false` in
`config/returns/production.yaml` — deterministic filtering/ranking only.

**Knowledge access.** None directly. Bay candidates are supplied by the caller
(`operations/warehouse/service.py::WarehousePlacementService`, which reads bay state
via `SQLBusinessStateRepository` and passes the result in) — the agent itself never
touches a database.

**Side effects.** None — pure function of its request.

**Failure semantics.** Never raises for a normal request; no eligible bay is a valid,
non-error outcome (`recommendedBayId=None`, populated `excludedReasons`).

**Configuration.** `agents.bay_assignment` in `config/returns/production.yaml`.

**Extension/replacement.** Resolve via
`AgentRegistry.build(configuration).bay_assignment` or
`AgentRegistry.resolve("bay_assignment")`.

**This agent does not directly invoke another agent.**
