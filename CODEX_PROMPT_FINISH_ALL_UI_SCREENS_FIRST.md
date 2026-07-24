# Codex Execution Prompt — Finish All Data Console UI Screens First

You are continuing work in the current Return Platform repository.

Your immediate objective is:

> **Finish every remaining Data Console UI screen and make all frontend interactions
> work with deterministic typed fixtures. Do not wait for backend endpoints. Full
> browser E2E, live backend integration, and final hardening will happen after the
> backend is complete.**

Work continuously through the remaining frontend stages. Do not stop after planning
one page or one stage. Do not ask for approval between ordinary frontend screens.
Stop only for a genuine architecture/security blocker or after every route in this
prompt is implemented.

---

## Locked execution rules

1. Use Docker for all frontend commands. Do not run host npm commands.
2. Do not implement new backend endpoints during this UI-first pass.
3. Do not create a Git commit.
4. Do not capture screenshots. Screenshots are deferred to the hardening phase.
5. Do not run or block progress on Playwright E2E, Playwright accessibility, or live
   frontend/backend integration. Those gates will run after the backend is complete.
6. Continue running lint, strict TypeScript, unit/component tests, production build,
   and production mock-bundle safety checks in Docker.
7. Preserve all existing working Overview, Inventory, Graph Evidence, Sources, and
   Data Browser behavior.
8. Never claim fixture-backed behavior is live backend validation.
9. Never expose secrets, credentials, DSNs, arbitrary SQL, MongoDB filters, Cypher,
   Python, or executable configuration.
10. Keep source SQL Server, source MongoDB, and Neo4j projections read-only.
11. Writable behavior is permitted only in explicitly labeled local/sandbox fixture
    workspaces.
12. AI generation produces a preview only. It must never write directly to a source,
    platform collection, or graph.

---

## Required implementation approach

For every backend capability that does not exist:

- Define strict TypeScript contracts.
- Define an explicit typed port.
- Provide a deterministic local fixture adapter.
- Optionally provide exact MSW handlers for later browser integration.
- Select fixtures only through the development capability boundary.
- Render a truthful blocked/unavailable state when fixture mode is disabled.
- Display `FIXTURE — NON-DURABLE` or `LOCAL SANDBOX` prominently.
- Keep future live adapters replaceable without rewriting the page.
- Propagate AbortSignals through query ports.
- Centralize deterministic React Query keys.
- Validate unknown payloads at the adapter boundary where practical.
- Do not use `any`, non-null assertions, broad lint suppressions, or weakened
  TypeScript settings.

Prefer working, composable screens over exhaustive integration infrastructure.
Implement focused populated-state and critical-interaction component tests now.
Defer the complete error/state permutation matrix to backend integration and
hardening unless a state is necessary to make the screen safe or understandable.

---

## Existing UI that must remain working

```text
/overview
/data-console/inventory
/data-console/graph-evidence
/data-console/sources
/data-console/sources/:sourceId
/data-console/browser
/data-console/browser/:engine/:assetId
/data-console/browser/:engine/:assetId/records/:recordId
```

Do not rewrite these screens unless a small shared-component or navigation change is
required for the remaining routes.

---

## Remaining routes to implement

### Stage 3D — Graph Explorer

```text
/data-console/graph
/data-console/graph/nodes/:nodeId
/data-console/graph/relationships/:relationshipId
```

Build:

- Exact governed identifier search only.
- Interactive graph canvas using the already approved `@xyflow/react`.
- Zoom, pan, fit, reset, focus, label filters, relationship filters, and legend.
- One bounded neighborhood expansion at a time.
- Visible maximum depth, node, relationship, and expansion caps.
- Accessible node/relationship table alternative.
- Desktop split-pane inspector.
- Full-width routed inspector on mobile.
- Direct-load restoration for node and relationship URLs.
- Node labels, properties, ownership, provenance, evidence, inventory links, and
  record links when stable identifiers exist.
- Relationship type, endpoints, properties, ownership, and evidence.
- Read-only controls only; no graph mutations and no free-form Cypher.
- Loading, empty, truncated, partial, unavailable, not-found, and error presentation.

Use a `GraphExplorerPort`, deterministic graph fixtures, and explicit unavailable
adapter. Fixture search/expansion must be bounded and deterministic.

### Stage 3E — Imports, Exports, Jobs and Activity

```text
/data-console/imports
/data-console/imports/new
/data-console/imports/:jobId
/data-console/exports
/data-console/exports/new
/data-console/exports/:jobId
/data-console/jobs
/data-console/jobs/:jobId
```

Build:

- Import and export history tables with filters, status, target, owner, counts, and
  timestamps.
- Import wizard: target, file format, file selection, parse preview, field mapping,
  duplicate policy, validation, review, approval, and fixture submission.
- CSV, JSON, and JSONL presentation.
- Bounded file-size and record-count notices.
- Import result counts and bounded validation issues.
- Export wizard: permitted source, selected/bounded records, format, field selection,
  redaction summary, size estimate, confirmation, and fixture download state.
- Unified job list and job detail timeline.
- Import, export, generation, validation, and future synchronization job types.
- Disabled retry/cancel actions unless a typed fixture capability explicitly enables
  them.
- No export of secrets, hidden fields, or redacted values.

Use typed `ImportJobPort`, `ExportJobPort`, and `JobQueryPort` fixture adapters.

### Stage 3F — Writable Sandbox Workspaces

```text
/data-console/workspaces
/data-console/workspaces/:workspaceId
/data-console/workspaces/:workspaceId/new
/data-console/workspaces/:workspaceId/records/:recordId/edit
```

Build:

- Workspace list and detail.
- Prominent sandbox/non-durable classification.
- Create, duplicate, archive, and delete fixture workspace interactions.
- Schema-driven form when a schema exists.
- JSON editor fallback for sandbox records only.
- Field-level and cross-record validation summaries.
- Unsaved-change warning.
- Preview-before-apply flow.
- Before/after diff for edits.
- Single-record delete confirmation.
- Bulk/permanent fixture deletion requiring a confirmation phrase.
- Relationship impact warnings.

All mutations must stay in deterministic local fixture state through a typed
`WorkspaceMutationPort`. Never write to source systems.

### Stage 3G — AI Scenario Studio

```text
/data-console/scenarios
/data-console/scenarios/new
/data-console/scenarios/:scenarioId
/data-console/scenarios/:scenarioId/preview
```

Build:

- Scenario library with positive, negative, boundary, and failure templates.
- Search/filter by entity, workflow stage, classification, and validation status.
- Scenario builder with natural-language description, template, entity selection,
  bounded record count, deterministic seed, classification, and consistency rules.
- Provider/model/configuration display without secrets.
- Deterministic local scenario generator through `ScenarioGenerationPort`.
- Generated entity totals.
- Tables, documents, and graph preview tabs.
- Canonical validation and cross-entity consistency reports.
- Exact entity references in issues.
- Regenerate, edit, revalidate, save draft, and approve-for-import interactions.
- Approval disabled until validation passes.
- Import approval restricted to writable sandbox workspaces.
- Prompt/configuration/model/seed provenance.
- Scenario detail with validation evidence, import history, and audit timeline.

Do not enable a live model provider during this pass.

### Stage 3H — Audit, Governance, Settings and Hardening Structure

```text
/data-console/audit
/data-console/governance
/data-console/settings
/data-console/hardening
```

Audit:

- Read-only activity table.
- Actor, operation, entity, outcome, time, and correlation ID.
- Bounded filters.
- Detail drawer with before/after summaries and evidence references.

Governance:

- Asset ownership matrix.
- Source, platform, derived, and sandbox classification.
- Read/write/import/export/AI-generation capabilities.
- Sampling policies and limits.
- Catalog version/digest and load status.
- No catalog mutation controls.

Settings:

- Current non-secret environment.
- Feature/capability matrix.
- Relative API-base behavior.
- Density and theme-ready UI preferences.
- No credentials, DSNs, raw secrets, or secret editing.

Hardening:

- Build the page structure only.
- Frontend gate status cards driven by typed fixture evidence.
- Responsive/accessibility checklist.
- API proxy validation placeholders.
- Known limitations.
- Screenshot checklist marked `DEFERRED`.
- Security, reliability, load, E2E, and live-integration items marked `PENDING` unless
  real evidence exists.

---

## Shared components to finish as needed

Reuse existing components and add only what the remaining screens require:

```text
MetricCard
SearchInput
FilterBar
DataTable
PaginationControls
DetailDrawer
Tabs
JsonInspector
PropertyList
SchemaTable
UnsavedChangesDialog
FileDropZone
StepIndicator
ValidationSummary
ValidationIssueTable
JobStatusTimeline
DiffViewer
GraphCanvas
GraphTable
Toast/inline operation feedback
```

Keep pages composable. Avoid monolithic page files and repeated large class strings.

---

## Navigation and routing

- Add every route to the declarative route manifest.
- Mark live routes as `LIVE`, local fixture routes as `FIXTURE`, and unavailable
  future capabilities as `BLOCKED`.
- Add navigation groups for Explore, Data Operations, Sandbox & AI, and Governance.
- Keep detail routes non-navigable but directly loadable.
- Ensure active navigation remains correct for nested routes.
- Encode and decode route identifiers safely.
- Provide breadcrumbs and contextual links.
- Every route must be reachable from navigation or a documented parent-screen link.

---

## UI-first testing policy

Write focused unit/component tests now for:

- Every route's populated fixture render.
- Primary navigation to list/detail screens.
- Critical form/wizard step progression.
- Search/filter behavior central to the page.
- Core create/edit/delete fixture interaction.
- Read-only versus writable-sandbox gating.
- Scenario validation blocking/enabling approval.
- Graph search, selection, bounded expansion, and table fallback.
- No secret/redacted-field display or export.

Do not spend this pass building exhaustive Playwright permutations.

Explicitly defer until the backend is complete:

- Full Playwright E2E execution.
- Full Playwright Axe/accessibility execution.
- Live frontend/backend route integration.
- Cross-service E2E scenarios.
- Screenshot capture.
- Load, security, failover, and reliability hardening.

Existing Playwright specs may be extended so they are ready for later execution, but
they are not a completion gate for this UI-first pass.

---

## Docker validation for each frontend stage

Use the existing frontend container:

```text
docker exec stage3-frontend npm run lint
docker exec stage3-frontend npm run typecheck
docker exec stage3-frontend npm test
docker exec stage3-frontend npm run build
```

Also retain:

- production mock-bundle assertion;
- direct negative `npx vite build --mode mock` check when build configuration changes.

Do not use `npm run build -- --mode mock`, because the current multi-command build
script does not forward that option to Vite reliably.

If a pre-existing unrelated test blocks a stage, document it precisely. Do not weaken
lint, TypeScript, tests, or production mock safety to make the gate green.

---

## Documentation and status

For each stage, create or update:

```text
docs/review/status/stage3d_status.md
docs/review/status/stage3e_status.md
docs/review/status/stage3f_status.md
docs/review/status/stage3g_status.md
docs/review/status/stage3h_status.md
```

Update:

```text
frontend/docs/evidence/data_console_complete_ui/route_inventory.md
frontend/docs/evidence/data_console_complete_ui/live_vs_fixture_capability_matrix.md
frontend/docs/evidence/data_console_complete_ui/validation_summary.md
README.md
```

Status rules:

- Say `UI-FIRST COMPLETE` for implemented fixture screens.
- Say `BACKEND INTEGRATION PENDING` for unavailable endpoints.
- Say `E2E DEFERRED UNTIL BACKEND COMPLETE`.
- Say `SCREENSHOTS DEFERRED TO HARDENING`.
- Never write an implementation-agent approval verdict.
- Reviewer verdicts belong only in `docs/review/verdict/`.

---

## UI-first completion criteria

This prompt is complete only when:

1. Every route listed above exists.
2. Every route is reachable through navigation or a parent-screen link.
3. Every screen renders useful deterministic populated fixture data.
4. Primary interactions work locally.
5. Fixture, live, blocked, read-only, and writable-sandbox boundaries are visible.
6. No source or graph production mutation is exposed.
7. Every unavailable backend capability is behind a typed replaceable port.
8. Lint, strict TypeScript, complete unit/component tests, production build, and
   production mock-bundle safety pass in Docker.
9. README, route inventory, capability matrix, validation summary, and stage status
   documents are current and truthful.
10. No screenshot or Git commit is created.

Do not report the overall product as complete. Report:

```text
ALL DATA CONSOLE UI SCREENS: UI-FIRST COMPLETE
BACKEND ENDPOINTS: PENDING
LIVE INTEGRATION: PENDING
PLAYWRIGHT E2E: DEFERRED UNTIL BACKEND COMPLETE
ACCESSIBILITY/HARDENING: DEFERRED
SCREENSHOTS: DEFERRED
GIT COMMIT: NOT CREATED
```

Then provide:

- implemented route list;
- live-backed screen list;
- fixture-backed screen list;
- unit/component test totals;
- remaining backend endpoint order;
- recommended E2E sequence after backend completion.
