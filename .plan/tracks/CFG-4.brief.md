# CFG-4 — Typed configuration screens, wave A: Overview, Discovery, Return policy, Fulfilment

Base: the trunk after CFG-3a merges (CFG-3b is already on trunk at `3bee4c87`). Branch `feat/cfg-4-screens-a`. Worktree `.claude/worktrees/cfg-4` (venv + node_modules junctioned, `.env` copied; frontend commands from `frontend/`, `PYTHONPATH` pinned for any backend read). Read first: `.plan/tracks/CFG.brief.md` §3.3–3.4, `frontend/src/components/forms/README.md` (the primitives and their conventions), `.plan/reviews/CFG-3b.md` (advisories A2, A5–A8 and C1 are yours), the CFG-3a routes in `backend/src/return_platform/configuration/api/router.py` (`validate`, `publish`, `adopt-packaged`, `packaged-drift`, audit filter) and their generated types in `frontend/src/api/generated/return-platform.d.ts`, and the existing pages `frontend/src/domains/config/*.tsx` with tests.

Owns: `frontend/src/domains/config/**` (new page files, `ConfigurationPage.tsx` tab wiring, `BusinessSection.tsx` only to remove the four sections that gain a page), `frontend/src/domains/registry.ts` (`CONFIG_SECTIONS` and icons) and `registry.test.ts`, `frontend/src/domains/routeManifest.ts`, `frontend/src/api/configuration.ts` (typed client methods for the four new routes), `frontend/src/mocks/handlers/canonicalHandlers.ts` (+ contract test) for the new routes, `frontend/src/components/forms/**` (only for the carried advisories), `frontend/e2e/**` (one Playwright spec per screen). Must not touch: backend, `frontend/src/features/**`, `domains/ai/**`.

Budget: 400k tokens (L). Stop rule: `drop.json` PARTIAL at 320k with what remains.

## Design (from CFG.brief.md §3.3, made concrete)

Every page: loads its slice from the shared `["config","runtime"]` query; typed form built from the primitives; **Advanced** toggle that swaps in `DocumentEditor` (JSON mode) for the same section; `DiffPreview` of the merge patch above a sticky `PublishBar`; **Validate** calls `POST /api/config/validate/{domain}` with `{patch}` and maps errors to fields (`errors` prop) and the page list; **Publish** calls `POST /api/config/publish` `{domain_key, patch, expected_head_revision}` and shows the per-step audit ids from the response; success notice as the Support Template tab does; capability `config.release.write` gates editing, `config.release.promote` gates Publish (read the advertised capabilities; do not hardcode role names).

| Section (URL) | Typed form |
|---|---|
| `/config/overview` | release, head, adoption per process class (existing), plus an **Undecided keys** panel from `GET /api/config/packaged-drift`: for each undecided unit show domain, key, a one-line diff summary (from `would_adopt`/`filled_leaves`), and a "Take packaged file" action calling `POST /api/config/adopt-packaged` `{units:[…], expected_head_revision}` with confirm; keys the file would adopt on the next start are listed separately as information |
| `/config/discovery` | `discovery.identification_fields` as an `OrderedList` of field cards (field_id, label, aliases `TagListInput`, intent_key, clarification_priority `NumberField`, searches as a `KeyValueTable`-style sub-list with entity/field/strategy `EnumSelect`, `narrow_with`, `searches_only_with`, `known_values` `TagListInput`); `discovery.ambiguity_gap_millionths` and other scalars as `NumberField`; `source_resolution.*_paths` as `PathPicker` lists (autocomplete from the active schema via the schema-releases API's active document); `clarification_policy.fields` as an `OrderedList` (priority, label, customer_answerable `Toggle`, confirmation_required `Toggle`); `selection_vocabulary` as `KeyValueTable` |
| `/config/return-policy` | `return_policy.return_method_derivation` (default_method `EnumSelect`, freight_keywords `TagListInput`, `ship_via_methods` `KeyValueTable`, bol_tendering_instruction_types); `return_method_requirements` as a matrix (method × requirement checkboxes); `return_eligibility_policy` rules as `FieldGroup`s with `EnumSelect` for outcomes; `policy_evaluation` as `Toggle` with the reason field required when off |
| `/config/fulfilment` | `shipment_tracking.statuses` as an `OrderedList` of rungs (code, label, `allowed_next` `TagListInput` limited to known codes, `projection_status` `EnumSelect`), `source_mirror`/`source_constants` `KeyValueTable`; `bay` (`require_physical_receipt`, `allow_prearrival_reservation` `Toggle`s, `eligible_statuses` `TagListInput`, capacity numbers); `omc` fields |

Enum options and ranges come from the generated OpenAPI types where they exist; where the schema is a free string, the field is a text input with the current value and a hint naming the model's expectation. Never invent an option list.

## Carried advisories (CFG-3b)
- C1: `DocumentEditor` — clear the `blocked` entry when the data-keyed table unmounts (mode switch to JSON), two-line effect cleanup, with a test.
- A2/A5/A6/A7/A8 as listed in `.plan/reviews/CFG-3b.md`; take each or record why not in the ledger.

## Acceptance
- Each screen: vitest test for load → edit → Validate (errors mapped) → Publish (client sends the merge patch under the section key on the right domain, `expected_head_revision` from the loaded snapshot) → success notice; `BusinessSection` no longer offers the four sections; `registry.test.ts` updated for the new section list (the two pre-existing failures there are fixed as part of this, and their entries removed from `scripts/ci/known_test_failures.json` frontend list).
- One Playwright spec per screen under `frontend/e2e/` that runs against the live stack (`http://localhost:5173`, backend `:8000`): open, change one field, Validate, Publish, assert `GET /api/config/runtime` reflects the value, then publish the revert. The orchestrator runs these after RV; you run them once yourself and paste the result (the stack is up; do not restart it).
- MSW handlers and contract tests for the four new routes; `npm run dev:mock` serves every config route without 404.
- `npx vitest run`, `npm run typecheck`, `npm run lint` clean (only the known failures, now expected to be zero on the frontend).

## Tests to add
Named per screen: `OverviewSection.test.tsx`, `DiscoverySection.test.tsx`, `ReturnPolicySection.test.tsx`, `FulfilmentSection.test.tsx`; `DocumentEditor.test.tsx` C1 case; e2e specs `config-overview.spec.ts` … `config-fulfilment.spec.ts`.

## Evidence to paste
step:00 base check; per screen the vitest tail; the Playwright run; `dev:mock` route sweep output; `drop.json` path.
