# TC-E2E-02 Ledger — append-only

One row per run. Never edited after append. `outcome`: clean | fail | infra.

| run_no | phase | gate | customer/order | first_failing_step | root cause | fix commit | outcome | evidence path |
|--------|-------|------|----------------|--------------------|------------|------------|---------|---------------|
| 1 | A | A1 | LUIS FLETCHER / CG807268 | 7 | elicitation used CLARIFY per question; suspended thread spends the per-thread clarification budget answer by answer (ORDER_AGENT_CLARIFICATION_BUDGET_EXCEEDED); qty/branch/proof also not operator-declared fact names, so their answers were discarded | responder rework + release tce2e02-elicitation-facts-20260825-221504 | fail | evidence/TC-E2E-02/run-1 |
| 2 | A | A1 | YUKI COUSINS / CT800996 | 7 | conversation dropped case_id after the confirming turn: coordinator never persisted/reseeded it, so elicitation fell through to a fresh customer search | fix(order-agent) caseId persistence (this commit) | fail | evidence/TC-E2E-02/run-2 |
| 3 | A | A1 | EVELYN GOODWIN / CH806814 | 7 | responder read captured facts by wire key `name`; the wire key is `fact`, so every captured detail looked missing and the reason question repeated | responder fix (fact key), harness-only | fail | evidence/TC-E2E-02/run-3 |
| 4 | A | A1 | LISA CHAPMAN / CI800157 | 7 | agent runtime built its fact catalogue from the packaged file, not the active release: answers to release-declared elicitation fields were discarded as unconfigured | fix(order-agent) release-sourced clarification policy (this commit) | fail | evidence/TC-E2E-02/run-4 |
| 5 | A | A1 | LIONEL KNAPP / CW802018 | 13 | steps 1-12 passed but the handoff carried order.items=[]: return_details_required=false let the workflow draft before the selection landed, and the support agent rightly refuses an item-less handoff (SKIPPED_NO_HANDOFF) | release tce2e02-details-required (config, no code) | fail | evidence/TC-E2E-02/run-5 |
| 6 | A | A1 | CONNIE FERRARO / CA806592 | 13 | steps 1-12 clean; support agent asked for return_method because nothing derives the shipping class from the product | feat(returns) return_method_derivation (this commit) + release tce2e02-method-derivation | fail | evidence/TC-E2E-02/run-6 |
| 7 | A | A1 | ANDRE BURGESS / CF804497 | 14 | steps 1-13 clean including RMA issue+consume; the relay turn's USER_PROVIDED_FACT statements lack source_message_id (harness rule bug -- case facts are REASONED_SUGGESTION) | responder fix, harness-only | fail | evidence/TC-E2E-02/run-7 |
| 8 | A | A1 | ROSA LATTIMORE / CK809684 | - | - | - | clean | evidence/TC-E2E-02/run-8 |
