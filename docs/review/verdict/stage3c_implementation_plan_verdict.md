# Stage 3C Data Sources and Governed Data Browser Plan Verdict

Review date: 2026-07-23  
Review type: design and implementation-plan review  
Reviewed scope: proposed Stage 3C Data Sources and Governed Data Browser plan  
Reference sources:

- `CODEX_PROMPT_DATA_CONSOLE_ALL_UI_SCREENS.md`
- `docs/evidence/data_console_complete_ui/stage3a/api_gap_register.md`
- `docs/evidence/data_console_complete_ui/stage3a/route_capability_matrix.md`
- `docs/evidence/data_console_complete_ui/stage3a/ownership_write_policy_matrix.md`
- current Stage 3B frontend architecture

## Verdict

**CHANGES REQUIRED — DIRECTION APPROVED, PLAN NOT YET IMPLEMENTATION-READY**

Using deterministic fixture data for the five Stage 3C routes is correct. The API gap
register explicitly classifies all five operations as unavailable and fixture-backed.
The proposed route set and broad page split also match the master UI prompt.

The plan must be expanded before implementation because it currently treats MSW as
the complete data abstraction, omits several mandatory browser behaviors and states,
does not define behavior outside development fixture mode, and lacks required
security, regression, evidence, and Docker validation coverage.

This is a plan verdict only. No Stage 3C implementation or runtime validation is
approved by this document.

At the end of this review, the current worktree also contains a blanket `docs/`
entry in `.gitignore`. That rule currently ignores this verdict and all required
Stage 3C status/evidence documents. It must be removed before implementation
evidence is considered retainable.

## Approved direction

The following decisions are approved:

1. Implement these routes as `FIXTURE` capabilities:
   - `/data-console/sources`
   - `/data-console/sources/:sourceId`
   - `/data-console/browser`
   - `/data-console/browser/:engine/:assetId`
   - `/data-console/browser/:engine/:assetId/records/:recordId`
2. Keep the backend unchanged during Stage 3C.
3. Define strict TypeScript contracts for sources, assets, record pages, and record
   details.
4. Use deterministic development fixtures and exact MSW handlers for the five
   unavailable API paths.
5. Keep source-system browsing read-only and label every fixture-backed screen
   truthfully.
6. Add focused component tests, E2E navigation tests, and Axe scans.

## Required plan corrections

### P0 — Restore documentation visibility

Remove the blanket `docs/` entry from `.gitignore`. Confirm with `git check-ignore`
that the following paths are not ignored:

- `docs/review/status/stage3c_status.md`
- `docs/review/verdict/`
- `docs/evidence/`

Use narrow ignore rules only for genuinely generated artifacts. Required governance,
status, review, and evidence documents must remain visible to Git.

### P0 — Define explicit ports and adapters

MSW is a transport simulator, not the domain/data-provider boundary required by the
master prompt. The plan must define:

- `DataSourcesPort`
- `DataBrowserPort`
- a deterministic `FixtureDataSourcesAdapter`
- a deterministic `FixtureDataBrowserAdapter`
- a future live adapter boundary
- an explicit development-only adapter selection mechanism

React Query hooks should depend on these ports, not embed knowledge of fixture mode
or silently fall back from a failed live request to fixtures.

MSW may remain as integration/E2E transport coverage for the exact API-gap paths, but
it must not be the only replaceable abstraction.

### P0 — Define behavior when fixture mode is disabled

The five routes must remain truthful when `VITE_MOCK_MODE` is not enabled. The plan
must specify one of these bounded behaviors:

- render an explicit `FIXTURE MODE REQUIRED`/blocked state without calling an
  unavailable endpoint; or
- select a typed unavailable adapter that returns a safe capability error.

Never send an invented endpoint request to the live backend and then convert its
failure into fixture success.

Every fixture-backed page must show a visible `FIXTURE — NON-DURABLE` capability
label in addition to any global development banner.

### P0 — Complete the governed browser contract

`BrowserRecord` must not be an unbounded heterogeneous object or use `any`. Define
discriminated contracts for:

- SQL row records
- MongoDB documents
- Neo4j nodes
- Neo4j relationships
- typed displayed values: null, missing, redacted, binary, string, number, Boolean,
  date/time, object, and array

Also define:

- stable source/asset/record identities;
- ownership and access capability;
- redacted/hidden/copyable metadata;
- page cursor or page number, page size, total/has-more semantics;
- allowed server-owned sort and filter fields;
- partial-result warnings and request metadata;
- related records and graph relationships;
- audit/activity entries;
- safe error codes for forbidden, not found, validation, timeout, cancellation, and
  unexpected failures.

Validate unknown fixture/transport payloads at the boundary, preferably with strict
Zod schemas consistent with the TypeScript contracts.

### P1 — Expand the Data Sources screens

The source-list plan must explicitly include:

- environment;
- last inventory time;
- search;
- engine, ownership, status, and read/write filters;
- responsive cards/table presentation;
- loading, empty, partial, forbidden, not-found where applicable, and hard-error
  states.

The source-detail plan must explicitly include:

- safe connection identity with no credentials or DSN;
- ownership and governance classification;
- access/read-write capability explanation;
- inventory totals;
- last successful metadata refresh;
- dependency warnings;
- Assets, Governance, Activity, and Configuration Summary tabs;
- safe invalid/missing `sourceId` behavior.

### P1 — Expand the browser interactions

The browser landing page must include:

- only assets present in the shared deterministic inventory fixture;
- recent assets;
- ownership and read-only/writable explanations;
- links that preserve encoded engine and asset identity safely.

The asset browser must include:

- bounded pagination and safe page-size options;
- allow-listed server-owned search/filter/sort parameters;
- column visibility and density controls;
- SQL row, expandable MongoDB JSON, and Neo4j result presentations;
- Neo4j links to the future Graph Explorer shown as unavailable until Stage 3D;
- selected-record inspector drawer;
- safe copy behavior that excludes redacted/hidden values;
- export-selection control shown as unavailable until the typed export capability
  exists;
- add/edit/delete actions enabled only for an explicitly writable sandbox asset;
- loading, empty, partial, validation, forbidden, timeout, cancelled, not-found, and
  unexpected-error states.

The record-detail page must include:

- stable identity and ownership;
- all defined typed-value states;
- related records and graph relationships;
- fixture-backed audit/activity timeline;
- safe copy rules;
- disabled mutation controls with explanations for every source-owned asset.

### P1 — Add reusable Stage 3C components

The plan currently lists only pages, which risks large monolithic implementations.
Add the reusable components Stage 3C needs, including:

- `SearchInput`
- `FilterBar`
- `DataTable`
- `PaginationControls`
- `Tabs`
- `DetailDrawer`
- `JsonInspector`
- `PropertyList`
- `EngineBadge`
- fixture/capability notice

Reuse the existing Stage 3B header, breadcrumb, capability, ownership, loading, empty,
error, partial-warning, and request-metadata components.

Update breadcrumbs so dynamic source, asset, and record parameters produce safe human
labels and do not create misleading links.

### P1 — Centralize query keys and cancellation

`sourceQueries.ts` and `browserQueries.ts` must:

- use the centralized `queryKeyFactory`;
- include engine, asset ID, record ID, pagination, filter, and sort inputs in
  deterministic keys;
- pass TanStack Query abort signals through the port;
- retain the complete API-style response envelope;
- never convert error responses into empty success data;
- avoid retries for deterministic fixture/4xx errors.

### P1 — Separate fixtures from handlers

Create deterministic fixture modules such as:

- `frontend/src/fixtures/sources.ts`
- `frontend/src/fixtures/browser.ts`

Handlers should validate request parameters and delegate to fixture logic rather than
contain large inline datasets. Include at least:

- SQL Server read-only data;
- source MongoDB read-only data;
- Neo4j derived read-only data;
- an explicitly labeled writable sandbox example only if mutation controls need to
  be demonstrated.

Fixture identifiers and relationships must be internally consistent across Sources,
Inventory, Browser, and record detail.

## Required testing expansion

Testing only loading, empty, error, and success for each page is insufficient. Add
deterministic coverage for:

1. Every route renders directly, including dynamic route parameters.
2. Source search and every declared filter.
3. Partial source results preserve usable sources.
4. Source detail tabs and missing-source behavior.
5. Browser asset selection.
6. Pagination bounds and page-size limits.
7. Allow-listed filtering and sorting.
8. SQL, MongoDB, Neo4j node, and Neo4j relationship rendering.
9. Expandable JSON and the accessible structured alternative.
10. Record drawer and record-detail navigation.
11. Null, missing, redacted, binary, nested, array, date, numeric, and Boolean values.
12. Copy operations never include redacted or hidden fields.
13. Read-only mutation controls are disabled with an explanation.
14. Writable controls appear only for an explicitly writable sandbox capability.
15. Partial, validation, forbidden, timeout, cancelled, not-found, and unexpected
    errors.
16. Fixture labels appear on every Stage 3C page.
17. Fixture-off mode renders a truthful blocked/unavailable state.
18. No rendered source, fixture, error, log, or test output contains passwords,
    credentials, tokens, secret environment values, or DSNs.
19. Existing Overview, Inventory, and Graph Evidence tests remain green.
20. Navigation active states work for list and nested detail routes.

E2E and accessibility tests must cover all five routes, populated fixture behavior,
keyboard navigation, and at least one small-screen navigation path. Axe is useful but
does not replace interaction and keyboard assertions.

## Required validation and evidence

The final Stage 3C verification plan must include:

```text
docker exec stage3-frontend npm ci
docker exec stage3-frontend npm run lint
docker exec stage3-frontend npm run typecheck
docker exec stage3-frontend npm test
docker exec stage3-frontend npm run build
docker exec stage3-frontend npm run test:e2e
docker exec stage3-frontend npm run test:a11y
```

Also retain the Stage 3B production bundle assertion and both negative mock-build
checks.

Validate the existing live frontend proxy routes without MSW:

```text
GET /data-console/v1/overview
GET /data-console/v1/inventory
GET /data-console/v1/graph-evidence
GET /data-console/v1/graph-evidence/validation/latest
```

The implementation report must distinguish those live routes from the five fixture
routes. A fixture handler response is not live backend validation.

Create or update:

- `docs/review/status/stage3c_status.md`
- `frontend/docs/evidence/data_console_complete_ui/validation_summary.md`
- `frontend/docs/evidence/data_console_complete_ui/route_inventory.md`
- `frontend/docs/evidence/data_console_complete_ui/live_vs_fixture_capability_matrix.md`
- README route and capability status

Do not capture screenshots during Stage 3C. Do not create a Git commit.

## Revised completion criteria

Stage 3C may be declared complete only when:

1. All five routes exist and are reachable.
2. All routes use explicit typed ports and deterministic fixture adapters.
3. MSW handlers match only the five exact Stage 3C API-gap paths.
4. Fixture-off mode is safe and truthful.
5. Every page is visibly labeled as fixture-backed and non-durable.
6. Source-system records remain read-only.
7. Browser pagination, filtering, sorting, redaction, copying, inspection, and
   capability gating are implemented and tested.
8. Required empty, partial, forbidden, timeout, cancelled, validation, not-found,
   and hard-error states are tested.
9. No secret or DSN leakage is possible in fixtures or rendered output.
10. Existing Stage 3B behavior and live proxy routes remain green.
11. All Docker gates, E2E tests, Axe tests, production bundle checks, and negative
    mock-build checks pass.
12. README, evidence, and `stage3c_status.md` truthfully distinguish live, fixture,
    blocked, and deferred behavior.

No screenshots were captured and no Git commit was created during this plan review.
