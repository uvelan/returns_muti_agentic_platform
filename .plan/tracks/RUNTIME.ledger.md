# RUNTIME — `_Runtime` grows a faithful `patched`

Base sha: `02f8d45e9fee8aed74ec10831532cf051172b964`
(`git log --oneline -1 refactor/unified-return-platform` →
`02f8d45e (merge) CI: give the backend job the .env it always needed -- RV PASS CI-ENV-4`;
887 commits ahead of `master`.)
Branch: `feat/runtime-patch-double`, worktree `K:\Projects\Ret\rmap-runtime-patch`.

**Interpreter note.** The repo's only venv is installed editable against the *main*
worktree's `src`, so a bare `backend\.venv\Scripts\python.exe` run from this worktree
imports the other branch's production code. Every command below therefore carries
`PYTHONPATH=K:\Projects\Ret\rmap-runtime-patch\backend\src`, which wins over the
editable `.pth`. Verified:

```
$ cd /k/Projects/Ret/rmap-runtime-patch/backend && PYTHONPATH="K:\Projects\Ret\rmap-runtime-patch\backend\src" /k/Projects/Ret/returns_muti_agentic_platform/backend/.venv/Scripts/python.exe -c "import return_platform; print(return_platform.__file__)"
K:\Projects\Ret\rmap-runtime-patch\backend\src\return_platform\__init__.py
```

`.env` is created the way CI does it (`checks.yml:204`, `cp .env.example .env`); it is
gitignored and is not part of the diff.

---

## Step 1 — verify the base, the defect, and every claim in the dispatch

Files touched: `.plan/tracks/RUNTIME.ledger.md` (new).

### 1a. The base

```
$ git log --oneline -1 refactor/unified-return-platform
02f8d45e (merge) CI: give the backend job the .env it always needed -- RV PASS CI-ENV-4

$ git rev-list --count master..refactor/unified-return-platform
887
```

Recent integration work, not an ancestor. Branched from that sha.

### 1b. The failure reproduces, and it is the only one

```
$ PYTHONPATH=...\src ...python.exe -m pytest tests/test_cumulative_support_outcomes.py -q
>           if workflow.patched(_PATCH_STRUCTURED_SUPPORT_DRAFT):
               ^^^^^^^^^^^^^^^^
E           AttributeError: '_Runtime' object has no attribute 'patched'

src\return_platform\workflows\return_case_workflow.py:2247: AttributeError
=========================== short test summary info ===========================
FAILED tests/test_cumulative_support_outcomes.py::test_a_rejected_return_still_opens_no_work_item
1 failed, 50 passed in 2.14s
```

### 1c. The three sites — checked at source, not taken from the brief

```
$ grep -n "workflow.patched" src/return_platform/workflows/return_case_workflow.py
1672:        if not workflow.patched(_PATCH_V3_CLARIFICATION_ROUND_TRIP):
2247:            if workflow.patched(_PATCH_STRUCTURED_SUPPORT_DRAFT):
2294:        if workflow.patched(_PATCH_SUPPORT_TEMPLATE_REVIEW_GATE):
```

Line numbers, constants and patch ids match the dispatch exactly:

```
$ grep -n "^_PATCH_" src/return_platform/workflows/return_case_workflow.py
157:_PATCH_STRUCTURED_SUPPORT_DRAFT: Final = "support-draft-returns-structured-payload"
174:_PATCH_SUPPORT_TEMPLATE_REVIEW_GATE: Final = "support-template-review-gate"
198:_PATCH_V3_CLARIFICATION_ROUND_TRIP: Final = "v3-clarification-round-trip"
```

### 1d. `patched` appears nowhere in the module

```
$ grep -c "patched" tests/test_cumulative_support_outcomes.py
0
```

Confirmed: 50 of 51 pass, so no passing test in the module reaches any of the three
sites. Three gates, six limbs, none exercised from here.

### 1e. Which sites this module can legitimately reach

- **2247 / 2294** — both inside `_open_support`, which the module reaches (that is
  where the traceback lands). Both limbs of both gates are reachable.
- **1672** — inside the `clarification_answered` signal handler
  (`return_case_workflow.py:1639`). This module never sends that signal:

  ```
  $ grep -c "clarification" tests/test_cumulative_support_outcomes.py
  0
  ```

  Reaching it would mean importing V3's `ClarificationAnsweredNotice`, its template
  review state and its `record_clarification_answer` activity into a module about
  *cumulative support outcomes* — building a second copy of a suite that already
  exists (`tests/test_support_template_review_gate.py`, and the replay pair in
  `tests/test_return_case_workflow_replay_compatibility.py`). Not forced. Recorded
  as out of reach for this module with the reason, and covered where it belongs.

### 1f. Sibling doubles

Four test modules substitute the `temporalio.workflow` module:

```
$ grep -rn 'setattr(.*"workflow"' tests/ src/
tests/acceptance/test_items_13_19_reminder_cadence_in_business_time.py:257
tests/policy/test_case_policy_gate.py:538,1156,1216,1243
tests/test_cumulative_support_outcomes.py:1441,1484
tests/test_return_case_workflow_replay_compatibility.py:455
tests/test_support_template_review_gate.py:450,1033,1143
```

Production's full surface on that module:

```
$ grep -o "workflow\.[a-z_]*" src/return_platform/workflows/return_case_workflow.py | sort -u
workflow.all_handlers_finished  workflow.continue_as_new  workflow.defn
workflow.execute_activity  workflow.info  workflow.logger  workflow.now
workflow.patched  workflow.query  workflow.run  workflow.signal
workflow.uuid  workflow.wait_condition
```

- `tests/policy/test_case_policy_gate.py::_Runtime` — complete, `patched` at :308
  with a `patches: bool = True` ctor flag. The house pattern.
- `tests/test_support_template_review_gate.py::_Runtime` — complete, `patched` at
  :152, same flag. `tests/acceptance/test_items_13_19_...py::_RecordingRuntime`
  subclasses it (:148), so the acceptance suite inherits the fix already.
- `tests/test_return_case_workflow_replay_compatibility.py::_LegacyRuntime` —
  `patched` at :393, hardcoded `False` *by design*; it exists to be an old history.
  It omits `wait_condition`/`info`/`all_handlers_finished`/`continue_as_new`, which
  is deliberate and safe: it drives `_open_support` only, and its `patched` asserts
  the marker is one of the two that method consults, so a third would fail loudly.
- `tests/test_cumulative_support_outcomes.py::_Runtime` (:1311) — **the only one
  missing `patched`, and `patched` is the only thing any of them is missing.**

No production defect found. `backend/src/` untouched.

Command: see 1a–1f above. Result: defect and all dispatch claims confirmed at source.
Next step: give `_Runtime` a `patched` on the house pattern.
