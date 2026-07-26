# Full Codebase Review — Findings Report

## Review Metadata

| Field | Value |
|---|---|
| **Review Run ID** | `9805935d-756d-42c5-b30d-7773b64a3905` |
| **Handoff ID** | `fcd7a39e-54e0-4601-8f4c-044674259584` |
| **UTC Timestamp** | 2026-07-26T02:20:00Z |
| **Environment** | Windows 11 (10.0.26200.0), PowerShell 5.1 |
| **Python** | 3.13.14 |
| **Poetry** | 2.4.1 |
| **Node** | v24.14.0 |
| **npm** | 11.1.0 |
| **Docker** | 29.6.2 |
| **Git** | 2.55.0.windows.3 |
| **Baseline Commit** | `a908c5c1a8db52d4d2bbfe12036568031ec234f1` |
| **Working-Tree Strategy** | In-place review — pre-existing user modifications preserved |
| **Tracked Files** | 574 |
| **Untracked Files (User)** | 67 new files |
| **Modified Tracked (User)** | 80 files |
| **Deleted Tracked (User)** | 9 files |

---

## Executive Verdict

| Dimension | Status |
|---|---|
| Repository Health | NEEDS_REMEDIATION |
| Windows Gate (Baseline) | **FAIL** — ruff: 207 errors; mypy: 32 errors; frontend lint: 57 errors; pytest: BLOCKED |
| Highest-Risk Defect | **F-002**: Async correctness in `MongoSimulationRepository.operation_counts` |
| Data-Integrity Risk | **F-004**: Post-transaction inconsistency in `create_work_item` |
| Security Risk | `--reload` in production uvicorn script; hardcoded MongoDB keyfile |
| Linux Validation | PENDING (Stage B) |

---

## Findings Table

| ID | Sev | Area | Location | Finding |
|---|---|---|---|---|
| F-001 | HIGH | Type | `dependency_simulation/repository.py:20` | `ReturnDocument` fallback class in `except` block causes mypy `no-redef` — same name already defined by import |
| F-002 | CRITICAL | Async | `dependency_simulation/repository.py:152` | `aggregate()` used as async iterable — mypy reports it returns a coroutine; requires PyMongo 4.17 source verification |
| F-003 | HIGH | Type | `workflows/production_return_state.py:267` | `replace(state, **updates)` with `dict[str, object]` — 4 mypy `arg-type` errors; loses type safety for state machine events |
| F-004 | HIGH | Data | `operations/return_support/service.py:289-298` | Post-transaction `update_return`/`append_event` calls outside transaction — no compensation if they fail |
| F-005 | HIGH | Type | `operations/return_support/service.py:344,463` | `int(updated["lastMessageSequence"])` — mypy `call-overload`; `int(object)` invalid; runtime `ValueError` if field is not numeric |
| F-006 | HIGH | Type | `api/ai_gateway.py:48` | `AIGatewayService` receives `OperationalRepository` but expects `AIGatewayRepository` — mypy `arg-type` |
| F-007 | MEDIUM | Type | `api/return_agents.py:52` | Redundant `cast(AsyncMongoClient[...], resources.mongo)` — mypy `redundant-cast` |
| F-008 | MEDIUM | Suppress | `workers/integration_outbox.py:27` | Stale `# type: ignore[call-arg]` on `Settings()` — mypy `unused-ignore` |
| F-009 | MEDIUM | Suppress | `main.py:385` | Same stale `# type: ignore[call-arg]` on `Settings()` |
| F-010 | MEDIUM | Suppress | `data_platform/graph/sandbox_runner.py:211` | Stale `# type: ignore` — mypy `unused-ignore` |
| F-011 | MEDIUM | Test | `tests/gate_tools/test_run_gate.py` | 6 test functions missing `-> None` return type annotations |
| F-012 | MEDIUM | Test | `tests/operations/return_support/test_provider_architecture.py` | 3 test functions missing `-> None` annotations |
| F-013 | MEDIUM | Test | `tests/test_ai_gateway_routing.py` | All async tests use `asyncio.run()` anti-pattern instead of `@pytest.mark.asyncio` |
| F-014 | MEDIUM | Test | `tests/test_dependency_simulation.py` | Same `asyncio.run()` anti-pattern |
| F-015 | MEDIUM | Script | `scripts/run_backend_host.sh` | `--reload` hardcoded in uvicorn command — production risk |
| F-016 | MEDIUM | Config | `compose.yaml:~63` | `gemini-3.5-flash` is not a valid Google model name — all GOOGLE AI requests fail by default |
| F-017 | MEDIUM | Reliability | `ai_gateway/routing.py:210` | `ai_provider_order.split(",")` without `.strip()` — whitespace-padded config silently excludes providers |
| F-018 | LOW | Test | `tests/operations/return_support/test_provider_architecture.py:36,44` | `httpx.AsyncClient()` created but never closed — resource leak in tests |
| F-019 | LOW | Perf | `operations/return_support/service.py:203` | `ensure_indexes()` called per-request on every `create_work_item` — unnecessary MongoDB round-trip |
| F-020 | LOW | Style | `dependency_simulation/repository.py:232,236` | Multiple statements per line with semicolons — ruff E702 |
| F-021 | LOW | Frontend | `frontend/src/api/operations.ts:1` | File-wide ESLint disable for `prefer-nullish-coalescing`; `\|\| null` converts empty string to null (semantic bug) |
| F-022 | LOW | A11y | `frontend/src/components/Shell.tsx:49` | `<div onClick>` modal backdrop missing `role`, `tabIndex`, `onKeyDown` — WCAG 2.1 AA violation |
| F-023 | LOW | Frontend | `frontend/src/features/operations/AIGatewayPages.tsx` | File-wide ESLint disables masking deprecated `FormEvent`, void expressions, unnecessary conditions |
| F-024 | LOW | Frontend | `frontend/src/features/operations/ProductionReturnPages.tsx` | Same ESLint issues: deprecated `FormEvent`, non-null assertion, template expression |
| F-025 | LOW | Hygiene | `.gitignore` | `.venv/` not gitignored — local virtual envs appear in `git status` |
| F-026 | LOW | Hygiene | Root directory | Stage documentation files in repo root — should be in `docs/` |
| F-027 | INFO | Dead Code | `frontend/src/dev/adapters/fixtureJobAdapter.ts` | Dead placeholder file — comment says superseded |
| F-028 | INFO | Security | `compose.yaml:145` | MongoDB keyfile hardcoded as literal string (labeled sandbox-only) |
| F-029 | INFO | Test | `tests/test_ai_gateway_routing.py` | `assert` in test double `update_ai_trace` — stripped by Python `-O` flag |
| F-030 | VERIFY | Async | `dependency_simulation/repository.py:152` | Verify PyMongo 4.17 `aggregate()` return type to determine if `await` is needed |

---

## Baseline Validation Results

| Gate | Command | Exit | Result |
|---|---|---|---|
| poetry check | `poetry check` | 0 | PASS |
| poetry install | `poetry install --sync` | 0 | PASS |
| ruff check | `ruff check .` | 1 | FAIL (207 errors: E501×176, UP037×9, I001×9, E702×4, F401×4, UP035×2, B904×1, RUF012×1, F841×1) |
| ruff format | `ruff format --check .` | 1 | FAIL (42 files need reformatting) |
| mypy | `mypy src tests` | 1 | FAIL (32 errors in 16 files) |
| pytest | `pytest` | 3 | BLOCKED (no `.env` file at repo root) |
| npm ci | `npm ci` | 0 | PASS (4 high severity vulnerabilities) |
| npm lint | `npm run lint` | 1 | FAIL (57 ESLint errors) |
| docker compose | `docker compose config` | 0 | PASS |

---

## Remediation Scope (Windows Phase)

**Fix immediately:**
F-001, F-002/F-030, F-003, F-005, F-006, F-007, F-008, F-009, F-010,
F-011, F-012, F-015, F-016, F-017, F-018, F-020, F-021, F-022, F-023,
F-024, F-025, F-027, F-029

**Deferred (architecture or ops approval needed):**
F-004 (outbox pattern), F-013/F-014 (asyncio pattern), F-026 (doc move),
F-028 (MongoDB keyfile)

**Requiring Linux verification:**
Full E2E return flow (Stage B)

---

## Optimization Candidates

| ID | Location | Current | Bottleneck | Fix | Impact |
|---|---|---|---|---|---|
| OPT-001 | `return_support/service.py:203` | `ensure_indexes()` per request | MongoDB round-trip on every `create_work_item` | Move to startup lifespan | ~2-5ms reduction per call |
| OPT-002 | `ai_gateway/routing.py` | Single lock during O(n log n) sort | All AI requests serialized | RWLock | Negligible (n<10) — DEFERRED |

---

*Report generated before any source modification. Remediation results appended below after execution.*

---

# Verification Audit of Commit `91b2bf8` (Findings-First Gate)

This section records the repository state observed on 2026-07-26 before any new
source remediation. It does not overwrite the earlier baseline above.

## Audit Metadata

| Field | Value |
|---|---|
| UTC timestamp | `2026-07-26T05:35:07Z` |
| Environment | Windows `10.0.26200.0`, PowerShell `5.1.26100.8875` |
| Baseline commit | `91b2bf8a8825f607d8045715064eb384c780c252` |
| Branch | `master` |
| Working tree | User-owned untracked `artifacts/`; no tracked changes before this report update |
| Python / Poetry | `NOT RUN` — neither command is available on `PATH`, and no repository virtual environment exists |
| Node / npm | Node `v24.14.0`; npm `11.9.0` |
| Docker / Compose | Docker `29.6.2`; Compose `v5.3.1` |
| Tracked files | 691 |
| Backend source files | 173 |
| Frontend source files | 171 |
| Test files | 79 |
| Configuration files | 83 |
| Scripts | 30 |
| Documentation files | 162 |
| Review exclusions | `.git`, dependency directories, caches, binary assets and pre-existing generated archives |

## Current Executive Verdict

Repository health is **FAILED** at this baseline and the tree is not mergeable.
The highest-risk defect is the committed truncation of 166 frontend source files
to zero bytes. This removes the application while allowing lint, typecheck, and
build to produce a misleading green result. Linux validation remains pending;
no Linux execution is claimed.

## Newly Confirmed Findings

| ID | Severity | Area | File/Location | Finding | Evidence | Adversarial Failure | Risk | Recommended Fix | Regression Test |
| -- | -------- | ---- | ------------- | ------- | -------- | ------------------- | ---- | --------------- | --------------- |
| F-031 | CRITICAL | Frontend integrity | `frontend/src/` (166 tracked files, including `main.tsx`, `App.tsx`, `routes.ts`) | Application and tests were truncated to zero bytes in commit `91b2bf8`. | `git diff --shortstat HEAD^ HEAD -- frontend` reports 17,109 deleted lines; current `main.tsx` is zero bytes; Vite transforms only four modules and emits a 0.69 kB bundle. | Any browser request receives an effectively empty application. | Total loss of frontend functionality hidden by superficial static gates. | Restore the last reviewed non-empty frontend sources, then reconcile the new Stage 4M/4N feature files against the restored application. | Run lint, typecheck, all Vitest suites, production build, bundle check, and route-level tests; reject zero-test runs. |
| F-032 | HIGH | Test integrity | 13 `frontend/src/**/*.test.*` files | Every discovered Vitest file contains zero tests. | `npm run test -- --run` exits 1 with 13 “No test suite found” failures and zero executed tests. | A regression can be committed while no frontend assertion runs. | No frontend regression protection. | Restore test sources and retain Vitest’s zero-test failure behavior. | Verify a non-zero test count and zero failed suites. |
| F-033 | HIGH | Required automation | `scripts/windows/`, `scripts/linux/`, `scripts/generated-fixes/` | The prompt-mandated Windows and Linux automation kit is absent. | Required-path audit found no `validate_linux_kit.ps1`, import/finalization scripts, master Linux validator, phase scripts, or result packager. | Linux operator cannot reconstruct or validate the reviewed tree without manual reasoning. | Stage A and Stage B acceptance criteria cannot be met. | Implement the required scripts using actual repository commands, bounded process handling, checkpoints, structured receipts, and safe evidence collection. | Run the Windows Linux-kit validator, Bash syntax checks, reference checks, and secret scans. |
| F-034 | HIGH | Evidence integrity | `docs/evidence/code_quality/` | All mandatory structured Windows, transfer, handoff, and Linux evidence files are absent. | Required-path audit found 0 of 8 sampled evidence JSON files. | A completion claim cannot be independently verified or safely imported. | The existing completion commit is unverifiable. | Generate schema-valid truthful Windows evidence and pending Linux placeholders that explicitly say `NOT_RUN`; never fabricate Linux success. | Parse every JSON file and validate required fields/checksums. |
| F-035 | MEDIUM | Repository hygiene | `backend_results.txt`, `docker_compose_logs.txt`, `playwright_results.txt`, `frontend/test-results/`, `linux_kit/returns_platform.tar.gz` | Generated logs, Playwright failure artifacts, and a 15 MB archive are tracked. | `git show --stat HEAD` lists these as newly committed runtime/generated artifacts. | Archives may leak environment details and cause repository bloat; stale test output can be mistaken for current evidence. | Poor hygiene and ambiguous evidence provenance. | Remove generated artifacts from version control after reviewing them for required evidence; add narrow ignore rules. | Repository cleanliness scan and tracked-artifact policy check. |
| F-036 | HIGH | Windows validation | Backend quality gates | The current Windows environment cannot run Python or Poetry gates. | `python` and `poetry` are not found and `backend/.venv` does not exist. | Backend regressions remain unverified on Windows. | Mandatory Stage A quality validation is blocked until Python 3.13 and Poetry are available. | Install or expose the supported toolchain, then run the exact configured backend gates. | Poetry check/install, Ruff, Ruff format, mypy, pytest, contract and architecture gates. |

## Current Baseline Validation

| Environment | Command | Exit Code | Status | Pass/Fail/Skip | Important Output |
| ----------- | ------- | --------: | ------ | -------------- | ---------------- |
| Windows | `python --version` | N/A | NOT RUN | BLOCKED | Command not found |
| Windows | `python -m poetry --version` | N/A | NOT RUN | BLOCKED | Command not found |
| Windows | `npm.cmd run lint` | 0 | COMPLETE | PASS (misleading) | Empty source files are syntactically valid |
| Windows | `npm.cmd run typecheck` | 0 | COMPLETE | PASS (misleading) | Application entry points contain no code |
| Windows | `npm.cmd run test -- --run` | 1 | COMPLETE | FAIL | 13 failed suites; zero tests |
| Windows | `npm.cmd run build` | 0 | COMPLETE | PASS (misleading) | Four modules transformed; 0.69 kB JS bundle |
| Windows | `docker compose config --quiet` | 0 | COMPLETE | PASS | Docker config-file access warning only |
| Windows | `git diff --check` | 0 | COMPLETE | PASS | No whitespace errors before remediation |

## Findings Gate Decision

Severity counts for newly confirmed findings: **CRITICAL 1, HIGH 4, MEDIUM 1,
LOW 0, INFO 0**.

Selected for immediate remediation: F-031, F-032, F-033, F-034, and F-035.
F-036 is environment-blocked but all backend-independent work can proceed.
Actual infrastructure, worker, seed, API, and E2E results require Linux
verification and must remain `NOT_RUN` until genuine Linux evidence is returned.

## Verification Audit Remediation Results

| Finding | Result | Evidence |
|---|---|---|
| F-031 | FIXED | Restored the last non-empty reviewed frontend source revision. Final Windows build transformed 1,816 modules and emitted the full route bundle. |
| F-032 | FIXED | Restored 13 frontend test suites; Vitest executed and passed 39 tests. |
| F-033 | FIXED FOR HANDOFF | Added the 19 numbered Linux phases, common process/checkpoint library, one-command master validator, result packager, two narrow generated repairs, and Windows validate/import/finalize/build scripts. The Windows kit validator passes all 22 required shell scripts, including Git Bash syntax checks. |
| F-034 | MITIGATED / LINUX PENDING | Added all required structured evidence paths. Windows evidence is populated; every Linux receipt truthfully remains `NOT_RUN` with `linuxExecutionClaim: false`. |
| F-035 | FIXED | Removed tracked backend, Docker, Playwright, test-results, and obsolete handoff archives; added narrow ignore rules. |
| F-036 | BLOCKED | Python 3.13 and Poetry are not installed or discoverable on this Windows host, so backend gates remain `NOT_RUN`. |

Additional remediation:

- Removed `--reload` from the standard backend host command so the production-like
  host process is single-start and PID-manageable.
- Added repository line-ending policy for Bash and source files.
- Removed 18 zero-byte newly added feature files instead of retaining stubs or
  claiming unfinished product screens were implemented.
- Generated a reverse-apply-verified binary patch and checksum-protected handoff
  archive. New shell scripts are encoded as executable files in the patch.

No Linux quality, infrastructure, host-process, heartbeat, seed, API, or E2E
execution is claimed. No final commit is permitted until genuine Linux evidence
is returned and validated. Backend Windows validation remains an explicit
toolchain blocker.

## Windows Toolchain and Final Gate Update

Python `3.13.14`, pip `26.1.2`, and Poetry `2.4.1` were subsequently located in
the registered Microsoft Store installation. F-036 is therefore **FIXED**.

| Command | Exit | Result |
|---|---:|---|
| `poetry check` | 0 | PASS |
| `poetry install --sync --no-interaction` | 0 | PASS; lock already synchronized |
| `ruff check .` | 0 | PASS |
| `ruff format --check .` | 0 | PASS; 246 files |
| `mypy src tests` | 0 | PASS; 232 files |
| `pytest` | 0 | PASS; 986 passed, 1 skipped, 1 dependency deprecation warning |
| Stage 4 source validation | 0 | PASS |
| Stage 4 contract validation | 0 | PASS; 6 contract tests |
| Stage 4M dependency simulation | 0 | PASS; 10-operation behavior check |
| Stage 4N AI gateway validation | 0 | PASS; 9 checks |
| OpenAPI drift | 0 | PASS after regenerating the schema and declarations |

The earlier removal of zero-byte Stage 4M files is superseded: the required
dependency-simulator artifacts are now typed, live API-backed overview, OMC,
parcel, freight, LSI, AI-metrics, and operation-detail screens. Frontend lint,
typecheck, 39 tests, and the 1,826-module production build pass. Zero-byte
unreferenced feature stubs outside the validated Stage 4M surface remain
removed rather than being represented as implemented.

The legacy contract-promotion script requires historical aggregate receipts
bound to an already committed source revision and rejects expected remediation
working-tree changes. That precondition is not met before the final commit;
current OpenAPI zero-drift and source contract gates pass.

No Linux execution is claimed. The final commit remains prohibited until the
Linux archive is returned and validated.
