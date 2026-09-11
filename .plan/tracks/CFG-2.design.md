# CFG-2 design — packaged configuration split into composed directories (D-CFG-3)

Spike on `feat/cfg-1-dead-code @ e036310d` (worktree `.claude/worktrees/cfg-1`,
`return_platform.__file__ = <wt>/backend/src/return_platform/__init__.py`). Read-only; no code changed.
Sizes at this base: `returns/production.yaml` 2,490 lines / 106,585 B; `ai_gateway.yaml` 1,141 lines /
71,080 B (the brief's 2,276/1,060 predate CFG-1).

## 1. Findings — every reader

**Loaders.** `return_configuration.py:2089` `load_return_configuration(path)` — `resolve(strict=True)`,
suffix must be `.yaml`/`.yml`, 1 MB bound, `yaml.safe_load`, root must be a mapping, returns
`LoadedReturnConfiguration(configuration, path, sha256=sha256(raw))` (`:2083`).
`ai/routing/tasks.py:378` `load_ai_gateway_configuration(path)` — same shape, no suffix or size check;
`tasks.py:391` `build_loaded_ai_gateway_configuration` already digests the *canonical JSON dump* rather
than bytes, so a non-byte digest is precedented.
**Path settings.** `settings.py:13` `DEFAULT_RETURN_CONFIGURATION_PATH`, `:17`
`DEFAULT_AI_GATEWAY_CONFIGURATION_PATH`; fields `:64`, `:68`. **Blocker:** both are listed in the
`validate_catalog_path` validator (`settings.py:406-420`), which raises `"Configuration registry paths
must reference YAML files."` for a non-YAML suffix and `"...must be absolute."` for a relative value — a
directory is refused before any loader runs. No test pins either message (grep over `backend/tests` empty).
**Process readers of the setting**, all of which hand it straight to a loader, so directory support in
the loader covers every one: `main.py:471,477` (and `:622-627`, which hands `baseline_*.path` to
`RuntimeConfigurationActivator`, re-loading it at `runtime_activation.py:276,285`),
`runtime_loader.py:69-70`, `cli/bootstrap_graph_configuration.py:411-412`, `ai/gateway/service.py:222`,
`dependency_simulation/ai.py:94`, `dynamic_knowledge/integration/runtime_factory.py:161`,
`operations/associate_flow.py:406`, `operations/orchestrator.py:169`.
**Deployment.** `compose.yaml:14,16` and **`backend/Dockerfile:124,126`** (baked `ENV`, not named in the
lease request; `Dockerfile:141` `COPY config ./config` carries the tree, so split files ship
automatically). `.env.example:204,297` are comments only — neither variable is set there.
`scripts/prepare_runtime_configuration.sh:152` names both files in a comment.
**Scripts.** `scripts/validate_stage4l_production.py:59-60` (loader; `:271` records `loaded.sha256`),
`scripts/validate_stage4n_ai_gateway.py:44` (`AI_CONFIG` → loader). `backend/scripts/` names neither.
**Tests.** 90 files reference the two files or the path constants; all but seven go through `load_*`.
The seven that read raw text or bytes: `tests/acceptance/test_item_10_the_tool_rung_is_unreachable.py:84`;
`tests/configuration/test_return_method_requirements_configuration.py:111,160` (`:160` asserts the
*position of an `OPERATOR REVIEW REQUIRED` comment banner* relative to `method:` lines inside
`return_method_requirements` — comments are load-bearing);
`tests/configuration/test_support_ai_gateway_tasks.py:41`;
`tests/configuration/test_support_gate_configuration.py:39,71`;
`tests/operations/test_support_template_draft.py:44`;
`tests/policy/test_window_policy_is_configuration.py:200,239` (reads the whole file, `.replace()`s one
value, writes `tmp_path/production.yaml`, loads it as a single file).
`scripts/ci/known_test_failures.json` names neither file. No test greps by line number.
**Frontend:** zero hits in `frontend/src` — nothing to change.
**Docs:** 40+ citations, only four live rather than archive — `backend/config/README.md:42,45,54`,
`configuration/README.md:24-25`, `ai/README.md:30,122`, `backend/config/workflows/return_session.yaml:12,35`.
**Structure.** `production.yaml` has 25 top-level keys: `schema_version`, `assumption_set_version` plus
23 sections. §3.2's `housekeeping` does **not** exist (grep empty) — drop it from the proposal.
`ai_gateway.yaml`: `schemaVersion`, `domain` plus `circuitBreaker`, `retry`, `rateLimits`,
`providerLimits`, `modelContexts`, `tasks` (25). `pricing` is model-defaulted and absent from the file.
**Anchors.** `production.yaml` defines `&tpl_sec_*` at 2265-2369 and dereferences them at 2442-2475 — all
inside `support_template`; no `<<:` merges anywhere. A part file must parse independently, so no anchor
may cross a part boundary and `support_template` must stay whole in one part.
**Digest invariance.** `bootstrap_graph_configuration.py:142` `_key_digests` and `:656` `payload_checksum`
both use `json.dumps(..., sort_keys=True)`, and the UNCHANGED test at `:670` is
`active_payloads == domain_payloads` (dict equality). Carry-forward units come from `_units` (`:85`) over
`model_dump(mode="json")`, never from the file layout. **So composition order cannot move a digest, a
release id, or an `--adopt-packaged-key` unit name** (`discovery`, `AI_GATEWAY/tasks.<TASK_ID>`) provided
the composed dump equals the old one — which is exactly what the equivalence test proves.

## 2. Decided design

A new `backend/src/return_platform/configuration/composition.py` holds one composer used by both loaders
(`ai/routing/tasks.py` may import it: only `platform/*` is layer-constrained,
`tests/platform/test_layering.py:14-21`, and `test_no_module_cross_imports.py` is a no-op until a
`module.py` lands). `load_*(path)`: a file runs today's code **unchanged**; a directory is composed.
`index.yaml` carries the document-level keys (`schema_version`/`assumption_set_version`;
`schemaVersion`/`domain` for the gateway), an ordered `parts:` list of relative POSIX paths, an optional
`entries:` mapping of *section → ordered list of paths* (the gateway's `tasks`; CFG-5's `agents` if it
ever wants it), and — per §3.2 — optional inline sections. Rules, every one a `ValueError`:
(1) no `index.yaml` → `configuration directory <dir> has no index.yaml`; (2) a listed path missing →
`index.yaml lists <p>, which does not exist in <dir>`; (3) a `*.yaml`/`*.yml` file anywhere in the tree
that no list names → `<p> is not listed in <dir>/index.yaml; this loader does not glob`, mirroring
`backend/config/README.md`'s manifest stance; (4) a part root that is not a mapping, or a path escaping
the directory; (5) a section declared twice → `section <k> is declared in both <a> and <b>` (an inline
index section counts as a declaration; a section named in `entries` may not also appear in a part);
(6) an `entries` key is the file **stem** (`tasks/support.message.classify.v1.yaml` →
`support.message.classify.v1`), and two stems equal under `casefold()` are an error naming both files;
(7) the existing 1 MB bound applies to the tree's total bytes; (8) `LoadedReturnConfiguration.path` is the
directory; (9) `sha256` is a framed digest over `index.yaml` then each listed file in list order
(`relpath\0len\0bytes`), keeping today's "the bytes on disk changed" meaning, comments included.

**Returns layout** — one part belongs to exactly one Configuration screen (§3.3); a screen may own two.
`agents` stays a part file: CFG-5 repoints the Agents API at the live section and edits this file in place.
`runtime_integrations` goes in `integrations.yaml` — the bootstrap-generated AI receipts live in the
*release* and nothing writes the packaged file back (`return_configuration.py:61`), so no writer has to
learn the layout.

| part | sections | ~lines |
|---|---|---|
| `index.yaml` | `schema_version`, `assumption_set_version`, `parts` | 15 |
| `agents.yaml` | `agents` | 43 |
| `discovery.yaml` | `discovery` | 770 |
| `discovery_resolution.yaml` | `source_resolution`, `clarification_policy`, `selection_vocabulary` | 380 |
| `return_policy.yaml` | `return_policy`, `policy_evaluation`, `return_eligibility_policy` | 356 |
| `workflow.yaml` | `workflow`, `return_case`, `business_calendars` | 145 |
| `support.yaml` | `support`, `context_assembly`, `support_ingress`, `support_resolver`, `support_gate`, `support_template` | 426 |
| `fulfilment.yaml` | `omc`, `bay`, `shipment_tracking` | 249 |
| `integrations.yaml` | `integrations`, `copilot`, `runtime_integrations` | 110 |

**Gateway layout.** `backend/config/ai_gateway/index.yaml` carries `schemaVersion`, `domain` and the five
cross-task sections inline (§3.2), `parts: []`, and `entries.tasks:` listing 25 `tasks/<TASK_ID>.yaml`,
each the single task's mapping with the id taken from the stem — ids **as-is**, including the five dotted
ones. Filesystem safety checked on this host: all 25 ids match `[A-Za-z0-9_.]+`, none collides
case-insensitively, none carries a reserved Windows device name in its first dot-segment
(`CON`/`PRN`/`AUX`/`NUL`/`COM1-9`/`LPT1-9`), the longest is 36 chars, and the deepest resulting path in
this worktree is ~160 chars, well inside `MAX_PATH`. The loader still enforces rule 6 and a
`^[A-Za-z0-9][A-Za-z0-9_.-]*$` stem rule so a future id cannot produce an illegal name silently.

**The split is text surgery** — cut the line ranges, carry every comment and anchor verbatim; never
`safe_load` + `safe_dump`, which destroys the comments two tests assert on and rewrites `&tpl_sec_*`.

## 3. The equivalence test — `backend/tests/configuration/test_packaged_configuration_composition.py`
- `test_the_composed_returns_document_equals_the_file_it_was_split_from`: pre-split bytes from
  `git show <SPLIT_BASE_SHA>:backend/config/returns/production.yaml` (`subprocess.run`, `cwd` = repo root;
  tests already shell out, e.g. `tests/test_openapi_contract_drift.py`), written to
  `tmp_path/production.yaml`, loaded as a single file; assert `model_dump(mode="json")` equality against
  `load_return_configuration(<dir>)`. A shallow or exported checkout has no such object, so the bytes are
  also frozen at `backend/tests/data/pre_split/returns_production.yaml`; the test uses the frozen copy when
  git cannot answer, and a companion test asserts the two are byte-identical when it can. Same pair for
  the gateway. Both split proofs are **one-shot** — the first deliberate edit to a part file retires them;
  their docstring says so and names CFG-6 as the lease that deletes them after its final PASS is pasted.
- `test_the_packaged_key_digests_are_unchanged_by_the_split`: `_key_digests` over both dumps equal, and
  `_units(dump, CARRY_FORWARD_SPLIT_KEYS[AI_GATEWAY])` yields the same 25 `tasks.<ID>` unit names — the
  carry-forward proof stated directly rather than inferred.
- `test_composing_the_directory_equals_loading_it_as_one_file` is the **durable** invariant: compose the
  raw mapping, `yaml.safe_dump` it to one temp file, load that as a file, compare dumps. It survives every
  future edit and is what guards the composer from here on.
- Five error tests (duplicate section naming both files, listed-but-missing, present-but-unlisted, missing
  index, case-colliding task stems), each asserting both file names appear in the message.

## 4. Risks

1. **`settings.py:406-420` refuses a directory** — the one omission that breaks every container. Move
   `return_configuration_path` and `ai_gateway_configuration_path` into a validator shaped like
   `resolve_configuration_directory` (`settings.py:386-400`): relative resolves against `REPOSITORY_ROOT`,
   no suffix rule, no existence check. That also relaxes today's absolute-only rule — a strict widening.
2. **`backend/Dockerfile:124,126`** carries the same paths as baked `ENV`. Change it with `compose.yaml`;
   a repointed compose over a stale image is still correct, the reverse is not.
3. `test_return_method_requirements_configuration.py:160` breaks unless `return_method_requirements`'
   comments move verbatim into `return_policy.yaml`. `test_window_policy_is_configuration.py:200,239`
   build an edited single file in `tmp_path`; fix by copying the whole directory and editing the one part
   file, via a shared `tests/harness/configuration_tree.py::copied_configuration_tree(dir, dest)`.
4. Anchors (§1): `support_template` whole, in one part, or the document stops parsing.
5. `scripts/check_openapi_drift.py` untouched — no route, schema or response shape changes.
   `scripts/ci/known_test_failures.json` untouched — it names neither file.
6. `ai/routing/tasks.py` gaining a `configuration.composition` import: run `tests/platform/test_layering.py`,
   `test_no_module_cross_imports.py` and `test_ai_lane_boundary.py` before the drop.
7. Composition order is cosmetic — `sort_keys=True` digests and dict equality make the release id and the
   UNCHANGED path order-invariant (§1). Do not add an order-sensitive assertion.
