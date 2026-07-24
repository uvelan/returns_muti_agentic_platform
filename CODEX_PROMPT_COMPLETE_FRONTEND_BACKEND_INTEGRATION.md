# Codex Master Prompt — Complete Frontend and Backend Integration

Continue work in the current Return Platform repository.

Your objective is:

> **Complete every remaining Data Console frontend screen, implement the required
> backend APIs, connect all typed frontend ports to live backend adapters, and finish
> Docker-based contract, integration, E2E, accessibility, and hardening validation.**

Work continuously through the integration sequence. Do not stop after producing a
plan or implementing one endpoint. Do not request approval between ordinary bounded
steps. Stop only for a genuine security/architecture blocker, an unavailable external
dependency that cannot be replaced by an approved sandbox, or after every completion
criterion in this prompt passes.

---

## Mandatory starting procedure

Before changing code:

1. Read:
   - `README.md`
   - `CODEX_PROMPT_DATA_CONSOLE_ALL_UI_SCREENS.md`
   - `CODEX_PROMPT_FINISH_ALL_UI_SCREENS_FIRST.md`
   - `docs/evidence/data_console_complete_ui/stage3a/api_gap_register.md`
   - `docs/evidence/data_console_complete_ui/stage3a/route_capability_matrix.md`
   - `docs/evidence/data_console_complete_ui/stage3a/ownership_write_policy_matrix.md`
   - all files under `docs/review/status/`
   - all files under `docs/review/verdict/`
2. Inspect the actual worktree and route manifest. Do not trust completion claims
   without source and Docker evidence.
3. Preserve existing user work and unrelated modifications.
4. Identify the current frontend/backend container names and processes.
5. Create or update:
   - `docs/review/status/full_stack_integration_status.md`
   - a precise route/API closure checklist.

The current repository may contain partial Stage 3D–3H implementation and route
names that differ from the required canonical routes. Reconcile them rather than
creating duplicate screens.

---

## Locked execution rules

1. Use Docker for frontend and backend execution. Do not run host npm, Poetry,
   Python, database, Playwright, or migration commands.
2. Do not create a Git commit.
3. Do not reset, discard, overwrite, or hide existing user changes.
4. Do not add `docs/` to `.gitignore`.
5. Keep MSW strictly development-only. Live integration and E2E must run with MSW
   disabled.
6. Keep production builds free of `mockServiceWorker.js`, fixture handlers, fixture
   banners, and fixture runtime code.
7. Do not capture screenshots until the hardening page is implemented and the
   integration gates are green. Record screenshot status as `DEFERRED` until then.
8. Never expose credentials, tokens, DSNs, raw environment values, driver errors,
   arbitrary SQL, MongoDB filters, Cypher, Python, or executable configuration.
9. SQL Server/OMC and source MongoDB remain read-only.
10. Neo4j remains a derived read-only projection for this Data Console integration.
11. Platform MongoDB owns internal application state, sandbox workspaces, jobs,
    scenarios, audit records, and evidence.
12. Temporal owns durable execution, timers, and workflow coordination—not business
    data ownership.
13. AI generation must use a provider-neutral port. Keep live model providers
    disabled unless separately approved. Provide a deterministic backend sandbox
    generator.
14. Do not weaken TypeScript, ESLint, Ruff, mypy, Pydantic strictness, tests, or
    production safety checks.

---

## Canonical frontend routes

Every route below must exist exactly and be reachable through navigation or a parent
screen.

```text
/overview

/data-console/sources
/data-console/sources/:sourceId

/data-console/inventory
/data-console/inventory/:engine/:assetId

/data-console/browser
/data-console/browser/:engine/:assetId
/data-console/browser/:engine/:assetId/records/:recordId

/data-console/workspaces
/data-console/workspaces/:workspaceId
/data-console/workspaces/:workspaceId/new
/data-console/workspaces/:workspaceId/records/:recordId/edit

/data-console/graph
/data-console/graph/nodes/:nodeId
/data-console/graph/relationships/:relationshipId

/data-console/graph-evidence

/data-console/imports
/data-console/imports/new
/data-console/imports/:jobId

/data-console/exports
/data-console/exports/new
/data-console/exports/:jobId

/data-console/scenarios
/data-console/scenarios/new
/data-console/scenarios/:scenarioId
/data-console/scenarios/:scenarioId/preview

/data-console/jobs
/data-console/jobs/:jobId

/data-console/audit
/data-console/governance
/data-console/settings
/data-console/hardening
```

Correct current mismatches:

- Do not substitute `/workspaces/new` for `/workspaces/:workspaceId/new`.
- Do not substitute scenario comparison for
  `/scenarios/:scenarioId/preview`; comparison may remain as an additional route.
- Provide separate Audit and Governance routes.
- Provide import and export job-detail routes even if they reuse the shared job
  detail component.
- Add the missing inventory asset-detail and hardening routes.

---

## API conventions

All Data Console APIs must use:

```text
/data-console/v1/...
```

Every success response must retain the repository's typed envelope:

```json
{
  "data": {},
  "page": null,
  "meta": {
    "schema_version": "string",
    "request_id": "string",
    "generated_at": "RFC3339 timestamp",
    "freshness": "LIVE | CACHED | UNKNOWN",
    "partial": false,
    "warnings": []
  }
}
```

Requirements:

- Preserve correlation/request IDs across frontend proxy and backend.
- Use safe stable error codes and messages.
- Never expose raw exceptions.
- Preserve cancellation.
- Enforce strict bounds server-side.
- Use allow-listed sort/filter fields.
- Reject unknown query parameters where practical.
- Use deterministic seek/page pagination contracts.
- Avoid hidden retries.
- Validate all path identifiers and encode frontend route identifiers safely.
- Generate OpenAPI from the backend and regenerate frontend types after endpoint
  closure.

---

## Integration sequence

Implement and validate in this order.

### Phase 1 — Stabilize current frontend

- Reconcile route manifest with the canonical route list.
- Remove temporary debug scripts, request dumps, broad lint suppressions, and stale
  Playwright failure artifacts.
- Ensure all existing frontend unit/component tests pass.
- Ensure fixture adapters remain available for isolated frontend development.
- Add explicit live adapters for every port.
- Select live adapters when mock mode is disabled.
- Select fixture adapters only in explicit development fixture mode.
- Never silently fall back from a failed live request to fixtures.

### Phase 2 — Inventory, Sources and Browser APIs

Implement:

```text
GET /data-console/v1/sources
GET /data-console/v1/sources/{sourceId}

GET /data-console/v1/inventory
GET /data-console/v1/inventory/{engine}/{assetId}

GET /data-console/v1/browser/assets
GET /data-console/v1/browser/{engine}/{assetId}/records
GET /data-console/v1/browser/{engine}/{assetId}/records/{recordId}
```

Requirements:

- Sources expose safe connection identity only—never credentials or DSNs.
- Inventory remains metadata-only.
- Browser access uses governed assets only.
- SQL Server and source MongoDB browsing is read-only.
- Neo4j browsing uses fixed parameterized queries.
- Enforce safe page-size limits.
- Enforce allow-listed filters/sorts.
- Render redacted, missing, null, binary, nested, array, date, number, and Boolean
  values safely.
- Partial dependency failures must preserve healthy results.
- Add exact backend tests and frontend live-adapter tests.

### Phase 3 — Graph Explorer APIs

Implement:

```text
GET /data-console/v1/graph/search
GET /data-console/v1/graph/nodes/{nodeId}
GET /data-console/v1/graph/relationships/{relationshipId}
```

If bounded expansion needs a distinct operation, add it explicitly to the API gap
register and OpenAPI before using it. Otherwise model it as a strictly bounded
parameter of exact-ID search.

Requirements:

- Exact governed identifier search only.
- No free-form Cypher.
- Fixed parameterized read queries.
- Visible and enforced node, relationship, depth, and expansion caps.
- Partial/truncated metadata.
- Node/relationship ownership and provenance.
- Evidence, inventory, and record links only when stable identifiers exist.
- Direct detail URL restoration.
- Accessible graph-table fallback.
- No graph mutation controls.

### Phase 4 — Sandbox Workspaces

Implement:

```text
GET    /data-console/v1/workspaces
POST   /data-console/v1/workspaces
GET    /data-console/v1/workspaces/{workspaceId}
PATCH  /data-console/v1/workspaces/{workspaceId}
DELETE /data-console/v1/workspaces/{workspaceId}

POST   /data-console/v1/workspaces/{workspaceId}/records
GET    /data-console/v1/workspaces/{workspaceId}/records/{recordId}
PATCH  /data-console/v1/workspaces/{workspaceId}/records/{recordId}
DELETE /data-console/v1/workspaces/{workspaceId}/records/{recordId}
```

Persist workspace state in Platform MongoDB only.

Requirements:

- Prominent sandbox classification.
- Stable identities.
- Optimistic concurrency/version checks.
- Schema and cross-record validation.
- Audit metadata.
- Before/after evidence.
- Soft archive where appropriate.
- Explicit single and bulk deletion confirmation contracts.
- Idempotency keys for mutations.
- No writes to source systems or Neo4j.

### Phase 5 — Jobs, Imports and Exports

Implement:

```text
GET  /data-console/v1/jobs
GET  /data-console/v1/jobs/{jobId}

GET  /data-console/v1/imports
POST /data-console/v1/imports
GET  /data-console/v1/imports/{jobId}

GET  /data-console/v1/exports
POST /data-console/v1/exports
GET  /data-console/v1/exports/{jobId}
GET  /data-console/v1/exports/{jobId}/download
```

Use Platform MongoDB for durable job metadata. Use Temporal only when durable
orchestration is necessary.

Import requirements:

- Writable sandbox target only.
- CSV, JSON, and JSONL.
- Bounded size and record counts.
- Parse preview and mapping validation.
- Duplicate policy limited to reject, skip, or sandbox replace.
- Explicit approval before submission.
- Bounded issue reporting.
- Idempotent submission.

Export requirements:

- Governed sources only.
- Bounded record counts and size.
- Explicit selected fields.
- Mandatory redaction.
- No secrets or hidden values.
- Expiring download metadata.
- Safe generated file names and content types.

### Phase 6 — AI Scenario Studio

Implement:

```text
GET  /data-console/v1/scenarios
POST /data-console/v1/scenarios
GET  /data-console/v1/scenarios/{scenarioId}
POST /data-console/v1/scenarios/{scenarioId}/generate
POST /data-console/v1/scenarios/{scenarioId}/validate
POST /data-console/v1/scenarios/{scenarioId}/approve
GET  /data-console/v1/scenarios/{scenarioId}/preview
```

Requirements:

- Deterministic backend sandbox generator with explicit seed.
- Positive, negative, boundary, and failure scenarios.
- Safe entity and record-count caps.
- Canonical and cross-entity validation.
- Exact issue references.
- Provider/model/configuration provenance without secrets.
- Approval blocked until validation passes.
- Approved data imports only into a writable sandbox workspace.
- Live AI provider remains disabled unless separately approved.
- No direct AI writes to business sources, Platform MongoDB collections, or Neo4j.

### Phase 7 — Audit, Governance, Settings and Hardening APIs

Implement:

```text
GET /data-console/v1/audit
GET /data-console/v1/audit/{auditId}
GET /data-console/v1/governance
GET /data-console/v1/settings
GET /data-console/v1/hardening
```

Requirements:

- Audit is read-only and bounded.
- Governance reflects actual ownership and capability policies.
- Settings expose non-secret environment and feature information only.
- Hardening reports only evidence that actually exists.
- No catalog mutation or secret editing.
- Screenshot, load, security, failover, and reliability entries remain `PENDING` or
  `DEFERRED` until validated.

---

## Persistence and ownership

Use these ownership rules:

| System | Allowed integration behavior |
|---|---|
| Platform MongoDB | Internal state, sandbox workspaces, jobs, scenarios, audit, evidence |
| SQL Server / OMC | Governed read-only metadata and record browsing |
| Source MongoDB | Governed read-only discovery and record browsing |
| Neo4j | Read-only derived graph exploration |
| Temporal | Durable job/workflow orchestration where required |
| Valkey | Transient cache/coordination only |

All sandbox seed/setup procedures must be:

- deterministic;
- idempotent;
- explicitly sandbox-only;
- safe to rerun;
- documented;
- separated from production catalog configuration.

---

## Frontend integration requirements

For every feature:

- Keep contracts strict and free of `any`.
- Use typed live and fixture adapters behind the same port.
- Keep query keys centralized.
- Propagate AbortSignals.
- Retain the complete API envelope where request metadata is displayed.
- Distinguish loading, empty, partial, forbidden, timeout, cancellation,
  validation, not-found, and unexpected errors.
- Never translate live failure into fixture success.
- Hide fixture banners in live mode.
- Show request IDs and freshness for live data.
- Keep responsive and keyboard-accessible behavior.
- Provide accessible alternatives for graph and complex structured data.
- Keep destructive controls disabled unless the live backend capability explicitly
  permits a sandbox mutation.

---

## Contract generation

After backend routes are implemented:

1. Export the backend OpenAPI schema in Docker.
2. Generate the frontend TypeScript contract in Docker.
3. Run the contract drift check.
4. Replace temporary manual HTTP contract duplication where generated types are
   available.
5. Keep domain ports independent from generated transport types.

Do not edit generated OpenAPI TypeScript manually.

---

## Testing requirements

### Backend tests

Add focused tests for:

- success envelopes;
- strict request validation;
- authentication/authorization behavior;
- not-found and forbidden behavior;
- pagination and caps;
- allow-listed filtering and sorting;
- cancellation where applicable;
- partial dependency failures;
- source read-only enforcement;
- sandbox mutation idempotency;
- optimistic concurrency;
- import validation and redaction;
- export redaction and expiry;
- deterministic scenario generation;
- scenario validation/approval gates;
- audit creation and safe retrieval;
- no secret/DSN/raw-driver-error leakage.

### Frontend tests

Retain and expand component tests for:

- every route;
- live adapter success;
- loading, empty, partial, error, forbidden, timeout, cancelled, and validation
  states;
- pagination/filter/sort;
- read-only/writable capability gating;
- CRUD confirmations;
- import/export wizard progression;
- graph search, selection, bounded expansion, and table fallback;
- scenario generation and approval gating;
- audit/governance/settings/hardening screens;
- request ID and freshness display;
- no secret/redacted-field leakage.

### Full-stack integration tests

Run with:

- frontend live mode;
- MSW disabled;
- backend container running;
- deterministic sandbox seed loaded;
- required infrastructure healthy.

Cover:

- every canonical route direct-load;
- frontend-to-backend proxy behavior;
- every API operation used by a screen;
- list-to-detail navigation;
- browser pagination and record detail;
- graph search and inspector routing;
- workspace create/edit/delete;
- import submission and job detail;
- export submission and safe download;
- scenario generate/validate/approve/preview;
- audit/governance/settings/hardening;
- refresh/deep-link restoration;
- mobile navigation;
- keyboard interaction;
- accessibility scans.

Do not use fixture handlers in the live integration suite.

---

## Docker gates

Inspect the actual repository scripts first and run equivalent commands inside the
existing containers.

### Backend

Run in `stage3-backend` or the repository's active backend container:

```text
ruff format --check src tests
ruff check src tests
python -m mypy --no-incremental src tests
python -m pytest -vv
```

Use Poetry-prefixed forms if that is how the container is configured.

### Frontend

Run in `stage3-frontend`:

```text
npm ci
npm run lint
npm run typecheck
npm test
npm run build
npm run check:bundle
```

### Browser integration

After backend closure:

```text
npm run test:e2e
npm run test:a11y
```

Configure these tests to use the live backend with MSW disabled.

Also verify:

- normal production build contains no mock artifacts;
- `VITE_MOCK_MODE=true` production build is rejected;
- direct `npx vite build --mode mock` is rejected;
- all required proxy/API routes return safe expected responses.

---

## Reliability and security checks

Before completion:

- Verify request bounds with over-limit inputs.
- Verify malformed IDs and encoded route values.
- Verify permission failures.
- Verify redacted fields never appear in UI, downloads, logs, or test output.
- Verify no arbitrary query execution.
- Verify source write attempts are impossible.
- Verify replay/idempotency for mutations.
- Verify unknown mutation outcomes are not blindly retried.
- Verify pagination remains deterministic.
- Verify partial dependency failures remain usable.
- Verify production frontend contains no MSW worker or fixture runtime.
- Verify logs contain request IDs but no secrets.

Do not claim load, security, failover, or reliability validation unless the
corresponding commands and evidence are actually recorded.

---

## Documentation and evidence

Update:

```text
README.md
docs/review/status/full_stack_integration_status.md
docs/evidence/data_console_complete_ui/stage3a/api_gap_register.md
docs/evidence/data_console_complete_ui/stage3a/api_gap_register.json
frontend/docs/evidence/data_console_complete_ui/validation_summary.md
frontend/docs/evidence/data_console_complete_ui/route_inventory.md
frontend/docs/evidence/data_console_complete_ui/live_vs_fixture_capability_matrix.md
```

Create backend evidence under:

```text
backend/docs/evidence/data_console_full_stack/
```

Include:

- API route inventory;
- persistence/ownership matrix;
- contract generation record;
- backend gate totals;
- frontend gate totals;
- live proxy results;
- full-stack E2E totals;
- accessibility totals;
- known limitations;
- deferred hardening items;
- screenshot status.

Status reports belong in `docs/review/status/`. Reviewer verdicts belong in
`docs/review/verdict/`. Implementation agents must not approve their own work.

---

## Completion criteria

Do not declare this prompt complete until:

1. Every canonical frontend route exists and is reachable.
2. Every frontend screen uses a live backend adapter in normal mode.
3. Every unavailable fixture-only API gap required by the UI is closed or explicitly
   removed from the route.
4. MSW is disabled during live integration.
5. Sources and Neo4j remain read-only.
6. Sandbox mutations persist only to Platform MongoDB.
7. Import/export/scenario operations enforce bounds, approval, redaction, and
   idempotency.
8. OpenAPI and generated frontend contracts match.
9. Complete backend format/lint/type/test gates pass in Docker.
10. Complete frontend lint/type/unit/build/bundle gates pass in Docker.
11. Full-stack Playwright E2E passes with the live backend.
12. Playwright accessibility passes across every canonical route.
13. Production mock exclusion and negative mock-build checks pass.
14. README, gap register, route inventory, capability matrix, status, and evidence
    are truthful and consistent.
15. No secrets, DSNs, raw errors, arbitrary queries, source writes, or unapproved
    graph mutations are exposed.
16. Screenshot capture occurs only during the hardening step, after functional gates
    pass, and is recorded truthfully.
17. No Git commit is created.

---

## Final report

When all criteria pass, report:

- every implemented frontend route;
- every implemented backend API;
- live-backed capabilities;
- remaining fixture-only capabilities, if any;
- database ownership and persistence behavior;
- OpenAPI/contract generation result;
- backend test totals;
- frontend test totals;
- live E2E totals;
- accessibility totals;
- security/reliability checks performed;
- known limitations and deferred evidence;
- screenshot evidence status;
- confirmation that no Git commit was created.

Use these exact truth labels:

```text
FRONTEND SCREENS: COMPLETE
BACKEND API INTEGRATION: COMPLETE
LIVE FRONTEND/BACKEND E2E: PASSED
ACCESSIBILITY: PASSED
PRODUCTION MOCK EXCLUSION: PASSED
SCREENSHOTS: CAPTURED AT HARDENING | DEFERRED
GIT COMMIT: NOT CREATED
```

If any required gate is not green, do not use `COMPLETE` or `PASSED`. Record the
specific blocker and continue working on safe in-scope corrections.
