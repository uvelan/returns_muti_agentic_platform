# Codex Remediation Prompt — Fix All Verified Frontend and Backend Issues

Continue in the current Return Platform repository.

Your objective is:

> **Fix every verified frontend, backend, contract, Docker, security, test, and
> documentation issue blocking complete live Data Console integration.**

Do not stop after writing a plan. Work continuously through implementation,
phase-level validation, live integration, and truthful evidence. Stop only for a
genuine security/architecture decision requiring new authority, unavailable
credentials or infrastructure, or a Docker Desktop failure that cannot be corrected
from the repository.

---

## Mandatory context

Read completely before changing code:

- `README.md`
- `CODEX_PROMPT_COMPLETE_FRONTEND_BACKEND_INTEGRATION.md`
- `docs/review/verdict/complete_frontend_backend_integration_plan_verdict.md`
- `docs/review/status/full_stack_integration_status.md`
- `docs/evidence/data_console_complete_ui/stage3a/api_gap_register.md`
- `docs/evidence/data_console_complete_ui/stage3a/api_gap_register.json`
- `docs/evidence/data_console_complete_ui/stage3a/route_capability_matrix.md`
- `docs/evidence/data_console_complete_ui/stage3a/ownership_write_policy_matrix.md`
- all other files under `docs/review/status/` and `docs/review/verdict/`

Inspect the current worktree before editing. It contains substantial modified and
untracked user work. Preserve it and reconcile it incrementally.

Do not trust completion claims without source inspection and executable evidence.

---

## Locked rules

1. Do not create a Git commit.
2. Do not reset, discard, hide, overwrite, or clean user changes.
3. Use `apply_patch` for source edits.
4. Use Docker for final frontend, backend, database, OpenAPI, Playwright, and
   production-build validation.
5. Do not silently substitute host validation for a required Docker gate.
6. Local frontend execution may be used only as an interim diagnostic when the user
   explicitly permits it; it does not satisfy final Docker gates.
7. Keep MSW and fixture adapters development-only.
8. Run live integration and E2E with MSW disabled.
9. Never convert a live API failure into fixture success.
10. Do not capture screenshots until the Hardening phase and only after functional,
    live E2E, and accessibility gates are green.
11. SQL Server/OMC and source MongoDB are governed read-only sources.
12. Neo4j is a read-only derived projection.
13. Platform MongoDB owns sandbox workspaces, jobs, scenarios, audit, and evidence.
14. Temporal owns durable orchestration, not business data.
15. Valkey is transient coordination/cache only.
16. Live AI providers remain disabled. Use a provider-neutral port and deterministic
    sandbox generator.
17. Do not weaken TypeScript, ESLint, Ruff, mypy, Pydantic, production checks,
    authorization, or tests.
18. Never expose secrets, DSNs, raw environment values, raw exceptions, driver
    errors, arbitrary SQL, Mongo filters, Cypher, or executable configuration.

---

## Current verified status

Treat these as starting facts and re-check them before editing:

### Frontend

- The canonical frontend route set is structurally present.
- Most new routes are still labeled `FIXTURE`.
- The local Vite server returned HTTP 200 at `http://127.0.0.1:5173/`.
- Local TypeScript validation passed.
- Local lint failed in
  `frontend/src/api/adapters/scenariosFixtureAdapter.ts` because of unsafe explicit
  `any`.
- `frontend/src/api/adapters/browser.ts` contains a blanket `eslint-disable`.
- `frontend/fix.js` is an untracked repair/debug artifact.
- The unit test process stalled and did not produce a final passing result.
- The latest recorded Playwright result is failed.
- Vite returned 502 for backend proxy calls because the backend was unavailable.
- Existing component tests do not cover all new routes and live adapters.

### Docker

- Docker Desktop processes were running, but the Linux engine API was unavailable.
- Docker logs showed `_ping` HTTP 500, timeouts reaching `192.168.65.7:2376`, and the
  initialization control API not responding.
- `compose.yaml` currently defines infrastructure services but no reproducible
  frontend or backend application services.
- Previous `stage3-frontend` and `stage3-backend` containers were manually created
  rather than defined as durable Compose services.

### Backend

- New routers are mounted for Sources, Browser, Graph, Workspaces, Jobs, Scenarios,
  Audit, Governance, Settings, and Hardening.
- `GET /data-console/v1/inventory/{engine}/{assetId}` is missing.
- Sources are hard-coded rather than derived from governed runtime resources.
- Browser pagination, filtering, sorting, record identity, redaction, and partial
  failures are incomplete.
- Browser SQL construction is interpolated and exceptions are printed.
- Browser record detail searches only the small list response rather than performing
  a governed exact record lookup.
- Neo4j browser records remain a placeholder.
- Workspace, Job, and Scenario modules define roles or import `Principal` without
  consistently enforcing authorization.
- Several handlers put `str(exception)` into response warnings.
- Workspace mutations lack complete idempotency, optimistic concurrency, schema
  validation, audit evidence, and safe delete behavior.
- Import/export operations are scaffolds and do not meet sandbox, validation,
  redaction, approval, bounds, expiry, or idempotency requirements.
- Export download returns an invented external URL.
- Scenario generate/validate only alter status, approval is not properly gated, and
  preview is placeholder text.
- Governance, Settings, and Hardening responses are placeholders.
- Hardening reports an unsupported score and zero vulnerabilities.
- New APIs lack focused backend tests.

### Contracts and evidence

- Backend OpenAPI and frontend generated TypeScript contracts were not regenerated
  for the new endpoints.
- The API gap register still marks all new endpoints unavailable.
- `full_stack_integration_status.md` still reports them as `FIXTURE/PENDING`.
- `git diff --check` currently fails because of trailing whitespace in
  `frontend/tests/a11y.spec.ts`.
- No complete live frontend/backend E2E or accessibility result exists.

---

## Execution order

Complete the remediation in this order.

## Phase 0 — Restore reproducible Docker execution

1. Re-check Docker Desktop.
2. If the Docker engine remains unavailable because of host Docker Desktop/VM state,
   record the exact error and request the minimum user action required. Do not
   pretend repository changes can repair a dead host engine.
3. Once the engine responds, inspect all existing containers, networks, volumes, and
   health states.
4. Add production-like development Dockerfiles and Compose services for:
   - `backend`
   - `frontend`
5. Preserve existing infrastructure services and volumes.
6. Add health checks and explicit dependency behavior.
7. Use container networking for the frontend proxy target.
8. Avoid host-mounted `node_modules`.
9. Use deterministic dependency installation.
10. Document the exact Docker startup and validation commands.

Required outcome:

```text
docker compose config
docker compose up -d <required-services>
docker compose ps
```

must provide a reproducible frontend, backend, and required dependency environment.

Do not run final host npm/Python gates as a substitute.

---

## Phase 1 — Stabilize frontend quality and test execution

1. Fix the unsafe `any` in `scenariosFixtureAdapter.ts` with a strict domain type and
   validated parsing.
2. Remove the blanket `eslint-disable` from the Browser adapter and fix every
   resulting lint issue correctly.
3. Remove `frontend/fix.js` if it is only a repair/debug artifact. Preserve it if it
   contains legitimate user work, but move that work into maintained source/tests.
4. Fix trailing whitespace and make `git diff --check` pass.
5. Diagnose the Vitest stall:
   - inspect configuration and open handles;
   - avoid overlapping test processes;
   - identify the exact hanging suite;
   - fix the cause rather than adding an arbitrary global timeout;
   - ensure tests terminate naturally.
6. Add focused tests for every page currently missing component coverage.
7. Test loading, empty, partial, forbidden, timeout, cancellation, validation,
   not-found, conflict where relevant, and unexpected errors.
8. Test live and fixture adapter selection.
9. Verify fixture mode is explicit and normal mode always uses live HTTP adapters.
10. Encode all dynamic route identifiers safely.
11. Keep comparison as an optional route, not a replacement for scenario preview.
12. Keep Audit and Governance distinct.

Run phase gates in the frontend container:

```text
npm ci
npm run lint
npm run typecheck
npm test
npm run build
npm run check:bundle
```

Do not proceed on the assumption that progress dots mean tests passed. Require a
clean exit code and recorded totals.

---

## Phase 2 — Establish shared backend API foundations

Before expanding endpoint logic, create or reuse consistent infrastructure for:

- authenticated principal resolution;
- role/capability authorization;
- strict Pydantic models with `extra="forbid"`;
- path/query identifier validation;
- response envelope construction;
- stable safe error codes;
- correlation/request IDs;
- bounded warnings;
- exception-to-safe-error mapping;
- pagination;
- idempotency keys;
- optimistic concurrency/version tokens;
- audit event creation;
- cancellation and dependency timeouts.

Every success response must preserve:

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

- never put `str(exception)` in a response;
- never `print` source or driver failures;
- log safe error context with request ID and no secrets;
- reject unknown request properties and unsupported query parameters;
- enforce role checks on every read and mutation;
- add exact tests for forbidden, unauthenticated, invalid, conflict, not-found,
  partial, dependency, and success behavior.

---

## Phase 3 — Fix Sources, Inventory, and Browser

Implement and validate:

```text
GET /data-console/v1/sources
GET /data-console/v1/sources/{sourceId}
GET /data-console/v1/inventory
GET /data-console/v1/inventory/{engine}/{assetId}
GET /data-console/v1/browser/assets
GET /data-console/v1/browser/{engine}/{assetId}/records
GET /data-console/v1/browser/{engine}/{assetId}/records/{recordId}
```

### Sources

- Derive sources from governed catalog/runtime configuration.
- Expose safe connection identity only.
- Never expose DSNs, hosts requiring redaction, usernames, passwords, or raw config.
- Compute truthful health, freshness, inventory totals, and partial warnings.
- Preserve healthy sources when one dependency fails.

### Inventory detail

- Add the missing canonical endpoint.
- Use stable engine/asset identities.
- Return metadata only.
- Reject unknown engines and malformed IDs.
- Link to governed Browser and Graph routes only when supported.

### Browser

- Permit governed catalog assets only.
- Keep SQL Server and source MongoDB strictly read-only.
- Keep Neo4j read-only with fixed parameterized queries.
- Do not accept arbitrary SQL, Mongo filters, or Cypher.
- Use allow-listed identifiers and safe identifier quoting.
- Enforce page size, record, response-size, and timeout bounds.
- Implement deterministic cursor/seek pagination.
- Support only allow-listed filter and sort fields.
- Implement exact record lookup instead of searching the first page.
- Preserve partial dependency failures truthfully.
- Sanitize/redact records before transport.
- Represent null, missing, redacted, binary, nested, array, date, number, Boolean,
  node, and relationship values safely.
- Do not turn a driver failure into empty success.

Add focused backend and frontend live-adapter tests for every operation and failure
state.

---

## Phase 4 — Fix Graph Explorer

Validate and harden:

```text
GET /data-console/v1/graph/search
GET /data-console/v1/graph/nodes/{nodeId}
GET /data-console/v1/graph/relationships/{relationshipId}
GET /data-console/v1/graph/nodes/{nodeId}/neighborhood
```

Requirements:

- exact governed identifier search;
- fixed parameterized Cypher only;
- no graph writes;
- enforce node, relationship, depth, and expansion caps;
- enforce authorization;
- return ownership, provenance, partial, and truncation metadata;
- direct detail URL restoration;
- accessible graph-table fallback;
- stable errors with no raw Neo4j details;
- focused backend tests and live-adapter/component tests.

---

## Phase 5 — Fix Workspaces

Use Platform MongoDB only.

Implement and validate the complete approved workspace and record surface.

Requirements:

- writable sandbox classification;
- role/capability authorization;
- strict request schemas;
- stable identity;
- schema and cross-record validation;
- idempotency keys for creation/deletion where applicable;
- optimistic concurrency/version checks for updates;
- transaction-safe record count maintenance;
- audit events with safe before/after evidence;
- explicit deletion confirmation contract;
- safe archive/soft-delete policy where required;
- no source or Neo4j mutation;
- no access to Motor collections directly from route handlers;
- repository/service boundaries instead of private-member access.

Correct malformed, missing workspace, duplicate, conflict, and partial outcomes.

---

## Phase 6 — Fix Jobs, Imports, and Exports

Implement and validate:

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

### Imports

- target writable sandbox workspaces only;
- support bounded CSV, JSON, and JSONL;
- validate content type, size, record count, mapping, and schema;
- provide parse preview and bounded issues;
- duplicate policy only `reject`, `skip`, or sandbox `replace`;
- require explicit approval before submission;
- enforce authorization and idempotency;
- store durable metadata in Platform MongoDB;
- use Temporal only when durable execution is actually needed.

### Exports

- source only from governed permitted assets/workspaces;
- require explicit selected fields;
- enforce record and size limits;
- apply mandatory redaction;
- create safe filenames and content types;
- store safe expiring download metadata;
- authorize the download request itself;
- never return invented URLs;
- never expose secrets, hidden fields, or unredacted values.

Add job state-transition and audit evidence tests.

---

## Phase 7 — Fix AI Scenario Studio

Implement the real lifecycle:

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

- provider-neutral generation port;
- deterministic backend sandbox generator;
- explicit reproducible seed;
- positive, negative, boundary, and failure cases;
- entity, record, and response-size caps;
- canonical and cross-entity validation;
- exact issue references;
- safe provenance without secrets;
- valid state-machine transitions;
- approval blocked until validation passes;
- preview returns typed generated records, not placeholder text;
- approved data may import only into a writable sandbox;
- live model provider remains disabled;
- no direct writes to sources, Neo4j, or arbitrary Platform MongoDB collections;
- authorization, idempotency, versioning, audit, and exact tests.

---

## Phase 8 — Fix Audit, Governance, Settings, and Hardening

Implement truthful read-only endpoints:

```text
GET /data-console/v1/audit
GET /data-console/v1/audit/{auditId}
GET /data-console/v1/governance
GET /data-console/v1/settings
GET /data-console/v1/hardening
```

Requirements:

- Audit is authorized, bounded, paginated, immutable, and safely redacted.
- Governance derives from actual ownership and capability policy.
- Settings exposes only allow-listed, non-secret configuration and feature state.
- Hardening derives only from recorded evidence.
- Remove fabricated scores, vulnerability counts, timestamps, and compliance states.
- Items without evidence must be `PENDING`, `DEFERRED`, or `NOT_RUN`.
- Screens and APIs must agree on exact truth labels.

Do not capture screenshots yet.

---

## Phase 9 — Regenerate and enforce contracts

In Docker:

1. Export backend OpenAPI.
2. Regenerate frontend TypeScript transport declarations.
3. Run the contract drift check.
4. Replace manual duplicate HTTP transport shapes where generated types exist.
5. Keep domain ports independent from generated transport types.
6. Validate unknown responses at the adapter boundary.
7. Update both API gap registers from actual implemented and tested status.

Do not edit generated TypeScript manually.

---

## Phase 10 — Live frontend/backend integration

Run with:

- frontend container;
- backend container;
- required dependencies healthy;
- deterministic sandbox seed;
- MSW disabled;
- live adapters selected.

Verify:

- every canonical route direct-loads;
- proxy requests preserve correlation IDs;
- Sources and Inventory list/detail;
- Browser pagination/filter/sort/detail;
- Graph search, selection, expansion, and direct detail routes;
- workspace create/update/record create/edit/delete;
- import preview/approval/submission/job detail;
- export submission/detail/safe download;
- scenario create/generate/validate/approve/preview;
- Audit, Governance, Settings, and Hardening;
- refresh and deep-link restoration;
- mobile navigation;
- keyboard interaction;
- safe error states;
- no fixture banners or fallback responses.

---

## Required Docker gates

Inspect the actual container commands and run the equivalent commands inside the
reproducible application containers.

### Backend

```text
ruff format --check src tests
ruff check src tests
python -m mypy --no-incremental src tests
python -m pytest -vv
```

Use Poetry-prefixed forms if required by the backend image.

### Frontend

```text
npm ci
npm run lint
npm run typecheck
npm test
npm run build
npm run check:bundle
```

### Live browser validation

```text
npm run test:e2e
npm run test:a11y
```

Run Playwright with MSW disabled and the live backend reachable.

Also prove:

- the normal production bundle contains no `mockServiceWorker.js`, fixture banner,
  `setupWorker`, or fixture runtime;
- a production build with `VITE_MOCK_MODE=true` is rejected;
- `vite build --mode mock` is rejected;
- `git diff --check` passes;
- all test processes terminate naturally.

---

## Security and reliability checks

Before completion, verify:

- unauthenticated and unauthorized requests;
- malformed and encoded identifiers;
- over-limit page/file/record inputs;
- unknown query/body properties;
- redaction in UI, API, download, logs, and tests;
- no arbitrary SQL/Mongo/Cypher execution;
- no source writes;
- no graph mutations;
- idempotent mutation replay;
- optimistic concurrency conflict behavior;
- no blind retry after unknown mutation outcome;
- deterministic pagination;
- partial dependency usability;
- dependency timeout behavior;
- correlation IDs in safe logs;
- no secrets, DSNs, raw errors, or driver traces.

Do not claim load, security, failover, or reliability validation without exact
commands and retained evidence.

---

## Hardening and screenshots

Only after all functional, contract, production-bundle, live E2E, and accessibility
gates pass:

1. update the Hardening API and page from actual evidence;
2. capture the approved screenshot matrix;
3. record each screenshot route, viewport, state, and timestamp;
4. keep unsupported checks labeled `PENDING` or `DEFERRED`.

Until then:

```text
SCREENSHOTS: DEFERRED
```

---

## Documentation and evidence

Update truthfully:

- `README.md`
- `docs/review/status/full_stack_integration_status.md`
- `docs/evidence/data_console_complete_ui/stage3a/api_gap_register.md`
- `docs/evidence/data_console_complete_ui/stage3a/api_gap_register.json`
- `frontend/docs/evidence/data_console_complete_ui/validation_summary.md`
- `frontend/docs/evidence/data_console_complete_ui/route_inventory.md`
- `frontend/docs/evidence/data_console_complete_ui/live_vs_fixture_capability_matrix.md`

Create/update backend evidence under:

```text
backend/docs/evidence/data_console_full_stack/
```

Record:

- route/API inventory;
- ownership and persistence;
- authorization matrix;
- OpenAPI generation;
- backend gate commands and totals;
- frontend gate commands and totals;
- live proxy results;
- E2E totals;
- accessibility totals;
- security/reliability checks;
- known limitations;
- deferred evidence;
- screenshot status.

Status belongs in `docs/review/status/`. Reviewer verdicts belong in
`docs/review/verdict/`. The implementation agent must not approve its own work.

---

## Completion criteria

Do not declare completion until all are true:

1. Docker provides reproducible frontend and backend services.
2. Every canonical frontend route exists and is reachable.
3. Every normal-mode screen uses a live backend adapter.
4. Every required backend API is implemented with strict contracts.
5. Inventory detail is implemented.
6. Sources are governed and runtime-derived.
7. Browser access is bounded, redacted, deterministic, and read-only.
8. Neo4j remains read-only and parameterized.
9. Mutations enforce authorization, idempotency, validation, and concurrency.
10. Imports target writable sandboxes only.
11. Exports enforce mandatory redaction and safe expiring downloads.
12. Scenario generation is deterministic and approval-gated.
13. Governance, Settings, and Hardening are evidence-derived.
14. No raw exceptions, secrets, DSNs, arbitrary queries, source writes, or graph
    mutations are exposed.
15. OpenAPI and generated frontend contracts match.
16. Backend Docker format/lint/type/test gates pass.
17. Frontend Docker install/lint/type/unit/build/bundle gates pass.
18. Vitest terminates naturally.
19. Live Playwright E2E passes with MSW disabled.
20. Accessibility passes across every canonical route.
21. Production mock exclusion and negative mock-build checks pass.
22. `git diff --check` passes.
23. Documentation and evidence match observed results.
24. Screenshots occur only at Hardening after green gates.
25. No Git commit is created.

---

## Final report

Report:

- frontend routes implemented;
- backend APIs implemented;
- live and remaining fixture capabilities;
- ownership/persistence behavior;
- authorization behavior;
- OpenAPI result;
- backend test totals;
- frontend test totals;
- live E2E totals;
- accessibility totals;
- production mock checks;
- security/reliability checks;
- known limitations;
- screenshot status;
- confirmation that no Git commit was created.

Use these labels only when supported:

```text
DOCKER FRONTEND/BACKEND: REPRODUCIBLE
FRONTEND SCREENS: COMPLETE
BACKEND API INTEGRATION: COMPLETE
OPENAPI CONTRACT DRIFT: NONE
BACKEND GATES: PASSED
FRONTEND GATES: PASSED
LIVE FRONTEND/BACKEND E2E: PASSED
ACCESSIBILITY: PASSED
PRODUCTION MOCK EXCLUSION: PASSED
SCREENSHOTS: CAPTURED AT HARDENING | DEFERRED
GIT COMMIT: NOT CREATED
```

If any gate is not green, do not use `COMPLETE`, `PASSED`, or `REPRODUCIBLE`.
Record the exact blocker and continue with safe in-scope remediation.
