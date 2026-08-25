# Support Response Agent

**Responsibility.** Plan Support's answer to a Channel B handoff: the RMA
reference, the tracking and label references where the confirmed return method
requires them, the return location, and the handling instructions for the
product coming back. Where the handoff lacks a fact the method requires, the
agent plans a clarification question instead of inventing the fact.

**Input.** `SupportResponseRequest` — the facts of one `support-handoff-v1`
payload, parsed by the caller. `returnMethod` is the operator-owned string
vocabulary, never an enum (D23/CFG-03).

**Output.** `SupportResponseAssessment` — either a `SupportRmaPlan` (ready) or
`missingFields` plus a `clarificationRequest` (not ready), always with the
message text the Support conversation shows and an `AgentDecisionView`.

**Policy.** The artifacts issued are exactly
`return_policy.return_method_requirements[method].requires`, read from the
released configuration: a method that requires no LABEL gets no label, a method
the table does not know blocks on `return_method`. References are deterministic
(`RMA-<case>`, `TRK-…`, `LBL-…`) so a retry plans the same artifacts.
Photo-required reason codes and non-new conditions add handling lines.

**AI route.** None. The handoff is structured data and the plan is rule work;
language on the thread is composed from facts, not generated.

**Side effects.** None. `operations/return_support/auto_responder.py` executes
the plan: it posts the message to the Support thread (what the Support UI chat
renders) and records the outcome through `DurableSupportEventStore`, the same
seam the human console's `submit_return_outcome` uses, idempotent on
`support-response-agent:<workItemId>`.

**Failure semantics.** Pure and total: any parseable request returns an
assessment. Refusals are expressed as `CLARIFICATION_REQUIRED`, never raised.

**Configuration.** `agents.support_response` in the returns release.

**Extension.** To let a model draft the thread message, route the composed
facts through the AI gateway (`SUPPORT_CASE_ANALYSIS_V1` is the nearest task) in
the executor — never in this agent, which stays deterministic.
