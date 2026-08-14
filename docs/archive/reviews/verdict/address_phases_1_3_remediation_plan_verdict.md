# Address Phases 1–3 Remediation Plan Verdict

Review date: 2026-07-23
Review type: corrective implementation-plan review
Reviewed scope: `Address Phases 1-3 Remediation Verdict`
Primary references:

- `CODEX_PROMPT_FIX_ALL_VERIFIED_FRONTEND_BACKEND_ISSUES.md`
- `docs/review/verdict/walkthrough_phase1_2_3_remediation_verdict.md`

## Verdict

**CHANGES REQUIRED — CORRECTIVE DIRECTION APPROVED, PLAN NOT YET SUFFICIENT TO
CLOSE PHASES 1–3**

The plan responds constructively to the previous verdict. It correctly proposes:

- the required frontend proxy environment variable;
- frontend/backend application health checks;
- a split frontend/E2E image design;
- a mandatory Poetry lock file;
- structural removal of redundant generic exception wrappers;
- safe centralized exception handling;
- removal of raw Browser error printing;
- role enforcement;
- strict Graph warning contracts;
- removal of the Vitest type suppression.

It still cannot be used as the complete closure checklist. It omits the backend
pytest gate, frontend unit/build/bundle gates, OpenAPI generation and drift checks,
negative mock-build verification, accessibility, `git diff --check`, several
Browser governance requirements, missing Sources and Inventory work, exact retained
E2E evidence, and explicit no-commit/worktree-preservation rules.

After incorporating the required corrections below, implementation may proceed
without another blanket approval request.

## Approved changes

The following changes are approved:

1. Replace `VITE_API_URL` with `FRONTEND_BACKEND_TARGET`.
2. Add health checks to the application services.
3. Make frontend startup depend on backend health.
4. Separate lightweight frontend validation from browser-based E2E dependencies.
5. Restore deterministic backend dependency locking.
6. Remove route-level `except Exception: raise` wrappers.
7. Retain a centralized safe exception boundary.
8. Remove raw Browser exception printing.
9. Add enforced read/write authorization.
10. Replace Graph `z.any()` warnings with the canonical warning schema.
11. Remove the Vitest `@ts-ignore` by using supported configuration.

## Required corrections

### P0 — Do not create a Git commit

The phrase “generate and commit a fresh `backend/poetry.lock`” conflicts with the
user's locked rule.

Use:

> Generate and retain/update `backend/poetry.lock` in the worktree. Do not create a
> Git commit.

The plan must also state:

- preserve all existing modified and untracked user work;
- do not reset, clean, discard, hide, or overwrite changes;
- do not run destructive Git commands;
- do not commit any remediation work.

### P0 — Fix all 121 Ruff findings, not only selected modules

The previous gate reported 121 errors across:

- `audit.py`;
- `browser.py`;
- `graph.py`;
- `jobs.py`;
- `scenarios.py`;
- `sources.py`;
- `workspaces.py`;
- `main.py`.

The proposed file list omits Graph and Sources even though Ruff reported findings in
both. Add every affected module to the correction scope.

Do not apply an unsafe bulk formatter or regex rewrite blindly. Correct imports,
undefined names, unused code, whitespace, line lengths, annotations, and control
flow structurally. Use Ruff formatting and safe fixes only after inspecting the
diff.

### P0 — Add missing backend test execution

The verification plan says authorization will be tested but does not run pytest.

Required backend gates:

```text
docker compose exec backend poetry run ruff format --check src tests
docker compose exec backend poetry run ruff check src tests
docker compose exec backend poetry run mypy --no-incremental src tests
docker compose exec backend poetry run pytest -vv
```

Use the actual installed command form, but record exact commands, exit codes, and
test totals.

Add focused API tests for:

- canonical success envelopes;
- safe 404/403/409/422/500 behavior;
- unauthenticated and unauthorized access;
- Browser dependency failure and partial behavior;
- source read-only enforcement;
- malformed identifiers;
- page and timeout bounds;
- no raw exception/DSN/secret leakage.

### P0 — Complete Browser governance

Bracket quoting alone does not make interpolated SQL safe. An identifier containing
`]` must be rejected or escaped, and all identifiers must originate from a validated
governed catalog entry.

The Browser correction must include:

- governed exact asset resolution;
- strict engine allow-list;
- identifier validation and SQL Server-safe quoting;
- no client-provided SQL fragments;
- deterministic bounded pagination;
- allow-listed filter and sort fields;
- query timeout and cancellation behavior;
- response-size and page-size caps;
- safe redaction and serialization;
- stable record identities;
- exact record lookup;
- SQL Server and source MongoDB read-only enforcement;
- fixed parameterized Neo4j reads;
- structured partial/error results;
- no conversion of a single dependency failure into unexplained empty success.

Prefer the existing governed sampling/connection abstraction where it satisfies the
contract. Do not create a second unmanaged connection layer without a documented
reason.

If a request depends entirely on one failed asset, return a safe dependency error.
Use a partial success only when usable data is actually preserved alongside bounded
warnings.

### P0 — Include Sources and Inventory detail in Phase 3

The prior verdict explicitly records:

- Sources remain hard-coded;
- `GET /data-console/v1/inventory/{engine}/{assetId}` remains missing.

The submitted correction plan does not address either issue.

Add:

```text
GET /data-console/v1/sources
GET /data-console/v1/sources/{sourceId}
GET /data-console/v1/inventory/{engine}/{assetId}
```

Sources must derive from governed runtime/catalog state and expose no credentials or
DSNs. Inventory detail must remain metadata-only, strictly validate engine/asset
identity, and provide safe not-found behavior.

### P0 — Make authorization shared and complete

Do not copy inconsistent `_require_role` helpers into each router.

Create or reuse a shared authorization dependency that:

- resolves an authenticated `Principal`;
- distinguishes 401 from 403;
- evaluates read/write roles;
- produces the canonical safe error envelope;
- is testable independently.

Apply it to:

- Sources, Inventory, and Browser reads;
- Graph read/expansion;
- Workspace reads and mutations;
- Job reads;
- Import/export creation and download;
- Scenario create/generate/validate/approve/preview;
- Audit, Governance, Settings, and Hardening.

Merely calling `_require_role` is insufficient unless the operation-specific tests
prove correct behavior.

### P0 — Correct Docker health behavior

Use backend readiness—not only liveness—for frontend dependency gating:

```text
GET /health/ready
```

Liveness may remain as a separate Docker health signal only if readiness is also
verified before live integration.

The Node base image may not contain `curl`. The frontend health check must either:

- use a tool guaranteed by the selected image;
- install the minimal required tool intentionally; or
- use a small Node HTTP health-check script.

Also require:

- explicit `target` selection for normal frontend and E2E image stages;
- a dedicated Compose E2E validation service/profile;
- no host-mounted `node_modules`;
- pinned browser/runtime versions;
- exact container network proxy configuration;
- clean frontend and backend rebuilds from the current source.

### P0 — Make the multi-stage frontend image complete

The proposed E2E stage must install both:

1. Playwright operating-system dependencies;
2. the pinned Chromium browser binary.

For example, the logical operations are:

```text
npx playwright install-deps chromium
npx playwright install chromium
```

Do not rely on an unpinned `latest` Playwright image.

The lightweight target must support lint, typecheck, unit tests, build, bundle
checks, and development startup without installing browser dependencies.

### P0 — Restore deterministic backend builds

Generate `backend/poetry.lock` inside Docker using the declared Poetry version.
Then:

- copy it explicitly in the Dockerfile;
- fail builds if it is absent;
- install from the lock without re-resolving;
- verify a clean image build;
- retain the lock in the worktree;
- do not create a Git commit.

Pin mutable base image tags by a deliberate version/digest policy where required by
the repository's reproducibility standard.

### P1 — Clarify exception handling

Importing `HTTPException` everywhere is acceptable for transport-level not-found or
validation behavior, but domain/service layers should not depend on FastAPI.

Use:

- domain/application exceptions below the API layer;
- route/handler mapping to stable HTTP codes;
- the global exception handler only for genuinely unexpected failures;
- safe structured logs with correlation IDs;
- no raw exception message in the response.

Test that an intended `HTTPException` is not swallowed or converted into a generic
500.

### P1 — Complete frontend verification

The proposed frontend gates omit unit tests, production build, and bundle safety.

Required lightweight frontend gates:

```text
npm ci
npm run lint
npm run typecheck
npm test
npm run build
npm run check:bundle
```

Require:

- natural Vitest termination;
- exact suite/test totals;
- no overlapping test processes;
- no `@ts-ignore`;
- no blanket ESLint disable;
- no `any` in Graph warnings;
- no fixture fallback in live mode.

### P1 — Regenerate OpenAPI and enforce drift

Backend response and Graph contract changes require:

1. export OpenAPI from the rebuilt backend container;
2. regenerate frontend transport declarations;
3. run the contract drift check;
4. never edit generated types manually;
5. retain domain ports separately from generated transport types.

Add exact Docker commands and recorded results.

### P1 — Strengthen live E2E evidence

“Run E2E inside a dedicated container” is correct but incomplete.

The retained evidence must include:

- exact Docker command;
- image ID/digest or reproducible target;
- frontend and backend container identity;
- `VITE_MOCK_MODE=false`;
- MSW worker/handlers not active;
- proxy target;
- test names, totals, duration, and exit code;
- live request IDs;
- evidence that SQL Server and Neo4j paths were real.

The two narrow Browser and Graph scenarios are useful smoke tests but do not prove
all canonical routes. Label them `LIVE_SMOKE_PASSED` unless the complete required
route/workflow E2E suite also passes.

### P1 — Add accessibility and negative production checks

The verification plan must include:

```text
npm run test:a11y
```

with the live backend and MSW disabled.

Also verify:

- normal production bundle excludes the MSW worker and fixture runtime;
- `VITE_MOCK_MODE=true` production build is rejected;
- `vite build --mode mock` is rejected;
- `git diff --check` passes.

### P1 — Update truthful evidence

After successful correction, update:

- `docs/evidence/walkthrough_phase1_2_3.md`;
- `docs/review/status/full_stack_integration_status.md`;
- Markdown and JSON API gap registers;
- frontend validation summary;
- route inventory;
- live/fixture capability matrix;
- backend evidence under
  `backend/docs/evidence/data_console_full_stack/`.

Do not describe a Markdown assertion or `.last-run.json` alone as executable proof.

## Revised closure checklist

Phases 1–3 may be declared complete only when:

1. frontend/backend Compose services build cleanly and become healthy;
2. backend dependency installation uses the retained lock;
3. backend Ruff reports zero errors;
4. backend mypy passes;
5. focused and full backend pytest pass;
6. no raw errors or redundant generic route catches remain;
7. authorization is enforced and tested;
8. Sources and Inventory detail are live and governed;
9. Browser is bounded, safe, read-only, redacted, and tested;
10. frontend lint and typecheck pass without suppressions;
11. Vitest passes and terminates naturally;
12. frontend production build and bundle checks pass;
13. OpenAPI generation and drift checks pass;
14. Docker live Browser and Graph smoke tests pass with MSW disabled;
15. the complete applicable Phase 1–3 E2E routes pass;
16. live accessibility checks pass;
17. negative mock-build checks pass;
18. `git diff --check` passes;
19. evidence records exact commands, totals, and exit codes;
20. no screenshots are captured before Hardening;
21. no Git commit is created.

## Current truth labels

```text
CORRECTIVE PLAN: APPROVED WITH REQUIRED CHANGES
PHASE 1 — FRONTEND STABILIZATION: NOT CLOSED
PHASE 2 — BACKEND FOUNDATIONS: NOT CLOSED
PHASE 3 — LIVE INTEGRATION: NOT CLOSED
DOCKER BACKEND: RUNNING / QUALITY GATES FAILING
DOCKER FRONTEND: NOT BUILT OR RUNNING
BACKEND RUFF: FAILED — 121 ERRORS
FRONTEND DOCKER GATES: NOT RUN
OPENAPI DRIFT: NOT VERIFIED
LIVE E2E: NOT REPRODUCIBLY VERIFIED
ACCESSIBILITY: NOT VERIFIED
SCREENSHOTS: DEFERRED
GIT COMMIT: NOT CREATED BY THIS REVIEW
```

No implementation files, tests, screenshots, or Git commits were created during
this plan review.
