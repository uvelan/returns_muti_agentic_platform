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
| 8b | A | A1 | ROSA LATTIMORE / CK809684 | - | downstream verification (step 16): omc row, graph sync, single case id, no branch-inventory posting | - | clean | evidence/TC-E2E-02/run-8 |
| 9 | A | A2 | KURT MONROE / CH808807 | 6 | order-discovery worker was not restarted after the derivation code change; its refresh loop rejected the new release field and the turn hung | worker restart (harness sequencing) | infra | evidence/TC-E2E-02/run-9 |
| 10 | A | A2 | NOEL WILKERSON / CT807912 | 1 | Temporal RPC timeouts under host load; neo4j container wedged (kill+start), sqlserver restarted | container restarts per infra ladder | infra | evidence/TC-E2E-02/run-10 |
| 11 | A | A2 | ERIC BOONE / CF803505 | 1 | order-discovery worker had died during the container recovery window; turn never reached a poller | worker restart per infra ladder | infra | evidence/TC-E2E-02/run-11 |
| 12 | A | A2 | GLENN WINSLOW / CI807883 | 8 | harness picked an order outside the standard return window; policy REVIEW_REQUIRED is the correct platform answer (OUTSIDE_STANDARD_RETURN_WINDOW) | run selection fixed (recent orders only) | fail | evidence/TC-E2E-02/run-12 |
| 13 | A | A2 | VERONICA LAMBERT / CT805233 | - | - | - | clean | evidence/TC-E2E-02/run-13 |
| 14 | A | A1 (re-clear after agent change) | NOEL WILKERSON / CT807912 | - | - | - | clean | evidence/TC-E2E-02/run-14 |
| 15 | A | A2 (re-clear after agent change) | ERIC BOONE / CF803505 | - | - | - | clean | evidence/TC-E2E-02/run-15 |
| 16 | A | A3 | SIDNEY ARNETT / CO809044 | 13 | platform produced 2 correct per-class records; harness assertion raced the async projection (later verified clean on resume) | run_flow poll fix, harness-only | fail | evidence/TC-E2E-02/run-16 |
| 17 | A | A3 | RACHEL WHITFIELD / CW809330 (2 items: parcel + freight) | - | - | - | clean | evidence/TC-E2E-02/run-17 |
| 18 | A | A4 | NADIA GALLARDO / CO807909 | 9 | bay answered from configuration (PRE_ARRIVAL_NOT_ALLOWED) before touching any store, so pausing SQL cannot force the timeout; timeout path needs allow_prearrival_reservation=true so placement performs real reads | release flip + neo4j pause (next run) | fail | evidence/TC-E2E-02/run-18 |
| 19 | A | A4 | YOLANDA KAMINSKI / CJ805218 | 1 | responder re-read answered request files each poll and raced the provider's unlink on Windows (WinError 32 -> PROVIDER_UNAVAILABLE) | responder filename-keyed skip, harness-only | fail | evidence/TC-E2E-02/run-19 |
| 20 | A | A4 | YOLANDA KAMINSKI / CJ805218 | 9 | return-workflow worker predated the bay-prearrival release; bay still answered from the old configuration | worker restart | infra | evidence/TC-E2E-02/run-20 |
| 21 | A | A4 | LANCE LIVINGSTON / CZ808074 | - | bay window elapsed with no answer (graph paused during the bay call under allow_prearrival_reservation=true); return proceeded without a bay, nothing downstream blocked | - | clean | evidence/TC-E2E-02/run-21 |

**Phase A gates: A1 = run 14, A2 = run 15, A3 = run 17, A4 = run 21 — all on the final agent code. Phase B begins.**
