# Merge state — orchestrator record

Base: `a50c5500788f99e909f23099a81731b37c736b8c` (`refactor/unified-return-platform`).
Merge order: `T0 → S1 → S2 → V1 → V2 → V3 → ACC`, RV `PASS` (zero unresolved findings) required between every arrow.
Each slice branches from the latest RV-approved integration commit recorded here; the integration agent's shared-file changes ride the same slice branch and are reviewed in the combined diff.

| Slice | Branch | Status | RV rounds | Merged at | Notes |
|---|---|---|---|---|---|
| T0 | (trunk) | IN_PROGRESS | — | — | investigations complete (contracts.md §2); DR-11 ruled; calibration fixture pending |
| S1 | feat/s1-model-identity | MERGED | 1 (PASS, zero findings) | 5d58b90 | review 6bdb5bd; S2/V1 pipelined bases confirmed valid (7438e07 = approved head); advisories A1 (legacy-name shadowing) + A2 (:: separator) watchlist for S2/V2 |
| S2 | feat/s2-delivery-spine | IN_PROGRESS (steps 01–03 done @1cf2d44; resumed at step:04) | — | — | PIPELINED off S1 approved head 7438e07. Agent killed by API session limit after step:03, resumed cold via ledger. Recorded deviations for RV: (a) brief path typo `operations/return_case_recovery.py` → real `workflows/return_case_recovery.py`; (b) append-only add of 2 constants to S1's fact_names.py (that file's own docstring invites it; contract forbids literals elsewhere) |
| V1 | feat/v1-template-review | IN_PROGRESS (phase 1 steps 01–04 done @176f1d5; resumed at step:05 config UI) | — | — | PIPELINED off S1 approved head 7438e07. Killed by session limit after step:04, resumed cold. Phase 2 (gate/API/panel UI) dispatches after S2 merges. Note: step:04 touched main.py (router mount, required for contracts:generate) — flag for RV |
| ACC-1 | feat/acc-harness | UNDER_RV_REVIEW (candidate f433237, off e0a5f6c) | 1 open | — | Phase 1 (brief items 1,2,7) complete: fact-name AST guard reading vocabulary from fact_names.py at runtime + proved against a literal-bearing source; Mon–Fri calendar fixture (id deliberately ≠ `default` so a forgetting scenario fails loudly); chaos_restart primitives. +35 passing, 0 new failures, 0 production files touched. Residual risk: live smoke test never executed (datastores down) — flagged for the acceptance run. Author-reported harness bug found+fixed by its own tests (orphaned children); job-object alternative measured worse and removed. Open observation for a future slice: promoting the private config→domain calendar mapping onto operations/business_calendar.py would let ACC drop a deliberate duplicate |
| V2 | feat/v2-ingress-relay | NOT_STARTED | — | — | |
| V3 | feat/v3-resolver-clarification | NOT_STARTED | — | — | |
| ACC | feat/acc-acceptance | NOT_STARTED | — | — | |
| RV calibration | rv-calibration/seeded-hardcoding | CAUGHT (CHANGES_REQUIRED, F1 blocking rule-1) | 1 | never merges | review at .plan/reviews/calibration-1.md (commit d59e017); RV cleared to gate real branches |
