# Phases 1–3 Remediation Walkthrough Verdict

Review date: 2026-07-23
Review type: implementation, evidence, and Docker re-verification
Reviewed artifact: `docs/evidence/walkthrough_phase1_2_3.md`
Primary authority: `CODEX_PROMPT_FIX_ALL_VERIFIED_FRONTEND_BACKEND_ISSUES.md`

## Verdict

**REJECTED AS COMPLETE — MATERIAL REMEDIATION WORK EXISTS, BUT PHASES 1–3 DO NOT
PASS THE REQUIRED GATES**

The walkthrough identifies useful changes: a global safe exception boundary now
exists, Docker application definitions were started, graph response nullability was
partially reconciled, generated OpenAPI artifacts changed, and a constrained Vitest
execution strategy was attempted.

However, the completion claim is not supported by the current source or Docker
evidence. The backend focused Ruff gate fails with 121 errors, the route refactor is
incomplete and has introduced undefined names, the new authorization constants are
mostly unenforced, Browser still prints raw errors and constructs interpolated SQL,
the Compose frontend configuration uses the wrong proxy environment variable, no
frontend image currently exists, and no reproducible Docker E2E result was retained.

The implementation must be corrected before any of Phases 1, 2, or 3 is marked
complete.

## Validation performed during review

### Docker service state

The following services were observed:

| Service | Result |
|---|---|
| Backend | Up |
| MongoDB | Up, healthy |
| Neo4j | Up, healthy |
| SQL Server | Up, healthy |
| SQL Server initializer | Exited successfully |
| Temporal | Up |
| Temporal PostgreSQL | Up, healthy |
| Valkey | Up, healthy |
| Frontend | Not running |

The backend being up proves that application startup succeeds. It does not prove
that every endpoint path is valid or that lint, typing, tests, authorization, or
live frontend integration passes.

### Focused backend gate

Executed in the running backend container:

```text
poetry run ruff check \
  src/return_platform/data_console/api \
  src/return_platform/main.py
```

Result:

```text
FAIL — 121 errors
```

Failures include:

- undefined `HTTPException` in Audit, Workspaces, Scenarios, and other refactored
  modules;
- unused `Principal`, role-support, Settings, Path, Query, status, and typing
  imports;
- import-order errors;
- extensive line-length and whitespace failures;
- unused variables;
- incomplete placeholder code;
- direct private service/collection access;
- unsafe Browser implementation findings.

This alone prevents a Phase 2 or Phase 3 completion verdict.

### Frontend image verification

No `returns_muti_agentic_platform-frontend:latest` image existed.

A clean Compose frontend lint run triggered a complete image build. The Dockerfile
attempted to install 293 Playwright operating-system packages into the general
frontend image. After more than four minutes, the build was still installing
packages and had not reached lint. The review cancelled that validation build.

Result:

```text
FRESH FRONTEND DOCKER LINT: NOT RUN
FRESH FRONTEND DOCKER UNIT TESTS: NOT RUN
FRESH FRONTEND DOCKER E2E: NOT RUN
```

The cancelled review build is not a product test failure, but it demonstrates that
the Docker target needs to separate normal frontend development/build dependencies
from Playwright browser-test dependencies.

### Worktree check

`git diff --check` still fails because
`backend/src/return_platform/main.py` contains trailing whitespace.

## Findings

### P0 — The route exception refactor is incomplete and unsafe

The walkthrough says generic route-level exception blocks were stripped. They were
not.

Jobs, Workspaces, and Scenarios still contain repeated patterns such as:

```python
try:
    ...
except Exception as e:
    raise e
```

These blocks provide no value, damage trace clarity, and contradict the claimed
centralized-boundary refactor. They appear to be the result of mechanical regex
replacement rather than a completed source-level refactor.

Several modules now raise `HTTPException` without importing it. A missing-resource
path can therefore raise `NameError` and become an incorrect 500 response instead
of the intended 404.

Required correction:

- remove redundant route-level generic catches manually and structurally;
- import and use HTTP exceptions consistently where they remain appropriate;
- use typed domain/application exceptions where possible;
- test exact 404, 403, 409, 422, dependency, and 500 behavior;
- run Ruff, mypy, and focused API tests before claiming the boundary complete.

### P0 — Raw Browser errors are still printed

`browser.py` still contains:

```python
print(f"Error fetching SQL records: {e}")
```

It then returns an empty success result. This violates both required behaviors:

- no raw driver errors in output/logs;
- no conversion of a dependency failure into empty success.

The Browser must emit a safe structured partial/error response and log only
allow-listed diagnostic context with the request ID.

### P0 — Browser SQL remains interpolated and insufficiently governed

The query remains structurally interpolated:

```python
SELECT TOP 10 * FROM {database}.{namespace}.{object_name}
```

Although the values originate from the catalog, the implementation still needs:

- strict governed-asset resolution;
- allow-listed/quoted identifiers;
- bounded pagination;
- allow-listed filtering and sorting;
- timeout and response-size limits;
- deterministic record identity;
- safe redaction and serialization;
- exact record lookup;
- no arbitrary query surface.

Changing connection construction does not establish these guarantees.

The walkthrough also calls this a `SQLProbeManager` change, but the current Browser
module does not expose a clearly bounded manager abstraction. It creates a new
`pymssql` connection inside a local function and executes it through
`asyncio.to_thread`. The claimed concurrency fix requires focused cancellation,
timeout, resource-closure, and repeated-request tests.

### P0 — Authorization is still missing from most new APIs

Workspaces, Jobs, Scenarios, and Audit define role sets or import `Principal`, but
the route operations do not enforce those roles.

Graph and Graph Evidence contain role checks; this pattern has not been consistently
applied to the other new modules.

Required correction:

- enforce read and write roles per operation;
- distinguish unauthenticated from forbidden;
- enforce authorization on export download and scenario approval;
- add tests for each operation class;
- remove unused security imports only after proper dependencies are in place.

### P0 — Backend reproducibility is broken

`backend/poetry.lock` is deleted while the Dockerfile uses:

```text
COPY pyproject.toml poetry.lock* ./
poetry install
```

The wildcard makes the lock optional, so dependency resolution can change between
builds. This does not meet deterministic Docker requirements.

Restore or regenerate the lock in Docker, retain it, and prove a clean backend image
build from that lock.

### P0 — Compose does not provide the claimed healthy application stack

Frontend and backend services were added, but neither has an application health
check.

The frontend service sets:

```text
VITE_API_URL=http://backend:8000
```

The Vite configuration actually requires:

```text
FRONTEND_BACKEND_TARGET
```

Therefore, the current Compose frontend command is not correctly configured for the
proxy and is expected to fail Vite startup unless an unrelated environment value is
injected.

Required correction:

- set the exact proxy variable used by Vite;
- add backend readiness and frontend HTTP health checks;
- make frontend depend on backend health, not only container creation;
- prove `docker compose up` reaches healthy application state.

### P0 — Docker E2E is not proven

The walkthrough says `npm run test:e2e inside frontend/` passed. That wording does
not prove it ran in Docker.

Current evidence shows:

- no frontend image exists;
- no frontend Compose container is running;
- Playwright starts its own Vite server on port 5174;
- the required Docker proxy variable is not configured correctly;
- the retained `.last-run.json` says passed but does not record environment, MSW
  state, backend identity, commands, test names, or request evidence.

The E2E suite itself currently demonstrates only a narrow SQL Browser path and Graph
search path. It does not cover every canonical route or the required live workflows.

Required evidence:

- exact Docker command;
- clean exit code;
- frontend image digest;
- backend container identity;
- MSW explicitly disabled;
- live proxy request evidence;
- test totals and names;
- direct proof that fixture handlers were not active.

### P1 — Vitest stabilization is not cleanly implemented

Single-thread execution is a reasonable bounded mitigation for a constrained
environment, but the configuration uses:

```typescript
// @ts-ignore: Vitest type mismatch
```

The remediation prompt prohibits weakening typing and suppressing errors. Use a
configuration shape supported by the installed Vitest version, or document and
resolve the version mismatch.

The walkthrough records 13 passing suites but does not retain the command, exit
code, test count, duration, container identity, or stdout. A statement in Markdown
is not sufficient evidence.

Before completion:

- run from the fresh Docker image;
- record exact suite and test totals;
- prove natural termination;
- confirm no overlapping runner processes;
- retain output in the validation summary.

### P1 — Graph schema correction remains incomplete

Using `.nullish()` for nullable provenance and ownership is appropriate if OpenAPI
defines those fields as nullable.

However, `GraphSearchResultSchema` still contains:

```typescript
warnings: z.array(z.any()).optional()
```

That is not a corrected strict warnings type. Reuse the canonical warning schema and
remove `any`.

Adding `page: null` to Graph responses is consistent with the canonical envelope,
but the same envelope contract must be enforced for every endpoint and regenerated
through OpenAPI.

### P1 — New APIs still lack focused backend tests

No focused backend tests were found for Sources, Browser, Graph Explorer,
Workspaces, Jobs, Imports, Exports, Scenarios, Audit, Governance, Settings, or
Hardening.

Graph Evidence tests do not substitute for these operations.

### P1 — Phase 3 is not endpoint-complete

The walkthrough focuses on one SQL Browser flow and one Graph search. It does not
close the verified gaps:

- missing Inventory asset-detail API;
- hard-coded Sources;
- Browser pagination/filter/sort/detail/redaction;
- workspace safety and authorization;
- import/export validation and redaction;
- scenario deterministic generation and approval gate;
- evidence-derived Governance/Settings/Hardening.

These may belong to later remediation phases, but the walkthrough must not label
general “Integration & Hardening” complete based on two paths.

### P1 — Docker frontend image design needs separation

The general frontend Dockerfile installs Playwright browser dependencies and
Chromium during every clean image build. This makes lint/type/unit validation
unnecessarily large and slow.

Use one of:

- a multi-stage Dockerfile with separate development/build and E2E targets; or
- a lightweight frontend image plus a pinned official Playwright validation image.

Do not use unpinned latest browser images.

### P1 — Dependency and hygiene gates remain open

The fresh frontend dependency install reported two high-severity vulnerabilities.
Assess them accurately and remediate without unrelated forced upgrades where
possible.

Also:

- make `git diff --check` pass;
- remove mechanical repair scripts after confirming they contain no required work;
- remove unused imports and placeholders;
- run production bundle and negative mock-build checks.

## Required re-verification

After correction, run in Docker:

### Backend

```text
ruff format --check src tests
ruff check src tests
python -m mypy --no-incremental src tests
python -m pytest -vv
```

### Frontend

```text
npm ci
npm run lint
npm run typecheck
npm test
npm run build
npm run check:bundle
```

### Live integration

```text
npm run test:e2e
npm run test:a11y
```

Live integration must use the Docker backend and MSW must be disabled.

Also run:

- OpenAPI regeneration and drift check;
- normal production mock-exclusion check;
- `VITE_MOCK_MODE=true` negative production build;
- `vite build --mode mock` negative production build;
- `git diff --check`;
- authorization and safe-error API tests.

## Current truth labels

```text
PHASE 1 — FRONTEND STABILIZATION: PARTIAL / NOT DOCKER-VERIFIED
PHASE 2 — BACKEND FOUNDATIONS: FAILED QUALITY AND SECURITY GATES
PHASE 3 — LIVE INTEGRATION: NARROW CLAIM / NOT REPRODUCIBLY VERIFIED
DOCKER BACKEND: RUNNING
DOCKER FRONTEND: NOT BUILT OR RUNNING
BACKEND RUFF: FAILED — 121 ERRORS
FRONTEND LINT: NOT RUN FROM FRESH IMAGE
FRONTEND UNIT TESTS: CLAIMED, EVIDENCE INSUFFICIENT
LIVE E2E: CLAIMED, DOCKER EVIDENCE INSUFFICIENT
ACCESSIBILITY: NOT VERIFIED
SCREENSHOTS: DEFERRED
GIT COMMIT: NOT CREATED BY THIS REVIEW
```

No source implementation was changed during this review. The incomplete frontend
validation build was cancelled after its Docker design issue was established. No
screenshots or Git commits were created.
