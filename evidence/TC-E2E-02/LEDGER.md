# TC-E2E-02 Ledger — append-only

One row per run. Never edited after append. `outcome`: clean | fail | infra.

| run_no | phase | gate | customer/order | first_failing_step | root cause | fix commit | outcome | evidence path |
|--------|-------|------|----------------|--------------------|------------|------------|---------|---------------|
| 1 | A | A1 | LUIS FLETCHER / CG807268 | 7 | elicitation used CLARIFY per question; suspended thread spends the per-thread clarification budget answer by answer (ORDER_AGENT_CLARIFICATION_BUDGET_EXCEEDED); qty/branch/proof also not operator-declared fact names, so their answers were discarded | responder rework + release tce2e02-elicitation-facts-20260825-221504 | fail | evidence/TC-E2E-02/run-1 |
| 2 | A | A1 | YUKI COUSINS / CT800996 | 7 | conversation dropped case_id after the confirming turn: coordinator never persisted/reseeded it, so elicitation fell through to a fresh customer search | fix(order-agent) caseId persistence (this commit) | fail | evidence/TC-E2E-02/run-2 |
| 3 | A | A1 | EVELYN GOODWIN / CH806814 | 7 | responder read captured facts by wire key `name`; the wire key is `fact`, so every captured detail looked missing and the reason question repeated | responder fix (fact key), harness-only | fail | evidence/TC-E2E-02/run-3 |
