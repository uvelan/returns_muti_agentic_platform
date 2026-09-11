# CFG-5 — Typed configuration screens, wave B: Workflow, Support, Integrations, Agents, Simulation, Data sources

Base: the trunk after CFG-4 merges. Branch `feat/cfg-5-screens-b`. Worktree `.claude/worktrees/cfg-5` (venv + node_modules junctioned, `.env` copied; frontend commands from `frontend/`). Read first: `.plan/tracks/CFG.brief.md` §3.3, `.plan/tracks/CFG-4.brief.md` and `.plan/reviews/CFG-4.md` (its advisories are yours), the wave-A screens and the shared `TypedSectionScreen` chrome under `frontend/src/domains/config/`, `frontend/src/components/forms/README.md`, `frontend/src/api/sourceBindings.ts` and `graphSync.ts` (existing clients with no screen), `backend/src/return_platform/configuration/api/agents.py` (the proposal path the Agents screen keeps), and the mock layer `frontend/src/mocks/handlers/*` and `frontend/src/main.tsx` (dead `/data-console/v1/` branch, RV CFG-1 F5).

Owns: `frontend/src/domains/config/**`, `frontend/src/domains/registry.ts` + test, `routeManifest.ts`, `frontend/src/api/configuration.ts`, `agentConfig.ts`, `sourceBindings.ts`, `graphSync.ts` (typed methods only), `frontend/src/mocks/**` (handlers for `/api/agents`, `/api/schema-releases/*`, replay/compare, source bindings — RV CFG-1 F5 and the CFG-C-UI-002/003 audit items), `frontend/src/main.tsx` (the dead branch only), `frontend/e2e/**`, `backend/config/manifest.yaml` and `backend/config/agents/*.yaml` (deletion, see item 5), `backend/src/return_platform/configuration/api/agents.py` and `application/agent_configuration.py` (repoint only), `backend/tests/configuration/test_agent_configuration_releases.py`. Must not touch: other backend code, `frontend/src/features/**`, `domains/ai/**`.

Budget: 450k (L). Stop rule: `drop.json` PARTIAL at 360k.

## Screens (all on `TypedSectionScreen`, Advanced/JSON toggle kept)

| Section (URL) | Typed form |
|---|---|
| `/config/workflow` | `workflow` stage sequence as `OrderedList` (stage id, handler type `EnumSelect`, agent ref where the handler is an agent); `return_case` waits and timeouts as `DurationField`s; `business_calendars` as a weekly grid per calendar (day toggles + open/close `DurationField`-style times); `housekeeping` if present in the model (check `ReturnPlatformConfiguration`; skip if absent) |
| `/config/support` | one page, tabs: **Template** (move the existing `SupportTemplateSection` here unchanged, its own tab route stays as an alias for one release), **Gate**, **Ingress**, **Resolver**, **Context assembly**, **Queues** (`support`); each tab a typed form over its section |
| `/config/integrations` | `integrations` topic bindings as `KeyValueTable`, `ai_may_fabricate_success`-style switches as `Toggle`s; `copilot` (`order_discovery_agent_id` `EnumSelect` from the agents present, `candidate_columns` `TagListInput`, poll intervals `DurationField`); **replaces** the read-only Integrations tab (`UNBACKED` entry removed) |
| `/config/agents` | the live `agents:` section as a typed table (name, version, enabled `Toggle`, ai_assisted `Toggle`, ai_route_ref `EnumSelect` from the AI gateway tasks) — **keeps the proposal path**: Save files a proposal via `PUT /api/agents/{id}` and shows the proposal id with a link to `/approvals`; states plainly that the active configuration has not changed |
| `/config/simulation` | `DEPENDENCY_SIMULATION` domain: enabled, banner, AI narration (`Toggle`, temperature `NumberField`), dependencies `KeyValueTable` of `{name → operations TagListInput}` |
| `/config/data-sources` (new) | source bindings: per dataset the declared asset and the override (rebind form with the `SourceAssetDefinition` fields; Clear); sync trigger and run history (move the `/sync` page's `StartSyncForm` and run list here; `/sync` redirects); named distinctly from the Analyzer's connections (audit finding D1) |

## Backend item 5 — retire the manifest agent copies (D-CFG-1, deferred from CFG-1)
Repoint `PUT /api/agents/{id}` and `application/agent_configuration.py` at the live `agents:` section of `RETURN_PLATFORM` (the proposal, when approved, publishes a release patching `agents.<id>` — reuse `publish_release_with_domains`); delete `backend/config/agents/*.yaml` and their `manifest.yaml` entries (leave `platform.system_store`; delete `workflow.*`, `sync.*`, `source.*`, `mapping.*`, `graph.*` entries too if the loader's only remaining consumer is the agents API — verify with grep and record it); update `test_agent_configuration_releases.py`; `backend/config/README.md`. Run the backend suites for configuration and api and paste.

## Other items
6. Retire the **Business tab** once every section has a page (keep `BusinessSection.tsx` only if a section remains without one; otherwise delete it, its test and the registry entry, and say so in the ledger).
7. MSW handlers + contract tests for `/api/agents`, `/api/schema-releases/*`, `/api/ai/requests/{id}/replay|compare`, `/api/source-bindings`; delete the dead `/data-console/v1/` branch in `main.tsx`; `npm run dev:mock` serves every config route.
8. CFG-4 review advisories, each closed or reasoned.

## Acceptance
- Per screen: vitest (load → edit → Validate → Publish/propose → notice), one Playwright spec run once against a disposable dev server with `--workers=1`, values reverted, pasted.
- `npx vitest run` 0 failures; `typecheck`, `lint` clean; backend `pytest tests/configuration tests/api tests/test_configuration_api.py -q` green; `check_openapi_drift.py` PASS if any backend response changed.
- `registry.test.ts` reflects the final section list; the `/sync` and `/config/support-template` aliases redirect.
