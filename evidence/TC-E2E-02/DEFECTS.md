# TC-E2E-02 Defect Log

Platform defects found by the campaign, each with its fix commit and the run that proved the fix.
Harness-only defects (responder rules, driver assertions) live in the ledger, not here.

| # | step | defect | fix | proved by run |
|---|------|--------|-----|---------------|
| D1 | 7/14 | A confirmed case did not survive into the conversation's later turns: the coordinator set `case_id` in graph state at confirmation but never persisted it to the conversation document nor reseeded it, so elicitation never engaged and a Support outcome had no path back into the associate's original chat — despite `AgentTurnContext.case_facts` documenting exactly that relay. | `fix(order-agent): a confirmed case survives into the conversation's later turns` (caseId persisted on the conversation document, reseeded each turn) | run 5 (elicitation engaged), run 8 (Support relay end-to-end) |
| D2 | 7 | The agent runtime built its fact catalogue and discovery config from the packaged file, not the active release: an operator adding a clarification field in a release changed `/api/runtime-config`'s advertisement but every answer to the new question was logged `order_agent_unconfigured_observed_facts` and discarded. | `fix(order-agent): the agent's clarification policy comes from the release, not the packaged file` (factory takes the caller's resolved release; both worker call sites pass it) | run 5 |
| D3 | 7/11 | Nothing derived the parcel-vs-freight class, so every chat-driven return stalled on Support asking for `return_method` — a question the spec says must never be asked. | `feat(returns): the parcel-vs-freight class is derived from the product, never asked` (`return_policy.return_method_derivation`, per-line, recorded as a DERIVED case fact) | run 8 |
| D4 | A3 | The Support Response Agent issued exactly one return record covering every selected line; a parcel-class and an LTL-class item shared one package, one label. | `feat(returns): one return record per shipping class, packages never mixed` (per-item derived method on the handoff; the agent plans one RMA per class group; the executor records all of them) | run 17 (2 records, distinct RMAs/labels, no line in two packages) |
| D5 | 4 (Phase B) | The reasoning prompt gave the live model no path from "customer confirmed" to "list that customer's orders" — models asked the associate for an order number the scenario says they do not have. | releases `tce2e02-prompt-orders` → `tce2e02-prompt-v20` (`promptVersion` v17→v20): mandatory worked example — GRAPH_QUERY customer→orders when no rows, RESPOND with per-row GRAPH_FACTs when rows present, plus the ORDER_SEARCH signal-key example. Prompt-only, release-versioned, never amended. | runs 25/28 (step 4 passing live) |

## Configuration gaps closed by release (no code)

| release | what it declares | why |
|---------|------------------|-----|
| tce2e02-elicitation-facts | `ordered_quantity`, `branch_location`, `proof_reference` on `clarification_policy.fields` | a fact name the policy does not define is discarded; the TC's elicitation details need operator-declared names |
| tce2e02-details-required | `return_case.return_details_required: true` | the deployment allowed a case to reach Support with no selected items; the handoff then carried `order.items: []` and the support agent rightly refused it |
| tce2e02-method-derivation / tce2e02-freight-keywords | the derivation table (BRANCH_UPS default, BRANCH_LTL freight, catalogue keywords incl. `WHTR`) | operator-owned rule for D3 |
| tce2e02-bay-prearrival | `bay.allow_prearrival_reservation: true` | A4 needs the bay to perform real reads so an infrastructure stall can exercise the timeout path; with the flag off, the bay answers instantly from configuration |
| tce2e02-phaseb-* | provider/model ranking releases | operator incident response to live provider degradation (see ledger runs 23–37) |

## Known environmental limits (not defects)

* Google free tier: gemini-3.7 ~20 requests/min rolling window; gemini-3.6 exhausted a longer quota during the campaign; 3.7 also intermittently answers 503 "high demand".
* NVIDIA llama-3.1-70b cannot reliably hold the AgentAction contract across a 15-step flow (clarify/search loops) — documented per run in the ledger; usable as emergency fallback only.
* The `.env` OPENAI key is empty (`[]`) and the ANTHROPIC key is invalid — neither provider is actually available in this environment despite being configured.
