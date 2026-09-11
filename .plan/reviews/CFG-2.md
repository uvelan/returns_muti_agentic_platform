# RV — CFG-2 @ e42d92e6 — VERDICT: PASS

Worktree `.claude/worktrees/cfg-2`, branch `feat/cfg-2-config-split`, base `73c276d2`.
`return_platform.__file__` = `<wt>\backend\src\return_platform\__init__.py` (resolves inside cfg-2).
Read-only throughout: no edit to code or config, no publish, no write to any database.
`git status --short` in the worktree was empty before and after every command below.

## Findings

| ID | Sev | file:line | What | Why | Fix |
|---|---|---|---|---|---|
| F1 | ADVISORY | `configuration/composition.py:62,173` + `configuration/return_configuration.py:2105-2111` | The transitional `ignore=frozenset({"production.yaml"})` survives the deletion commit. Proved live: a file re-added at `backend/config/returns/production.yaml` is **silently excluded** from the no-globbing scan instead of refused (my probe G below). | The no-globbing rule is the composer's whole manifest stance, and this is a permanent hole for exactly the filename an operator is most likely to restore off an old branch. It loads nothing today, so it is not a correctness defect at HEAD. | Delete the `ignore` parameter, its two call sites and the two tests that pass it (CFG-6, or a one-line follow-up). The monolith is gone at HEAD, so nothing depends on it. |
| F2 | ADVISORY | `backend/config/ai_gateway/tasks/*.yaml` (deviation 1) | 86 alias sites were materialised into 9 task files. I verified every one byte-identical to the anchor body (below). But from here nothing keeps the duplicates in sync: editing e.g. `voice` means touching six files with no test that notices a missed one. | The shared-prompt single-edit-point is genuinely gone, as the ledger says. Accepted as the smallest deviation that satisfies one-task-per-file, but the cost is now unguarded. | CFG-6: either reintroduce sharing at the composer layer (an `includes:` in a task file), or add a test asserting the duplicated blocks stay equal across the tasks that carry them. |
| F3 | ADVISORY | `scripts/validate_stage4l_production.py:17` | Pre-existing broken import (`ReturnAgentRegistry` exists nowhere) means the repointed path at `:59` is never actually exercised. Confirmed pre-existing (`grep -rn "class ReturnAgentRegistry"` empty). | The one owned line is correct by inspection but has no run behind it. | Orchestrator/CFG-7: fix or retire the script. Not CFG-2's surface. |
| F4 | ADVISORY | `scripts/ci/known_test_failures.json` | `suites.backend.known_failures` is still `[]` against 42 real failures. | Pre-existing; already raised as F7 in `.plan/reviews/CFG-1.md` and carried to CFG-7. RV re-confirms the count is unchanged by this lease. | CFG-7. |
| F5 | ADVISORY | `docs/evidence/stage4o_complete_audit/generate_audit_artifacts.py:264,266` (and the four matrices it generates) | A live generator still emits `backend/config/returns/production.yaml` / `backend/config/ai_gateway.yaml` as "Source evidence" paths that no longer exist. | Functional strings, not prose — but the brief scopes docs to `docs/configuration/**`, so out of Owns. Nothing loads them. | Docs pass / CFG-7. |
| F6 | ADVISORY (informational) | `configuration/settings.py:424-445` | `validate_packaged_configuration_path` **does not** refuse a relative path (it resolves against `REPOSITORY_ROOT`) and **does not** refuse a non-existent path. | This is exactly what the brief (Scope 2) and the design (§4 risk 1) mandate — "relative resolves against `REPOSITORY_ROOT`, no suffix rule, no existence check" — so it is compliant, not a defect. Worth naming: a typo'd `PLATFORM_RETURN_CONFIGURATION_PATH` is now caught by the loader's `resolve(strict=True)` at first load rather than at `Settings()` construction. | None required. Named so the widening is a recorded decision, not a discovery. |

**Zero BLOCKING.**

## Q1 — equivalence is real, and against git, not a copy

`test_packaged_configuration_composition.py:55-70` fetches the pre-split bytes with
`git show 73c276d2:backend/config/returns/production.yaml` (`cwd=REPOSITORY_ROOT`), falling back to the
frozen copy only when git cannot answer; `:105-132` asserts the frozen copies are byte-equal to what git
returns, so the fallback cannot drift. Both split tests compare `model_dump(mode="json")`.

```
$ pytest tests/configuration/test_packaged_configuration_composition.py tests/configuration/test_settings_configuration_paths.py -v
19 passed in 3.90s   (12 composition + 7 settings, every test named PASSED)
```

My own independent check — `git show` both old files, load them and both directories through the
worktree's loaders, assert dump equality myself:

```
REPOSITORY_ROOT: ...\.claude\worktrees\cfg-2
=== backend/config/returns/production.yaml ===
  git-show sha256: f23eb2dbe806bec672ce5afded48701b3a41f50d8cf7ac6672838011a0416b4b
  dump equal: True        json-canonical equal: True
  loaded.path: ...\cfg-2\backend\config\returns
  new sha256: c7032fe6c5bfb0245e8150688702ae85ce98b6af8745b3ceef92d2c366199235
=== backend/config/ai_gateway.yaml ===
  git-show sha256: f91a1cb61c868b31396efc08c6858e423f71848f891cce8a0869d890c0941cde
  dump equal: True        json-canonical equal: True
  loaded.path: ...\cfg-2\backend\config\ai_gateway
  new sha256: a71f73b3dcc14f880ecca8a5b0d4dc28679b5ad0074fecf2418d980fe9a82420
```

Stronger than the lease's own proof, and the one I would not take on trust: **pre-model** raw equality,
which `model_dump` could otherwise launder (a section the model drops would survive a dump comparison).

```
RETURNS raw safe_load equal: True
GATEWAY raw safe_load equal: True
```

23 sections across 8 parts (1+1+3+3+3+6+3+3), matching the design's table exactly; all 21 `&tpl_sec_*`
anchor/alias lines are inside `support.yaml` alone, so no anchor crosses a part boundary.

## Q2 — anchor materialisation: 86 sites, zero drift

The original had 25 anchors and 86 alias references across 9 sibling tasks. For each alias site I pulled
the anchor's own source block from `73c276d2:backend/config/ai_gateway.yaml` and looked for it verbatim
in the new task file (after the uniform 4-space dedent that per-task files require, since the old text
sat under `tasks:`):

```
alias sites checked: 86
materialised byte-identical (after uniform 4-space dedent): 86
drift: NONE
residual alias references in new tree: none
```

The anchor *definitions* remain tagged in `ORDER_AGENT_REASONING_V1.yaml` (now unreferenced, harmless).
The raw `safe_load` equality above independently confirms no resolved value moved. See F2 for the cost.

## Q3 — the composer's rules are real; `ignore` is now dead

Five error tests exist and pass (`:221-296`): duplicate section naming both files, listed-but-missing,
present-but-unlisted, missing `index.yaml`, casefold-colliding entry stems. I probed the rules the tests
do *not* cover, against copies of the real `returns/` tree:

```
unknown section in a part: ValidationError: 1 validation error for ReturnPlatformConfiguration
part root not a mapping  : ValueError: ...agents.yaml is not a mapping at its root; every part of ...index.yaml must be a YAML object
path escapes the directory: ValueError: ...index.yaml lists ../escape.yaml, which escapes ...returns
tree over 1 MB           : ValueError: configuration directory ...returns exceeds 1 MB (1907527 bytes)
index missing a document key: ValueError: ...index.yaml is missing document key(s): schema_version
unlisted yaml in a subdirectory: ValueError: ...sub\stray.yaml is not listed in ...index.yaml; this loader does not glob
re-added production.yaml (ignore set): NO ERROR RAISED  <-- gap
```

There is no "unknown section" rule in the composer by design: an unrecognised key becomes a document key
and the strict pydantic model refuses it, exactly as it did for a single file. Every other rule fires and
names the offending path. The last line is F1 — **advisory, not blocking**: the brief's own non-negotiable
"deletion commit contains no other change" is what forced `ignore` to outlive the monolith, the parameter
loads nothing at HEAD, and the fix belongs to the next lease.

## Q4 — settings

`git diff` on `settings.py` removes exactly `return_configuration_path` and `ai_gateway_configuration_path`
from `validate_catalog_path`'s field list and adds `validate_packaged_configuration_path` for those two.
The old validator's body is untouched, and the three fields still on it — `catalog_path`,
`schema_registry_path`, `dependency_simulation_configuration_path` — lose nothing. Two of the seven new
tests pin that directly (`test_unrelated_catalog_paths_still_enforce_the_old_absolute_yaml_rule`,
`..._still_refuse_a_relative_value`, both PASSED above). A directory and a `.yaml` file are both accepted;
relative and non-existent are accepted too — see F6, which is the brief's instruction, not a slip.

## Q5 — readers

`compose.yaml:14,16` → `/app/config/returns`, `/app/config/ai_gateway`; `backend/Dockerfile:124,126` the
same baked `ENV`; `.env.example:204,297` repointed; `scripts/validate_stage4l_production.py:59` and
`scripts/validate_stage4n_ai_gateway.py:44` take the directory. `backend/scripts/` names neither file
(grep empty). `grep -rnI "production\.yaml\|ai_gateway\.yaml" backend/ scripts/ docs/ compose.yaml
.env.example` leaves **no functional path construction**: every remaining hit is prose in a docstring,
comment, `.md` narrative or the frozen `backend/tests/data/pre_split/` copies. The two that look
functional at a glance are not — `configuration/application/loader.py:101` is a docstring on `load_file`,
which has **zero callers** (`grep -rn "load_file(" backend/src backend/tests` returns only its own
definition); `docs/evidence/stage4o_complete_audit/generate_audit_artifacts.py:264,266` is F5.

## Q6 — bootstrap untouched, and the live simulation says UNCHANGED

```
$ git diff --stat 73c276d2..HEAD -- .../cli/bootstrap_graph_configuration.py .../runtime_activation.py
(empty)
```

Only four `.py` files under `backend/src` changed at all — `ai/routing/tasks.py`,
`configuration/{composition,return_configuration,settings}.py` — so the brief's "must not touch" holds.

Read-only simulation against the live graph (worktree `.env`, `bolt://localhost:17687`), replaying
`main()`'s own functions — `_key_digests`, `_units`, `_carry_forward`, `_drop_retired_keys`, `_assemble`,
the same `payload_checksum`, the same `active_payloads == domain_payloads` test. Only
`get_active_release` / `get_domain_config` / `get_all_domain_configs` / `get_head_revision` were called;
nothing was written and nothing published.

```
return_configuration_path : ...\cfg-2\backend\config\returns
head_revision: 78
active release_id: return-platform-d2f7787021d4622d   status: RELEASED
composed returns sha256 : c7032fe6c5bfb0245e8150688702ae85ce98b6af8745b3ceef92d2c366199235
composed gateway sha256 : a71f73b3dcc14f880ecca8a5b0d4dc28679b5ad0074fecf2418d980fe9a82420
RETURN_PLATFORM baseline recorded: yes
RETURN_PLATFORM unadopted (would warn adopt-packaged-key):
  ['agents', 'clarification_policy', 'policy_evaluation', 'return_eligibility_policy',
   'return_policy', 'support_ingress']
AI_GATEWAY unadopted units: []      DEPENDENCY_SIMULATION unadopted units: []
computed release id: return-platform-d2f7787021d4622d
=== WOULD PRINT: graph_configuration_status=UNCHANGED
```

The computed release id is **byte-identical to the active one**, and the six warned keys are exactly the
six `.plan/reviews/CFG-1.md:38` already names — no new `adopt-packaged-key` warning. The digest check
stated directly, composed document against the release's recorded baselines:

```
RETURN_PLATFORM: packaged keys 26 | baseline recorded for 20
  digest == recorded baseline: 20
  no recorded baseline      : the six keys above
  baseline differs (file edited since publish): []
AI_GATEWAY: units 33 | baseline units 33 | identical digests: 33 | differing: []
```

Every recorded baseline still matches the composed document, for both domains. The split moved no digest.

## Q7 — the 42 failures: same ids, same modules, nothing new

Neither the ledger nor `.plan/reviews/CFG-1.md` pasted the 42 node ids, so I ran them myself. Full suite,
this worktree, `production.yaml` and `ai_gateway.yaml` deleted:

```
$ cd backend && PYTHONPATH=$WT/backend/src .venv/Scripts/python.exe -m pytest tests -q \
    -p no:cacheprovider --ignore=tests/configuration/test_concurrent_activation.py -rf
42 failed, 5295 passed, 10 skipped, 515 deselected, 2 warnings in 302.44s (0:05:02)
```

Identical to the ledger's step:07 line. Distribution by module:

```
 9 tests/dynamic_knowledge/test_confirmation_starts_the_case_workflow.py
 6 tests/dynamic_knowledge/test_order_discovery_smoke_net.py
 4 tests/dynamic_knowledge/test_reasoning_stage_prompts.py
 2 tests/dynamic_knowledge/test_turn_temporal_grounding.py
13 tests/test_ai_a_rejected_parse_is_repaired_on_its_own_route.py
 3 tests/test_ai_route_balancing_design.py
 2 tests/test_ai_single_dispatch_boundary.py
 2 tests/test_enforced_contracts_are_disclosed.py
 1 tests/test_keyless_reasoning_is_held_for_a_human.py
```

Running just those nine modules gives `42 failed, 112 passed in 30.10s` — the *same pair of numbers*
`.plan/reviews/CFG-1.md:163` recorded for the **base** worktree at `06b43b18` (`42 failed, 112 passed in
28.69s`), with the same per-module split. No id differs; no difference, so nothing BLOCKING. Every failure
is `StandardReasoningUnavailable … PROVIDER_UNAVAILABLE` or a routing/temporal assertion — none touches
configuration loading. 5295 = CFG-1's final 5276 + the 19 tests this lease adds, the exact acceptance bound.

Lint/types re-run by me: `ruff check` → *All checks passed!*; `ruff format --check` → *84 files already
formatted*; `mypy src/return_platform/configuration src/return_platform/ai/routing` with `PYTHONPATH` set →
**Success: no issues found in 53 source files** (the ledger's "47 pre-existing errors" does not reproduce
here — the difference is the unset `PYTHONPATH` in its run; either way nothing is owed by this lease).
`python scripts/check_openapi_drift.py` → `"diffs": [], "status": "PASS", "exit_code": 0`.

## Q8 — scope and docs

The deletion commit `fa7d39f7` contains exactly two deletions and nothing else — verifiable at a glance.
Outside `backend/config/**` and `backend/tests/**` the changed set is `.env.example`, `CFG.ledger.md`,
`backend/Dockerfile`, the three READMEs, the four owned `.py` files, `compose.yaml`, the two validate
scripts, `drop.json`, and `docs/evidence/stage4n_ai_gateway/validation_summary.json` (two timestamp lines;
`checksPassed: 9` unchanged) — all owned or declared byproducts.

I read every test diff. The seven raw-text readers are substantive but sound: `test_item_10` reads
`support.yaml` and keeps its "a deliberate closed default and a deleted key are not the same release"
assertion verbatim; `test_return_method_requirements_configuration` must keep the **whole** document
because `_payload_with_rows` overrides only `return_policy`, so composing is the right answer, and the
`OPERATOR REVIEW REQUIRED` banner-position check reads `return_policy.yaml` and still passes;
`test_support_gate_configuration` correctly splits into two part files because `support_gate` and
`return_case` landed in different ones; `test_window_policy_is_configuration` copies the tree and edits
one part instead of one file. `test_support_ai_gateway_tasks` swaps a raw `safe_load` +
`model_validate` for `load_ai_gateway_configuration` — the same validation, not weaker. For the 53
mechanical files I filtered the diff down to every line that is *not* a path substitution: what remains is
four multi-line calls collapsed onto one line and four prose corrections. **No assertion was weakened,
removed, or loosened anywhere.**

Docs check out against the tree: `backend/config/README.md`'s new "Composed directories" section states
the `index.yaml` shape and the four load errors accurately (its `ai_gateway/` inline list —
`circuitBreaker`, `retry`, `rateLimits`, `providerLimits`, `modelContexts` — matches `index.yaml`, whose
`entries.tasks` lists exactly 25 files); `configuration/README.md:24-26` and `ai/README.md:30,122` both
name the directories and point at `config/README.md`. `backend/config/workflows/return_session.yaml:12,35`
is updated.

## Judgement

The claim this lease rests on is that moving bytes between files changed nothing an operator would see,
and it is true under every test I could put to it that the lease did not choose for itself. The composed
documents equal the pre-split files not merely after `model_dump` but at the raw `safe_load` level, which
is the comparison that cannot be laundered by the model; every one of the 86 materialised anchor sites is
byte-identical to the block it replaced, with no alias left unresolved; the live graph, asked what the
bootstrap would do with these directories, computes the *same release id* the RELEASED release already
carries, with all 20 recorded RETURN_PLATFORM baselines and all 33 AI_GATEWAY unit digests still matching
and only the six long-known undecided keys warned; and the 42 failures reproduce as the same nine modules
with the same 42-failed/112-passed split RV measured on the base for CFG-1. The three deviations were
recorded rather than hidden, and all three were forced by real gaps in the design note — the anchors it
never inspected, the monolith that already lived inside its own replacement directory, and the 53 tests
that name the old file straight to a loader. My only reservations are cleanliness, not correctness: an
`ignore` set that now silently excuses one filename from the very rule it exists to enforce, and 25
duplicated prompt blocks with nothing keeping them in sync. Both belong to CFG-6, and neither can load a
wrong value today. Zero BLOCKING — **PASS**.
