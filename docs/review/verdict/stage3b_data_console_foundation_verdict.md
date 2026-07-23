# Stage 3B Data Console Foundation Review Verdict

Review date: 2026-07-23  
Reviewed revision: `4aff786` plus the current uncommitted working tree  
Reviewer scope: the submitted Stage 3B completion statement, its evidence, and Docker reproducibility

## Verdict

**CHANGES REQUIRED — Stage 3B is not currently reproducible as complete.**

The implementation contains the claimed foundation files and the existing Vitest
suite passes 20/20. However, the required Docker lint, typecheck, and production
check gates do not pass from the declared lockfile, the dependency installation is
not reproducible, accessibility test artifacts record three failures, and the mock
boundary does not prove that mock mode is impossible in a production build.

This verdict applies to Stage 3B only. It does not approve Stage 3C–3H or the full
Data Console UI prompt.

## Claim-by-claim review

| Submitted claim | Review result | Evidence |
|---|---|---|
| Shared foundation components were implemented | **Present, not fully verified** | The named component files exist under `frontend/src/components/`. No focused tests for the newly added components were found. |
| Desktop sidebar and mobile hamburger navigation were implemented | **Present** | `frontend/src/components/Shell.tsx` contains both navigation variants and derives links from the route manifest. |
| Declarative Wouter lazy routes were implemented | **Present with a typing violation** | `frontend/src/routes.ts` uses `React.lazy`, but explicitly suppresses `no-explicit-any` and declares `React.LazyExoticComponent<any>`, contrary to the master prompt's no-`any` rule. |
| A typesafe `openapi-fetch` wrapper and query-key factory were implemented | **Present, Docker verification failed** | The wrapper, generated path types, and key factory exist. The running container cannot resolve `openapi-fetch` until dependencies are refreshed, and the lockfile cannot currently be installed with `npm ci`. |
| MSW is bounded to local development and excluded from production | **Not proven** | `main.tsx` checks only `VITE_MOCK_MODE`; it does not also require `import.meta.env.DEV`. `vite.config.ts` rejects mock mode only when `mode === "production"` and `process.env.VITE_MOCK_MODE === "true"`, which does not prevent a `vite build --mode mock`. Removing the worker asset alone does not establish that fixture code cannot enter a production bundle. |
| Playwright E2E and accessibility scaffolding exists | **Present, with recorded failures** | `frontend/playwright.config.ts`, `frontend/tests/e2e.spec.ts`, and `frontend/tests/a11y.spec.ts` exist. `frontend/test-results/.last-run.json` records a failed run and three accessibility error contexts record `page-has-heading-one` failures. |
| `npm run lint`, `typecheck`, and `check` pass | **Not reproducible in Docker** | Independent Docker rerun failed. Details are below. |
| All 20 pre-existing Vitest tests pass | **Verified** | Docker run completed with 3 test files and 20 tests passing. |
| Evidence and README were updated | **Present but overstated** | The evidence and README mark Stage 3B complete despite the reproducibility and accessibility findings in this review. |

## Independent Docker verification

The stopped `stage3-backend` and `stage3-frontend` containers were restarted. No host
Node/npm command was used.

| Command | Result |
|---|---|
| `docker exec stage3-frontend npm run lint` | **FAIL** — 30 errors caused by unresolved `openapi-fetch` and `msw/browser` types in the container |
| `docker exec stage3-frontend npm run typecheck` | **FAIL** — `TS2307` for `openapi-fetch` and `msw/browser`, followed by implicit-`any` errors |
| `docker exec stage3-frontend npm test` | **PASS** — 3 files, 20 tests |
| `docker exec stage3-frontend npm run check` | **FAIL** — stops at lint with the same 30 errors |
| `docker exec stage3-frontend npm ci` | **FAIL** — `ERESOLVE`; `openapi-typescript@7.13.0` requires TypeScript `^5.x`, while the project declares TypeScript `~6.0.2` |

Because `npm ci` fails, the clean-install path described by `package-lock.json` is not
reproducible. Running against a previously populated `node_modules` directory is not
sufficient evidence for completion.

## Blocking findings

1. **P0 — Clean Docker dependency installation fails.** Resolve the
   `openapi-typescript`/TypeScript peer conflict and regenerate the lockfile using the
   project's supported Docker workflow. A clean `npm ci` must pass without
   `--force` or `--legacy-peer-deps`.
2. **P0 — Mandatory Docker gates do not pass.** After a clean install, rerun lint,
   strict typecheck, all unit tests, and the production build/check.
3. **P1 — Development-only MSW boundary is incomplete.** Require both
   `import.meta.env.DEV` and explicit mock mode at runtime, and reject mock mode for
   every build command rather than only when the Vite mode string is `production`.
   Add an automated production-safety test.
4. **P1 — Accessibility suite has recorded failures.** Make each scan wait for the
   lazy route's stable page heading/content, rerun `test:a11y`, and retain truthful
   results. Do not delete failure artifacts as a substitute for a passing run.
5. **P1 — The route manifest violates the no-`any` requirement.** Replace
   `React.LazyExoticComponent<any>` with an accurate component type and remove the
   suppression.
6. **P1 — New shared behavior lacks focused tests.** Add tests for route rendering,
   navigation active state/mobile behavior, dialog cancel/confirm behavior, toast
   behavior, and representative loading/error/partial components.
7. **P1 — Completion evidence is ahead of verified truth.** Until the blockers pass,
   change the README/evidence status from `COMPLETE` to an in-progress or
   changes-required state. Restore `COMPLETE` only after recording fresh Docker
   outputs.

## Re-review acceptance

Stage 3B can be approved when all of the following are demonstrated:

1. A clean Docker `npm ci` succeeds.
2. Docker lint, typecheck, Vitest, and production build/check all pass.
3. Playwright E2E and accessibility suites pass, or any intentionally deferred suite
   is stated as deferred without contradictory completion language.
4. The production build cannot enable or bundle the MSW fixture runtime.
5. The explicit `any` suppression is removed.
6. New shared foundation behavior has focused deterministic tests.
7. `docs/review/status/stage3b_status.md`, the Stage 3B evidence, and README report the
   same verified state and exact gate totals.

No screenshots were captured and no Git commit was created during this review.
