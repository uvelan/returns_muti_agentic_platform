# CFG — execution ledger

Append-only. One entry per step. Every command and its output is pasted from the terminal, never
transcribed from memory. Brief: `.plan/tracks/CFG.brief.md`. Audit: `evidence/config_audit/FINAL_REPORT.md`.

## Decisions

| ID | Decision | Answer | Recorded |
|---|---|---|---|
| D-CFG-1 | delete never-wired files/loaders | pending | — |
| D-CFG-2 | feature_flags / extensions | pending | — |
| D-CFG-3 | split production.yaml by function | pending | — |
| D-CFG-4 | env switches → release `deployment` section | pending | — |
| D-CFG-5 | retire `/data-console/v1` modules | pending | — |
| D-CFG-6 | undecidable dev-host keys | pending | — |

## Leases

| Lease | Branch | Base sha | Model | Status | Drop | RV | Merged at |
|---|---|---|---|---|---|---|---|
| CFG-0 | feat/cfg-0-audit-fixes | 42b0536b + applied fixes (worktree cfg-verify) | orchestrator | NOT_STARTED | — | — | — |
| CFG-1 | feat/cfg-1-dead-code | after CFG-0 | Sonnet | NOT_STARTED | — | — | — |
| CFG-2 | feat/cfg-2-config-split | after CFG-1 | Opus spike → Sonnet | NOT_STARTED | — | — | — |
| CFG-3a | feat/cfg-3a-config-api | after CFG-0 | Sonnet | NOT_STARTED | — | — | — |
| CFG-3b | feat/cfg-3b-form-primitives | after CFG-0 | Opus note → Sonnet | NOT_STARTED | — | — | — |
| CFG-4 | feat/cfg-4-screens-a | after CFG-3a + CFG-3b | Sonnet | NOT_STARTED | — | — | — |
| CFG-5 | feat/cfg-5-screens-b | after CFG-4 | Sonnet | NOT_STARTED | — | — | — |
| CFG-6 | feat/cfg-6-deployment-section | after CFG-2 + CFG-3a | Opus spike → Sonnet | NOT_STARTED | — | — | — |
| CFG-7 | feat/cfg-7-acceptance | after CFG-5 + CFG-6 | Haiku + Sonnet + RV | NOT_STARTED | — | — | — |

## Steps

(none yet — the first entry is CFG-0 step:00, the base check.)
