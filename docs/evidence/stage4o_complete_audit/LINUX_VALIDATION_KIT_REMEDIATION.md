# Stage 4O — Linux Validation Kit Remediation

## Status

```text
Source validation kit: REMEDIATED
Static and dependency-light checks: PASS
Linux Docker/full-stack execution: NOT RUN IN THIS ENVIRONMENT
Runtime classification: PENDING
Production external integrations: NOT PROVEN
```

This remediation was prepared from the uploaded Stage 4O repository snapshot.
The snapshot contains neither `.git` metadata nor a root `.env`, so its claimed
commit ancestry and local credentials cannot be inspected. The execution runner
has Python 3.13.5 and Git 2.47.3, but only Node 22.16.0/npm 10.9.2 and no Docker
or Poetry. Therefore no Linux infrastructure, worker, browser, accessibility,
restart, or live-provider result is claimed here.

## Confirmed validation-kit defects

1. The Windows-to-Linux transfer manifest still required historical commit
   `91b2bf8a8825f607d8045715064eb384c780c252` and a missing reviewed patch.
2. The master Linux runner omitted all six Stage 4M scenarios, exact live AI
   pool validation, accessibility, restart/replay, and manual 23-screen proof.
3. Environment reconstruction did not enforce Node 24/npm 11 or install the
   Chromium browser required by Playwright.
4. The Stage 4M startup path sourced the entire secret-bearing `.env` with Bash.
   Unquoted JSON arrays and DSNs containing `&` were unsafe under that behavior.
5. The configuration gate did not validate the containerized application
   profile or assert the mandatory frontend route inventory.
6. The AI probe could select catalog alternatives instead of testing the exact
   configured model pools and inspected only the first credential per provider.
7. Scenario summary evidence did not explicitly require exactly six records.
8. The manual-screen gate duplicated its route list and did not require a real
   resolved URL for dynamic routes or a timezone-aware timestamp.
9. The final repository-state phase recorded a dirty tree but did not fail.
10. The Linux receipt generator could report `PASS` with missing or stale phase
    receipts and resolved Git state relative to the caller's current directory.
11. The Stage 4O audit summary still exposed the pre-remediation
    `SOURCE_INCOMPLETE` result as though it were the current classification.

## Implemented remediation

### Commit and evidence integrity

- Replaced the stale transfer contract with schema 2 ancestor verification.
  The validated tree must be on `master`, clean, and descend from the Stage 4O
  commit prefix `b278776`.
- Added one canonical 21-phase manifest used by both the master runner and final
  receipt generator.
- `--from-start` removes stale checkpoints and phase receipts before execution.
- Checkpoints are rejected when staged, unstaged, or untracked non-ignored
  repository changes exist.
- The final receipt now fails closed on missing receipts, invalid JSON,
  non-Linux receipts, failed phases, or commit/tree-fingerprint mismatches.
- The final repository-state gate now rejects any dirty working tree.

### Safe configuration

- Added a non-printing dotenv validator that checks `.env` permissions,
  duplicate assignments, executable shell syntax, required simulation modes,
  non-placeholder infrastructure passwords, and non-empty Google/NVIDIA pools.
- JSON arrays in `.env.example` are single-quoted for consistent parsing.
- Mongo DSNs are quoted so their query strings remain intact.
- Stage 4M startup no longer sources all of `.env`; it validates the file and
  exports only the five literal simulation flags.
- Bootstrap creates `.env` with mode `600` when the file is absent.

### Complete Linux proof sequence

- Added prerequisite enforcement for Python 3.13, Node 24, npm 11, Docker with
  Compose, Git, curl, jq, tar, sha256sum, and safe `.env` configuration.
- Added Chromium installation during environment reconstruction.
- Added automated closure proof for all six scenarios:
  `BRANCH_PARCEL`, `OFFSITE_HEAVY`, `BRANCH_LTL`, `OFFSITE_PARCEL`,
  `DIRECT_VENDOR`, and `NO_PHYSICAL_RETURN`.
- Added exact live AI credential/model pool validation and Stage 4N live-stack
  evidence capture.
- Added real browser JSON-result enforcement, accessibility JSON-result
  enforcement, restart/reseed/replay proof, and worker heartbeat revalidation.
- Added a commit/tree-bound manual attestation for all 23 mandatory routes.
  Static URLs must match exactly; dynamic routes must record a concrete
  session/operation URL.

### AI probe hardening

- Every configured Google and NVIDIA credential is authenticated against its
  provider catalog and represented only by a generated safe ID.
- Every distinct configured model is checked for catalog presence and receives
  one bounded minimal generation request through a healthy credential.
- Duplicate model IDs across complexity tiers, failed credentials, absent
  models, HTTP errors, and non-200 generations fail closed.
- Credentials, headers, prompts beyond the fixed minimal probe, and provider
  response bodies are never printed.

### Documentation and audit classification

- Updated README and Stage 4M/4N/Linux runbooks with the complete Linux sequence,
  safe dotenv format, all-six scenario loop, and manual-attestation workflow.
- Preserved the original Stage 4O findings as a pre-remediation baseline while
  marking commit prefix `b278776` as `SOURCE_VALIDATED` and Linux runtime proof
  as `PENDING`.
- The audit generator now writes a baseline summary instead of overwriting the
  post-remediation status.

## Files changed

```text
.env.example
README.md
docs/code_quality/LINUX_LIVE_VALIDATION_RUNBOOK.md
docs/evidence/code_quality/windows_to_linux_transfer.json
docs/evidence/stage4o_complete_audit/generate_audit_artifacts.py
docs/evidence/stage4o_complete_audit/validation_summary.json
docs/evidence/stage4o_complete_audit/LINUX_VALIDATION_KIT_REMEDIATION.md
docs/implementation/STAGE_4N_AI_GATEWAY_HARDENING.md
docs/runbooks/STAGE_4M_SIMULATED_E2E_RUNBOOK.md
docs/runbooks/STAGE_4N_AI_SIMULATOR_E2E.md
scripts/bootstrap_host.sh
scripts/start_stage4m_simulation.sh
scripts/probe_configured_ai_models.py
scripts/tests/test_configured_ai_model_probe.py
scripts/linux/00_validate_prerequisites.sh
scripts/linux/01_verify_transfer.sh
scripts/linux/02_reconstruct_environment.sh
scripts/linux/03_run_backend_quality.sh
scripts/linux/05_run_contract_and_config_checks.sh
scripts/linux/14_run_accessibility.sh
scripts/linux/14_run_ai_live_stack.sh
scripts/linux/14_run_real_e2e.sh
scripts/linux/14_run_simulated_scenarios.sh
scripts/linux/15_restart_and_replay.sh
scripts/linux/16_generate_linux_receipt.sh
scripts/linux/19_verify_repository_state.sh
scripts/linux/20_verify_manual_screen_attestation.sh
scripts/linux/lib/common.sh
scripts/linux/lib/scenario_evidence.sh
scripts/linux/mandatory_routes.json
scripts/linux/run_full_linux_validation.sh
scripts/linux/validate_env.py
scripts/linux/validation_phases.txt
scripts/linux/verify_mandatory_routes.py
```

## Validation executed against the uploaded snapshot

| Gate | Result |
|---|---|
| ZIP extraction/inventory | PASS — 878 entries |
| Bash syntax for all repository shell scripts | PASS |
| Python compileall for backend, scripts, and Stage 4O evidence tools | PASS |
| Mandatory route catalog/source verification | PASS — 23/23 |
| AI exact model/credential probe unit tests | PASS — 4 tests |
| Full `.env.example`-derived safe fixture | PASS — 102 assignments |
| Unquoted JSON rejection without value disclosure | PASS |
| Manual attestation create/accept/reject synthetic checks | PASS |
| Receipt missing/complete/mismatch synthetic checks | PASS |
| Transfer ancestor/clean/dirty synthetic checks | PASS |
| Canonical Linux phase inventory | PASS — 21 unique phases |
| Git patch replay against pristine uploaded snapshot | PASS |
| Ruff | BLOCKED — executable unavailable; package fetch unavailable |
| Strict MyPy | BLOCKED — executable unavailable; Poetry unavailable |
| Full backend pytest | BLOCKED — dependencies not installed |
| Frontend TypeScript/ESLint/Vitest/build | BLOCKED — Node/npm below contract and dependencies absent |
| Docker/full-stack/worker/scenario/browser/a11y/restart proof | BLOCKED — Docker unavailable |
| Git HEAD/branch/cleanliness/ancestor proof | BLOCKED — uploaded archive has no `.git` |
| Live Google/NVIDIA calls | NOT RUN — root `.env` intentionally absent |

## Required integration into the real Linux clone

Apply the remediation patch to a clean clone at or after Stage 4O, inspect it,
then commit it before running the master gate. The transfer validator rejects an
uncommitted tree by design.

```bash
git checkout master
git pull --ff-only
git merge-base --is-ancestor b278776 HEAD
git status --short

git apply --check stage4o_linux_validation_remediation.patch
git apply stage4o_linux_validation_remediation.patch
git diff --check
git status --short

git add .
git commit -m "fix: make Stage 4O Linux validation fail closed"
git status --short
```

Preserve the existing ignored `.env`. Do not copy any `.env` from an archive or
commit it. When creating a new local environment only:

```bash
test -f .env || cp .env.example .env
chmod 600 .env
```

Populate all placeholders and the Google/NVIDIA key arrays locally without
printing them. Then execute:

```bash
chmod +x scripts/*.sh scripts/linux/*.sh scripts/linux/lib/*.sh
./scripts/bootstrap_host.sh
./scripts/linux/run_full_linux_validation.sh --from-start
```

The first complete automated pass intentionally stops at:

```text
.runtime/linux-validation/evidence/manual-screen-validation.json
```

Inspect every route using the live stack, fill the concrete `resolvedUrl` for
both dynamic routes, set all route statuses and the top-level status to `PASS`,
record the operator and timezone-aware `checkedAt`, then run:

```bash
./scripts/linux/run_full_linux_validation.sh --resume
./scripts/linux/package_validation_results.sh
```

Only a final receipt with `overallStatus: PASS`, 21 validated phases, six closed
scenarios, exact live AI pools, browser/a11y results, restart/replay evidence,
and the 23-screen attestation supports Linux runtime promotion.
