# CFG — Configuration remediation: fix every audit finding, one config system, premium screens

**Authority.** User-authorized execution overlay for the 2026-09-11 configuration audit
(`evidence/config_audit/FINAL_REPORT.md`). `AGENTS.md` rule 4 ("do not create another
implementation plan") is acknowledged: this brief converts audit findings into dependency-ordered
leases and carries execution state in `.plan/tracks/CFG.ledger.md`; it does not replace the
approved architecture. Where the two appear to differ, the architecture and `AGENTS.md` win.

**Baseline.** `refactor/unified-return-platform @ 42b0536b` (2026-09-10) with the audit fixes applied
(verified 2026-09-11: `git apply --check` clean, 54 backend + 65 frontend tests green there; see
FINAL_REPORT §K). The verification worktree `.claude/worktrees/cfg-verify` holds exactly that state
and is the CFG-0 candidate. The audit itself ran on `feat/acc-frontend @ 9b92633c`; every finding
was re-checked on the trunk and holds. Every lease branches from the
RV-approved head of the previous lease, never from `origin/` (see `.plan/merge.md`, stale-base
near-misses).

**Goal in one sentence.** One authoritative configuration system (the Neo4j release, three
domains) with no dead or duplicated configuration, packaged files split by business function,
every operator-changeable value reachable from a typed, premium screen grouped by function, and
every change validated, audited, adopted live and proven by an end-to-end test.

---

## 1. Operating model (multi-agent, context-safe, kill-tolerant)

### 1.1 Roles and models

| Role | Model | Why | Concurrency |
|---|---|---|---|
| Orchestrator (this session) | Fable 5.1 | merges, decisions, acceptance, ledger bookkeeping; cheap per step, holds the whole picture | 1 |
| Implementer | Sonnet 5 | well-specified slices with files, acceptance and tests named in the brief; ~250–400k tokens per lease | **1 at a time** (AGENTS.md rule 9: one production writer) |
| Design/architecture spike | Opus 5 | leases whose shape is not yet decided (loader split, env→release migration, form primitives API) — produce a design note + interface, not code | 1, never alongside an implementer on the same files |
| Reviewer (RV) | Opus 5 | independent review between every merge; verdict PASS / CHANGES_REQUIRED with evidence; may not edit | 1, after the implementer's drop |
| Scanner | Haiku 4.5 | mechanical work: dead-reference greps scoped to `backend/ frontend/src scripts/`, MSW handler generation, fixture data, mock catalogues, a11y sweep runs | up to 2 alongside anything |

Account limit (memory `session-limit-concurrency`): ~3 concurrent substantial agents exhaust the
usage window in ~25 minutes. Rule: **one implementer or one spike per usage window**, plus at most
one reviewer *after* it drops, plus Haiku scanners freely. Never two Sonnet/Opus leases on
production code at once.

### 1.2 Context management (per lease)

1. **Self-contained brief** `.plan/tracks/CFG-<n>.brief.md` (≤ 300 lines): scope, exact files it
   owns, files it must not touch, acceptance criteria, tests to add, commands to run, the
   evidence pointers it needs. The agent reads *only* the brief, `.plan/contracts.md`, and the
   files the brief names. No repo-wide reads; no re-audit.
2. **Worktree per lease** under `.claude/worktrees/` from the named base sha, with
   `export PYTHONPATH="$WT/backend/src"` and `python -c "import return_platform; print(return_platform.__file__)"`
   printed in the first ledger step (memory `worktree-venv-pth-trap`). Copy `.env`. Never
   `git stash`. Never `scripts/reset_all.*`. Greps scoped to `backend/ frontend/src scripts/`
   (memory: `.claude/worktrees/` holds eight stale checkouts; root-level greps stall).
3. **Ledger, append-only** `.plan/tracks/CFG.ledger.md`: one entry per step with the command and
   its pasted output. Never transcribed from memory.
4. **Drop after every step**: `evidence/orchestration/drops/LEASE-CFG-<n>/drop.json` with
   `merge_status: PARTIAL`, the branch, head sha, files changed, tests run, and what remains;
   rewritten to `PENDING` when the lease's own definition of done is met. A killed lease with a
   drop is completed work; a killed lease without one is lost work.
5. **Stale-base check** as step:00 of every lease: `git rev-parse` local and origin trunk, count
   left/right, name the base sha in the ledger.
6. **Budget line** in each brief: expected tokens and the stop rule ("write the drop at 300k
   regardless of state").

### 1.3 Session-failure protocol

| Event | Detection | Action |
|---|---|---|
| Usage-window kill mid-lease | agent task ends without a final message; drop says PARTIAL | wait for the window; `SendMessage` the same agent id with "resume from your drop" (transcript survives); do not respawn unless the resume fails twice |
| Resume fails / transcript gone | resumed agent cannot recall state | spawn a fresh implementer with the brief + the drop + the ledger tail as its only context; it continues from the last pasted step |
| Reviewer killed | no verdict file | re-run RV from the same candidate sha; verdicts are idempotent |
| Orchestrator restart | new session | recovery order: `CFG.ledger.md` tail → `drops/*/drop.json` with PARTIAL/PENDING → `git status` of each worktree → resume the highest-priority executable lease. Never resume from remembered conversation |
| Two leases touch one file | RV or merge conflict | the later lease rebases onto the merged head; the orchestrator never hand-merges production code |

### 1.4 Gates between leases

`implementer drop (PENDING)` → `RV PASS` (zero unresolved findings, pasted command output for
every claim) → orchestrator merge to trunk → next lease branches from that head. A lease whose RV
returns CHANGES_REQUIRED is resumed, not respawned.

---

## 2. Decisions needed before wave 2 (orchestrator asks the user once, records in the ledger)

| ID | Decision | Default if no answer |
|---|---|---|
| D-CFG-1 | Delete never-wired files (`policies/*.yaml`, `live_validation/`, `internal_manifests/`, `data_platform/*.yaml`, `reasoning.yaml` + loader, manifest `agents/` copies) or keep as design intent? | delete, with the design intent captured in one `docs/configuration/DEFERRED_DESIGN.md` |
| D-CFG-2 | `feature_flags` and `extensions` blocks: wire to real switches or remove? | remove from the model and file (nothing reads them) |
| D-CFG-3 | Split `returns/production.yaml` (2,276 lines) into per-function files composed by the loader? | yes — one file per business group (§3.2), same model, same release payload |
| D-CFG-4 | Move the eight env-held business switches (provider order, model pools, dependency modes, feedback learning, support ticket mode, thinking budget) into the release? | yes, into a new `deployment` section, keeping production gates and env as the bootstrap default |
| D-CFG-5 | Retire the `/data-console/v1` router modules (unmounted since Wave F1)? | yes (Wave F5 as already planned) |
| D-CFG-6 | Resolve the ten undecidable release keys on the dev host (`--adopt-packaged-key`)? | `source_resolution` yes; `policy_evaluation`, `support_ingress` no; the rest reviewed one by one in CFG-0 |

---

## 3. Target design

### 3.1 One configuration system

- **Runtime authority:** Neo4j RELEASED release, domains `RETURN_PLATFORM`, `AI_GATEWAY`,
  `DEPENDENCY_SIMULATION` (+ the new `deployment` section under `RETURN_PLATFORM` after D-CFG-4).
- **Files** are the packaged baseline only, composed by the loader from a directory (§3.2);
  the bootstrap carries them forward per key/unit (already done).
- **Settings/env** keeps hosts, ports, credentials, Vault references, and the bootstrap defaults
  for the migrated switches. No business value is read from env at request time after CFG-6.
- **Mongo-held config** stays where it is authoritative by design: graph schema releases, source
  bindings, AI gateway intercept settings (the `providerOrder` duplicate is removed by CFG-6).
- **Manifest system** (`loader.py`/`compatibility.py`/`precedence.py`) is retired with D-CFG-1;
  the Agents editing API is repointed at the live `agents:` section.

### 3.2 Packaged files split by business function (D-CFG-3)

```
backend/config/returns/
  index.yaml                # ordered list of part files; schema_version; assumption_set_version
  discovery.yaml            # discovery, source_resolution, clarification_policy, selection_vocabulary
  return_policy.yaml        # return_policy, return_eligibility_policy, policy_evaluation
  workflow.yaml             # workflow, return_case, business_calendars, housekeeping
  support.yaml              # support, support_gate, support_ingress, support_resolver, context_assembly, support_template
  fulfilment.yaml           # shipment_tracking, bay, omc
  integrations.yaml         # integrations, runtime_integrations (bootstrap-generated part kept), copilot
  agents.yaml               # agents
  deployment.yaml           # (CFG-6) provider order, model pools, dependency modes, switches
backend/config/ai_gateway/
  index.yaml                # circuitBreaker, retry, rateLimits, providerLimits, modelContexts
  tasks/<TASK_ID>.yaml      # one file per task (25)
backend/config/dependency_simulation.yaml   # unchanged (small)
```

`load_return_configuration(path)` accepts a file **or** a directory; a directory is composed in
`index.yaml` order, a section declared twice is a load error, and the composed document is the
same `ReturnPlatformConfiguration` as today. `production.yaml` is deleted once composition is
proven byte-equivalent (`model_dump` equality test against the old file at the split commit).

### 3.3 Configuration screens grouped by function

The Configuration domain's sections become one page per business group, each a **typed form**
built from shared primitives, with the existing `DocumentEditor` kept as the *Advanced* (JSON)
mode of every page — the escape hatch, not the default.

| Section (URL) | Typed form covers | Advanced |
|---|---|---|
| `/config/overview` | release, head, adoption, undecided keys with per-key "take packaged file" action (calls a new `POST /api/config/adopt-packaged` that runs the bootstrap's per-unit adoption) | — |
| `/config/discovery` | identification fields (table), aliases, source paths (path pickers with autocomplete from the schema), clarification policy, selection vocabulary | yes |
| `/config/return-policy` | method derivation (enum + keyword lists), ship-via map (key/value table), requirements matrix, eligibility rules, policy evaluation toggle with reason | yes |
| `/config/workflow` | stage sequence (ordered list), waits and timeouts (duration fields with units), calendars (weekly grid), housekeeping | yes |
| `/config/support` | tabs: Template (existing, with preview), Gate, Ingress, Resolver, Context assembly, Queues | yes |
| `/config/fulfilment` | shipment status ladder (ordered rungs with allowed-next chips and projection status), bay rules, order-management rules | yes |
| `/config/integrations` | topic bindings, copilot settings, fabrication guard switches; **replaces the read-only tab** | yes |
| `/config/agents` | live `agents:` block as a typed table (enabled, AI-assisted, route ref); the proposal path is kept and shown inline | yes |
| `/config/deployment` (CFG-6) | provider order (drag list), model pools per provider/tier, dependency modes, switches; production gates rendered as disabled-with-reason | yes |
| `/config/simulation` | dependency simulation: enabled, banner, AI narration, dependencies table | yes |
| `/config/data-sources` (new) | source bindings (rebind/clear per dataset) and sync trigger, distinct from the Analyzer's connections | — |
| `/config/releases`, `/config/runtime`, `/config/audit` | as today; audit filtered to `CONFIGURATION_*` by default, with before/after paths | — |

AI Control Center keeps tasks and providers (already typed) and gains the per-task Advanced mode.

### 3.4 Premium form primitives (`frontend/src/components/forms/`)

Built on the existing kit (`premium-panel`, `premium-kicker`, `premium-field`, M3 teal tokens,
`outline-control` for input edges); no new palette, no new fonts. One component each, tested:

- `Field` (label, hint, error from a backend path, required marker), `FieldGroup` (section with
  kicker and description), `Toggle` (with reason field when the model requires one),
  `NumberField` (unit suffix, min/max from the model), `DurationField` (s/min/h with a stored
  seconds value), `EnumSelect` (options from the model's enum, current value always present),
  `TagListInput` (string arrays as chips with add/remove/reorder), `OrderedList` (drag to reorder,
  used for stages, ladders, provider order), `KeyValueTable` (mapping editor: add, rename,
  delete, inline validation, sorted or insertion order preserved), `PathPicker` (source path
  with autocomplete from the active schema), `DiffPreview` (the merge patch that will be sent,
  rendered as before/after per path — every page shows it before Publish), `PublishBar` (sticky
  footer: dirty count, validate, publish, progress from `PublishProgress`).
- **Validation** is the backend's: a `POST /api/config/validate/{domain}` (new, CFG-3b) returns
  the pydantic error list mapped to paths; the form shows each error under its field and the
  page-level list. The `DocumentEditor` gains the same path-mapped errors.
- **Key/value editing** replaces the generated nested form for mappings: the current `Node`
  renderer in `DocumentEditor` becomes `KeyValueTable` for objects whose keys are data (ship-via
  codes, dependencies, tasks) and `FieldGroup` for objects whose keys are schema.

### 3.5 Optimize

- PATCH gains `expected_version` (domain node `version`) → 409 on a stale draft.
- `CONFIG_RELEASE_WRITE` capability gates create/patch (promote already narrowed).
- Publish pipeline: one `POST /api/config/publish` that opens, patches, validates and releases in
  a single transaction with `expected_head_revision`, replacing four round trips from every
  screen (the frontend `runPublishPipeline` stays as the fallback).
- Frontend: `GET /api/config/runtime` is fetched once per page and shared via the existing query
  key; section pages subscribe to their slice only.
- Dead code out: unmounted console routers, dead Settings, never-wired loaders (D-CFG-1),
  manifest translation path, `compatibility.py` `features`/`platform` lookups.

---

## 4. Work breakdown (leases in merge order)

Sizes: S ≤ 150k tokens, M ≤ 300k, L ≤ 450k (one usage window each; L may need a resume).

| Lease | Wave | Model | Size | Depends | Scope (owns) | Acceptance |
|---|---|---|---|---|---|---|
| **CFG-0** land audit fixes | 0 | orchestrator + RV Opus | S | — | branch `feat/cfg-0-audit-fixes` from `42b0536b` (the cfg-verify worktree), commit the applied audit fixes as `(CFG) step:00`; resolve D-CFG-6 keys on the dev host; RV; merge to trunk | trunk carries the bootstrap/API/launcher/Business-tab fixes; bootstrap 20/20, API 8/8, frontend config suites green; ledger records the adopt-key decisions |
| **CFG-1** dead code and duplicates | 1 | Sonnet | M | CFG-0, D-CFG-1/2/5 | delete never-wired files and loaders; remove console router modules and `test_every_console_path_is_mounted` pins; remove manifest translation path (`compatibility.py`, `precedence.py`, `adapters.py`, `validator.py` where test-only) and their tests; `system_store` dead keys; `seed_version` default; `feature_flags`/`extensions` per D-CFG-2; README updates | `grep` shows zero references to removed modules in `backend/ frontend/src scripts/`; full backend suite ≥ current pass count minus deleted tests; `backend/config/README.md` lists only loaded files |
| **CFG-2** packaged file split | 1 | Opus spike (design note, 40k) then Sonnet | M | CFG-0, D-CFG-3 | directory loader for `returns/` and `ai_gateway/`; `index.yaml`; byte-equivalence test at split; bootstrap and `PLATFORM_*_CONFIGURATION_PATH` accept a directory; compose/launchers unchanged | `model_dump(old) == model_dump(new)` test; bootstrap against the dev graph reports UNCHANGED; docs updated |
| **CFG-3a** backend config API v2 | 1 | Sonnet | M | CFG-0 | `POST /api/config/validate/{domain}` (path-mapped errors); `POST /api/config/publish` (single transaction); PATCH `expected_version`; `CONFIG_RELEASE_WRITE`; `POST /api/config/adopt-packaged` (per unit, audited); audit filter param; OpenAPI regenerated in all three copies + drift check | API tests for each route incl. 409 paths; `scripts/check_openapi_drift.py` passes |
| **CFG-3b** form primitives | 1 | Sonnet (after a 30k Opus API note) | M | CFG-0 | `frontend/src/components/forms/*` per §3.4 with vitest + a11y (axe) tests; `DocumentEditor` path-mapped errors and `KeyValueTable` for data-keyed objects | every primitive has keyboard operation, labelled controls, error rendering and a test; no new palette/font |
| **CFG-4** screens wave A | 2 | Sonnet | L | CFG-3a, CFG-3b | `/config/discovery`, `/config/return-policy`, `/config/fulfilment`, `/config/overview` (undecided-keys panel); registry sections and route manifest; MSW handlers; tests per screen (load → edit → DiffPreview → publish → mocked adoption) | each screen passes the full loop against the live stack once (ledger pastes head revision before/after and the runtime value); Playwright spec per screen |
| **CFG-5** screens wave B | 2 | Sonnet | L | CFG-4 | `/config/workflow`, `/config/support` (6 tabs, template preview kept), `/config/integrations` (replaces read-only tab), `/config/agents` typed table, `/config/simulation`, `/config/data-sources` (source bindings + sync); Business tab retired once every section has a page; MSW handlers for `/api/agents`, `/api/schema-releases/*`, replay/compare | same loop evidence per screen; registry test updated to the new section list; `dev:mock` serves every config route |
| **CFG-6** env → release `deployment` section | 3 | Opus spike (60k) then Sonnet | L | CFG-2, CFG-3a, D-CFG-4 | `deployment` section model; bootstrap seeds it from env on first publish, then env is default-only; AI routes read provider order/model pools from the release with a hot-adopt path; remove the Mongo `providerOrder` duplicate; `/config/deployment` screen; production gates preserved | provider order change adopts live without restart (ledger proof); production refuses SIMULATED/MANUAL from the release exactly as from env; `.env.example` marks the switches as bootstrap defaults |
| **CFG-7** acceptance and hardening | 3 | Haiku sweeps + Sonnet fixes + RV Opus | M | CFG-5, CFG-6 | full-loop Playwright suite over every config screen against live infra; axe sweep of `/config/*`; fix the two pre-existing test failures (`registry.test.ts`, `test_cumulative_support_outcomes`); update `docs/`; acceptance record under `.plan/acceptance/config-screens.md` | all config screens green in the live loop; zero axe violations; trunk suites fully green |

Wave 1 leases CFG-1, CFG-2, CFG-3a, CFG-3b are file-disjoint and may be *pipelined* (each
branches off the previous candidate head) but still run **one implementer at a time**.

---

## 5. Per-lease brief template (the orchestrator writes one file per lease)

```
# CFG-<n> — <title>
Base: <sha> (RV-approved head of <previous lease>). Branch: feat/cfg-<n>-<slug>. Worktree + PYTHONPATH pinned (step:00).
Owns: <exact paths>. Must not touch: <paths>. Decisions in force: <D-CFG ids>.
Scope: <numbered items, each with the file and the behaviour>.
Acceptance: <observable checks, commands to run, expected output>.
Tests to add: <names and what they assert>.
Evidence to paste: <commands whose output goes in the ledger>.
Budget: <tokens>; stop rule: write drop.json PARTIAL at 80% and report.
```

## 6. Definition of done (track)

1. Every FINAL_REPORT §G defect is `fixed` or has a recorded decision to defer, with the ID in
   the ledger.
2. `backend/config/` contains only files the runtime loads, split by function; the READMEs match.
3. Every `RETURN_PLATFORM` section, `AI_GATEWAY` unit and `DEPENDENCY_SIMULATION` has a typed
   screen with Advanced mode, path-mapped validation, DiffPreview and audited publish; each proven
   once in the live loop with pasted evidence.
4. No business value is read from env at request time; env holds deployment values and
   bootstrap defaults only.
5. Trunk suites fully green, OpenAPI drift check green, axe sweep clean.
