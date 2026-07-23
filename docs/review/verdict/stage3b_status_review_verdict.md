# Stage 3B Status Review Verdict

Review date: 2026-07-23  
Reviewed revision: `4aff786` plus the current uncommitted working tree  
Reviewed submission: `docs/review/status/stage3b_status.md`

## Verdict

**CHANGES REQUIRED — FUNCTIONAL GATES PASS, PRODUCTION MOCK BOUNDARY AND EVIDENCE HYGIENE FAIL**

The main remediation is materially implemented. A clean Docker install succeeds,
the mandatory frontend gates pass, the route `any` was removed, the runtime MSW
guard now requires development mode, and the new component tests are present.

Stage 3B is not yet approved because the production bundle still contains the MSW
service worker, all root `docs/` evidence and verdict files are ignored by Git, and
the submitted status contradicts README and Stage 3B evidence.

## Claim review

| Status claim | Review result |
|---|---|
| Shared components and navigation are implemented | **Verified within Stage 3B scope.** |
| Typesafe `openapi-fetch` client and query-key factory exist | **Verified.** Clean install, lint, and strict typecheck pass. |
| Earlier Docker dependency conflict is resolved | **Verified.** TypeScript is now `~5.6.2`; clean `npm ci` succeeds. |
| Explicit route `any` is removed | **Verified.** The route manifest no longer contains the suppression or `any`. |
| MSW is development-only and rejected for production | **Partially verified; production artifact failure remains.** Runtime activation requires `import.meta.env.DEV`, and both negative mock-build variants are rejected. However, a normal production build still copies `public/mockServiceWorker.js` into `dist/`. |
| Vitest passes 32 tests | **Verified.** 7 files and 32 tests pass. |
| E2E passes 3 tests | **Verified.** 3/3 Chromium tests pass. |
| Accessibility passes 4 tests | **Verified for this run.** 4/4 Axe tests pass. The wait condition remains weaker than intended because it waits for the shell `<main>`, not the lazy route heading. |
| Known issues: none | **Incorrect.** Production worker leakage, ignored documentation, contradictory evidence, deprecated test-script behavior, dev-only audit findings, and debug artifacts remain. |
| Ready for Stage 3C | **Not approved.** The blocking production/evidence issues must be corrected first. |

## Independent Docker verification

All npm commands were executed inside the running `stage3-frontend` container. No
host npm command was used.

| Docker check | Result | Details |
|---|---|---|
| `npm ci` | **PASS** | 399 packages installed from the lockfile. |
| `npm run lint` | **PASS WITH TOOLING NOTICE** | No ESLint findings; ESLint reports that `.eslintignore` is no longer supported. |
| `npm run typecheck` | **PASS** | Zero TypeScript errors. |
| `npm test` | **PASS** | 7 files, 32 tests. |
| `npm run check` | **PASS** | Lint, typecheck, and Vite production build pass. |
| `npm run test:e2e` | **PASS** | 3/3 tests. `vite optimize` emits a deprecation notice. |
| `npm run test:a11y` | **PASS** | 4/4 tests. `vite optimize` emits a deprecation notice. |
| Build with `VITE_MOCK_MODE=true` | **EXPECTED REJECTION — PASS** | Vite throws `Production build rejects mock mode.` |
| Build with `--mode mock` | **EXPECTED REJECTION — PASS** | Vite throws `Production build rejects mock mode.` |
| Normal production bundle inspection | **FAIL** | `dist/mockServiceWorker.js` exists and is 9,666 bytes; it is the MSW 2.15.0 worker implementation. |
| `npm audit --omit=dev` | **PASS** | Zero production dependency vulnerabilities. |
| Full `npm audit` | **NOTICE** | Two high-severity dev-tool findings through `@redocly/openapi-core` → `js-yaml`; a fix is reported as available. |

## Blocking findings

### P0 — MSW worker is shipped in the production bundle

`vite.config.ts` attempts to delete `bundle["mockServiceWorker.js"]` in
`generateBundle`, but Vite copies the worker from `public/` into `dist/` after that
hook. The successful production build therefore contains the complete MSW worker.

Required correction:

- Keep the worker outside the normal Vite `public/` production-copy path, or remove
  it using a build lifecycle that runs after public assets are copied.
- Add an automated production-bundle assertion that fails when
  `dist/mockServiceWorker.js`, MSW setup code, fixture handlers, or fixture-mode
  markers are present.
- Rerun the normal and both negative production-build checks.

### P0 — Review and evidence documents are ignored

`.gitignore` contains `docs/`, so Git ignores:

- `docs/review/status/stage3b_status.md`
- every verdict under `docs/review/verdict/`
- Stage 3A/3B evidence under `docs/evidence/`

This prevents the required status and evidence from being retained in the repository
and hides them from ordinary `git status`.

Required correction:

- Remove the blanket `docs/` ignore rule.
- Ignore only specific generated artifacts when necessary.
- Confirm the required status, evidence, and verdict paths are no longer ignored.

### P1 — Completion records contradict each other

The submitted status says `COMPLETE` and reports 32/3/4 tests, while:

- README says Stage 3B is `CHANGES REQUIRED`.
- Stage 3B evidence says fixes are in progress.
- Stage 3B evidence still reports 20 Vitest tests and says E2E/Axe are only
  scaffolding.

Required correction:

- Keep Stage 3B as `CHANGES REQUIRED` until the production worker blocker is fixed.
- Then update README, Stage 3B evidence, and `stage3b_status.md` together with the
  independently reproducible totals.

## Non-blocking quality findings

1. `a11y.spec.ts` should wait for the page-specific `h1`, not the shell `<main>`,
   before scanning lazy routes.
2. `Shell.test.tsx` clicks the mobile-menu button but makes no assertion that the
   menu opened or that its accessibility state changed.
3. Remove `frontend/debug.cjs`, `frontend/debug.js`, and `frontend/debug2.cjs`; they
   are temporary browser-debug scripts, not product or governed test files.
4. Remove the unsupported `.eslintignore` and the deprecated explicit
   `vite optimize` calls after ensuring the equivalent configuration remains in the
   supported files.
5. Review and resolve the two dev-tool audit findings without using a forced
   dependency upgrade.

## Re-review acceptance

Stage 3B can be approved after:

1. A normal production build contains no MSW worker or fixture runtime artifacts.
2. Both mock-enabled build variants continue to be rejected.
3. Root status, verdict, and evidence documents are no longer ignored.
4. README, evidence, and status report the same truthful state and exact test totals.
5. Clean Docker install, lint, strict typecheck, 32+ Vitest tests, production build,
   E2E, and accessibility all pass again.

No screenshots were captured and no Git commit was created during this review.
