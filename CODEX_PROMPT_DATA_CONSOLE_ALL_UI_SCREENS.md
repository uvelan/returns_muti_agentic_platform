# Complete Codex Prompt — Build Every Data Console UI Screen

Use this document as the complete execution prompt for the current repository.

---

## Role and objective

You are Codex working directly in the existing repository:

```text
K:\Projects\FEG\Ret\returns_muti_agentic_platform
```

Complete the entire Data Console frontend experience before returning to backend
implementation. Build every screen, route, reusable component, frontend contract,
query/mutation adapter, fixture, and test described below.

This is a frontend-first implementation. Do not implement or modify backend behavior
in this workstream. Existing live backend APIs may be consumed. APIs that do not yet
exist must be represented by typed frontend ports and deterministic development/test
fixtures so the full UI can be completed and reviewed now, then connected to backend
services later without redesigning the screens.

Do not stop after creating a shell, wireframe, or one example page. Continue until
all routes, interaction states, responsive layouts, accessibility behavior, tests,
and Docker frontend gates pass.

---

## Mandatory working rules

```text
Use Docker for all frontend commands and validation.
Do not create a Git commit.
Do not create or switch branches.
Do not reset, discard, overwrite, or reformat unrelated user changes.
Do not modify backend source files in this frontend workstream.
Do not modify the repository-root .env unless explicitly requested.
Do not create frontend secrets or expose backend infrastructure addresses.
Use relative /data-console/v1/... API paths through the existing frontend proxy.
Keep screenshots deferred until the hardening page.
```

Inspect the current worktree and these files before editing:

```text
CODEX_CONTINUATION_CONTEXT_DATA_CONSOLE_MANAGEMENT.md
README.md
frontend/package.json
frontend/src/App.tsx
frontend/src/components/Shell.tsx
frontend/src/api/client.ts
frontend/src/contracts/api.ts
frontend/src/features/data-console/pages/OverviewPage.tsx
frontend/src/features/data-console/pages/InventoryPage.tsx
frontend/src/features/data-console/pages/GraphEvidencePage.tsx
frontend/src/features/data-console/components/
```

Preserve and extend existing conventions:

- React 19.
- TypeScript in strict mode.
- Vite.
- Tailwind CSS.
- Wouter routing.
- TanStack Query for server state.
- TanStack Table for data grids where appropriate.
- Lucide icons.
- Vitest and Testing Library.
- Existing API response envelope and `APIError` behavior.

Do not introduce another component framework or state-management library unless the
existing stack cannot satisfy a concrete requirement.

---

## Product definition

The Data Console is a developer/admin data-management experience that allows users
to understand, browse, validate, import, export, and safely manage permitted data
across:

```text
SQL Server
MongoDB
Neo4j
Platform workflow/session evidence
Sandbox scenario workspaces
```

It is not the customer-facing Return application.

The experience must make the distinction between source, platform, derived graph,
and sandbox data obvious at every mutation point.

---

## Locked data ownership and safety rules

These rules must be visible in UI language and enforced in frontend behavior:

| Data class | Ownership | UI capability |
|---|---|---|
| SQL Server / OMC | Authoritative source facts | Read-only unless explicitly declared writable by a future backend capability |
| Governed source MongoDB | Source discovery data | Read-only |
| Platform MongoDB | Internal platform state/evidence | Governed operations only |
| Neo4j | Derived and rebuildable | Read-only by default; mutations only in an explicitly writable sandbox graph |
| Scenario workspace | Synthetic sandbox data | Add, edit, delete, import, and AI generation allowed |

Never expose or accept arbitrary SQL, arbitrary MongoDB filters, or arbitrary Cypher.

All unavailable mutations must be represented as disabled controls with truthful
explanations. Do not simulate a successful production write. Fixture-backed sandbox
mutations may operate locally in frontend state and must be labeled clearly as
`SANDBOX PREVIEW` or `LOCAL FIXTURE`.

Deletion behavior:

- Require a confirmation dialog.
- Display the asset, record identity, and impact.
- Prefer soft-delete language when supported.
- Require an additional explicit confirmation for bulk or permanent deletion.
- Never implement destructive production behavior from the browser alone.

AI behavior:

- AI generates a preview package only.
- Generated data must pass validation before the approval action is enabled.
- AI never writes directly to a source, platform collection, or graph.
- Display model/provider/configuration provenance when available.
- Provide deterministic fixture generation when no live provider exists.

---

## Information architecture and routes

Implement all routes below. Use a responsive console shell with desktop sidebar and
compact mobile navigation. Navigation groups should be understandable without icons.

### 1. Platform overview

```text
/overview
```

Retain and improve the existing infrastructure overview:

- Dependency health cards.
- Healthy, degraded, unavailable, and partial states.
- Inventory totals summary.
- Recent import/export/scenario jobs using typed fixture data until APIs exist.
- Quick links to Inventory, Data Browser, Graph Explorer, Imports, Exports, and AI
  Scenario Studio.
- Request ID, freshness, and last-updated information.

### 2. Data sources

```text
/data-console/sources
/data-console/sources/:sourceId
```

Source list:

- Engine type, environment, ownership, health, access mode, and last inventory time.
- Search and filters for engine, ownership, status, and read/write capability.
- Cards/table responsive presentation.
- Empty, loading, partial, and hard-error states.

Source detail:

- Connection identity without credentials or DSNs.
- Ownership and governance classification.
- Read/write capability banner.
- Inventory totals.
- Last successful metadata refresh.
- Safe dependency warnings.
- Tabs for Assets, Governance, Activity, and Configuration summary.
- Never display secret values.

### 3. Unified inventory

```text
/data-console/inventory
/data-console/inventory/:engine/:assetId
```

Extend the existing Inventory page:

- SQL Server schemas, tables, views, columns, approximate row counts, and type detail.
- MongoDB collections, approximate document counts, indexes, uniqueness, TTL, sparse,
  hidden, partial-index, and ordered index-key detail.
- Neo4j labels, relationship types, constraints/index placeholders, and counts when
  available.
- Search across assets.
- Filters by engine, asset type, ownership, access mode, and status.
- Sort by name, type, count, or last observed time.
- Compact/list and detailed/table view modes.
- Selection that links to asset detail and the Data Browser.
- Partial engine failures must preserve healthy engine results.

Asset detail:

- Breadcrumbs and stable identity.
- Ownership/access banner.
- Schema/field/property detail.
- Keys, indexes, constraints, and relationships.
- Approximate record count and observation timestamp.
- Tabs for Schema, Preview, Relationships, Governance, and Activity.
- Preview tab must route through the typed data-browser adapter.

### 4. Governed data browser

```text
/data-console/browser
/data-console/browser/:engine/:assetId
/data-console/browser/:engine/:assetId/records/:recordId
```

Browser landing page:

- Asset selector using only inventoried assets.
- Recent assets.
- Read-only/writable badges.
- Clear ownership explanations.

Asset browser:

- Bounded pagination.
- Page-size choices limited to safe values.
- Server-owned sort/filter controls only; no arbitrary query input.
- Column visibility and density controls.
- SQL row table.
- MongoDB document table with expandable structured JSON.
- Neo4j node/relationship result table linked to Graph Explorer.
- Selected-record inspector drawer.
- Copy permitted values without copying hidden/redacted fields.
- Export selection action.
- Add/Edit/Delete actions shown only for writable sandbox capabilities.
- Loading skeleton, empty result, partial response, validation error, forbidden,
  timeout, cancelled request, and hard-error states.

Record detail:

- Stable record identity.
- Structured field/property inspector.
- Type-aware value display.
- Null, missing, redacted, binary, nested object, array, date, numeric, and Boolean
  rendering.
- Related records and graph relationships when available.
- Audit/activity timeline fixture adapter.
- Edit/delete actions only when capability permits.

### 5. Writable sandbox record editor

```text
/data-console/workspaces
/data-console/workspaces/:workspaceId
/data-console/workspaces/:workspaceId/new
/data-console/workspaces/:workspaceId/records/:recordId/edit
```

Workspace list/detail:

- Sandbox classification displayed prominently.
- Record and entity totals.
- Scenario source and creation time.
- Validation state.
- Local fixture versus backend-persisted capability label.
- Create, duplicate, archive, and delete controls with truthful capability states.

Record create/edit:

- Schema-driven form where contracts exist.
- JSON editor fallback for sandbox data only.
- Field-level validation.
- Cross-record validation summary.
- Unsaved-changes guard.
- Preview changes before applying.
- Before/after diff for edits.
- Save action uses a typed mutation port; local fixture adapter is allowed until the
  backend exists.

Delete dialogs:

- Single-record delete confirmation.
- Bulk-selection delete confirmation.
- Impact summary and relationship warnings.
- Require typing a confirmation phrase for bulk/permanent fixture deletion.

### 6. Graph Explorer

```text
/data-console/graph
/data-console/graph/nodes/:nodeId
/data-console/graph/relationships/:relationshipId
```

Graph canvas:

- Interactive node/edge visualization built with repo-native React/SVG/canvas code or
  an already-installed dependency. Do not add a heavy graph library without need.
- Zoom, pan, fit, reset, and focus controls.
- Node label and relationship-type filters.
- Search by governed exact identifier.
- Expand one bounded neighborhood at a time.
- Maximum depth and result caps visibly enforced.
- Legend and accessible non-canvas/table alternative.
- Empty, truncated, partial, and error states.

Inspectors:

- Node identity, labels, properties, ownership, and evidence.
- Relationship identity, type, endpoints, properties, and evidence.
- Link to source inventory asset and record detail when available.
- Add/edit/delete controls only for a writable sandbox graph.
- No free-form Cypher input.

### 7. Existing graph evidence

```text
/data-console/graph-evidence
```

Preserve existing tested behavior and integrate it into the new navigation:

- Latest validation status.
- Immutable evidence history.
- Exact document, sync-run, and report lookup.
- Summary inspection.
- Admin full-evidence inspection.
- Seek pagination.
- Request IDs, warnings, and safe authorization errors.

Do not add graph synchronization mutation controls to this page.

### 8. Imports

```text
/data-console/imports
/data-console/imports/new
/data-console/imports/:jobId
```

Import center:

- Job history with status, target, format, counts, owner, and timestamps.
- Filters and search.
- Retry action only as a disabled/future capability unless backed by a typed adapter.

Import wizard:

1. Choose writable sandbox target.
2. Choose CSV, JSON, or JSONL.
3. File selection/drop zone.
4. Bounded file size and row/document limits.
5. Parse preview.
6. Map source fields to target fields.
7. Choose duplicate policy: reject, skip, or replace in sandbox only.
8. Validate.
9. Review errors/warnings.
10. Explicit approval.
11. Submit through typed import adapter or label as local fixture preview.

Import job detail:

- Progress and lifecycle timeline.
- Parsed, valid, rejected, inserted, skipped, and replaced counts.
- Bounded validation error table.
- Downloadable error-report control through a typed adapter.
- Request/job IDs and provenance.

### 9. Exports

```text
/data-console/exports
/data-console/exports/new
/data-console/exports/:jobId
```

Export center and wizard:

- Choose permitted source/asset.
- Export selected records or a bounded governed dataset.
- CSV, JSON, or JSONL.
- Column/field selection.
- Redaction summary.
- Estimated record count and size.
- Explicit confirmation.
- Job status and expiry.
- Download control only when a generated fixture/blob adapter provides a file.
- Never allow exporting secrets or hidden/redacted fields.

### 10. AI Scenario Studio

```text
/data-console/scenarios
/data-console/scenarios/new
/data-console/scenarios/:scenarioId
/data-console/scenarios/:scenarioId/preview
```

Scenario library:

- Positive, negative, boundary, and failure scenario templates.
- Search/filter by entity, workflow stage, classification, and validation status.
- Duplicate and archive actions in local fixture state.

Scenario builder:

- Natural-language scenario description.
- Template selection.
- Entity selection: Customer, CustomerAccount, SalesOrder, OrderLine, Product, Return,
  ReturnItem, Shipment, TrackingEvent, Warehouse, Bay, session/evidence entities.
- Record-count controls with hard safe maximums.
- Seed control for deterministic generation.
- Positive/negative/boundary/failure classification.
- Requested consistency rules.
- Provider/model/configuration display.
- `Generate preview` action through a provider-neutral typed port.
- Deterministic local fixture generator must make the screen fully functional when no
  AI provider is configured.

Scenario preview:

- Generated entity totals.
- Tables/documents/graph tabs.
- Relationship graph preview.
- Canonical validation report.
- Cross-entity consistency report.
- Errors and warnings with exact entity references.
- Regenerate, edit, validate again, save draft, and approve-for-import controls.
- Approval disabled until validation passes.
- Approved data still imports only to a writable sandbox workspace.
- Show prompt/configuration/model/seed provenance without secrets.

Scenario detail:

- Description, classification, seed, configuration, and timestamps.
- Generated data summary.
- Validation evidence.
- Import history.
- Audit timeline.

### 11. Jobs and activity

```text
/data-console/jobs
/data-console/jobs/:jobId
```

- Unified import, export, generation, validation, and future synchronization jobs.
- Status filters and search.
- Progress, safe error, request ID, owner, timestamps, and target.
- Job detail timeline and evidence.
- Cancel/retry controls enabled only when a typed capability explicitly permits them.

### 12. Audit and governance

```text
/data-console/audit
/data-console/governance
```

Audit:

- Read-only activity table.
- Actor, operation, entity, outcome, time, and correlation ID.
- Bounded filters with no arbitrary query syntax.
- Detail drawer with before/after summaries and evidence references.

Governance:

- Asset ownership matrix.
- Source/platform/derived/sandbox classification.
- Read/write/import/export/AI-generation capabilities.
- Sampling policy and safe limits.
- Catalog version/digest and last load status.
- Production catalog truth boundary.
- No catalog mutation action.

### 13. Settings and capability reference

```text
/data-console/settings
```

- Environment vocabulary and current non-secret environment.
- Feature/capability matrix.
- API base behavior through relative paths.
- UI preferences such as density and theme-ready tokens.
- No credentials, raw environment values, DSNs, or secret editing.

### 14. Hardening evidence page

```text
/data-console/hardening
```

Build the page structure now, but do not capture screenshots yet.

- Frontend lint/type/test/build status cards fed by typed fixture evidence.
- Responsive/accessibility checklist.
- API proxy validation summary.
- Known limitations.
- Screenshot checklist with `DEFERRED` status.
- Security/reliability/load evidence placeholders that never claim validation which
  has not occurred.

---

## Shared component system

Create reusable components instead of repeating large Tailwind strings across pages.
At minimum provide:

```text
PageHeader
Breadcrumbs
StatusBadge
CapabilityBadge
OwnershipBadge
EngineBadge
MetricCard
EmptyState
LoadingState / Skeleton
ErrorState
PartialWarningBanner
RequestMetadata
SearchInput
FilterBar
DataTable
PaginationControls
DetailDrawer
Tabs
JsonInspector
PropertyList
SchemaTable
ConfirmationDialog
UnsavedChangesDialog
FileDropZone
StepIndicator
ValidationSummary
ValidationIssueTable
JobStatusTimeline
DiffViewer
GraphCanvas and accessible GraphTable
Toast/inline operation feedback
```

Prefer small composable components with typed props. Avoid a monolithic page file.

---

## Frontend data architecture

Create a clean boundary between screens and data providers.

Recommended structure:

```text
frontend/src/
  api/
    inventoryQueries.ts
    sourceQueries.ts
    browserQueries.ts
    graphQueries.ts
    importQueries.ts
    exportQueries.ts
    scenarioQueries.ts
    jobQueries.ts
    auditQueries.ts
  contracts/
    inventory.ts
    sources.ts
    browser.ts
    graph.ts
    imports.ts
    exports.ts
    scenarios.ts
    jobs.ts
    audit.ts
    capabilities.ts
  fixtures/
    inventory.ts
    sources.ts
    browser.ts
    graph.ts
    imports.ts
    exports.ts
    scenarios.ts
    jobs.ts
    audit.ts
  features/data-console/
    components/
    pages/
```

Rules:

- Retain the complete API envelope when calling live endpoints.
- Keep query keys centralized and deterministic.
- Abort requests through TanStack Query signals.
- Never silently convert an API failure into fixture success.
- Choose fixture adapters explicitly through a development-only capability boundary.
- Label fixture-backed screens visibly.
- Validate unknown external payloads at the boundary where practical.
- Do not use `any`.
- Do not weaken TypeScript strictness.
- Do not use non-null assertions to bypass missing states.

For APIs not implemented yet, define typed ports such as:

```text
DataBrowserPort
WorkspaceMutationPort
GraphExplorerPort
ImportJobPort
ExportJobPort
ScenarioGenerationPort
AuditQueryPort
```

Provide deterministic local adapters for UI development and tests. Keep method names
and result shapes suitable for later backend replacement.

---

## UX requirements

- Desktop, tablet, and mobile layouts.
- Keyboard-accessible navigation and dialogs.
- Visible focus states.
- Semantic headings and landmarks.
- Labels for every field and icon-only button.
- Do not communicate state by color alone.
- Respect reduced motion.
- Tables must have accessible names and usable small-screen alternatives.
- Drawers/dialogs must manage focus and close with Escape.
- Long identifiers must wrap or truncate with an accessible full-value mechanism.
- JSON and graph data must have a nonvisual/table alternative.
- Every page must distinguish loading, empty, partial, forbidden, timeout, cancelled,
  validation, and unexpected-error states where applicable.
- Never display fabricated success as live production evidence.

Use the existing restrained slate-based visual language. Improve consistency and
information density without turning the console into a marketing landing page.

---

## Testing requirements

Add focused tests for every major page and interaction. At minimum cover:

- Route renders.
- Loading and empty states.
- Hard error and retry.
- Partial responses preserving usable data.
- Search/filter/sort behavior.
- Pagination.
- Asset and record selection.
- Read-only controls disabled with explanation.
- Writable sandbox create/edit/delete flows.
- Delete confirmation and bulk confirmation phrase.
- Import wizard progression and validation blocking.
- Export selection/redaction summary.
- Graph filtering, selection, and accessible table fallback.
- Scenario generation using deterministic fixture adapter.
- Failed scenario validation blocking approval.
- Successful validation enabling sandbox import approval.
- Job status and audit detail rendering.
- Navigation active states.
- No secret/DSN leakage in rendered fixture content.

Tests must use deterministic fixtures. Do not rely on live external services for the
complete unit/component suite.

---

## Required validation commands

Use the running frontend Docker container when available. Otherwise create an
equivalent Docker-only frontend command. Do not run host npm commands.

Required gates:

```text
docker exec stage3-frontend npm run lint
docker exec stage3-frontend npm run typecheck
docker exec stage3-frontend npm test
docker exec stage3-frontend npm run build
```

Also validate existing live frontend proxy routes that are available, including:

```text
GET /data-console/v1/overview
GET /data-console/v1/inventory
GET /data-console/v1/graph-evidence
GET /data-console/v1/graph-evidence/validation/latest
```

Do not fail the frontend completion solely because a future backend endpoint is not
implemented. Such screens must use the explicit typed fixture adapter and show the
fixture/sandbox label.

Do not capture screenshots. Screenshot capture happens only when the user returns for
the hardening page.

---

## Documentation and evidence

Update README truthfully after the full frontend is green:

- List every implemented Data Console route.
- Separate live-backed screens from fixture-backed screens.
- State that source mutation remains disabled.
- State that backend CRUD/import/export/AI adapters remain pending where applicable.
- Record exact lint/type/test/build results.
- Keep screenshot status as deferred.

Create frontend evidence under:

```text
frontend/docs/evidence/data_console_complete_ui/
```

Include:

```text
validation_summary.md
route_inventory.md
live_vs_fixture_capability_matrix.md
```

Never claim live validation for fixture-backed functionality.

Every implementation agent must also create or update a Markdown walkthrough,
self-review, handoff, or status report under:

```text
docs/review/status/
```

Use one stable file per stage or bounded work package, for example
`stage3c_status.md`. The report must state the actual implementation state, exact
Docker gate results, live-versus-fixture boundaries, known failures, deferred work,
evidence links, screenshot status, and whether a Git commit was created. Update the
same file when continuing a stage instead of creating timestamped duplicates.

Reviewer-authored verdicts belong under `docs/review/verdict/`. Implementation agents
must not write their own approval verdict.

---

## Completion criteria

Do not declare this prompt complete until:

1. Every route listed above exists and is reachable through the console navigation or
   a documented contextual link.
2. Every screen has responsive loading, empty, partial/error, and populated states.
3. All unavailable backend capabilities use explicit typed fixture adapters and are
   labeled truthfully.
4. Read-only and writable-sandbox boundaries are visible and enforced.
5. CRUD, import, export, graph, and AI-generation interactions are fully represented
   in the UI without pretending to perform production writes.
6. Existing Overview, Inventory, and Graph Evidence behavior remains functional.
7. Frontend lint, strict TypeScript, complete tests, and production build pass in
   Docker.
8. README and frontend evidence documents accurately distinguish live and fixture
   capabilities.
9. The stage walkthrough/review/status file under `docs/review/status/` matches the
   README and evidence.
10. No screenshots are captured.
11. No Git commit is created.

After all criteria pass, stop and report:

- Implemented routes.
- Live-backed capabilities.
- Fixture-backed capabilities awaiting backend adapters.
- Test/build totals.
- Remaining backend work in recommended order.
- Confirmation that screenshots and Git commit were not created.
