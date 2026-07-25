# Complete Frontend and Backend Integration Plan Verdict

Review date: 2026-07-23
Review type: architecture, implementation-plan, and repository-context review
Reviewed scope: `Complete Frontend & Backend Integration Plan`
Primary authority: `CODEX_PROMPT_COMPLETE_FRONTEND_BACKEND_INTEGRATION.md`

## Verdict

**CHANGES REQUIRED — DIRECTION APPROVED; EXECUTION MAY PROCEED AFTER THE CORRECTIONS
BELOW ARE INCORPORATED**

The seven-phase sequence is broadly aligned with the master prompt and is a suitable
way to close the Data Console frontend/backend gap. The plan correctly prioritizes
route reconciliation, live adapters, backend API closure, OpenAPI regeneration,
Docker verification, live E2E, accessibility, and hardening.

The plan is not yet sufficient as the controlling execution specification. It
omits several mandatory security, ownership, API-contract, mutation-safety, and
truthful-evidence requirements. It also incorrectly introduces a blanket approval
pause that conflicts with the already approved master prompt.

This verdict approves continuous execution once the implementing agent incorporates
the required corrections into its working checklist. A separate click on
`Proceed` is not required for ordinary bounded implementation steps. The agent must
stop only for a genuine security or architecture blocker, an unavailable external
dependency without an approved sandbox alternative, or a material expansion of
scope.

This is a plan verdict, not an implementation verdict. It does not certify any
route, API, test, or integration as complete.

## Repository context verified during review

The following observations are based on the current worktree, not only the submitted
plan:

1. The backend currently mounts Data Console routers for Overview, Inventory, and
   Graph Evidence. The proposed Sources, Browser, Graph Explorer, Workspaces, Jobs,
   Imports, Exports, Scenarios, Audit, Governance, Settings, and Hardening APIs are
   not currently present as equivalent mounted live routers.
2. The current frontend route manifest still lacks required canonical routes for:
   - `/data-console/inventory/:engine/:assetId`
   - `/data-console/workspaces/:workspaceId/new`
   - `/data-console/imports/:jobId`
   - `/data-console/exports/:jobId`
   - `/data-console/scenarios/:scenarioId/preview`
   - `/data-console/governance`
   - `/data-console/hardening`
3. The current manifest contains `/data-console/workspaces/new`. It must not replace
   the canonical workspace-scoped create route. It may remain only as an additional
   workspace-creation route if its purpose is distinct and accurately named.
4. The current manifest contains scenario comparison. Comparison may remain as an
   additional feature, but it must not replace scenario preview.
5. Audit and Governance are currently represented as one navigable Audit &
   Governance entry. They require distinct canonical routes and truthful content.
6. Stage 3D status explicitly records that E2E and accessibility execution was
   paused. Therefore Graph Explorer is not yet live-integration verified.
7. Much of the Stage 3D–3H frontend work is currently untracked or modified in a
   dirty worktree. It is existing user work and must be preserved.
8. The API gap register remains incomplete relative to the master prompt. It lists
   only a subset of the operations now required for integration closure.

These findings make the proposed integration necessary, but they also prevent any
current `COMPLETE` or `PASSED` claim.

## Approved decisions

The following plan decisions are approved:

1. Use the seven proposed implementation phases in the stated dependency order.
2. Reconcile existing screens rather than build a second parallel Data Console.
3. Keep fixture adapters for isolated local frontend development.
4. Add live adapters behind the same strict domain ports.
5. Implement governed read-only browsing for source systems.
6. Use fixed, parameterized Neo4j operations with no arbitrary Cypher.
7. Store writable sandbox and internal application state in Platform MongoDB.
8. Keep live AI providers disabled and implement a deterministic generator behind a
   provider-neutral port.
9. Regenerate OpenAPI and frontend transport contracts in Docker.
10. Run live E2E and accessibility tests only after the backend operations used by
    those screens are available, with MSW disabled.
11. Capture screenshots only during hardening after functional gates are green.

## Required corrections

### P0 — Remove the contradictory approval pause

Delete the submitted plan's `User Review Required` pause. The master prompt already
authorizes the bounded implementation sequence and explicitly says not to stop
between ordinary steps.

The agent must still request direction if implementation reveals a genuine material
architecture choice, unsafe data operation, new external dependency, credential
requirement, or scope expansion not covered by the master prompt.

### P0 — Preserve and reconcile existing Stage 3D–3H work

Do not refactor the existing Stage 3D–3H implementation wholesale.

Use this rule:

- retain existing domain contracts, ports, components, fixtures, tests, and layouts
  when they meet the canonical contract;
- adapt them incrementally to generated transport types and live adapters;
- replace code only when a documented contract, security, accessibility, or
  maintainability defect requires it;
- preserve unrelated user modifications;
- do not delete fixture adapters merely because live adapters are added;
- do not treat an untracked file as disposable.

Record every material route or contract replacement in
`docs/review/status/full_stack_integration_status.md`.

### P0 — Restate the locked execution rules in the plan

The execution checklist must explicitly retain these rules:

- run frontend, backend, database, contract-generation, and Playwright commands only
  in Docker;
- do not create a Git commit;
- do not reset or discard current worktree changes;
- keep MSW development-only;
- run live integration and E2E with MSW disabled;
- never fall back from a live failure to fixture success;
- keep production bundles free of fixture/MSW runtime artifacts;
- do not capture screenshots before the hardening phase and green functional gates;
- do not expose credentials, DSNs, raw environment values, driver errors, or
  arbitrary executable queries.

### P0 — Make source ownership and write boundaries complete

Phase 2 names SQL Server/OMC and Neo4j but omits source MongoDB. State the complete
policy:

| System | Permitted behavior |
|---|---|
| SQL Server / OMC | Governed read-only metadata and record browsing |
| Source MongoDB | Governed read-only discovery and record browsing |
| Neo4j | Read-only derived graph projection |
| Platform MongoDB | Internal state, sandbox workspaces, jobs, scenarios, audit, evidence |
| Temporal | Durable orchestration only; not business-data ownership |
| Valkey | Transient cache/coordination only |

Backend code and tests must prove that source writes and graph mutations are
impossible through the Data Console.

### P0 — Define the common API and error contract

Every new endpoint must return the repository's typed success envelope with `data`,
`page`, and `meta`, including:

- schema version;
- request/correlation ID;
- generated timestamp;
- freshness;
- partial-result flag;
- bounded warnings.

The plan must also require:

- stable safe error codes;
- no raw exceptions;
- strict identifier and request validation;
- server-side bounds;
- allow-listed sort/filter fields;
- unknown-parameter rejection where practical;
- deterministic pagination;
- cancellation propagation;
- no hidden mutation retries;
- safe `404`, `403`, `409`, `422`, timeout, cancellation, and dependency-failure
  behavior.

### P0 — Reconcile the exact route surface

Phase 1 must use the complete canonical route checklist from the master prompt.
Specifically:

- add inventory asset detail;
- add the canonical workspace-scoped new-record route;
- add import and export job-detail routes, reusing the shared job page if suitable;
- add scenario preview while retaining comparison only as an optional extra;
- separate Audit from Governance;
- add Hardening.

Every dynamic route must support direct load, refresh, safe encoded identifiers,
not-found behavior, and parent navigation.

### P0 — Do not invent ambiguous CRUD operations

The phrase `CRUD for /scenarios` exceeds the exact approved Phase 6 surface. Implement
only the listed scenario methods unless a new operation is first:

1. justified by an actual screen requirement;
2. added to the API gap register;
3. defined in OpenAPI;
4. assigned ownership and authorization semantics;
5. covered by tests.

Apply the same rule to workspace record listing. The current approved workspace
surface includes record creation and single-record operations. If the workspace
detail screen needs a distinct record-list endpoint, specify and register it rather
than relying on an undocumented response shape.

### P0 — Specify mutation safety

Workspace, import, export, scenario, and approval mutations require:

- authentication and authorization checks consistent with repository policy;
- idempotency keys;
- optimistic concurrency/version checks where state is updated;
- strict Pydantic request models;
- stable identities;
- audit metadata and before/after evidence;
- safe conflict behavior;
- no blind retries after an unknown outcome;
- explicit destructive-action confirmation;
- soft archive where required by policy.

Imports may target writable sandbox workspaces only. They must enforce bounded file
size, record count, parsing, mapping, duplicate policy, validation preview, and
explicit approval.

Exports must enforce selected fields, bounds, mandatory redaction, safe filenames
and content types, expiring download metadata, and authorization at download time.

### P0 — Define scenario safety and lifecycle

The deterministic scenario generator must include an explicit seed and bounded
positive, negative, boundary, and failure cases. The flow must enforce:

`generate -> validate -> approve -> preview/import`

Approval must remain blocked until validation passes. Generated or approved data may
be imported only into a writable sandbox workspace. The generator must record
provider/model/configuration provenance without secrets, and it must never write
directly to source systems, Neo4j, or arbitrary Platform MongoDB collections.

### P1 — Validate continuously, not only after Phase 7

Keep a final full-stack gate, but add phase-level validation:

1. backend format, lint, type, and focused tests for the changed phase;
2. frontend lint, typecheck, focused component and adapter tests;
3. OpenAPI drift check when an API contract changes;
4. live smoke verification for the completed phase with MSW disabled;
5. update the route/API closure checklist with evidence.

This prevents seven phases of contract drift from accumulating before discovery.
Full Playwright E2E and accessibility may remain deferred until the backend surface
is complete, as the user requested.

### P1 — Expand backend test coverage

The final plan must explicitly cover:

- response envelope and correlation IDs;
- authentication and authorization;
- strict query/path/body validation;
- bounds and allow-listed sort/filter behavior;
- deterministic pagination;
- not-found, forbidden, conflict, validation, timeout, and partial failure;
- read-only source enforcement;
- parameterized graph queries and graph caps;
- mutation idempotency and optimistic concurrency;
- import validation;
- export redaction and expiry;
- deterministic scenario generation and approval gates;
- audit creation/retrieval;
- absence of secrets, DSNs, and raw driver errors.

### P1 — Expand frontend integration coverage

For every port, test both fixture and live adapters. Normal mode must select the live
adapter; only explicit development fixture mode may select the fixture adapter.

Required screen states include loading, empty, partial, forbidden, timeout,
cancelled, validation, not-found, conflict where relevant, and unexpected failure.
Live failures must remain visible as failures.

Full-stack Playwright must direct-load every canonical route and cover list/detail,
pagination, graph selection, workspace mutations, import/export flows, scenario
generation/validation/approval/preview, downloads, deep-link restoration, mobile
navigation, keyboard use, and accessibility with MSW disabled.

### P1 — Generate and consume contracts safely

Generate OpenAPI and frontend transport types in Docker after each coherent backend
API group or at minimum before its live adapter is finalized. Do not edit generated
types manually.

Domain ports should remain independent of transport types. Live adapters are
responsible for validated mapping between generated transport schemas and domain
contracts.

### P1 — Keep governance, settings, and hardening truthful

- Audit must be bounded and read-only.
- Governance must reflect actual ownership and capability rules, not fixture claims.
- Settings must expose only safe, non-secret feature/environment information.
- Hardening must show only recorded evidence.
- Load, security, failover, reliability, E2E, accessibility, and screenshots must
  remain `PENDING` or `DEFERRED` until their exact gates run successfully.

## Required execution checklist

Before implementation, create or update:

- `docs/review/status/full_stack_integration_status.md`;
- the Markdown API gap register;
- the JSON API gap register;
- a route/API closure table with frontend route, port, live adapter, backend
  operation, persistence owner, tests, and current capability.

During implementation:

1. inspect the actual container names and existing scripts;
2. extend existing backend domain/application/infrastructure boundaries rather than
   placing source-driver logic in route handlers;
3. reconcile existing frontend screens incrementally;
4. validate each phase in Docker;
5. record only observed results.

At final integration:

1. start the required infrastructure and frontend/backend containers;
2. load deterministic, idempotent, sandbox-only seed data;
3. disable MSW;
4. regenerate OpenAPI and frontend contracts;
5. pass all backend gates;
6. pass all frontend unit/build/bundle gates;
7. pass live E2E and accessibility across every canonical route;
8. run production mock-exclusion and negative mock-build checks;
9. run bounded security and reliability checks;
10. capture screenshots only now, in the hardening step;
11. update README, status, gap registers, capability matrices, and evidence;
12. confirm no Git commit was created.

## Decision on the submitted open question

**Do not completely refactor existing Stage 3D–3H code.**

The approved approach is contract-led incremental reconciliation. Existing code may
be modified wherever necessary to:

- match canonical routes;
- use the approved API envelope;
- connect a live adapter;
- remove unsafe behavior;
- correct accessibility or test defects;
- replace duplicated manual transport types with generated schemas.

Code that already satisfies those requirements should remain intact. This protects
the user's current work, reduces regression risk, and keeps fixture-mode development
available while live integration is added.

## Approval boundary

After incorporating this verdict into the working checklist, the agent is approved
to proceed continuously through the implementation phases without another general
approval request.

This approval does not authorize:

- source-system writes;
- Neo4j mutations;
- enabling a live AI provider;
- adding an unreviewed external dependency;
- exposing secrets or connection strings;
- destructive worktree operations;
- creating a Git commit.

## Current truth labels

```text
PLAN DIRECTION: APPROVED WITH REQUIRED CHANGES
FRONTEND SCREENS: PARTIAL / NOT LIVE-INTEGRATION VERIFIED
BACKEND API INTEGRATION: INCOMPLETE
LIVE FRONTEND/BACKEND E2E: NOT RUN
ACCESSIBILITY: NOT FULLY VERIFIED
PRODUCTION MOCK EXCLUSION: PREVIOUS CLAIM REQUIRES FINAL REGRESSION CHECK
SCREENSHOTS: DEFERRED UNTIL HARDENING
GIT COMMIT: NOT CREATED BY THIS REVIEW
```

No runtime tests were run for this plan-only review. No screenshots were captured,
and no Git commit was created.
