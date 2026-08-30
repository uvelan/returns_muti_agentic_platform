# Merge state — orchestrator record

Base: `a50c5500788f99e909f23099a81731b37c736b8c` (`refactor/unified-return-platform`).
Merge order: `T0 → S1 → S2 → V1 → V2 → V3 → ACC`, RV `PASS` (zero unresolved findings) required between every arrow.
Each slice branches from the latest RV-approved integration commit recorded here; the integration agent's shared-file changes ride the same slice branch and are reviewed in the combined diff.

| Slice | Branch | Status | RV rounds | Merged at | Notes |
|---|---|---|---|---|---|
| T0 | (trunk) | IN_PROGRESS | — | — | investigations complete (contracts.md §2); DR-11 ruled; calibration fixture pending |
| S1 | feat/s1-model-identity | MERGED | 1 (PASS, zero findings) | 5d58b90 | review 6bdb5bd; S2/V1 pipelined bases confirmed valid (7438e07 = approved head); advisories A1 (legacy-name shadowing) + A2 (:: separator) watchlist for S2/V2 |
| S2 | feat/s2-delivery-spine | IN_PROGRESS | — | — | PIPELINED off S1 candidate 7438e07 (user-directed ≤3 parallel agents); rebases if S1-1 ≠ PASS |
| V1 | feat/v1-template-review | IN_PROGRESS (phase 1: brief items 1,2,5) | — | — | PIPELINED off S1 candidate 7438e07; phase 2 (gate/API/panel UI) after S2 merges |
| V2 | feat/v2-ingress-relay | NOT_STARTED | — | — | |
| V3 | feat/v3-resolver-clarification | NOT_STARTED | — | — | |
| ACC | feat/acc-acceptance | NOT_STARTED | — | — | |
| RV calibration | rv-calibration/seeded-hardcoding | CAUGHT (CHANGES_REQUIRED, F1 blocking rule-1) | 1 | never merges | review at .plan/reviews/calibration-1.md (commit d59e017); RV cleared to gate real branches |
