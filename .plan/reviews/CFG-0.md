# RV — CFG-0 @ 0e7a2e60 — VERDICT: PASS

Round 2. Round 1 was `CHANGES_REQUIRED` on `1d2c8bed` (3 BLOCKING, 9 ADVISORY); the fixes are
`0e7a2e60 (CFG) step:02`, ledger `.plan/tracks/CFG.ledger.md` step:02.

Worktree `.claude/worktrees/cfg-verify`, branch `feat/cfg-0-audit-fixes`, HEAD `0e7a2e60`.
Import check: `return_platform.__file__` = `…\.claude\worktrees\cfg-verify\backend\src\return_platform\__init__.py` — inside the worktree.
Read-only: nothing edited, committed, published or run against the live databases. Greps scoped to `backend/ frontend/src scripts/`.

The two commits between the rounds carry no source: `23b8a254` (step:01) is evidence + the CFG-1
brief + the drop, `cc43bbd0` is two briefs — confirmed by `git show --stat`. So the code under
review is `1d2c8bed` + `0e7a2e60` and nothing else.

## Round-1 findings and their disposition

| ID | Sev (r1) | file:line | What | Disposition @ `0e7a2e60` |
|---|---|---|---|---|
| F1 | **BLOCKING** | `bootstrap_graph_configuration.py:147-176`, `:216-222` | A key whose release value differed from the file only by leaves the release lacked was filled, *not* named, and stamped decided — so an operator's deletion of a mapping entry came back silently and the key never surfaced again. | **FIXED.** `:225-231` — the `else` branch now appends to `unadopted` unconditionally and `recordable` is guarded by `key not in unadopted`. Docstrings at `:154-166` and `:198-205` corrected to match, and they now name the operator's route (`--adopt-packaged-key` once, then the deletion). Verified by T1/T2 below and by the new test. |
| F2 | **BLOCKING** | `compose.yaml`, `.env.example` | `PLATFORM_SEED_RECORD_LIMIT` removed as "dead" although `seed_manifest.py:65-69` resolves it by name via `e2e_seed_manifest.json:10`. | **FIXED.** Restored in both files, and the `.env.example` comment now names the real reader ("Read by name through backend/config/seed/e2e_seed_manifest.json (recordLimitEnvironmentVariable), not through Settings"), so the next audit does not re-delete it. |
| F3 | **BLOCKING** | `releases.py:264/405/450/508`; `operations/repository.py:2470-2478` | The audit write ran unguarded after the authoritative graph write; a store outage turned a completed RELEASED promote into a 503 and an operator retry would cut a second release. | **FIXED.** `releases.py:302-320` — `resolve_operational_repository` and `append_audit` are both inside `try/except Exception` with `logger.exception("configuration_audit_not_recorded action=… actor=… target=… details=…")`. Verified against the *real* resolver raising 503, not a stand-in — output below. |
| F4 | ADVISORY | `bootstrap_graph_configuration.py:627`, `:688` | `if recordable_baseline:` also gated the *domain* baselines, so an empty business baseline silently dropped the AI-gateway one. | **FIXED.** Both sites now read `if recordable_baseline or any(recordable_domain_baselines.values()):` (`:635`, `:696`). |
| F5 | ADVISORY | `bootstrap_graph_configuration.py:470` vs `:537` | The RETURN_PLATFORM unknown-key refusal sits inside `if active_payload is not None:` (`:404`) while the per-domain one (`:537`) runs unconditionally, so a mistyped bare `--adopt-packaged-key` against a graph with no active release is silently ignored. | **NOT ADDRESSED, and not listed in the ledger's "left as is" set.** Still `:404`/`:470` vs `:537`/`:548`. Carried forward — advisory, not blocking. |
| F6 | ADVISORY | `releases.py:258-259`; `save_domain_config` | A stale baseline copied onto a clone survives a full-document PUT that replaces the domain. | **DEFERRED, reason verified.** `.plan/tracks/CFG-1.brief.md:15` explicitly scopes "the full-document `PUT` handler `save_domain_config` (no consumer; the PATCH path is the write)" for deletion. The deferral is tracked, not merely asserted. Accepted. |
| F7 | ADVISORY | `bootstrap_graph_configuration.py:85-110` | `_assemble` reconstructed a non-mapping split key as `{}` and discarded the value. | **FIXED.** `:100-112` keeps a non-mapping value (`payload[head] = value`) and merges a mapping one. Verified T4 + new test. |
| F8 | ADVISORY | `frontend/src/api/mergePatch.ts:58-77` | RFC 7396 cannot express "set to null"; `mergePatchOf` inherits that silently and it is untested. | **NOT DONE AS DESCRIBED.** The ledger says it is "documented in the module"; `git diff --stat 1d2c8bed..0e7a2e60 -- frontend/` is empty and `mergePatch.ts` carries no such note. The *substantive* argument in the ledger holds (the backend model re-defaults every Optional field the release carries), so the behaviour is acceptable — but the disposition as written is inaccurate. Carried forward as advisory. |
| F9 | ADVISORY | `releases.py:308-316` | `_changed_paths` truncated at 50 with no marker. | **FIXED.** `:323-336` — `_changed_paths` wraps `_all_changed_paths` and appends `"... N more"` past `_CHANGED_PATHS_CAP`. The recursion no longer short-circuits mid-walk, so the count is the true total. |
| F10 | ADVISORY | `backend/tests/test_configuration_api.py:338-340` | The canonical-shape test asserts key-set equality rather than the model-dump round trip it is named for. | **NOT ADDRESSED, and not listed in the ledger's "left as is" set.** Unchanged at `:338-340`. Carried forward — the coercion assertion on `:337` still carries the finding, so the test is not wrong, only weaker than its name. |
| F11 | ADVISORY | `BusinessSection.tsx:198`, `:307` | Unused dirty state. | **DEFERRED, reason verified.** `SupportTemplateSection.tsx:102`/`:162` carries the identical pattern; consistency with the sibling editor is a fair reason to leave it to CFG-3b/CFG-4. Accepted. |
| F12 | ADVISORY | `bootstrap_graph_configuration.py:730-740` | `--adopt-packaged` help understated its reach across the other two domains. | **FIXED.** Help now reads "every key and unit they declare -- business keys, every AI task and limit, every simulated dependency". |

Zero BLOCKING findings remain. Three advisories (F5, F8, F10) are carried into CFG-1; two (F6, F11)
are deferred with reasons I checked against the plan rather than took on report.

## Re-checks requested

### 1. `_carry_forward` names any key that needed filling and records no baseline for it

T1 is the round-1 reproduction, rerun verbatim. T2 is the new half that matters: the workflow the
corrected docstring promises — `--adopt-packaged-key` once to record the baseline, then the
deletion — actually makes the deletion survive.

```
$ cd <wt>/backend
$ PYTHONPATH=<wt>/backend/src .venv/Scripts/python.exe -c "…_carry_forward/_units/_assemble…"
T1 merged   = {'return_policy': {'ship_via': {'A': 1, 'B': 2}}}
T1 unadopted= ('return_policy',)
T1 recordable keys= []

T2 with baseline: merged= {'return_policy': {'ship_via': {'A': 1}}}  unadopted= ()  recordable= ['return_policy']

T3 exact match still decided: () ['return_policy']
T3 key absent from release still adopted: () ['return_policy']

T4 assemble(tasks=list) = {'tasks': [1, 2], 'retry': {}}
T4 assemble(tasks={})   = {'tasks': {}, 'retry': {}}
T4 dotted id roundtrip  = {'tasks': {'A.B': 1}}
T4 split roundtrip      = {'tasks': {'A': 1, 'B': 2}, 'retry': {'n': 3}}
```

T1: was `unadopted=()` / `recordable=['return_policy']` at `1d2c8bed`; now the key is named and no
baseline is recorded. The entry still comes back — nothing can tell a deletion from a code the file
gained — but it is no longer silent and no longer stamped decided.
T2: with a baseline the operator's deletion **survives**, which is what makes the documented route real.
T3: the two decidable cases are untouched, so CFG-DEF-02 does not come back through this change.

The new test `test_a_deleted_entry_the_file_still_carries_is_named_not_stamped_decided`
(`tests/test_graph_configuration_bootstrap.py:527-566`) is behavioural, not a unit stub: it deletes a
real `ship_via_methods` code from the packaged document, drives the actual
`bootstrap_graph_configuration.main()` through the repository double, then asserts the code is back
in the published payload, `return_policy` is in the warning, and `return_policy` is absent from the
written `packaged_key_digests`. That is exactly the three-part claim F1 made.

### 2. Seed limit restored

```
$ git show 0e7a2e60 -- .env.example compose.yaml
+PLATFORM_SEED_RECORD_LIMIT=            (.env.example, with the seed_manifest reader named in the comment)
+  PLATFORM_SEED_RECORD_LIMIT: ${PLATFORM_SEED_RECORD_LIMIT:-}   (compose.yaml:130)
```

Back in both, `:-` empty by default as before, so the default path is unchanged and a value set in
`.env` reaches the containers again.

### 3. `record_configuration_audit` is best-effort

Driven through the **real** `resolve_operational_repository` on a stack with no Mongo — the actual
production failure mode, rather than the `RuntimeError` the new test injects:

```
$ PYTHONPATH=<wt>/backend/src .venv/Scripts/python.exe -c "…resolve_operational_repository / record_configuration_audit…"
resolver raises: 503 Platform MongoDB is unavailable
LOG ERROR configuration_audit_not_recorded action=CONFIGURATION_RELEASE_PROMOTED actor=op target=rel-1 details={"headRevision": 74, "status": "RELEASED"}
record_configuration_audit returned normally -> the request is not failed
```

The 503 is swallowed, the request is not failed, and the log line carries action, actor, target and
the full details payload — the record is recoverable from the log. `except Exception` leaves
`BaseException` (cancellation) to propagate, which is right. Nit, not a finding:
`test_an_audit_store_outage_does_not_undo_a_completed_write` injects a `RuntimeError` where
production raises `HTTPException`; both are caught, and the check above closes the gap.

### 4. `_assemble` and the metadata guard

`_assemble` — T4 above: a non-mapping split key is preserved (`{'tasks': [1, 2]}`, was `{'tasks': {}}`),
and the empty-dict, dotted-id and ordinary split round trips are unchanged. Pinned by
`test_assemble_keeps_a_split_key_whose_value_is_not_a_mapping`. One residual, noted not filed: if a
merged payload ever held both `tasks.A` and a non-mapping `tasks` unit, the latter would overwrite
the former — but the domain model then refuses the payload and the publish falls back to the
packaged file loudly, which is better than the silent `{}` it used to produce.

Metadata guard — `:635` and `:696` now read
`if recordable_baseline or any(recordable_domain_baselines.values()):`, so an empty business
baseline no longer takes the AI-gateway and simulation baselines down with it.

### 5. Anything the fixes broke

The F1 fix changes only what is *recorded and warned*, never what is published: `merged` is computed
identically (the `else` branch still calls `_fill_absent_leaves` and assigns it). The real risk was
that naming every filled key would leave most of the release permanently undecided and re-open
CFG-DEF-02 from the other side. Measured against the live dev-host release from step:01's own
evidence, read-only:

```
$ PYTHONPATH=<wt>/backend/src .venv/Scripts/python.exe -c "…_carry_forward(packaged, after_cfg0/RETURN_PLATFORM.json, release.meta.packaged_key_digests)…"
release id: cfg0-return-method-requirements-receipt-20260911-170203 | head rev: 77
recorded baseline keys: 22
undecided : 6 ['agents', 'clarification_policy', 'policy_evaluation', 'return_eligibility_policy', 'return_policy', 'support_ingress']
recordable: 22 of 28 packaged keys
```

Six undecided, twenty-two still decided — and the six are the operator decisions the audit already
named (`policy_evaluation`, `support_ingress`) plus four the FINAL_REPORT listed as undecided. No
flood of new warnings, no loss of baseline coverage. Nothing else in the commit touches a caller:
the frontend is byte-identical to `1d2c8bed`, and `_changed_paths`' signature change is internal
(both call sites pass two arguments).

Independent lint and type check, not taken from the ledger:

```
$ PYTHONPATH=<wt>/backend/src .venv/Scripts/python.exe -m ruff check src/return_platform/configuration/cli/bootstrap_graph_configuration.py src/return_platform/configuration/api/releases.py tests/test_graph_configuration_bootstrap.py tests/test_configuration_api.py
All checks passed!
$ PYTHONPATH=<wt>/backend/src .venv/Scripts/python.exe -m mypy src/return_platform/configuration/cli/bootstrap_graph_configuration.py src/return_platform/configuration/api/releases.py
Success: no issues found in 2 source files
```

## Backend tests

```
$ cd <wt>/backend
$ PYTHONPATH=<wt>/backend/src .venv/Scripts/python.exe -m pytest tests/test_graph_configuration_bootstrap.py tests/test_configuration_api.py -q -p no:cacheprovider
...............................                                          [100%]
============================== warnings summary ===============================
.venv\Lib\site-packages\fastapi\testclient.py:1
  …\backend\.venv\Lib\site-packages\fastapi\testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
31 passed, 1 warning in 11.82s
```

28 at `1d2c8bed`, 31 now: the three new tests, no test removed, none loosened. The frontend suites
were not rerun — `git diff --stat 1d2c8bed..0e7a2e60 -- frontend/` is empty, so round 1's
`7 passed (7) / 65 passed (65)` still stands for this tree.

## Judgement

All three blocking findings are fixed at the level they were raised, and each one I re-checked by
driving the real code rather than reading the diff: the carry-forward now names a key the moment it
had to fill anything into it and records no baseline for it, so the deletion an operator makes
through the Business tab is loud instead of silent — and, with a baseline in place, survives, which
is the half that makes the documented `--adopt-packaged-key`-then-delete route honest rather than a
consolation. `PLATFORM_SEED_RECORD_LIMIT` is back in both files with its real reader named in the
comment, which is the part that stops the next audit deleting it again. The audit write is
best-effort against the actual 503 the platform raises, and the log line carries the whole record, so
the trail is recoverable and a completed promote can no longer be reported as a failure. The four
advisories the fix took are all genuine improvements, and the two it deferred are deferred against
things I checked — CFG-1's brief really does own the PUT handler, and `SupportTemplateSection`
really does carry the same unused dirty state. What I would not let pass in silence is the ledger's
account of F8: it says the null-versus-delete behaviour is documented in the module, and the frontend
is untouched, so it is not; F5 and F10 were dropped without appearing in the "left as is" list at
all. None of the three changes behaviour, and all three belong in CFG-1 rather than another round
here — but the ledger's disposition list should say so rather than imply work that was not done.
PASS.
