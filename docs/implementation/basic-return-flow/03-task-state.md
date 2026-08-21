# 03 · Task state

`PENDING | IN_PROGRESS | BLOCKED | FAILED | COMPLETED`. At most one
`IN_PROGRESS`. Nothing is `COMPLETED` before its scoped validation passes.

| ID | Task | Phase | Status |
|---|---|---|---|
| T-01 | Record baseline, inspect worktree, initialise context files | 1 | COMPLETED |
| T-02 | Start and validate Docker infrastructure | 2 | COMPLETED |
| T-03 | Record the targeted test baseline | 1 | PENDING |
| T-04 | Start host services; verify liveness, readiness, manual mode, connectivity | 2 | COMPLETED |
| T-05 | Verify the live-path contracts C1 to C7 | 1 | COMPLETED |
| T-06 | Select one deterministic seeded order number | 3 | COMPLETED |
| T-07 | Display and confirm the order with all primary details | 4 | IN_PROGRESS |
| T-08 | Capture every configured required return detail | 5 | PENDING |
| T-09 | Disable policy evaluation through runtime configuration | 6 | PENDING |
| T-10 | Workflow Agent invokes Bay Assignment Agent; persist result | 7 | PENDING |
| T-16 | D-1: materialise seeded warehouse bays into `platform.bay_configuration` | 7 | COMPLETED |
| T-17 | D-2: run manual mode on the MANUAL provider; fix F-7, F-8, F-9 | 2 | COMPLETED |
| T-18 | Phase 6 config switch `policy_evaluation.enabled` (model + gate + activity) | 6 | COMPLETED |
| T-11 | Create one support work item with structured return and bay data | 8 | PENDING |
| T-12 | Render the complete template in Support Chat UI | 9 | PENDING |
| T-13 | Adversarial validations (24 listed) | -- | PENDING |
| T-14 | Regression tests for every fixed defect | -- | PENDING |
| T-15 | Final gates and `06-final-result.md` | -- | PENDING |
