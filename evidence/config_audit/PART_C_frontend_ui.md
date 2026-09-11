# PART C (frontend half) — Configuration UI findings (CFG-C-UI-*)

Produced by the frontend sub-agent; verified from source only (backend not running).

## 1. Configuration-editing screens

| Page | Component (file:line) | API client (file:line) | Backend route | Editable fields | Editor shape | Save path |
|---|---|---|---|---|---|---|
| Configuration → Releases | `PromotionControls` — `frontend/src/domains/config/ConfigurationPage.tsx:317-419` | `configApi.promote` — `frontend/src/api/configuration.ts:210-222` | `POST /api/config/releases/{release_id}/promote` (`backend/src/return_platform/configuration/api/releases.py`) | expected head revision (RELEASED only) | Domain-specific control | Real persisted write (release status in Neo4j via `ReleaseTransitions`). |
| Configuration → Agents | `AgentsSection`/`AgentEditor` → `DocumentEditor` — `AgentsSection.tsx:133-194`, `DocumentEditor.tsx:78-412` | `agentConfigApi.save` — `frontend/src/api/agentConfig.ts:59-73` | `PUT /api/agents/{manifest_id}` → `AgentConfigurationUpdate={document}` | Whole agent module document, arbitrary keys | Hybrid generic editor (schema-free auto-form + raw JSON) | **Not a config write.** Files a governance **proposal** (`AgentsSection.tsx:174-178`); UI text at `:184` says "The active configuration has not changed." Requires approval in `/approvals`. |
| Configuration → Support Template | `SupportTemplateSection` → `DocumentEditor` — `SupportTemplateSection.tsx:91-181` | `runPublishPipeline` (`frontend/src/api/releasePublish.ts:20-54`) → `configApi.createRelease/patchDomain/promote` (`configuration.ts:180-222`); preview `supportTemplateApi.preview` (`supportTemplate.ts:38-55`) | `POST /api/config/releases`, `PATCH .../domains/RETURN_PLATFORM` (validated against `ReturnPlatformConfiguration`), `POST .../promote` ×2; preview `POST /api/v1/config/support-template/preview` | `support_template` (`template_id`, `default_variant_id`, `variants[]`) | Same hybrid `DocumentEditor` | **Real persisted write** via draft→patch→VALIDATED→RELEASED (`releasePublish.ts:27-53`). Field `support_template` matches `return_configuration.py:1714`; preview shape matches `api/template_preview.py:82-85`. |
| AI Control Center → Configuration | `TasksConfigTab`/`TaskDialog` — `AiControlCenterPage.tsx:2608-3094` | `runPublishPipeline` → `configApi.patchDomain(..., "AI_GATEWAY", {tasks})`; patch by `taskPatchOf` (`:2580-2597`) | `PATCH .../domains/AI_GATEWAY` validated against `AIGatewayConfiguration` (`ai/routing/tasks.py:303`) | Per-task: `promptVersion`, `tier`, `maximumOutputTokens`, `maximumInputTokens`, `fallbackStrategy`, `fallbackTemplate`, `allowTierEscalation`, `allowedProviders[]`, `allowedInputKeys[]`, `systemPrompt`/`systemPromptSections[]` | True domain-specific form | Real persisted write. Fields 1:1 with `TaskConfiguration` (`tasks.py:124-178`); `systemPrompt: null` + sections merge behaviour `tasks.py:180-223`. |
| AI Control Center → Providers & Models | `ProvidersTab`/`ProviderDialog` — `AiControlCenterPage.tsx:1830-2607` | `runPublishPipeline` → `configApi.patchDomain(..., "RETURN_PLATFORM", {runtime_integrations:{ai_providers}})` | `PATCH .../domains/RETURN_PLATFORM` validated against `AIProviderRuntimeConfiguration` (`return_configuration.py:1483-1523`) | provider `priority`, `base_url`, `enabled`, `credentials[].profile_key`; model `model_id`, `model_class`, `priority`, `display_name`, `enabled` | True domain-specific form | Real persisted write. `task_keys` client-computed (`:1901-1919`), backend requires `min_length=1`. |
| AI Control Center → Safety | `SafetyTab` — `AiControlCenterPage.tsx:1466-1604` | `aiControlCenterApi.safetyTest` (`aiControlCenter.ts:195-200`) | `POST /api/ai/safety-test` | Free-text JSON + task | Diagnostic tester | Not configuration. |
| Graph Schema Analyzer → Schema (PROPOSED) | `EntityEditor`/`RelationshipEditor` — `features/graph-analyzer/pages/SchemaWorkspacePage.tsx:65-67` | `updateEntity`/`updateRelationship` (`api/graphAnalyzer.ts:75-81`) | `PUT /api/graph-analyzer/v1/schemas/proposed/{entities,relationships}/{id}` | entity/relationship fields | Domain-specific form | Real write to the analyzer's proposed-schema draft store (separate from `/api/config`). |
| Graph Schema Analyzer → Schema (RUNTIME) | `RuntimeSchemaEditor` — `features/graph-analyzer/components/RuntimeSchemaEditor.tsx:109-402` | `schemaReleasesApi.putActiveDocument` (`api/schemaReleases.ts:135-144`) | `PUT /api/schema-releases/active/document` → `SchemaDocumentEdit={document, baseChecksum, activate}` | Whole running schema document (leaf editor or raw JSON) | Generic JSON/leaf editor, no schema awareness | Real persisted write; publishes a new schema release, optionally activates (`:363-372`). No mock handler. |
| Graph Schema Analyzer → Data Sources | `SourceDialog` via `DataSourcesWorkspacePage.tsx:56-58,181-200` | `saveSource` (`graphAnalyzer.ts:36-40`) | `POST/PUT /api/graph-analyzer/v1/sources[/:id]` | connection fields | Domain-specific form | Real write to analyzer connection store. Different concept from `/api/config/sources` and `/api/source-bindings`. |
| Source Sync (`/sync`) | `StartSyncForm` — `SyncControlPage.tsx:309-444` | `graphSyncApi.startRun` (`graphSync.ts:118-126`) | `POST /api/graph-sync/runs` | `mode`, `incremental`, `maxRecordsPerAsset` | Domain-specific form | Operational trigger, not persisted configuration. |
| **No screen** | — | `sourceBindingsApi` — `frontend/src/api/sourceBindings.ts:44-62` | `GET/PUT/DELETE /api/source-bindings[/{dataset}]` | — | — | **Dead client**: zero importers in `frontend/src`. |

Read-only surfaces: `OverviewTab`, `RuntimeTab`, `AuditTab`, `ReleaseDetail` (all `ConfigurationPage.tsx`) via `JsonView` (`JsonView.tsx:17-54`, 200,000-char truncation, no client-side re-masking).

## 2. Route manifest (`registry.ts`, `routeManifest.ts`)

| Path | Page |
|---|---|
| `/` → `/returns` | redirect (`registry.ts:375-376`) |
| `/all` | launcher (`registry.ts:361`) |
| `/returns` | Return Business Copilot |
| `/support`, `/support/work-queue`, `/support/rma-tickets` | Returns Support |
| `/config` + `overview|agents|support-template|runtime|releases|integrations|business|modules|security|audit` | `ConfigurationPage.tsx` |
| `/approvals` | governance proposal queue |
| `/graph-schema` + `data-sources|graph-analyzer|schema|sync` | `GraphAnalyzerWorkspace.tsx` |
| `/ai` + `overview|requests|interceptions|providers-models|routes-tasks|safety|configuration|audit` | `AiControlCenterPage.tsx` |
| `/sync` | `SyncControlPage.tsx` |
| `/operations` + `cases|return-sessions` | Operations |
| `/shipments` | Shipments |

## 3. Mock handler coverage

| Routes hit by config UI | Mocked? | Location |
|---|---|---|
| `/api/config/runtime,releases,{id}`, `PATCH .../domains/{key}`, `POST .../promote`, `/api/config/audit`, `/api/config/sources` | Yes | `mocks/handlers/canonicalHandlers.ts:1203-1534` |
| `POST /api/v1/config/support-template/preview` | Yes | `canonicalHandlers.ts:1251` |
| `/api/source-bindings` | Yes, no UI consumer | `canonicalHandlers.ts:1443-1454`, contract test `:274-291` |
| `/api/graph-sync/runs` | Yes | `canonicalHandlers.ts:1578-1591` |
| `/api/ai/*` (routes, tasks, metrics, requests, interceptions, safety-test) | Yes | `canonicalHandlers.ts:1686-1857` |
| `POST /api/ai/requests/:id/replay`, `/compare` | **No** | used by `ReplayControls` (`AiControlCenterPage.tsx:717-785`); no test references |
| `GET/PUT /api/agents[/{id}]` | **No** | `AgentsSection.test.tsx:21` mocks the module directly |
| `/api/schema-releases/*` | **No** | used by `RuntimeSchemaEditor` |
| `/api/graph-analyzer/v1/*` | Yes | `mocks/handlers/analyzerHandlers.ts:204-297` |

## DEFECTS

- **CFG-C-UI-001 (P2)** `sourceBindingsApi` (`frontend/src/api/sourceBindings.ts:44-62`) is complete, mocked and contract-tested, but no component imports it. No screen lets an operator view or change a dataset's source binding.
- **CFG-C-UI-002 (P3)** No MSW handlers for `/api/agents` and `/api/schema-releases/*`; Agents tab and Schema→Runtime tab 404 under `npm run dev:mock`. Unit tests bypass via `vi.mock` (`AgentsSection.test.tsx:21`, `ConfigurationPage.test.tsx:39`).
- **CFG-C-UI-003 (P3)** Replay/Compare (`AiControlCenterPage.tsx:717-785`, `aiControlCenter.ts:289-302`) have no mock handler and no test.
- **CFG-C-UI-004 (P3, edge)** `ProvidersTab.publish` (`AiControlCenterPage.tsx:1906-1919`) could stage an empty `task_keys` if the task catalogue has no entries for a tier; backend 422s and the publish progress surface shows the failing step. Not silent. UNVERIFIED reachable.

No PLACEHOLDER_CONFIG_UI found: no hardcoded/mock values presented as editable, no local-only state, no localStorage config persistence, no no-op save buttons, no TODO handlers, no success toasts without a backend call in `domains/config/`, `domains/ai/AiControlCenterPage.tsx`, `domains/sync/SyncControlPage.tsx`, `features/graph-analyzer/`. Only `sessionStorage` in `features/graph-analyzer/GraphAnalyzerContext.tsx:34,52` (analyzer chat context string; not configuration).

Note: Agents tab save is a proposal, not a write; count it as PARTIAL (needs `/approvals`).

UNVERIFIED (needs running backend): RFC7396 `null`-deletes round trip through `_apply_merge_patch` (`configuration/api/releases.py:342`) exactly as `taskPatchOf` assumes (`AiControlCenterPage.tsx:2585,2588`); provider/model `enabled` defaults vs draft optional fields.

STATUS: COMPLETE (frontend half)
