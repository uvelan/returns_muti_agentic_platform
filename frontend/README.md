# Frontend — the operations UI

**Current as of 2026-08-14, commit `dcbb7dc`.**

React 19 + TypeScript + Vite. Nine domain screens and a landing page over the
platform's canonical API surface.

This file used to be the stock `React + TypeScript + Vite` template, verbatim,
advising the reader on which official Vite React plugin to choose and how to enable
type-aware ESLint rules. None of it described this application.

## Layout

```text
frontend/
  src/
    api/          typed HTTP clients, one per canonical domain
      client.ts       envelope enforcement, correlation ids, error mapping
      generated/      openapi-typescript output — DO NOT hand-edit
    domains/      the nine domain screens, the shell, and the registry
    hooks/        capability context and shared hooks
    mocks/        MSW handlers for `npm run dev:mock`
  openapi/        the exported contract the generated types come from
  scripts/        contract export, bundle check
  tests/e2e/      Playwright
```

## The domain registry is the source of truth for navigation

`src/domains/registry.ts` declares every domain: path, name, description, purpose,
icon, required capability, sections, and an optional status badge.

Adding a domain is a **code** change, not configuration. That is deliberate — a
domain is a screen, and a screen that can be configured into existence cannot be
typechecked.

Section labels are declared once as `as const` tuples and used twice: to build the
sidebar, and by the screen to derive the union its body switches on. So a label
renamed in the registry is a **compile error in the screen's switch**, not a section
that silently renders nothing.

`requires` is the capability that makes a domain **visible**, and it is deliberately
the domain's cheapest read: a principal who cannot read anything in a domain has no
use for its entry. **Hiding is presentation only** — the backend refuses regardless,
and a screen appearing is not an authorization decision.

Per-screen functional documentation: [`../docs/screens/`](../docs/screens/README.md).

## House rules

These are not style preferences. Each one exists because its absence produced a bug
or a lie.

**Never fabricate state.** Where the platform publishes no field, say so in those
words. A fabricated `HEALTHY` is worse than an admitted gap, because the gap is
fixable and the fabrication is trusted.

**Derive progress; never assume it.** A stage a response cannot speak to stays
pending. The temptation is a bar that advances on a timer — it looks finished and
means nothing.

**Distinguish empty from broken.** "There is nothing here" and "we could not load it"
render differently on every screen. Each screen's document states how.

**Show the correlation id on failure.** `meta.request_id` is what makes a support
conversation about a failed request actionable.

**No versioned paths in the shell.** The canonical surface is versionless.
`src/api/noVersionedPaths.test.ts` asserts it, because describing a leftover in a
README is exactly what let one survive three deletion waves. `/api/runtime-config` is
the single boot exception.

**Mirror backend lifecycle tables; do not reimplement them.** Approvals mirrors
`DECISIONS_BY_STATUS` so an operator is not offered a button that returns 409 — and
surfaces the backend's refusal **verbatim** when it happens anyway. A mirror can
drift; the backend cannot be wrong about its own lifecycle.

**Do not invent navigation ahead of the screens.** A rail entry that routes nowhere
is worse than an absent one. Several domains have empty `sections` for this reason.

## Commands

```bash
npm ci

npm run dev              # against a running backend on :8000
npm run dev:mock         # against MSW handlers, no backend needed

npm run lint             # eslint --max-warnings=0
npm run typecheck        # tsc -b
npm run test             # vitest run
npm run test:watch
npm run test:coverage
npm run build            # typecheck + vite build + bundle check
npm run check            # lint + build

npm run contracts:check  # regenerate types and fail on any diff
```

`npm run build` runs `typecheck` first and `check:bundle` after, so a build cannot
succeed with type errors or an over-budget bundle.

### Gate expectations

`lint` at `--max-warnings=0`, `typecheck` clean, and the production build clean. All
three are release gates, not advisory.

## Contracts

Types under `src/api/generated/` are produced from the exported OpenAPI document.
**Do not hand-edit them.**

```bash
npm run contracts:generate   # export + openapi-typescript
npm run contracts:check      # the same, then `git diff --exit-code`
```

`contracts:check` fails on any diff, so a backend contract change that has not been
regenerated fails CI rather than drifting silently. The backend has the mirror-image
check — `python scripts/check_openapi_drift.py`, wired into pytest.

If `contracts:check` fails, run `contracts:generate` and commit the result. Do not
patch the generated file to make the diff go away.

## API client conventions

Every response is `{ data, meta }`. `src/api/client.ts` **enforces** the envelope
rather than trusting the shape, and maps status codes to typed errors.

Send `X-Correlation-ID` on every request; it comes back as `meta.request_id`.

One client module per canonical domain. A screen imports its domain's client and does
not construct URLs.

Error handling per status: `403` is a capability problem (the screen should not have
offered the action), `409` is a concurrency or lifecycle conflict and needs a re-read
rather than a retry, `502` on the shipment path means the SQL row committed and the
projection did not — resubmit, do not re-enter.

Full contract dimensions: [`../docs/api/README.md`](../docs/api/README.md).

## Live state

Polling with React Query — refetch on interval and on window focus. There is no
websocket.

"Live" means "as of the last successful fetch", and screens show that time rather
than implying continuous truth. Two places are polled harder because staleness is
materially misleading:

- **AI interceptions** — a held request has a person blocked behind it;
- **configuration adoption** — a release that went `LIVE` and still shows
  `ACTIVATING` sends an operator chasing a worker that is fine.

## Testing

```bash
npm run test                                    # vitest
npx playwright install --with-deps chromium     # once
npm run test:e2e
```

Cross-service and real-infrastructure E2E belong to the platform's own E2E suite, not
here.

## Requirements

Node 24 and npm 11. Older Node is rejected by the launcher rather than allowed to
produce a subtly different build; rerun `./scripts/bootstrap_host.sh` after
switching.

## Related

- [`../docs/screens/`](../docs/screens/README.md) — per-screen functional docs
- [`../docs/api/README.md`](../docs/api/README.md) — endpoint contracts
- [`../docs/architecture/security-boundaries.md`](../docs/architecture/security-boundaries.md) — capabilities and why hiding is presentation only
- [`../docs/operations/startup.md`](../docs/operations/startup.md)
