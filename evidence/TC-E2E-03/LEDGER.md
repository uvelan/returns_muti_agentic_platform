# TC-E2E-03 Ledger — append-only

Pre-work SHA (rollback point): 138bbb80b3e74bd59526bc208fe4c4d5e7b8f745

| run_no | phase | gate | customer/order | first_failing_step | root cause | fix commit | outcome | evidence path |
|--------|-------|------|----------------|--------------------|------------|------------|---------|---------------|
| 1 | manual | G1 | DUANE ALVARADO / CJ800022 | - | - | - | clean | evidence/TC-E2E-03/run-1 |
| 2 | manual | G2 | RONALD OKONKWO / CG808900 | 16 | seeded shipment carried an empty rma_reference: the RMA is the record's upsert key and lives on the incoming notice, not in the merged field set | fix(shipments) RMA sourcing (this commit) | fail | evidence/TC-E2E-03/run-2 |
| 3 | manual | G2 | CARLOS ROARK / CL808800 | - | harness launched blind with colliding evidence dir; killed mid-flight before assertions (mixed-run evidence is rejected by rule) | run_flow TCE2E02_RUN_BASE env, harness-only | infra | - |
| 4 | manual | G2 | RUSSELL FITZGERALD / CA808587 | 18 | steps 1-17 clean; second console event 500d -- _record_on_case's insert-only fact append raised DuplicateKeyError on a repeated AWAITING_HANDOFF reading, contradicting its own idempotence contract | fix(shipments) idempotent reading append (this commit) | fail | evidence/TC-E2E-03/run-4 |
