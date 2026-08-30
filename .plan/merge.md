# Merge state — orchestrator record

Base: `a50c5500788f99e909f23099a81731b37c736b8c` (`refactor/unified-return-platform`).
Merge order: `T0 → S1 → S2 → V1 → V2 → V3 → ACC`, RV `PASS` (zero unresolved findings) required between every arrow.
Each slice branches from the latest RV-approved integration commit recorded here; the integration agent's shared-file changes ride the same slice branch and are reviewed in the combined diff.

| Slice | Branch | Status | RV rounds | Merged at | Notes |
|---|---|---|---|---|---|
| T0 | (trunk) | IN_PROGRESS | — | — | investigations complete (contracts.md §2); DR-11 ruled; calibration fixture pending |
| S1 | feat/s1-model-identity | NOT_STARTED | — | — | branches from base.sha |
| S2 | feat/s2-delivery-spine | NOT_STARTED | — | — | |
| V1 | feat/v1-template-review | NOT_STARTED | — | — | |
| V2 | feat/v2-ingress-relay | NOT_STARTED | — | — | |
| V3 | feat/v3-resolver-clarification | NOT_STARTED | — | — | |
| ACC | feat/acc-acceptance | NOT_STARTED | — | — | |
| RV calibration | rv-calibration/seeded-hardcoding | NOT_STARTED | — | never merges | isolated bait branch |
