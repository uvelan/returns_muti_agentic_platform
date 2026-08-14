# Stage 3B Fixes Re-review Verdict

Review date: 2026-07-23
Reviewed revision: `4aff786` plus the current uncommitted working tree
Prior verdict: `stage3b_data_console_foundation_verdict.md`
Submitted plan: `docs/review/status/stage3b_fixes_plan.md`

## Verdict

**REJECTED — FIXES NOT IMPLEMENTED**

The fixes plan is a reasonable proposal, but the planned changes are not present in
the reviewed worktree. The original P0 and P1 findings remain open. Stage 3B must
remain **CHANGES REQUIRED** and is not approved for progression on the basis of this
submission.

## Source review

| Planned fix | Current result |
|---|---|
| Change TypeScript from `~6.0.2` to a compatible 5.x version | **Not implemented.** `frontend/package.json` and the lockfile still declare TypeScript `~6.0.2`; the lockfile resolves `6.0.3`. |
| Reject mock mode during every Vite build | **Not implemented.** `vite.config.ts` still destructures only `mode` and checks only `mode === "production"` with `process.env.VITE_MOCK_MODE`. |
| Require `import.meta.env.DEV` before starting MSW | **Not implemented.** `main.tsx` still checks only `VITE_MOCK_MODE`. |
| Remove the route-manifest `any` suppression | **Not implemented.** `routes.ts` still contains `React.LazyExoticComponent<any>` and an ESLint suppression. |
| Wait for lazy page content before Axe scans | **Not implemented.** `frontend/tests/a11y.spec.ts` scans immediately after navigation. |
| Add Shell, dialog, toast, and state-component tests | **Not implemented.** No proposed test files were found. |
| Change README/evidence to `CHANGES REQUIRED` during remediation | **Not implemented.** README and Stage 3B evidence still claim completion. |
| Create `docs/review/status/stage3b_status.md` | **Not implemented.** Only the fixes plan and reporting instructions exist. |

## Independent Docker results

The existing `stage3-frontend` and `stage3-backend` containers were running. All
Node/npm verification was performed inside `stage3-frontend`; no host npm command was
used.

| Docker command | Result | Details |
|---|---|---|
| `npm ci` | **FAIL** | `ERESOLVE`: `openapi-typescript@7.13.0` requires TypeScript `^5.x`, while the project resolves TypeScript `6.0.3`. |
| `npm run lint` | **FAIL** | 30 errors, primarily unresolved `openapi-fetch` and `msw/browser` types after the failed clean install. |
| `npm run typecheck` | **FAIL** | `TS2307` for `openapi-fetch` and `msw/browser`, plus implicit-`any` errors. |
| `npm test` | **PASS** | 3 test files; 20 tests passed. These are the unchanged existing tests, not the planned shared-component tests. |
| `npm run build` | **FAIL** | Stops during typecheck with the same unresolved-module errors. |
| `npm run test:e2e` | **FAIL / NOT RUNNABLE** | `playwright: not found` after the failed clean install. |
| `npm run test:a11y` | **FAIL / NOT RUNNABLE** | `playwright: not found` after the failed clean install. The previous failed Axe artifacts remain in `frontend/test-results/`. |
| Production build with `VITE_MOCK_MODE=true` | **INCONCLUSIVE AT RUNTIME; SOURCE DEFECT REMAINS** | Typecheck fails before Vite evaluates the build boundary. Static review confirms the planned `command === "build"` guard is absent. |
| Production build with `--mode mock` | **INCONCLUSIVE AT RUNTIME; SOURCE DEFECT REMAINS** | Typecheck fails first. Static review confirms `mode === "mock"` is not rejected. |

## Required action before another re-review

1. Implement the fixes rather than submitting only the plan.
2. Regenerate `package-lock.json` in the supported Docker environment.
3. Prove that a clean `npm ci` succeeds without `--force` or
   `--legacy-peer-deps`.
4. Add the planned shared-component tests and repair the accessibility test timing.
5. Make both MSW runtime activation and build-time inclusion development-only.
6. Remove the explicit route `any`.
7. Update `README.md`, Stage 3B evidence, and
   `docs/review/status/stage3b_status.md` to the same truthful status.
8. Rerun clean install, lint, strict typecheck, Vitest, production build, E2E,
   accessibility, and negative mock-build checks in Docker.

The implementation agent should not mark Stage 3B complete until every required gate
passes from a clean Docker install. A plan document is not completion evidence.

No screenshots were captured and no Git commit was created during this re-review.
