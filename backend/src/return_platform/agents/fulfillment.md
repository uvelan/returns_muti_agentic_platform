# Return Fulfillment Agent

`fulfillment.py` — `ReturnFulfillmentAgent`

**Responsibility.** Normalize raw OMC/carrier fulfillment status into a consistent
return-fulfillment view: normalized return status, whether customer resolution,
physical return, warehouse processing, and vendor recovery are each complete, and what
event is expected next.

**Input.** `FulfillmentAssessmentRequest` — return version, raw OMC/customer/product
resolution status strings, and a list of supporting facts.

**Output.** `FulfillmentAssessment` — normalized status, four completion booleans, the
next expected event, and a decision record.

**Queue / state.** `task_queue: returns.return-fulfillment`,
`state_namespace: return_fulfillment` (declarative today).

**Prompt / policy / AI route.** None. `ai_assisted: false` in
`config/returns/production.yaml` — this agent is intentionally deterministic status
normalization, never AI-assisted.

**Knowledge access.** None. No database or graph read — facts are supplied by the
caller.

**Side effects.** None — pure function of its request and configuration.

**Failure semantics.** Never raises for a normal request; an unrecognized raw status
normalizes to `"UNKNOWN"` rather than erroring.

**Configuration.** `agents.return_fulfillment` in `config/returns/production.yaml`
plus `omc.normalized_statuses`.

**Extension/replacement.** Resolve via
`AgentRegistry.build(configuration).return_fulfillment` or
`AgentRegistry.resolve("return_fulfillment")`.

**This agent does not directly invoke another agent.**
