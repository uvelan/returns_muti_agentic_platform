# Codex Continuation Context — Stage 3 Data Console Customer Graph Evidence Screens

## How to use this document

Give this entire document to Codex as the execution context.

Codex must begin by inspecting the existing repository and current worktree. It
must continue from the exact bounded step below instead of redesigning or
restarting the project.

---

# 1. Repository

```text
Repository:
https://github.com/uvelan/returns_muti_agentic_platform.git

Expected local repository:
returns_agentic_platform

Backend:
returns_agentic_platform/backend

Frontend:
returns_agentic_platform/frontend
```

Branch policy:

```text
Work directly against main.
Do not create a pull request.
Do not rewrite history.
Do not reset or discard existing user changes.
Inspect the worktree before editing.
```

The repository is platform independent.

---

# 2. Current Bounded Step

Start from:

```text
Stage 3 — Data Readiness and Graph Validation
Frontend Step — Data Console Customer Graph Synchronization and Evidence Screens
```

The backend Graph Evidence APIs are already implemented, tested, and live
sandbox-validated.

The current task is to integrate, correct, validate, and finish the frontend
screen slice in the real repository.

Do not move to SalesOrder graph support, Temporal workflows, Package,
PPLTracking, or the customer-facing return workflow until this frontend slice
passes all acceptance gates.

---

# 3. Locked Project Execution Order

The required execution order is:

```text
1. Complete the basic end-to-end return workflow
2. Build the Data Management Console
3. Begin the hardening phase
```

Within the current Stage 3 data-readiness sequence:

```text
Customer source/mapping/normalization
→ Neo4j command materialization
→ Neo4j write
→ deterministic Neo4j read-back
→ second-run idempotency validation
→ Platform MongoDB evidence persistence
→ read-only Graph Evidence APIs
→ Data Console Graph Evidence screens
```

The project depends heavily on Neo4j graph services. Data readiness and
evidence are higher priority than cosmetic UI polish.

---

# 4. Locked Data Ownership

These ownership rules must not be changed:

```text
Platform MongoDB
  Authoritative for internal platform state:
  sessions, audits, configuration, outbox, evidence.

Source MongoDB
  Read-only discovery data:
  Orders, Customers, SKUs.
  No workflow state.

SQL Server / OMC
  Authoritative business facts:
  returns, RMA, fulfillment, tracking.
  Read-only from the platform.

Neo4j
  Derived and rebuildable graph projection.
  Never the authoritative business-state owner.

Temporal
  Workflow execution and timers only.
  Never the business-state owner.

Valkey
  Transient coordination, rate limiting, and event delivery only.
```

The Data Management Console is a developer/admin evidence tool. It is not the
primary customer return demo.

---

# 5. Current Validated Backend Status

The following Customer graph foundation is complete:

```text
Customer canonical models                         COMPLETE
Customer mapping contracts                        COMPLETE
Customer mapping compiler                         COMPLETE
Customer normalization                            COMPLETE
Customer graph materialization                     COMPLETE
Customer Neo4j command builder                     COMPLETE
Customer Neo4j writer                              CONTRACT_TESTED
Customer graph read-back contracts                 CONTRACT_TESTED
Second-run idempotency contracts                    CONTRACT_TESTED
Customer graph sandbox execution                   SANDBOX_VALIDATED
Live Customer Neo4j graph write                    SANDBOX_VALIDATED
Live Customer Neo4j graph read-back                SANDBOX_VALIDATED
Platform MongoDB graph-evidence persistence        SANDBOX_VALIDATED
Graph Validation API                               SANDBOX_VALIDATED
Graph Inspection APIs                              SANDBOX_VALIDATED
```

## Customer graph sandbox evidence

```text
Process exit code:
0

Status:
SANDBOX_VALIDATED

Report digest:
75b63cf87a1742e93dd05eb2542d6bfe17f3b345ffe3542d73fac32d664b33c8

Platform evidence document ID:
CUSTOMER_GRAPH_SANDBOX:d084d10c-5bdf-4002-befb-8ccb9948f9e7

Sync run ID:
d084d10c-5bdf-4002-befb-8ccb9948f9e7

Platform evidence document digest:
6ce23e2568171b3f53827dfb8b822f4c4cd2cec60080a6c959326136bdb81f5b

Platform evidence persistence status:
CREATED
```

## Live Graph Evidence API validation

```text
Status:
SANDBOX_VALIDATED

Process exit code:
0

Routes validated:
6

Evidence:
backend/docs/evidence/graph_evidence_api/validation_summary.json
```

The live validator confirmed:

```text
HTTP 200 for all six routes
standard response-envelope integrity
request/correlation IDs
document identity
sync-run identity
report digest
Platform document digest
cross-route consistency
idempotency evidence
admin-only full evidence access
known secret/configuration leakage scan
```

---

# 6. Validated Backend API Routes

The frontend must use only these read-only routes:

```text
GET /data-console/v1/graph-evidence
GET /data-console/v1/graph-evidence/validation/latest
GET /data-console/v1/graph-evidence/documents/{document_id}
GET /data-console/v1/graph-evidence/documents/{document_id}/full
GET /data-console/v1/graph-evidence/sync-runs/{sync_run_id}
GET /data-console/v1/graph-evidence/reports/{report_digest}
```

Important constraints:

```text
No POST/PUT/PATCH/DELETE route exists for graph evidence.
No arbitrary MongoDB filter is accepted.
No arbitrary Cypher is accepted.
Listing uses bounded seek pagination.
The full evidence route requires console_admin.
Summary routes allow console_viewer or console_admin.
```

Do not invent a graph synchronization mutation API.

The frontend should describe the page as synchronization status/evidence, not
as an operator-triggered synchronization page.

---

# 7. Canonical Environment Configuration

The canonical local configuration file is:

```text
<repository-root>/.env
```

Do not create `backend/.env`.

The actual environment vocabulary is:

```text
development
test
staging
production
```

The correct local value is:

```dotenv
PLATFORM_ENVIRONMENT=development
```

Do not change it to `dev`.

Relevant Graph Evidence settings:

```dotenv
PLATFORM_MONGO_DATABASE=return_platform
PLATFORM_GRAPH_EVIDENCE_COLLECTION=graph_evidence_runs
PLATFORM_GRAPH_EVIDENCE_QUERY_TIMEOUT_SECONDS=5.0
```

The frontend must use relative API paths and the existing Vite proxy. Do not
hard-code `http://localhost:8000` in production frontend source.

---

# 8. Frontend Foundation to Preserve

Inspect the repository before editing, but the frontend is expected to use:

```text
React
TypeScript strict mode
Vite
Wouter
TanStack Query
Zod
Tailwind CSS
Lucide React
Vitest
Testing Library
```

Reuse the existing:

```text
App routing structure
Shell/navigation structure
QueryClient provider
API error conventions
Tailwind design tokens
test setup
Vite proxy
npm scripts
```

Do not introduce a second router, a second query client, a new design system,
or new packages unless the current repository proves one is missing and the
change is necessary.

No package change is expected for this step.

---

# 9. Intended Frontend Files

The implementation should add or reconcile these files:

```text
frontend/src/contracts/graphEvidence.ts
frontend/src/api/graphEvidence.ts
frontend/src/api/graphEvidenceQueries.ts

frontend/src/features/data-console/components/graph-evidence/GraphEvidenceStatusCard.tsx
frontend/src/features/data-console/components/graph-evidence/GraphEvidenceTable.tsx
frontend/src/features/data-console/components/graph-evidence/GraphEvidenceInspector.tsx

frontend/src/features/data-console/pages/GraphEvidencePage.tsx

frontend/src/api/graphEvidence.test.ts
frontend/src/features/data-console/pages/GraphEvidencePage.test.tsx
```

Expected integration changes:

```text
frontend/src/App.tsx
frontend/src/components/Shell.tsx
README.md
```

If the repository uses different equivalent paths, follow the real repository
structure while preserving the bounded capability.

If any generated files are already present, review and correct them instead of
duplicating them.

---

# 10. Required Screen Behavior

Implement a premium, responsive, read-only Data Console screen at:

```text
/data-console/graph-evidence
```

Required capabilities:

```text
Latest validation status card
SANDBOX_VALIDATED status
Execution timestamp
Expected Customer count
Expected CustomerAccount count
Expected HAS_ACCOUNT relationship count
Immutable sync-run ID
Source-document ID
Manual refresh

Newest-first evidence history
Bounded seek-pagination controls
Previous and next navigation
No offset pagination

Exact lookup by:
  document ID
  sync-run ID
  report digest

Evidence summary inspection
All immutable digests
Source hash
Configuration digest
Execution-plan digest
Command-batch digest

Admin-only full evidence inspection
Validated full report payload
Safe console_viewer 403 state
Summary must remain visible when full access is forbidden

Loading state
Empty state
Backend unavailable state
Malformed-contract state
Lookup failure state
Query timeout state
Manual retry
Request/correlation ID display
Responsive layout
Keyboard-accessible controls
Semantic labels and alerts
```

Do not show raw source Customer data.

Do not show passwords, DSNs, tokens, secret settings, or arbitrary database
documents.

---

# 11. Frontend Contract Requirements

Use Zod at the network boundary.

The UI must fail closed when a successful response violates the expected
contract.

Required fixed literals include:

```text
evidence_type:
CUSTOMER_GRAPH_SANDBOX_RUN

evidence_classification:
SANDBOX_VALIDATED
```

Required identity validation includes:

```text
document ID format
UUID sync-run ID
64-character lowercase SHA-256 digests
timezone-aware timestamps
nonnegative expected counts
boolean idempotent value
bounded page size
optional next seek cursor
request/correlation ID
```

Do not use:

```text
explicit any
unsafe type assertions for API payloads
unchecked JSON
silent response fallback
automatic hidden retries
caller-controlled MongoDB filters
caller-controlled sort definitions
```

TanStack Query retries should remain disabled for this developer evidence page
unless the repository has an explicit approved retry policy.

The browser-side request timeout must be bounded.

---

# 12. Required UX Decision

Do not add a button named:

```text
Run synchronization
Sync now
Rebuild graph
Write to Neo4j
```

The backend currently exposes only validated read APIs.

The page should explain that it displays immutable synchronization evidence.

A future governed write endpoint must be separately designed, implemented,
authorized, tested, and sandbox-validated before any mutation control appears
in the UI.

---

# 13. Tests Required

At minimum, frontend tests must cover:

```text
API list URL and bounded page-size query
seek cursor URL construction
exact document lookup URL
exact sync-run lookup URL
exact report-digest lookup URL
successful strict Zod parsing
contract rejection for unsupported evidence classification
safe non-JSON response handling
safe malformed-error response handling
client timeout mapping
viewer 403 mapping for full evidence

latest validation rendering
history list rendering
request ID rendering
empty state
hard API error state
exact lookup flow
summary inspection
admin full evidence rendering
viewer-safe full-evidence denial
pagination next/previous behavior
manual refresh
```

Tests must not depend on the developer's real `.env` or live infrastructure.

---

# 14. Acceptance Commands

First inspect the actual package scripts:

```bash
cd frontend
cat package.json
```

Then run the repository-equivalent commands.

Expected focused gate:

```bash
cd frontend
nvm use
npm ci

npm run lint
npm run typecheck

npm run test -- \
  src/api/graphEvidence.test.ts \
  src/features/data-console/pages/GraphEvidencePage.test.tsx

npm run build
```

Expected complete gate:

```bash
npm run check
```

If `npm run check` does not include all tests:

```bash
npm run test
```

Do not weaken ESLint, TypeScript strictness, Zod validation, or tests to make
the gate pass.

Do not add suppressions such as:

```text
eslint-disable
@ts-ignore
@ts-expect-error
as any
```

unless there is a proven unavoidable external typing defect, documented with
evidence. None is expected in this step.

---

# 15. Live Browser Integration Validation

Backend command:

```bash
cd backend

poetry run uvicorn return_platform.asgi:app \
  --host 127.0.0.1 \
  --port 8000
```

Do not use `--reload` for captured validation evidence.

Frontend command:

```bash
cd frontend

npm run dev
```

Open:

```text
http://localhost:5173/data-console/graph-evidence
```

Validate and record:

```text
latest validation card
history list
request ID
sync-run lookup
report-digest lookup
document-ID lookup
summary inspector
admin full evidence
safe viewer denial
manual refresh
pagination with multiple records when available
backend unavailable state
empty collection state when safely isolated
responsive desktop layout
responsive narrow/mobile layout
```

Use the existing validated retained evidence:

```text
Document ID:
CUSTOMER_GRAPH_SANDBOX:d084d10c-5bdf-4002-befb-8ccb9948f9e7

Sync run ID:
d084d10c-5bdf-4002-befb-8ccb9948f9e7

Report digest:
75b63cf87a1742e93dd05eb2542d6bfe17f3b345ffe3542d73fac32d664b33c8
```

Capture:

```text
environment
exact commands
exit codes
test counts
lint output
typecheck output
build output
browser route
API requests
correlation IDs
screenshots
known limitations
reproduction steps
```

---

# 16. README Requirement

Every implementation step must update `README.md` with:

```text
what was implemented
files added
files changed
setup/configuration
commands
verification results
current phase status
known limitations
next bounded step
enough reconstruction guidance for another engineer
```

Add a section similar to:

```markdown
### Data Console Customer graph synchronization and evidence screens

The Data Console provides a read-only Customer graph synchronization evidence
experience at `/data-console/graph-evidence`.

Capabilities include latest validation status, expected graph counts,
newest-first immutable evidence history, bounded seek pagination, exact lookup
by document ID/sync-run ID/report digest, summary inspection, admin-only full
evidence inspection, safe viewer denial, strict runtime response validation,
and correlation ID display.

No graph or evidence mutation is exposed.
```

Do not claim frontend gates or live browser validation passed until exact
output is captured.

---

# 17. Evidence Classification

Before repository execution:

```text
Frontend source implementation:       IMPLEMENTED
Frontend tests:                       IMPLEMENTED
Frontend lint/typecheck/build:        PENDING
Live browser/backend integration:     NOT VALIDATED
```

After focused and complete gates pass:

```text
Frontend source implementation:       CONTRACT_TESTED
Frontend tests:                       CONTRACT_TESTED
Frontend lint/typecheck/build:        PASS
Live browser/backend integration:     NOT VALIDATED
```

After live browser integration passes:

```text
Data Console Graph Evidence screens:  SANDBOX_VALIDATED
```

Never use `PRODUCTION_VALIDATED` for local or sandbox evidence.

---

# 18. Existing Generated Artifact

A prior implementation artifact was produced with this name:

```text
STAGE_3_DATA_CONSOLE_CUSTOMER_GRAPH_EVIDENCE_SCREENS.md
STAGE_3_DATA_CONSOLE_CUSTOMER_GRAPH_EVIDENCE_SCREENS.zip
```

Its SHA-256 was:

```text
215de2c0706b91fb95f7498b92ede011884c6e6d4859c3fe583473af8ef05beb
```

Treat it as an implementation starting point, not unquestionable truth.

Codex must reconcile it against the actual repository:

```text
actual API response shapes
actual frontend directory structure
actual App.tsx routing
actual Shell.tsx navigation
actual Tailwind configuration
actual npm scripts
actual ESLint rules
actual TypeScript strict configuration
actual test setup
```

Fix mismatches in the implementation. Do not force the repository to match an
artifact assumption.

---

# 19. Known Historical Corrections

Preserve these corrections:

```text
Canonical local dotenv file is repository-root .env.
Do not create backend/.env.

PLATFORM_ENVIRONMENT must be development locally.
The value dev is invalid for the actual Settings contract.

Graph Evidence central settings belong in the backend Settings model:
  mongo_database
  graph_evidence_collection
  graph_evidence_query_timeout_seconds

MongoDB projected literal strings must be runtime-validated before strict
Literal model construction.

ResponseMeta.warnings is an immutable tuple in the backend contract.

Graph evidence APIs are read-only.
```

---

# 20. Engineering Rules

Mandatory:

```text
Production-grade code only
No TODOs
No stubs
No fake success
No hidden retry
No deprecated APIs
No type-check suppression
No lint suppression
No weakened tests
No weakened strictness
No secrets in source, logs, screenshots, or README
No arbitrary database query surface
No destructive worktree operations
```

Configuration should be used only for values expected to vary across
environments or deployments.

Stable domain rules, authorization boundaries, evidence contracts, query
shapes, transaction rules, and workflow behavior remain version-controlled
code.

---

# 21. Codex Execution Procedure

Execute in this order:

```text
1. Inspect git status and repository structure.
2. Inspect frontend package.json, tsconfig, ESLint, Vite, router, shell, and tests.
3. Inspect the live backend Graph Evidence response models and routes.
4. Inspect whether any Stage 3 frontend files already exist.
5. Create a short implementation reconciliation note.
6. Integrate or correct the frontend files.
7. Add route and navigation integration.
8. Add focused tests.
9. Run formatter/lint/typecheck/focused tests/build.
10. Fix all failures without weakening gates.
11. Run complete frontend checks.
12. Start backend and frontend.
13. Validate the live page against retained evidence.
14. Capture exact evidence.
15. Update README.
16. Report files changed, commands, exit codes, results, limitations, and next step.
```

Do not stop after generating code. Continue until the bounded step passes all
available gates or a real external blocker is proven.

---

# 22. Required Final Codex Report

The final response must include:

```text
Stage name
Implementation verdict
Files added
Files changed
Design reconciliation decisions
Exact commands executed
Exit codes
Lint result
Typecheck result
Focused test result and count
Complete test result and count
Build result
Live browser validation result
Captured evidence paths
README update
Known limitations
Next bounded step
```

If a gate fails, provide the exact failure and fix it before concluding.

---

# 23. Stop Boundary

Do not proceed beyond this bounded step until:

```text
focused frontend lint passes
strict TypeScript passes
focused frontend tests pass
complete frontend tests pass
Vite production build passes
live page reads the validated backend
admin full evidence works
viewer denial is safe
request IDs are displayed
README contains exact evidence
```

After this step is accepted, stop and ask for the next bounded instruction.

Do not automatically begin:

```text
SalesOrder graph synchronization
OrderLine graph support
Product graph support
Warehouse graph support
Shipment graph support
Temporal workflows
Package
PPLTracking
customer-facing return workflow
hardening phase
```
