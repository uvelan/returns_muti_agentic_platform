# Stages 3E through 3H Implementation Plan

This plan encompasses the remaining UI stages (Imports, Exports, Jobs, Workspaces, Scenarios, Audit, Governance, Settings, Hardening) as per the CODEX UI-First execution prompt.
No user review is required; execution will proceed immediately and continuously.

## Stage 3E - Imports, Exports, Jobs and Activity
**Goal:** Build data operation tracking and execution interfaces.

### Core Contracts & Ports (`frontend/src/contracts/jobs.ts` and `frontend/src/api/ports/`)
- `Job` type supporting `import`, `export`, `generation`, `validation`, and `synchronization` operations.
- `JobQueryPort` (list/get), `ImportJobPort` (submit import), `ExportJobPort` (submit export).
- Fixtures for `jobs.ts` under `frontend/src/fixtures/`.

### Pages (`frontend/src/features/data-console/pages/jobs/`)
- `JobsListPage.tsx` (`/data-console/jobs`) - Unified jobs list.
- `JobDetailPage.tsx` (`/data-console/jobs/:jobId`) - Detailed timeline view.
- `ImportsListPage.tsx` (`/data-console/imports`)
- `ImportWizardPage.tsx` (`/data-console/imports/new`) - Wizard with target, file, mapping, validation.
- `ExportsListPage.tsx` (`/data-console/exports`)
- `ExportWizardPage.tsx` (`/data-console/exports/new`) - Wizard with source, format, redaction summary.

## Stage 3F - Writable Sandbox Workspaces
**Goal:** Build sandbox management for non-durable records.

### Core Contracts & Ports
- `Workspace` and `SandboxRecord` types.
- `WorkspaceMutationPort` for deterministic local fixture edits (Create/Duplicate/Archive/Delete/Edit Record).

### Pages (`frontend/src/features/data-console/pages/workspaces/`)
- `WorkspacesListPage.tsx` (`/data-console/workspaces`)
- `WorkspaceCreatePage.tsx` (`/data-console/workspaces/new`)
- `WorkspaceDetailPage.tsx` (`/data-console/workspaces/:workspaceId`)
- `WorkspaceRecordEditPage.tsx` (`/data-console/workspaces/:workspaceId/records/:recordId/edit`) - Schema-driven form / JSON fallback.

## Stage 3G - AI Scenario Studio
**Goal:** Build scenario modeling interfaces.

### Core Contracts & Ports
- `Scenario` type.
- `ScenarioGenerationPort` for deterministic generation of scenario content.

### Pages (`frontend/src/features/data-console/pages/scenarios/`)
- `ScenariosListPage.tsx` (`/data-console/scenarios`)
- `ScenarioBuilderPage.tsx` (`/data-console/scenarios/new`)
- `ScenarioDetailPage.tsx` (`/data-console/scenarios/:scenarioId`)
- `ScenarioPreviewPage.tsx` (`/data-console/scenarios/:scenarioId/preview`)

## Stage 3H - Audit, Governance, Settings and Hardening Structure
**Goal:** Implement remaining admin and status screens.

### Core Contracts
- Deterministic fixtures for audit logs, governance matrices, and settings profiles.

### Pages (`frontend/src/features/data-console/pages/admin/`)
- `AuditPage.tsx` (`/data-console/audit`)
- `GovernancePage.tsx` (`/data-console/governance`)
- `SettingsPage.tsx` (`/data-console/settings`)
- `HardeningPage.tsx` (`/data-console/hardening`)

## Navigation Updates (`frontend/src/App.tsx` and Navigation bar)
- Mount all the new routes.
- Update global navigation with "Explore", "Data Operations", "Sandbox & AI", and "Governance" groups.

## Testing Strategy
- Populate component tests for all screens.
- Run `npm run typecheck`, `npm run lint`, `npm test` against mock builds.
- Defer E2E and Screenshots.
