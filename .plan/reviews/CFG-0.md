# RV — CFG-0 @ 1d2c8bed — VERDICT: CHANGES_REQUIRED

Worktree `.claude/worktrees/cfg-verify`, branch `feat/cfg-0-audit-fixes`, base `42b0536b`.
Import check: `return_platform.__file__` = `…\.claude\worktrees\cfg-verify\backend\src\return_platform\__init__.py` — inside the worktree.
Read-only: nothing was edited, committed, published or run against the live databases.

## Findings

| ID | Sev | file:line | What | Why it matters | Fix |
|---|---|---|---|---|---|
| F1 | **BLOCKING** | `backend/src/return_platform/configuration/cli/bootstrap_graph_configuration.py:147-176`, `:216-222` | With no baseline for a key, an operator's **deletion** of a mapping entry the file still carries is silently restored by `_fill_absent_leaves`, `unadopted` stays empty so no warning names the key, and `recordable` stamps the key as decided. | `_fill_absent_leaves`'s own docstring promises "that entry comes back on the next publish, **and the warning names the key**". It does not: `unadopted.append(key)` fires only `if filled != value` (`:220`), and a pure deletion makes `filled == value`. The Business tab shipped in this same commit exists precisely so "a removed entry leaves the release" (`BusinessSection.tsx:32-35`); one bootstrap run puts it back with no log line, and the key is then recorded as decided so it never surfaces again. See T1 below. | In `_carry_forward`, compare against the *active* value, not the packaged one: name the key whenever `filled != active_payload[key]` and `key not in known` (it was changed without a baseline), regardless of whether it also equals `value`. Either warn-and-record or warn-and-leave-undecided, but do not do both silently. |
| F2 | **BLOCKING** | `compose.yaml` (removed `PLATFORM_SEED_RECORD_LIMIT`), `.env.example` (same) | The commit message and CFG-DEF-09 say all five removed env variables are "read by nothing". `PLATFORM_SEED_RECORD_LIMIT` **is** read: `backend/config/seed/e2e_seed_manifest.json:10` declares `"recordLimitEnvironmentVariable": "PLATFORM_SEED_RECORD_LIMIT"` and `backend/src/return_platform/operations/seed_manifest.py:65-69` resolves that name through `os.getenv` at import time. | Dropping the passthrough from `compose.yaml` means a value an operator sets in `.env` no longer reaches the backend container, so the low-resource-host cap silently stops working and the full seed counts run — the exact failure the knob exists to prevent. This is a deletion that breaks a live caller. | Restore `PLATFORM_SEED_RECORD_LIMIT` in `compose.yaml` and `.env.example` (it was already `:-` empty by default, so nothing else changes), or delete the `runtimeOptions.recordLimitEnvironmentVariable` mechanism in `seed_manifest.py` as well. Correct the commit message / CFG-DEF-09 to "four". |
| F3 | **BLOCKING** | `backend/src/return_platform/configuration/api/releases.py:264`, `:405`, `:450`, `:508`; `backend/src/return_platform/operations/repository.py:2470-2478` | All four `record_configuration_audit` calls are unguarded and run **after** the authoritative graph write. `resolve_operational_repository` raises `HTTPException(503)` when Mongo is unavailable, and `append_audit` (`repository.py:2181-2198`) propagates any insert failure. | Partial write. On `POST …/promote` the release is already promoted to RELEASED, the head revision has moved and the runtime has adopted it — and the caller gets a 503. `runPublishPipeline` (`frontend/src/api/releasePublish.ts:51-53`) marks the step FAILED and the operator retries, cutting a second release for a change that already landed. Before this commit the configuration API needed only Neo4j; it now fails on a Mongo outage *after* mutating Neo4j. An audit record is a P2 nicety; the release write is the authority. | Make the audit best-effort: wrap the body of `record_configuration_audit` in `try/except Exception` and `logger.error(...)` with the action/actor/target, so the record is lost loudly and the API still reports the write it actually performed. Do not swallow silently. |
| F4 | ADVISORY | `bootstrap_graph_configuration.py:627`, `:688` | `if recordable_baseline:` guards a write of `release_metadata`, which carries **both** `PACKAGED_KEY_DIGESTS` *and* `PACKAGED_DOMAIN_KEY_DIGESTS`. | If every RETURN_PLATFORM key is undecidable (possible — see T2, where `recordable` comes back empty for a key), the AI_GATEWAY and DEPENDENCY_SIMULATION baselines are never recorded either, and those domains stay undecidable forever. That is CFG-DEF-04 re-entering through the back door, coupled to an unrelated domain's state. | Guard each metadata key on its own, or write whenever `recordable_baseline or recordable_domain_baselines`. |
| F5 | ADVISORY | `bootstrap_graph_configuration.py:462` vs `:529` | The `unknown_keys` refusal for RETURN_PLATFORM sits **inside** `if active_payload is not None:`; the equivalent `unknown_units` refusal for the other two domains (`:529`) runs unconditionally. | `--adopt-packaged-key discvoery` (a typo) against a graph with no active release is accepted and silently does nothing, while the same typo qualified as `AI_GATEWAY/…` is refused. An operator answering a warning gets no feedback that the answer missed. | Hoist the RETURN_PLATFORM unknown-key check out of the `active_payload` block, next to where `adopt_requests` is parsed. |
| F6 | ADVISORY | `releases.py:258-259`; `save_domain_config` `:389-412` | `create_release(from_active=True)` copies the whole baseline onto the clone; a subsequent full-document **PUT** replaces the domain without refreshing or clearing it. The PUT handler still exists (CFG-DEF-17 defers its removal to F5). | The baseline then describes the packaged file as of the *cloned* release while the payload is unrelated to it. If the PUT happens to restore a key to the value the file held at that moment, the next bootstrap reads it as unedited and overwrites it. PATCH is safe here (an edit moves the key away from the baseline); a wholesale replace is not. Exposure is API-only — no frontend caller uses PUT (`frontend/src/api/configuration.ts` exposes `createRelease`/`patchDomain`/`promote` only). | On a successful PUT, delete the `packaged_key_digests` entries for that domain (or the whole metadata key), so the next bootstrap decides nothing for it rather than deciding wrongly. Alternatively bring the PUT's removal forward into CFG-1. |
| F7 | ADVISORY | `bootstrap_graph_configuration.py:85-110` | `_units` treats a split key whose value is not a non-empty dict as one unit; `_assemble` then reconstructs it as `{}` and discards the value (T3). | `_assemble(_units({'tasks': [1,2]})) == {'tasks': {}}`. Low reachability now that `_canonical_domain_payload` validates every stored domain, but it is a silent data loss rather than a validation error. | In `_assemble`, when `dot` is empty, assign the value (`payload[head] = value`) instead of relying on `setdefault`; or refuse a non-mapping split key in `_units`. |
| F8 | ADVISORY | `frontend/src/api/mergePatch.ts:58-77` | RFC 7396 cannot express "set this key to null", and `mergePatchOf` inherits that without saying so: a value the operator legitimately sets to `null` in `after` is emitted as `{key: null}`, which `_apply_merge_patch` (`releases.py:372-373`) pops. | For an `X \| None = None` field the round trip is benign (validation restores `null`); for a field with a non-`None` default, the operator's `null` silently becomes the default. Not covered by `mergePatch.test.ts`. | Say it in the module docstring (the RFC's own limitation), and add a test pinning the behaviour so the next editor does not assume otherwise. |
| F9 | ADVISORY | `releases.py:308-316` | `_changed_paths` truncates at 50 paths with no marker in the record. | An audit record for a large publish reads as "these 50 paths changed", which is a statement the data does not support. | Append a sentinel (`"…truncated"`) or record `changedPathCount` alongside. |
| F10 | ADVISORY | `backend/tests/test_configuration_api.py:335-337` | The test named `…stored_in_the_shape_the_bootstrap_compares` asserts key-set and `.keys()` equality against the pre-patch dump — an implementation-shape assertion, not the behaviour it names. | The behaviour that matters is `stored == ReturnPlatformConfiguration.model_validate(stored).model_dump(mode="json")` (what the bootstrap's equality check does). The coercion assertion on `:334` (`"false"` → `is False`) is the good half and does carry the finding. | Replace the two key-set assertions with the model-dump round-trip equality. |
| F11 | ADVISORY | `frontend/src/domains/config/BusinessSection.tsx:198`, `:307` | `const [, setDirty] = useState(false);` — the value is never read; `onDirtyChange={setDirty}` only forces a re-render. | Dead state on an editor that re-renders on every dirty transition. | Drop the state and the `onDirtyChange` prop, or use it (e.g. to guard navigation / label the publish button). |
| F12 | ADVISORY | `bootstrap_graph_configuration.py:719-729` | `--adopt-packaged` help says "every key it declares" (of the business file), but `:527` also applies it to every AI_GATEWAY task and DEPENDENCY_SIMULATION dependency. | An operator reaching for the broad flag to unstick the business domain also discards every AI Control Center task edit — the exact loss CFG-DEF-04 was raised for. | Say "every key of every domain" in the help text, or scope the flag per domain. |

## Answers to the review questions

1. **Carry-forward.** The baseline path (`key in known`) is sound: a value that moved off the recorded digest is kept, and the digest recorded is the packaged one, so the key stays readable as edited on every later run. The `--adopt-packaged` / `--adopt-packaged-key` union (`:459-467`, `:526-533`) is the only overwrite path and it is gated behind a flag, as documented. A dotted task id round-trips correctly (`partition` splits on the first dot — T3). The empty-`tasks` case round-trips by accident but a non-dict one does not (F7). The real defect is F1: `recordable` marks a key decided in the one case where the publish *changed the release without being able to justify it*, and the warning that is supposed to cover that case never fires. F5 and F12 are the flag-handling gaps.
2. **Baseline carry in `create_release`.** Metadata is copied only when `payload.from_active` is true and the active release has metadata (`:258`), matching the existing governed path (`application/release_promotion.py:243`). A stale baseline *can* ride onto a draft whose domains are then replaced by the full PUT — F6.
3. **`_canonical_domain_payload` for existing clients.** No client depends on the PATCH response: `runPublishPipeline` (`releasePublish.ts:38-53`) discards every step's return value and the screens re-read through `queryClient.invalidateQueries` / `runtime.refetch()`. Nothing in `frontend/src/api` reads `data.payload` from a patch, and nothing iterates a domain payload's key order. `taskPatchOf` (`AiControlCenterPage.tsx:2580-2596`) deliberately sends `systemPrompt: null` so the backend recomposes from sections; under the canonical dump the composed prompt is now persisted rather than omitted, which is the intended direction — `TaskConfiguration` composes it during validation (`ai/routing/tasks.py:106-158`), so no disagreement is stored. No regression found here.
4. **Audit records.** Yes — this is a real partial write, worst on promote. See F3; recommendation is best-effort with a logged error, not a 503 after the graph has already moved.
5. **Launcher.** `$LASTEXITCODE` is unaffected by the `try/finally`: assigning `$ErrorActionPreference` does not touch it, and the native exit code propagates out of the `& $run $script` scriptblock invocation. A genuinely failing script still aborts, and a stderr-writing exit-0 script no longer does. Verified on this host (output below).
6. **Frontend.** `mergePatchOf` matches RFC 7396 for deletion (`:62`), whole-array replacement (arrays are values; nulls *inside* an array survive because `_apply_merge_patch` only treats a top-level `None` as a delete), nested recursion with empty-patch elimination (`:72`), and non-object replacement (`:59`). The one divergence is the RFC's own: no way to set a value to null (F8). `subjectId` is `${domainKey}:${key ?? "*"}` (`:179-181`) and is unique across all groups. `agents`, `support_template` and `runtime_integrations` appear **only** in `EDITED_ELSEWHERE` (`:149-153`) and in no `BUSINESS_GROUPS` subject — correctly excluded, and offered as pointers only when the release actually carries them (`:215`). A11y: the section controls are real `<button type="button">` with `aria-pressed={isSelected}` (`:248-251`) and their accessible name from text content (`:259`), inside a `role="group"` with `aria-label={group.title}` (`:243`) under a labelled `<nav>` (`:235`) — keyboard-reachable, Enter/Space activate, state exposed. Gap (not blocking): selecting a section neither moves focus to the editor nor links the two with `aria-controls`, so a screen-reader user gets no announcement that the panel below changed.
7. **Tests.** Both suites green (output below). F10 is the one assertion that pins shape rather than behaviour. `mergePatch.test.ts` has no case for a legitimate `null` in `after` (F8) and none for nested-empty-object elimination. `test_every_release_change_leaves_an_audit_record` pins the exact ordered action list and `patchKeys` — acceptable, but it would not notice F3 because the fixture's Mongo double never fails.
8. **Scope.** Everything in the commit maps to a CFG-DEF finding. Deletions checked by grep over `backend/ frontend/src scripts/`: `ai_max_attempts_per_provider`, `ai_max_concurrency`, `ai_prompt_version` and their four env spellings — zero references, safe. `PLATFORM_AI_VALIDATION_RECEIPT_TTL_HOURS`, `PLATFORM_AI_TRANSIENT_COOLDOWN_INITIAL_SECONDS`, `PLATFORM_AI_TRANSIENT_COOLDOWN_MAX_SECONDS`, `PLATFORM_AI_MAX_RECOVERY_PROBES_PER_REQUEST` — zero references, safe. `PLATFORM_SEED_RECORD_LIMIT` — **not** safe, F2. `backend/assets.yaml` — safe: every surviving `assets.yaml` hit is either `data_assets.yaml` (`settings.py:11`, `Dockerfile:122`) or a `tmp_path` fixture in `tests/test_catalog_loader.py`. The 23k lines of `evidence/config_audit/**` are the audit's own record and untracked-to-tracked by design; `.plan/tracks/CFG.brief.md` + `.ledger.md` are the track's authority. Nothing outside the audit's stated scope.

## Evidence

### T1 — F1 reproduced (`_carry_forward`, no baseline, operator deleted `ship_via.B`)

```
$ PYTHONPATH=<wt>/backend/src backend/.venv/Scripts/python.exe -c "...from bootstrap import _carry_forward,_units,_assemble..."
T1 merged   = {'return_policy': {'ship_via': {'A': 1, 'B': 2}}}
T1 unadopted= ()   <- empty: no warning names the key
T1 recordable keys= ['return_policy']

T2 merged   = {'discovery': {'x': 9, 'y': 2}}  unadopted= ('discovery',)  recordable= []

T3 assemble(units(tasks=list)) = {'tasks': {}, 'retry': {}}
T3 assemble(units(tasks={}))   = {'tasks': {}, 'retry': {}}
T3 dotted id roundtrip         = {'tasks': {'A.B': 1}}
```

T1: the deleted entry is back, `unadopted` is empty (no log line), and the key is recorded as decided.
T2: `recordable` can come back empty — the precondition for F4.
T3: a dotted task id round-trips; a non-dict split key is discarded (F7).

### Launcher (question 5), run on this host, Windows PowerShell 5.1

```
$ErrorActionPreference = "Stop"
$run = { param($s) & cmd /c "echo warn-$s 1>&2 & exit 4" }
$preference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try { & $run "prep.py" } finally { $ErrorActionPreference = $preference }
-->
warn-prep.py
via-scriptblock LASTEXITCODE=4 EAP=Stop
would-throw: Runtime preparation failed
```

and with a stderr-writing script that exits 0:

```
warn
LASTEXITCODE2=0 EAP=Stop
```

Exit code survives the scriptblock and the `try/finally`, `$ErrorActionPreference` is restored to `Stop`, a failing script still throws, and a warning on stderr with exit 0 no longer aborts the stack.

### Backend tests

```
$ cd <wt>/backend
$ PYTHONPATH=<wt>/backend/src .venv/Scripts/python.exe -m pytest tests/test_graph_configuration_bootstrap.py tests/test_configuration_api.py -q -p no:cacheprovider
............................                                             [100%]
============================== warnings summary ===============================
.venv\Lib\site-packages\fastapi\testclient.py:1
  …\backend\.venv\Lib\site-packages\fastapi\testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
28 passed, 1 warning in 11.49s
```

### Frontend tests

```
$ cd <wt>/frontend
$ npx vitest run src/api/mergePatch.test.ts src/domains/config

 RUN  v4.1.10 K:/Projects/Ret/returns_muti_agentic_platform/.claude/worktrees/cfg-verify/frontend

 Test Files  7 passed (7)
      Tests  65 passed (65)
   Start at  16:59:42
   Duration  33.80s (transform 3.10s, setup 25.40s, import 5.02s, tests 12.73s, environment 159.14s)
```

## Judgement

The commit does what it claims on the three defects that were costing the deployment real data: the per-key and per-unit carry-forward with a recorded baseline is the right shape and the AI-task case (CFG-DEF-04) is genuinely closed; the canonical dump on PATCH removes the every-start republish; the launcher fix is correct and I verified the exit-code path on this host rather than taking it on report. The Business tab is a real write surface where there was a read, and `mergePatchOf` is a faithful RFC 7396 generator. What stops a PASS is that the carry-forward's one remaining silent path is exactly the one the new tab makes reachable: an operator deleting a mapping entry through the Business tab has that entry restored on the next bootstrap with no warning and the key stamped as decided, while the code's own docstring says the warning names it — the fix is to judge "did we change the release?" against the active value rather than the packaged one. Alongside that, `PLATFORM_SEED_RECORD_LIMIT` is not the dead variable the audit says it is (`seed_manifest.py` resolves it by name through the manifest), so its removal from `compose.yaml` takes a working knob off the containers; and the new audit records are written unguarded after the authoritative graph write, so a Mongo outage now turns a completed RELEASED promotion into a 503 the operator will retry. All three are small, local fixes. The advisory findings — the coupled metadata guard, the asymmetric unknown-key refusal, the stale baseline surviving a full PUT, and the two test gaps — are worth taking in the same pass but none of them would block on their own.
