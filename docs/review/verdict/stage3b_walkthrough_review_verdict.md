# Stage 3B Remediation Walkthrough Review Verdict

Review date: 2026-07-23
Reviewed revision: `4aff786` plus the current uncommitted working tree
Reviewed submission: `docs/review/status/walkthrough.md`
Prior verdict: `docs/review/verdict/stage3b_status_review_verdict.md`

## Verdict

**PASS WITH NON-BLOCKING FOLLOW-UPS — STAGE 3B APPROVED**

The two prior P0 blockers are resolved:

1. A normal production build no longer contains `mockServiceWorker.js` or the
   inspected fixture/MSW markers.
2. The root `docs/` tree is no longer ignored, so status, evidence, and verdict files
   are visible to Git.

The P1 documentation, accessibility-wait, mobile-navigation-test, debug-file, and
tooling-cleanup changes are also present. The complete Stage 3B Docker gate is
reproducible. Stage 3C may proceed without waiting for another Stage 3B re-review.

Two non-blocking accuracy/tooling findings remain and must be tracked truthfully:

- the full development dependency audit still reports two high-severity findings;
- `npm run build -- --mode mock` does not pass `--mode mock` to Vite after the build
  script gained a trailing bundle-check command.

## Walkthrough claim review

| Walkthrough claim | Review result |
|---|---|
| Production MSW worker leakage was fixed | **Verified.** The normal production build finishes without `dist/mockServiceWorker.js`. |
| An automated production bundle check was added | **Verified.** `npm run build` invokes `scripts/check-bundle.js`, and the check passes. |
| Root documentation is no longer ignored | **Verified.** `.gitignore` no longer contains `docs/`; evidence, status, and verdict paths appear in `git status`. |
| README, evidence, and Stage 3B status are aligned | **Verified for status and test totals.** All mark Stage 3B complete and report 32 Vitest, 3 E2E, and 4 accessibility tests. |
| Accessibility waits for lazy page content | **Verified.** The three lazy routes wait for their first `h1` before Axe scans. |
| Mobile-menu test asserts expanded state | **Verified.** The test checks `aria-expanded` before and after interaction and checks for the close-menu control. |
| Debug scripts and unsupported `.eslintignore` were removed | **Verified.** None of the three debug scripts or `.eslintignore` remains. |
| Audit findings were handled | **Not fully accurate.** Production dependencies audit cleanly, but the full audit still reports two high-severity dev-tool findings through `@redocly/openapi-core` and `js-yaml`. |
| Complete Docker pipeline passes | **Verified.** Exact independently observed results are below. |
| Mock-enabled production builds are rejected | **Verified for the two actual Vite inputs.** `VITE_MOCK_MODE=true npm run build` and direct `npx vite build --mode mock` reject the build. |

## Independent Docker verification

All Node/npm commands were run inside `stage3-frontend`. No host npm command was
used.

| Docker check | Result | Details |
|---|---|---|
| `npm ci` | **PASS** | 399 packages installed from the lockfile. |
| `npm run check` | **PASS** | ESLint, strict TypeScript, Vite production build, and bundle assertion passed. |
| `npm test` | **PASS** | 7 test files; 32 tests. |
| `npm run test:e2e` | **PASS** | 3/3 Chromium tests. |
| `npm run test:a11y` | **PASS** | 4/4 Axe tests. |
| `VITE_MOCK_MODE=true npm run build` | **EXPECTED REJECTION — PASS** | Vite reports `Production build rejects mock mode.` |
| `npx vite build --mode mock` | **EXPECTED REJECTION — PASS** | Vite reports `Production build rejects mock mode.` |
| Independent production output inspection | **PASS** | No `dist/mockServiceWorker.js`; no `FIXTURE MODE` or `setupWorker` markers in built JavaScript assets. |
| `npm audit --omit=dev` | **PASS** | Zero production dependency vulnerabilities. |
| Full `npm audit` | **FOLLOW-UP** | Two high-severity dev-only findings remain through `@redocly/openapi-core` → `js-yaml`. |
| Required documentation visibility | **PASS** | `docs/evidence/` and `docs/review/` are not ignored and are visible as untracked worktree content. |

## Non-blocking follow-ups

### F1 — Correct the audit statement

The walkthrough says `npm audit fix` handled the two high-severity findings, but a
clean `npm ci` still reports them and a full `npm audit` reproduces them. Update the
walkthrough/status known-issues section to say:

- production dependencies: zero known vulnerabilities;
- development toolchain: two high-severity transitive `js-yaml` findings remain;
- resolution is pending a compatible dependency update.

Do not use `--force` to silence the result.

### F2 — Make mock-mode build verification unambiguous

Because the `build` script now ends with `npm run check:bundle`, this command:

```text
npm run build -- --mode mock
```

forwards `--mode mock` to `check:bundle`, not to Vite. It therefore performs a normal
production build and emits npm argument warnings. This does not leak mocks, and the
direct Vite mock build is correctly rejected, but the interface is misleading.

Add explicit scripts such as:

```text
build:vite
check:mock-build
```

or move argument-sensitive Vite invocation into a small Node script. Ensure CI tests
both `VITE_MOCK_MODE=true` and `vite build --mode mock` as expected failures.

## Approval boundary

This verdict approves the **Stage 3B frontend foundation** only. It does not claim
that Stage 3C–3H screens or the full Data Console UI prompt are complete.

No screenshots were captured and no Git commit was created during this review.
