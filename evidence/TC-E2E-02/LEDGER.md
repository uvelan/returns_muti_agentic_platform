# TC-E2E-02 Ledger — append-only

One row per run. Never edited after append. `outcome`: clean | fail | infra.

| run_no | phase | gate | customer/order | first_failing_step | root cause | fix commit | outcome | evidence path |
|--------|-------|------|----------------|--------------------|------------|------------|---------|---------------|
| 1 | A | A1 | LUIS FLETCHER / CG807268 | 7 | elicitation used CLARIFY per question; suspended thread spends the per-thread clarification budget answer by answer (ORDER_AGENT_CLARIFICATION_BUDGET_EXCEEDED); qty/branch/proof also not operator-declared fact names, so their answers were discarded | responder rework + release tce2e02-elicitation-facts-20260825-221504 | fail | evidence/TC-E2E-02/run-1 |
