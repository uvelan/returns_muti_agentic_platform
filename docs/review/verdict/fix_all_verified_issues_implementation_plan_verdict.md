# Fix All Verified Issues Implementation Plan Verdict

Review date: 2026-07-23
Review type: remediation implementation-plan review
Reviewed scope: `Implementation Plan: Fix All Verified Frontend and Backend Issues`
Primary authority: `CODEX_PROMPT_FIX_ALL_VERIFIED_FRONTEND_BACKEND_ISSUES.md`

## Verdict

**CHANGES REQUIRED — DIRECTION APPROVED; EXECUTION MAY PROCEED AFTER THE PLAN IS
EXPANDED INTO THE REQUIRED CHECKLIST**

The proposed phase order is sensible and broadly matches the remediation prompt.
Creating reproducible application containers, stabilizing the frontend, establishing
shared backend foundations, correcting the feature APIs, regenerating contracts, and
running live integration in that order is approved.

The submitted plan is too compressed to control this remediation safely. It omits
important import/export requirements, persistence boundaries, authorization and
mutation details, phase-level validation, negative production-mock checks, security
verification, evidence files, and required manual hardening verification. It also
introduces an unnecessary blanket approval pause.

This is a plan verdict only. It does not certify any implementation, API, screen,
test, or Docker service as complete.

## Docker state verified during this review

Docker Desktop is now responding:

```text
Docker Desktop: 4.83.0
Docker Engine: 29.6.2
```

Observed Compose state:

| Service | Observed state |
|---|---|
| MongoDB | Up, healthy |
| Neo4j | Up, healthy |
| Temporal PostgreSQL | Up, healthy |
| Temporal schema setup | Exited successfully |
| Temporal | Up |
| Temporal UI | Up |
| Valkey | Up, healthy |
| SQL Server | Created, not running |
| SQL Server initializer | Created, not running |
| Backend application | Not defined as a Compose service |
| Frontend application | Not defined as a Compose service |

Therefore, “Docker is running” is true only for the engine and part of the
infrastructure. It is not evidence that the application stack is reproducible or
healthy.

## Approved direction

The following decisions are approved:

1. Add reproducible frontend and backend Docker build definitions.
2. Add frontend and backend services to Compose with health checks.
3. Repair strict frontend lint failures and test termination.
4. Establish reusable authentication, authorization, envelope, error, pagination,
   idempotency, concurrency, and audit foundations.
5. Reconcile the existing backend API modules rather than build parallel routers.
6. Regenerate OpenAPI and frontend transport types after backend correction.
7. Run live E2E and accessibility with MSW disabled.
8. Capture screenshots only during Hardening after functional gates pass.

## Required corrections

### P0 — Remove the blanket approval pause

Delete `User Review Required` from the implementation plan. The user has already
authorized the remediation through the master prompt.

Execution may proceed continuously after this verdict is incorporated. Stop only
for a genuine security/architecture choice, unavailable credential or external
dependency, destructive host action, or material scope expansion.

### P0 — Preserve existing work and prohibit commits

The plan must explicitly state:

- do not create a Git commit;
- do not reset, clean, discard, overwrite, or hide existing changes;
- reconcile Stage 3D–3H incrementally;
- do not treat untracked files as disposable;
- remove `frontend/fix.js` only after confirming it is a debug/repair artifact;
- do not delete fixture adapters when adding live adapters.

### P0 — Make Docker Phase 0 accurate and testable

Replace “Check Docker status (done; it is running)” with the verified service table
above.

Phase 0 must also require:

1. diagnose why SQL Server and `sqlserver-init` remain only `Created`;
2. start them safely or record the exact blocker;
3. create frontend/backend Dockerfiles with pinned, deterministic dependencies;
4. add Compose application services and health checks;
5. connect the frontend proxy to the backend by service name;
6. avoid host-mounted `node_modules`;
7. preserve persistent infrastructure volumes;
8. define startup dependencies without masking partial dependency states;
9. verify backend readiness and frontend HTTP health;
10. ensure Playwright browser dependencies exist in the validation image.

`docker compose exec` gates are valid only after the application services are
actually defined and healthy. For clean reproducibility checks, prefer appropriate
`docker compose run --rm` validation services where a long-running service is not
required.

### P0 — Restore the complete API envelope contract

The plan describes only `data` and `meta`. Every endpoint must use the complete
envelope:

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

Shared foundations must include correlation propagation, bounded warnings,
cancellation, safe timeouts, stable error codes, unknown-parameter rejection,
deterministic pagination, allow-listed filtering/sorting, and safe logging.

Never expose `str(exception)`, driver errors, DSNs, secrets, raw queries, or
environment values.

### P0 — Define authentication and authorization precisely

“RBAC decorators/dependencies” is directionally correct but insufficient. Add an
operation-level authorization matrix covering:

- source/inventory/browser read;
- graph read and expansion;
- workspace read and mutation;
- job read;
- import creation and approval;
- export creation and download;
- scenario authoring, generation, validation, approval, and preview;
- audit, governance, settings, and hardening read.

Test unauthenticated and unauthorized requests for every operation category. Merely
declaring role constants or importing `Principal` does not satisfy authorization.

### P0 — Restore explicit persistence and write boundaries

The plan must preserve:

| System | Permitted behavior |
|---|---|
| SQL Server / OMC | Governed read-only metadata and record browsing |
| Source MongoDB | Governed read-only discovery and record browsing |
| Neo4j | Read-only derived graph exploration |
| Platform MongoDB | Workspaces, jobs, scenarios, audit, evidence |
| Temporal | Durable orchestration where justified |
| Valkey | Transient cache and coordination only |

Source writes and graph mutations must be impossible through Data Console code and
must have exact tests.

### P0 — Do not collapse Phases 3–8 into an ambiguous refactor

The implementation checklist must list every exact required endpoint from the
remediation prompt and track each endpoint through:

- route;
- request/response model;
- authorization;
- persistence owner;
- service/repository implementation;
- frontend port/live adapter;
- backend tests;
- frontend tests;
- live smoke test;
- OpenAPI status.

The current high-level description is not enough to prevent missed operations.

### P0 — Add the omitted import/export requirements

The submitted plan does not adequately describe Imports and Exports.

Imports must:

- target writable sandbox workspaces only;
- support bounded CSV, JSON, and JSONL;
- validate content type, file size, record count, mapping, duplicates, and schema;
- provide preview and bounded issues;
- allow duplicate policies only `reject`, `skip`, or sandbox `replace`;
- require explicit approval;
- enforce authorization and idempotency;
- store durable job metadata in Platform MongoDB.

Exports must:

- use governed permitted sources/workspaces only;
- require selected fields;
- enforce size and record limits;
- apply mandatory redaction;
- generate safe filenames and content types;
- store expiring download metadata;
- authorize download requests;
- never return an invented URL;
- prove that secrets and redacted values cannot appear in downloaded content.

### P0 — Make workspace mutation safety explicit

Replace “pessimistic/optimistic locking where required” with the approved behavior:

- optimistic concurrency/version tokens for updates;
- idempotency keys for applicable mutations;
- strict schemas and cross-record validation;
- transaction-safe record counts;
- audit events with safe before/after evidence;
- explicit delete confirmation contracts;
- soft archive where policy requires it;
- repository/service boundaries;
- no direct use of private Motor collection members from route handlers;
- no source or Neo4j writes.

Do not add pessimistic locking without a demonstrated requirement and compatible
MongoDB design.

### P0 — Make scenario behavior concrete

The checklist must enforce:

```text
create -> generate -> validate -> approve -> preview/import
```

Generation needs a deterministic seed and bounded positive, negative, boundary, and
failure cases. Validation requires exact issue references. Approval must be rejected
until validation passes. Preview must return typed generated records, not placeholder
text. Approved data may be imported only into a writable sandbox.

Live AI providers remain disabled.

### P0 — Keep governance and hardening evidence-based

Remove fabricated scores, compliance results, vulnerability counts, and timestamps.
Governance must derive from actual ownership policy. Settings must expose only an
allow-list of non-secret information. Hardening must show `PENDING`, `DEFERRED`, or
`NOT_RUN` until exact evidence exists.

### P1 — Validate after every phase

The plan currently places nearly all verification in Phase 10. Add phase-level
gates:

1. backend format/lint/type and focused tests for each API group;
2. frontend lint/type and focused adapter/component tests;
3. OpenAPI drift verification after a coherent API group changes;
4. live smoke tests with MSW disabled;
5. route/API checklist and evidence update.

Run the complete suite again at the end.

### P1 — Clarify the frontend test-stall investigation

Do not call the observed stall an OOM unless memory evidence proves it. The previous
local run had overlapping Vitest processes and later still failed to terminate
normally with one worker.

The investigation must:

- identify the exact hanging file or open handle;
- avoid overlapping runners;
- preserve strict tests;
- avoid masking the defect with an arbitrary timeout;
- record a natural clean exit and exact test totals.

Progress dots are not a passing result.

### P1 — Expand frontend verification

Add explicit tests for:

- all canonical routes and dynamic deep links;
- fixture/live adapter selection;
- live failures remaining failures;
- loading, empty, partial, forbidden, timeout, cancellation, validation, not-found,
  conflict, and unexpected states;
- request IDs and freshness;
- redaction and safe copying;
- graph table fallback;
- workspace confirmations and concurrency conflicts;
- import/export wizard gates;
- scenario approval gating;
- mobile and keyboard behavior.

### P1 — Expand contract verification

OpenAPI work must run inside a container layout that can access both backend source
and frontend generated output. Do not assume the existing relative
`contracts:generate` script works in a service mounting only `frontend/`.

Requirements:

- export OpenAPI from the corrected backend;
- regenerate frontend types;
- never edit generated types manually;
- validate transport responses at adapter boundaries;
- keep domain ports independent of generated transport types;
- run a drift check with a clean exit code.

### P1 — Add missing production and security checks

The final gates must include:

- normal production bundle contains no MSW worker or fixture runtime;
- `VITE_MOCK_MODE=true` production build is rejected;
- `vite build --mode mock` is rejected;
- malformed and encoded identifiers;
- over-limit inputs;
- mutation replay/idempotency;
- optimistic concurrency conflicts;
- no arbitrary SQL/Mongo/Cypher;
- no source write or graph mutation path;
- partial dependency behavior;
- safe logs with request IDs and no secrets;
- `git diff --check`.

### P1 — Correct the documentation scope

Updating only `docs/evidence/data_console_complete_ui/` is insufficient. Update:

- `README.md`;
- `docs/review/status/full_stack_integration_status.md`;
- Markdown and JSON API gap registers;
- frontend validation summary;
- route inventory;
- live/fixture capability matrix;
- backend evidence under
  `backend/docs/evidence/data_console_full_stack/`.

Record exact commands, exit codes, totals, known limitations, deferred checks, and
screenshot status.

### P1 — Manual verification is required

Replace `Manual Verification: N/A`.

Automated tests are necessary but do not replace:

- visual inspection of every major screen;
- responsive layout checks;
- keyboard interaction review;
- graph canvas/table usability;
- destructive confirmation review;
- redaction and copy behavior;
- final screenshot-matrix capture during Hardening.

Manual verification and screenshots remain deferred until functional, live E2E, and
accessibility gates are green.

## Required execution sequence

After incorporating the corrections:

1. restore the complete Docker application stack;
2. stabilize frontend lint and naturally terminating tests;
3. add shared backend security/contract foundations;
4. remediate each API group with focused tests;
5. regenerate contracts incrementally;
6. connect and verify live frontend adapters;
7. run full backend and frontend Docker gates;
8. run live E2E and accessibility with MSW disabled;
9. run production-mock and security/reliability checks;
10. perform hardening manual review and screenshots;
11. update truthful status/evidence;
12. confirm no Git commit was created.

## Current truth labels

```text
PLAN DIRECTION: APPROVED WITH REQUIRED CHANGES
DOCKER ENGINE: AVAILABLE
DOCKER INFRASTRUCTURE: PARTIAL
DOCKER FRONTEND/BACKEND: NOT DEFINED
FRONTEND SCREENS: STRUCTURALLY PRESENT / NOT LIVE-VERIFIED
BACKEND API INTEGRATION: PARTIAL / NOT VERIFIED
OPENAPI CONTRACT DRIFT: NOT CHECKED AFTER NEW ROUTES
BACKEND GATES: NOT RUN FOR NEW API SURFACE
FRONTEND GATES: FAILING OR INCOMPLETE
LIVE FRONTEND/BACKEND E2E: NOT PASSED
ACCESSIBILITY: NOT FULLY VERIFIED
SCREENSHOTS: DEFERRED
GIT COMMIT: NOT CREATED BY THIS REVIEW
```

No implementation files were changed by this review. No tests or screenshots were
run. No Git commit was created.
