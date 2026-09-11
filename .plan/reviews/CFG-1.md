# RV — CFG-1 @ e036310d — VERDICT: PASS

Base `06b43b18`, five commits, worktree `.claude/worktrees/cfg-1`. Env verified before anything
else: `PYTHONPATH=$WT/backend/src backend/.venv/Scripts/python.exe -c "import return_platform"` →
`K:\...\.claude\worktrees\cfg-1\backend\src\return_platform\__init__.py` (inside this worktree).
Every claim below is measured in this worktree, not read from the ledger.

Zero BLOCKING findings. Seven advisories, none of which changes runtime behaviour.

## Findings

| ID | Sev | file:line | What | Why it is not blocking | Fix |
|---|---|---|---|---|---|
| F1 | ADVISORY | `docs/implementation/basic-return-flow/00-execution-context.md:89` | Still names `backend/config/policies/*.yaml` in its "Policy modules" table row. The only reference to a deleted path that the drop's `remaining` list does not name (it names three other docs plus an archive file). | Prose in an unowned doc; no loader, no build step, no test reads it. | One table row: point it at `docs/configuration/DEFERRED_DESIGN.md`, or hand it to whoever owns `docs/implementation/`. |
| F2 | ADVISORY | `backend/config/agents/order_discovery.yaml:8,10`, `order_analysis.yaml:8`, `graph_schema_design.yaml:8` | `dependencies: - module_id: policy.clarification / policy.candidate_scoring / policy.return_eligibility / policy.privacy` now name manifest ids `manifest.yaml` no longer declares. `loader.py:63` still maps `"policy" -> "POLICY"` and `backend/config/README.md`'s module-type table still lists `policy.* → POLICY`. | Nothing resolves `dependencies`: `ConfigurationLoader.load_manifest_entries` iterates `manifest.modules` only, and the one validator that walked the dependency graph (`application/validator.py`) is deleted by this lease. `backend/config/README.md:27` now says so explicitly ("for documentation purposes only — `ConfigurationLoader` … does not resolve or validate this field"), and `/api/agents` (`application/agent_configuration.py:143-146`) filters to AGENT modules. Brief also forbids touching `agents/*.yaml` (item 8) and `loader.py`. | CFG-5, with the agents-copy retirement: drop the four `dependencies` entries and the `policy` prefix mapping together. |
| F3 | ADVISORY | `backend/src/return_platform/configuration/domain/release_model.py:44` | `ConfigurationRelease` now has **zero** importers anywhere in `backend/src` or `backend/tests`. Step:01's stated reason for keeping the file cites it as used by `graph_repository.py`, `dynamic_knowledge/graph/generation.py` and `platform/reasoning/checkpoint.py` — false: `generation.py:129` **declares its own** `class ConfigurationRelease`, `checkpoint.py:84` mentions the name in a docstring, and `graph_repository.py` never names it. | The *decision* is still right for the right reason — see §Q2 below: `RuntimeSnapshot` in the same file is the live class. Keeping the file is correct; only half the justification is wrong. | Correct the ledger line; delete `ConfigurationRelease` in a later lease if nothing claims it. |
| F4 | ADVISORY | `configuration/api/router.py:14` vs `:359,370,383,424,441` | The module docstring says "**Every response is scrubbed.** … `redact_secret_values` runs on the way out". Five canonical routes return the delegated handler's own `APIResponse` directly and never pass through `_ok` (`router.py:90`): `GET /api/config/sources`, `/sources/{id}`, `/sources/{id}/assets/{id}`, `/audit`, `/audit/{id}`. | **Pre-existing and unchanged by this lease** — byte-identical at `06b43b18` (`git show 06b43b18:…/sources.py`, `…/audit.py` have no `redact` call either, and `git diff 06b43b18..HEAD -- api/router.py` is docstring-only). The brief's "keep `redact_secret_values` on every response" is satisfied in the sense that nothing was removed. | Wrap the five delegations in `_ok(request, response.data)` like the release routes already do, or narrow the docstring. Not CFG-1's to decide. |
| F5 | ADVISORY | `frontend/src/main.tsx:69` | MSW `onUnhandledRequest` still special-cases `request.url.includes("/data-console/v1/")`. | It is a condition over a URL, not a call to one. No `fetch`/client in `frontend/src` targets `/data-console`; the branch is now unreachable. | Delete the branch with the next `frontend/src/mocks` change. |
| F6 | ADVISORY | `configuration/cli/bootstrap_graph_configuration.py:535` | `_drop_retired_keys` is applied to `RETURN_PLATFORM` only. `AI_GATEWAY` / `DEPENDENCY_SIMULATION` carry forward through the same `_carry_forward` + `model_validate` + fallback shape (`:592-608`) with no equivalent drop, so retiring a unit from either of those models would reproduce exactly the deadlock this lease just fixed. | Brief item 4 scopes the drop to the business model; neither of the other two models lost a key in this lease. | Generalise when the next domain retires a key. |
| F7 | ADVISORY | `scripts/ci/known_test_failures.json:33-47` | `suites.backend.known_failures` is `[]` and its comment asserts "the backend suite runs clean". It does not: 42 failures, reproduced by RV on **both** the branch and the base (see Q6). `assert_known_failures.py` therefore fails the job on any of them. | Pre-existing on the base; already flagged by the implementer in `drop.json.remaining`. RV confirms the measurement. | Orchestrator decides: fix the nine modules or register them. Not CFG-1's surface. |

## Q3 — read-only simulation against the live graph

`backend/.venv/Scripts/python.exe` with `PYTHONPATH=<cfg-1>/backend/src`, neo4j driver on
`PLATFORM_NEO4J_URI=bolt://localhost:17687` from the worktree `.env`, reads only
(`get_active_release`, `get_domain_config`), then `_carry_forward` → retired-key drop →
`model_validate` exactly as `main()` sequences them. **Nothing was written or published.**

```
module under test: K:\...\.claude\worktrees\cfg-1\backend\src\return_platform\configuration\cli\bootstrap_graph_configuration.py
active release_id: cfg0-return-method-requirements-receipt-20260911-170203
active status: RELEASED
metadata keys: ['packaged_domain_key_digests', 'packaged_key_digests']
active RETURN_PLATFORM top-level keys: 28
packaged file: K:\...\.claude\worktrees\cfg-1\backend\config\returns\production.yaml
packaged top-level keys: 26

UNDECIDED (packaged_configuration_not_adopted): agents,clarification_policy,policy_evaluation,return_eligibility_policy,return_policy,support_ingress
recordable baseline keys: 20
merged keys before drop: 28
WOULD DROP (retired_configuration_key): extensions, feature_flags
retired key in unadopted?  []
retired key in recordable? []
retired_configuration_key key=extensions      (logged, WARNING)
retired_configuration_key key=feature_flags   (logged, WARNING)
merged keys after drop: 26
ADOPTED FROM PACKAGED (release lacked the key): (none)

model_validate: PASS
round-trip equal to merged: True
```

Answers to the four sub-questions:

- **Only undeclared top-level keys, once each, before validation.** `_drop_retired_keys`
  (`:237-267`) computes `set(ReturnPlatformConfiguration.model_fields)` and deletes
  `sorted(set(merged_payload) - declared)`, logging once per key in sorted order. It is called at
  `:535`, immediately before `model_validate` at `:537` and after the
  `packaged_configuration_not_adopted` warning at `:521`.
- **Interaction with `_carry_forward` is correct.** `unadopted` and `recordable` are built only
  inside `for key, value in packaged.items()` (`:216-230`); a retired key is by definition absent
  from `packaged`, so it can only reach `merged` through the final `merged.setdefault` loop
  (`:231-232`). Measured above: both retired keys are in neither list, and both leave the merged
  payload. The six undecided keys are unchanged from the CFG-0 measurement — the drop touches
  nothing else.
- **Could it drop a key an operator legitimately added?** Only a key the model does not declare at
  all, and such a key cannot survive anyway: `StrictConfigModel` forbids extras, so without the drop
  the same payload takes the `except ValidationError` path at `:539` and the fallback discards
  *every* operator value on the release. Dropping one undeclared key is strictly less destructive
  than the alternative it replaces. The API write path patches inside declared keys, so this is not
  a route an operator reaches through `/api/config`.
- **Validation passes** on the live payload, and the model round trip is exact.

Bootstrap test file, run in this worktree:

```
$ PYTHONPATH=$WT/backend/src backend/.venv/Scripts/python.exe -m pytest \
    tests/test_graph_configuration_bootstrap.py tests/test_every_console_path_is_mounted.py \
    tests/configuration/test_configuration_health.py -q -p no:cacheprovider
43 passed in 16.49s
```

## Q1 — deletions

Scoped grep over `backend/src backend/tests backend/scripts backend/config frontend/src scripts
docs compose.yaml` for every deleted module and file name: `load_reasoning_configuration`,
`application.{compatibility,precedence,adapters,validator}`, `test_canonical_application`,
`save_domain_config`, `SaveDomainPayload`, `ConsoleSettingsView`, `Hardening{Check,Summary}`,
`GovernanceSummary`, `get_active_snapshot`, `list_releases`/`get_release_detail` (console copies),
`AuditService.{governance,settings_view,_operational_reading}`, `migration_mode`,
`migration_lock_required`, `config/policies`, `live_validation`, `internal_manifests`,
`reasoning.yaml`. Every surviving hit is prose (a retiring docstring, `DEFERRED_DESIGN.md`, the
regenerated OpenAPI descriptions) or the three historical planning docs the drop already names.
`compose.yaml`, `backend/Dockerfile*`, `scripts/*.ps1`, `scripts/linux/*.sh` and
`backend/pyproject.toml` contain no reference to any deleted path. `operations/alerts.py` keeps its
own `OperationalReading` (`:39`) — independent of the deleted `AuditService._operational_reading`.
The only misses are F1 and F2.

## Q2 — the two deviations, both true

- **`active-schema.example.yaml` kept.** The brief's condition is "delete only if no doc links it".
  It is linked from `docs/UNIFIED_RETURN_PLATFORM_TARGET_DESIGN.md` and
  `…_EXECUTION_STATE.md:1212`, and — stronger — it is a live test fixture:
  `backend/tests/dynamic_knowledge/test_schema_and_fingerprint.py:12` calls
  `load_active_schema(root / "config/dynamic_knowledge/active-schema.example.yaml")`. Deleting it
  would have broken a test outside this lease's owned paths. Reason true.
- **`domain/release_model.py` kept.** `RuntimeSnapshot` is **the same class**, not a Path A
  leftover: `application/runtime_configuration.py:8` and `application/snapshot.py:13` import it by
  name from `domain.release_model`, and `snapshot.py:60` constructs it. `RuntimeConfiguration`
  (which holds that snapshot) is imported by `main.py`, `bootstrap/context.py`,
  `bootstrap/reconciler.py`, `runtime_activation.py`, `runtime_loader.py`, `agents/contracts/
  context.py` and the bootstrap CLI — the live path, not the retired one. The brief's own gate
  ("imported by nothing but the others in this list") is genuinely not met. Reason true; see F3 for
  the half of the stated reason that is not.

## Q4 — console router retirement

```
$ pytest tests/test_every_console_path_is_mounted.py -q     → part of the 43 passed above
```
`test_no_data_console_path_is_served` (`test_every_console_path_is_mounted.py:250-259`) asserts
`sorted(p for p in served_paths if p.startswith("/data-console"))` is empty against
`create_app().openapi()["paths"]`, and `test_every_mounted_path_is_declared_under_a_known_prefix`
independently bounds the whole table to `^/(api|health)(/|$)`. Both pass. `releases.py`,
`sources.py` and `audit.py` declare no `APIRouter` (grep: the only `APIRouter` constructions left
under `configuration/api/` are `router.py:80` `/api/config` and `agents.py:45` `/api/agents`).
Nothing in `frontend/src` or `scripts/` calls a `/data-console` path — the two remaining string
matches are F5 and a comment in `scripts/run_all_host.ps1:76` / `scripts/linux/09_start_workers.sh:5`
explaining why the `jobs` worker is gone. `redact_secret_values` is unchanged on every route that
had it (`git diff 06b43b18..HEAD -- api/router.py` is docstring-only); F4 records the five that
never had it.

## Q5 — `feature_flags` / `extensions`

No reader, fixture, OpenAPI entry or frontend type names either. `grep -c` for both names over
`openapi.json`, `backend/openapi/`, `frontend/openapi/`, `openapi/` and
`frontend/src/api/generated/return-platform.d.ts` returns **0** in all five. All four OpenAPI copies
are byte-identical (`sha256 57ca6a807dda8fdc…` on each). The regenerated diff is descriptions only —
three routes, no path or schema field added or removed.

```
$ PYTHONPATH=$WT/backend/src backend/.venv/Scripts/python.exe scripts/check_openapi_drift.py
  "openapi_sha256": "57ca6a807dda8fdc36957e0b6bb9387aaa323d9d47c8f994ee495f15750243b2",
  "snapshots": ["openapi/return-platform.openapi.json","backend/openapi/return-platform.openapi.json",
                "frontend/openapi/return-platform.openapi.json","openapi.json"],
  "diffs": [], "status": "PASS", "exit_code": 0
```

## Q6 — the 42-failure claim: verified, nothing new

Full suite reproduced in this worktree:

```
$ PYTHONPATH=$WT/backend/src backend/.venv/Scripts/python.exe -m pytest tests -q \
    -p no:cacheprovider --ignore=tests/configuration/test_concurrent_activation.py -rf
42 failed, 5276 passed, 10 skipped, 515 deselected, 2 warnings in 303.22s (0:05:03)
```

The 42 ids were extracted and the nine modules they live in were run in a throwaway
`git worktree add --detach … 06b43b18` (same venv, base `.env` copied in):

```
$ (base 06b43b18) pytest <the nine modules> -q -p no:cacheprovider -rf
42 failed, 112 passed in 28.69s
$ diff branch_failed.txt base_failed.txt   →  (no output)  IDENTICAL
```

Same 42 node ids, exactly. Nothing fails on the branch that does not fail on the base — no BLOCKING
regression. None of the nine modules touches configuration (AI routing/dispatch, temporal grounding,
case-workflow confirmation).

Collection diffed as well, to prove no test silently stopped being collected:

```
base   5379/5894 collected (515 deselected)   →  5378 ids
branch 5328/5843 collected (515 deselected)   →  5327 ids
removed: 54 × tests/configuration/test_canonical_application.py
          1 × tests/test_every_console_path_is_mounted.py  (renamed, re-added below)
added:   tests/configuration/test_configuration_health.py::test_the_shipped_configuration_has_no_retired_blocks
         tests/test_every_console_path_is_mounted.py::test_no_data_console_path_is_served
         tests/test_graph_configuration_bootstrap.py::test_a_key_the_model_retired_is_dropped_from_the_carried_release
         tests/test_graph_configuration_bootstrap.py::test_an_unknown_adopt_packaged_key_refuses_before_any_write_with_no_active_release
```

Net −51, entirely accounted for by the one deleted file plus the rename. The acceptance bound
("drops by no more than the deleted tests") holds, and the three "Tests to add" all exist.
Both throwaway worktrees were removed and `git worktree prune` run; `git worktree list` is clean.

## Q7 — edits outside the brief's Owns list

| File | Judgement |
|---|---|
| `backend/tests/test_configuration_api.py` | **Mechanical casualty.** It imported `configuration.api.releases.router`, the `APIRouter` the brief mandates deleting. Retargeted at `api.router.router` and `/api/config`; the `PUT` tests converted to the equivalent `PATCH` (same `save_draft_domain` guard), the `active-snapshot` assertions to `/api/config/runtime`. The one coverage loss (`source == "VERSION_CONTROLLED_BASELINE"`) is still asserted at `tests/test_graph_configuration.py:106` — verified. |
| `backend/src/return_platform/api/return_agents.py:100` | **Mechanical casualty.** The only reader of `config.extensions`, and display-only (`dict[str, Any]` response, so no OpenAPI drift; no `frontend/src` consumer of the key — verified by grep). One dict key removed. |
| `scripts/validate_stage4l_production.py:85-86` | **Mechanical casualty.** Two assertions on `configuration.extensions.*` that cannot compile after the model change. |
| `scripts/dev/run_changed_gate.py:202` | **Mechanical casualty.** Its config-change gate ran the deleted test file; repointed at `tests/configuration`. Broader than before, still config-scoped. |
| `backend/src/return_platform/operations/alerts.py:7-14` | **Docstring only.** It cited `HardeningCheck` and "an HTTP route" as existing authority; both were deleted in step:02. Reworded. Not scope creep. |
| `backend/tests/test_graph_configuration_bootstrap.py` | Named by the brief's own "Tests to add". In scope. |
| `docs/configuration/families.md`, `docs/configuration/DEFERRED_DESIGN.md` | Owned (`docs/configuration/**`). |
| `platform/reasoning/__init__.py`, `platform/reasoning/README.md` | The brief owns "`platform/reasoning/configuration.py` and its export"; the README is the one-line row naming the deleted file. In scope. |
| 4 × OpenAPI snapshots, `frontend/src/api/generated/return-platform.d.ts`, `docs/evidence/stage4_contract_closure/openapi_drift_receipt.json` | Regenerated by the project's own `scripts/check_openapi_drift.py --write`; description-text only. Necessary, not creep. |
| `.plan/tracks/CFG.ledger.md` | The ledger. |

No edit to `application/loader.py`, `application/agent_configuration.py`, `configuration/api/agents.py`,
`frontend/src/features/**`, agents' runtime code or workflows — the brief's "must not touch" list is
respected. Spot-checked `DEFERRED_DESIGN.md`'s live-rule citations: `discovery.ambiguity_gap_millionths`
at `agents/order_discovery.py:82` ✓, `clarification_policy` at `:89` ✓, `return_eligibility_policy`
at `workflows/return_case_activities.py:1043` (the doc says 1036 — off by seven lines, within "the
surrounding gate"), `platform.redaction.sensitive_keys` ✓ (`ai/gateway/redaction.py:43`).

## Q8 — frontend

`BusinessSection.tsx:122-131`: the `platform` group drops the `feature_flags` and `extensions`
`section(...)` entries and its blurb; nothing else in the file changes.

```
$ npx vitest run src/domains/config
 Test Files  6 passed (6)      Tests  59 passed (59)     [exited with code 0]
$ npm run -s typecheck          TYPECHECK exit 0
$ npx eslint src/domains/config --max-warnings=0   ESLINT exit 0
```

## Lint / types (backend)

```
$ ruff check src/return_platform/configuration          All checks passed!
$ ruff format --check src/return_platform/configuration 48 files already formatted
$ mypy src/return_platform/configuration                Success: no issues found in 48 source files
```

## Judgement

This lease does what a deletion lease should: it deleted nothing whose reader it had not first gone
looking for, and where the grep said "still imported" it stopped and said so rather than forcing the
brief's literal list through — `release_model.py` stays because `RuntimeSnapshot` is the live class
`runtime_configuration.py` and `snapshot.py` build every process's configuration out of, and
`active-schema.example.yaml` stays because a test outside the owned paths loads it. The one change
that could have broken a running deployment is the bootstrap retired-key drop, and it is the one I
tested hardest: simulated read-only against the actual RELEASED release on the dev graph, it drops
exactly `extensions` and `feature_flags`, leaves the six undecided keys and the twenty recorded
baselines untouched, keeps the operator's `return_policy` edits, and validates with an exact model
round trip — where without it that same release would have failed `model_validate` on every future
bootstrap and taken every operator value with it into the fallback. The 42-failure claim is not an
assertion I had to trust: the ids are byte-identical between branch and base, and a collection diff
shows the 51 missing tests are the one deleted file plus a rename and nothing else. The seven
advisories are all either prose, pre-existing (F4, F7), or explicitly fenced off by the brief (F2,
F6); none of them is a reason to hold the merge. PASS.
