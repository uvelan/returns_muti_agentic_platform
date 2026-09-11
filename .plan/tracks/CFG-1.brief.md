# CFG-1 — Dead configuration, dead loaders, duplicate copies out

Base: the RV-approved head of CFG-0 (`feat/cfg-0-audit-fixes`, on `refactor/unified-return-platform @ 42b0536b`). Branch `feat/cfg-1-dead-code`. Worktree under `.claude/worktrees/cfg-1` with `backend/.venv` junctioned to the main venv, `.env` copied, `frontend/node_modules` junctioned, and `PYTHONPATH="$WT/backend/src"` printed with `return_platform.__file__` in step:00.

Decisions in force: D-CFG-1 (delete never-wired files, keep the intent in one doc), D-CFG-2 (remove the unread `feature_flags` and `extensions` blocks), D-CFG-5 (retire the `/data-console/v1` router modules).

Owns: `backend/config/**`, `backend/src/return_platform/configuration/**`, `backend/src/return_platform/platform/reasoning/configuration.py` and its export, `backend/src/return_platform/platform/system_store/**`, `backend/tests/configuration/**`, `backend/tests/test_every_console_path_is_mounted.py`, `docs/configuration/**`, both configuration READMEs, `frontend/src/domains/config/BusinessSection.tsx` (group lists only). Must not touch: agents' runtime code, workflows, the Agents editing API (`configuration/api/agents.py`, `application/agent_configuration.py`, `application/loader.py` — CFG-5 repoints them), `frontend/src/features/**`.

Budget: 300k tokens. Stop rule: write `drop.json` (`merge_status: PARTIAL`) at 240k regardless of state and report.

## Scope

1. **Never-wired files** (evidence: FINAL_REPORT §F, PART_D §1–5, PART_B §5; each has zero loaders in `backend/src`): delete `backend/config/policies/` (4 files), `backend/config/live_validation/`, `backend/config/dynamic_knowledge/internal_manifests/` (4 files), `backend/config/reasoning.yaml` with `load_reasoning_configuration` in `platform/reasoning/configuration.py` and its re-export in `platform/reasoning/__init__.py`, `backend/config/dynamic_knowledge/active-schema.example.yaml` only if no doc links it (grep `docs/`). Leave `backend/config/data_platform/` and its mapping engine alone (16 tests exercise it; separate decision). Before deleting, write `docs/configuration/DEFERRED_DESIGN.md`: one paragraph per removed file naming the rule it described and where the equivalent live rule is (or that none exists), from PART_B1 §1.3 and the audit's "how policy checks happen" trace (eligibility → `return_eligibility_policy` in `workflows/return_case_activities.py:1036`; clarification → `clarification_policy` in `agents/order_discovery.py:89`; scoring → `discovery.ambiguity_gap_millionths` in `agents/order_discovery.py:82`; privacy → code in `ai/gateway/redaction.py`).
2. **Manifest translation path**: delete `configuration/application/compatibility.py`, `precedence.py`, `adapters.py`, `validator.py`, `domain/release_model.py` and any domain model only they use, with `tests/configuration/test_canonical_application.py`, `test_validator_smoke.py`, `test_loader_and_compatibility.py` and siblings — **only after** `grep -rn` over `backend/src` (excluding tests) shows each symbol is imported by nothing but the others in this list. `application/loader.py` and `ConfigurationLoader` stay (the Agents API uses them). Remove the `policy.*` entries from `backend/config/manifest.yaml`; keep the agent/workflow/sync/source/mapping/graph entries until CFG-5. `README.md` in both places: drop the manifest-translation and "singleton compatibility" sections, keep the "what actually runs" text CFG-0 added.
3. **Console router modules** (D-CFG-5): the canonical router `configuration/api/router.py` imports handler functions from `releases.py`, `sources.py`, `audit.py`. Move those handler bodies into the canonical router (or a `configuration/api/handlers/` module with no `APIRouter`), delete the `/data-console/v1` `APIRouter` objects, the full-document `PUT` handler `save_domain_config` (no consumer; the PATCH path is the write), and the `ConsoleSettingsView` route. Update `test_every_console_path_is_mounted.py` to assert the canonical paths only and that no route path begins with `/data-console`. Keep `redact_secret_values` on every response.
4. **Unread blocks** (D-CFG-2): remove `feature_flags` and `extensions` from `backend/config/returns/production.yaml`, from `ReturnPlatformConfiguration` and its sub-models, from `BusinessSection.tsx` groups, and from any fixture. Then make the bootstrap tolerate their presence in old releases: in `cli/bootstrap_graph_configuration.py`, after `_carry_forward`, drop any top-level key of the merged `RETURN_PLATFORM` payload that `ReturnPlatformConfiguration.model_fields` does not declare, logging `retired_configuration_key key=<k>` once per key, before `model_validate`. Without this the next start would fail validation on every existing release and fall back to the packaged file, dropping operator values (the deadlock the bootstrap's own comments describe). Test: an active release carrying `feature_flags` publishes cleanly without it and keeps its other values.
5. **System store dead keys**: remove `migration_mode` and `migration_lock_required` from `backend/config/platform/system_store.yaml` and from the Path A model (deleted in item 2 anyway); the live loader `_SystemStoreConfigPayload` is unchanged.
6. **Settings drift**: `seed_version` default `e2e-v1` → `e2e-v2` to match `.env.example` and `compose.yaml`.
7. **Carried from RV CFG-0 (advisories F5, F10):** make the unknown-unit refusal in `bootstrap_graph_configuration.py` symmetric (a bare key that names a unit of another domain, or a qualified key naming an unknown domain, both fail before any write, with the same message shape); strengthen `test_a_patched_domain_is_stored_in_the_shape_the_bootstrap_compares` to assert the stored payload equals `ReturnPlatformConfiguration.model_validate(stored).model_dump(mode="json")` (the round trip), not only the key set.
8. **Duplicate agents copy — do not touch.** `backend/config/agents/*.yaml` are read by `/api/agents`; retiring them is CFG-5.

## Acceptance

- `grep -rn` for every deleted module and file name over `backend/ frontend/src scripts/ docs/` returns only `DEFERRED_DESIGN.md` and the audit evidence.
- `backend/config/README.md` lists exactly the files the runtime loads.
- `PYTHONPATH=$WT/backend/src python -m pytest tests -q -p no:cacheprovider --ignore=tests/configuration/test_concurrent_activation.py` passes with the known-failure registry unchanged (`scripts/ci/known_test_failures.json`), and the pass count drops by no more than the deleted tests.
- `ruff check`, `ruff format --check`, `mypy` on `backend/src/return_platform/configuration` clean.
- Bootstrap against the dev graph (run by the orchestrator after RV, not by this lease) reports `READY` with `retired_configuration_key key=feature_flags` and `key=extensions` and no fallback error.
- Frontend: `npx vitest run src/domains/config`, `npm run typecheck`, eslint on changed files clean.

## Tests to add

- `tests/test_graph_configuration_bootstrap.py::test_a_key_the_model_retired_is_dropped_from_the_carried_release`.
- `tests/test_every_console_path_is_mounted.py::test_no_data_console_path_is_served`.
- A test that `load_return_configuration` of the shipped file has no `feature_flags`/`extensions` attribute.

## Evidence to paste in the ledger

step:00 base check (`git rev-parse`, left/right count, `return_platform.__file__`); the grep proving each deletion had no reader; the pytest tail; ruff/mypy output; `drop.json` path.
