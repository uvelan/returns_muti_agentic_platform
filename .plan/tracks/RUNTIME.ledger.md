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

---

## Step 2 — `_Runtime.patched`, faithful and controllable per test

Files touched: `backend/tests/test_cumulative_support_outcomes.py`.

**The decision.** `patched` answers from a constructor argument, defaulting to
`True` because every test in this module starts from no history and is therefore a
new execution -- which is what a real `workflow.patched` returns there. A double
hardcoded to `True` was rejected explicitly: it makes the patched limb reachable and
leaves the un-patched limb exactly as unreachable as an `AttributeError` did, while
looking finished.

The argument is `bool | Mapping[str, bool]`, not `bool`. Production's own
`_PATCH_STRUCTURED_SUPPORT_DRAFT` comment names a live population carrying *some*
markers and not others (`return-case-7b216e58`, `return-case-2328a586`), so the
subsets are real and a single flag cannot express them. It is also the only way to
hold the draft gate fixed while moving the review gate, which is what separating the
two gates' limbs requires. An id missing from a supplied mapping raises `KeyError`
rather than defaulting, so a fourth `workflow.patched` call in `_open_support` would
fail these tests loudly instead of being answered at random.

`self.patch_ids` records which markers were consulted, in order.
`execute_activity` now also records its keyword options in `self.options`: the two
limbs of the draft gate call the same activity with the same input and differ only
in whether `result_type` is pinned, so the activity name alone cannot tell them
apart.

Shape copied deliberately from `tests/policy/test_case_policy_gate.py::_Runtime`
(:308) and `tests/test_support_template_review_gate.py::_Runtime` (:152).

### The module still has exactly one failure, and it is no longer the AttributeError

```
$ PYTHONPATH=...\src ...python.exe -m pytest tests/test_cumulative_support_outcomes.py -q
FAILED tests/test_cumulative_support_outcomes.py::test_a_rejected_return_still_opens_no_work_item
1 failed, 50 passed in 1.72s
```

```
$ ...pytest tests/test_cumulative_support_outcomes.py::test_a_rejected_return_still_opens_no_work_item -q
src\return_platform\workflows\return_case_workflow.py:2371: in _template_review_gate
    drafted: TemplateReviewDraftSet = await workflow.execute_activity(
E           temporalio.exceptions.ActivityError: 'record_template_draft'
1 failed in 1.56s
```

`patched` now works -- the run got past 2247 and 2294 -- and a **second** staleness
in the same test is uncovered underneath it. Diagnosed in step 3.

Next step: the second staleness, then the coverage the fix unblocks.

---

## Step 3 — the second staleness: the test's own premise never fired

Files touched: `backend/tests/test_cumulative_support_outcomes.py`.

**Diagnosis.** With `patched` working, `test_a_rejected_return_still_opens_no_work_item`
runs *into* `_open_support`, which is the one place it must never reach. A probe on
the run (temporary file, removed in step 5) said why:

```
POLICY CaseEligibilityOutcome(state='SKIPPED_BY_CONFIGURATION', route=None, decision=None, reason_codes=(), support_queue=None, failure_reason=None)
CALLS ['record_case_customer_identity', 'record_case_status', 'request_bay_assignment', 'evaluate_case_eligibility', 'draft_support_request', 'record_template_draft']
PATCH_IDS ['support-draft-returns-structured-payload', 'support-template-review-gate']
STATUSES ['AWAITING_BAY']
```

`config/returns/production.yaml:1449` sets `policy_evaluation.enabled: false`
("Suspended on this development host while order-discovery turns are answered
through the MANUAL provider"). `return_case_activities.py:1000` short-circuits on it
before the rule set is even required, and `_evaluate_policy` treats
`SKIPPED_BY_CONFIGURATION` as **cleared**. So the test's rejected return was never
rejected: the case sailed past the gate, and "no work item" would have been true of a
case nobody judged.

Two independent stalenesses in one test, and the ordering hid the second: without
`patched` the `AttributeError` fires at 2247 *before* any assertion, so the
configuration problem could not be seen. Both are harness staleness. **Production is
correct; `backend/src/` is untouched.**

**Fix.** A `POLICY_ENABLED_CONFIGURATION` module constant -- the shipped release with
`policy_evaluation.enabled` and nothing else overridden -- and the test runs against
it. Not invented here: `tests/policy/test_case_policy_gate.py`'s `configuration`
fixture (:365-390) overrides exactly that one value for exactly this reason, and the
comment says so. Same probe, with the gate on:

```
OUTCOME POLICY_REJECTED None
POLICY CaseEligibilityOutcome(state='EVALUATED', route='STANDARD_RETURN', decision='REJECT', reason_codes=('STANDARD_RETURN_CONDITION_FAILED',), support_queue=None, failure_reason=None)
CALLS ['record_case_customer_identity', 'record_case_status', 'request_bay_assignment', 'evaluate_case_eligibility', 'record_case_status']
PATCH_IDS []
STATUSES ['AWAITING_BAY', 'POLICY_REJECTED']
```

Assertions **strengthened**, not weakened -- the previous four were all true of a
suspended gate:

- the policy state is `EVALUATED` and the decision is `REJECT`;
- `runtime.patch_ids == []`. `_open_support` holds the only two `workflow.patched`
  calls on this path, so an empty marker log restates 3A.7's positional guarantee
  directly: nothing past the gate ran at all.

```
$ PYTHONPATH=...\src ...python.exe -m pytest tests/test_cumulative_support_outcomes.py -q
...................................................                      [100%]
51 passed in 1.65s
```

Next step: the coverage the fix unblocks.

---

## Step 4 — the coverage the fix unblocks: both limbs of both reachable gates

Files touched: `backend/tests/test_cumulative_support_outcomes.py`.

Five tests added. Four take the four limbs of the two gates `_open_support` holds;
the fifth guards the double itself.

| test | gate | limb |
|---|---|---|
| `test_a_new_execution_asks_the_draft_activity_for_the_typed_shape` | 2247 | patched |
| `test_an_unmarked_history_decodes_the_bare_string_the_activity_used_to_return` | 2247 | un-patched |
| `test_a_new_execution_consults_the_review_gate_before_it_sends` | 2294 | patched |
| `test_an_unmarked_history_never_reaches_the_review_gate_at_all` | 2294 | un-patched |
| `test_a_patch_marker_this_module_does_not_know_about_fails_loudly` | — | the mapping refuses an unpinned id |

The marker ids are read off production (`workflow_module._PATCH_...`), never
restated, so a renamed marker fails these tests instead of pinning a string nobody
consults.

Discriminators, chosen so each limb is distinguishable from its twin:

- **2247** — the two limbs call the *same* activity with the *same* input and differ
  only in whether `result_type` is pinned, so the assertion is on the recorded
  options. The un-patched test also feeds the activity the bare string a
  pre-`eaed61c` history holds and asserts it reaches Support unchanged: the failure
  this limb guards against is not an exception, it is a thread opened with the wrong
  words in it.
- **2294** — `record_template_draft` in the call log, which cannot happen on the
  un-patched limb. The gated run uses `template_available=False`, one of the gate's
  two documented ways of handing the outcome back, which proves the gate was
  *entered* without rebuilding the reviewer machinery
  `tests/test_support_template_review_gate.py` already owns. The un-patched test
  asserts positionally — the send is the statement immediately after the draft — and
  registers the gate activity anyway, so its absence from the log is a fact rather
  than an accident of the table.

**Site 1672 is not covered from here, and is not forced.** It is inside the
`clarification_answered` signal handler; this module sends no such signal and holds
no V3 clarification vocabulary. Covered where it belongs
(`tests/test_support_template_review_gate.py`,
`tests/test_return_case_workflow_replay_compatibility.py`).

```
$ PYTHONPATH=...\src ...python.exe -m pytest tests/test_cumulative_support_outcomes.py -q
........................................................                 [100%]
56 passed in 1.39s
```

### Injection — the tests were flipped against, not just run

Three mutations of `_Runtime.patched`, each applied to a green tree and reverted.

**A. `return True` for every marker** (the "convenient double" the dispatch warns
about — the one that stops the `AttributeError` and adds nothing):

```
E       AttributeError: 'str' object has no attribute 'text'
E       AssertionError: assert 'record_template_draft' not in ['record_case_customer_identity', 'record_case_status', 'request_bay_assignment', 'evaluate_case_eligibility', 'draft_support_request', 'record_template_draft', ...]
E       Failed: DID NOT RAISE KeyError
FAILED tests/test_cumulative_support_outcomes.py::test_an_unmarked_history_decodes_the_bare_string_the_activity_used_to_return
FAILED tests/test_cumulative_support_outcomes.py::test_an_unmarked_history_never_reaches_the_review_gate_at_all
FAILED tests/test_cumulative_support_outcomes.py::test_a_patch_marker_this_module_does_not_know_about_fails_loudly
3 failed, 53 passed in 1.86s
```

Exactly the two un-patched-limb tests, each on its own claim. The draft one fails
with `'str' object has no attribute 'text'` — the typed branch taken against a bare
string, which is the production wedge itself. The two patched-limb tests stay green,
correctly: `True` is what they asked for. **This is the direct evidence that an
always-True double would have been half a fix.**

**B. `return False` for every marker:**

```
E       TypeError: draft_support_request returned SupportRequestDraft, which is not a shape this workflow has ever recorded
E       AssertionError: assert False is True
FAILED tests/test_cumulative_support_outcomes.py::test_a_new_execution_asks_the_draft_activity_for_the_typed_shape
FAILED tests/test_cumulative_support_outcomes.py::test_a_new_execution_consults_the_review_gate_before_it_sends
FAILED tests/test_cumulative_support_outcomes.py::test_an_unmarked_history_never_reaches_the_review_gate_at_all
FAILED tests/test_cumulative_support_outcomes.py::test_a_patch_marker_this_module_does_not_know_about_fails_loudly
4 failed, 52 passed in 2.60s
```

Both patched-limb tests redden. The un-patched *review* test reddens too, on the
neighbouring draft gate that B also flipped — collateral, and reported as such; its
own claim is what fires under A.

**C. `return not ...` — every decision inverted:**

```
E       TypeError: draft_support_request returned SupportRequestDraft, which is not a shape this workflow has ever recorded
E       AttributeError: 'str' object has no attribute 'text'
E       AssertionError: assert False is True
FAILED ...::test_a_new_execution_asks_the_draft_activity_for_the_typed_shape
FAILED ...::test_an_unmarked_history_decodes_the_bare_string_the_activity_used_to_return
FAILED ...::test_a_new_execution_consults_the_review_gate_before_it_sends
FAILED ...::test_an_unmarked_history_never_reaches_the_review_gate_at_all
FAILED ...::test_a_patch_marker_this_module_does_not_know_about_fails_loudly
5 failed, 51 passed in 1.87s
```

All five new tests redden; **all 51 pre-existing tests stay green**, which is the
same finding step 1 made by grep, now made by execution: not one of them depends on
a patch answer, because not one of them reaches a gate.

A note worth recording, found by injection rather than by reading. Under B and C the
legacy draft limb fails with `TypeError: draft_support_request returned
SupportRequestDraft, which is not a shape this workflow has ever recorded`. That is
the harness, not a production defect: `SupportRequestDraft` is declared twice on
purpose (workflow module and `return_case_activities`, "which the workflow sandbox
may not import"), the payload converter bridges them on a real server, and this
double has no converter. It is why the un-patched draft test feeds the activity a
plain string — which is what a pre-`eaed61c` history actually holds — rather than
relying on in-process object identity.

Temporary probe file `tests/test_zz_probe.py`, used in steps 3–4, was deleted; the
tree is clean apart from the test module.

Next step: the allowlist.

---

## Step 5 — the allowlist entry is deleted, and the deletion is verified both ways

Files touched: `scripts/ci/known_test_failures.json`.

`suites.backend.known_failures` is now `[]`. The `$comment` is rewritten rather than
left describing an entry that is gone — a stale comment on a self-pruning list is the
next person's wrong mental model.

`assert_known_failures.py:88` reads `known_failures` with a `.get(..., [])` default
and `allowed` is then an empty set, so every failure is `unexpected` and the job
fails on any of them. An empty list is the correct, strictest state; nothing needed
to change in the script.

### The CI allowlist self-test still passes

```
$ python scripts/ci/test_assert_known_failures.py
...
  [ok  ] rejects a missing report

all negative controls passed
```

### And the gate is load-bearing, checked in both directions

Against the real JUnit report from the fixed suite:

```
$ python scripts/ci/assert_known_failures.py --suite backend --report /tmp/after.xml
5249 tests ran, 0 failed, 0 allowlisted
only the 0 known, still-failing tests failed
exit=0
```

Against the real JUnit report from the *base tip* — the exact run the deleted entry
used to excuse:

```
$ python scripts/ci/assert_known_failures.py --suite backend --report /tmp/before.xml
5244 tests ran, 1 failed, 0 allowlisted
::error::NEW FAILURE (not on the allowlist): tests.test_cumulative_support_outcomes::test_a_rejected_return_still_opens_no_work_item
exit=1
```

The second is the one that matters: the list no longer excuses anything, and the
entry could not be quietly re-added without that failure returning.

### Full backend suite, before and after

Measured by checking the two changed files back to `02f8d45e`, running, and
restoring — so the only difference between the two runs is this branch's diff.

Before (base tip):

```
$ PYTHONPATH=...\src ...python.exe -m pytest -q --junitxml=/tmp/before.xml
FAILED tests/test_cumulative_support_outcomes.py::test_a_rejected_return_still_opens_no_work_item
1 failed, 5232 passed, 11 skipped, 514 deselected, 2 warnings in 250.07s (0:04:10)
```

After:

```
$ PYTHONPATH=...\src ...python.exe -m pytest -q --junitxml=/tmp/after.xml
5238 passed, 11 skipped, 514 deselected, 2 warnings in 255.06s (0:04:15)
```

`5232 + 1 = 5233` before, `5238` after: the one red is green and five tests are new.
The 11 skips and 514 deselections are unchanged — no test was skipped, xfailed,
weakened or deleted.

### Lint

```
$ python -m ruff check .
All checks passed!
$ python -m ruff format --check .
1159 files already formatted
```

### Rule 13 — the gate that runs every guard added here

Everything added is in `backend/tests/test_cumulative_support_outcomes.py`, which has
no marker, so `pytest_collection_modifyitems` puts it in the **normal** suite. The
default run is `-m "not live_infra and not browser"` (`pyproject.toml:139`), which
*selects* it. `checks.yml:244` runs that suite on every push and then hands the
report to `assert_known_failures.py --suite backend`, which now allows nothing.

So: **CI-gated, not live-infra-gated.** All five new tests are in the 5238 above and
in the 5249 the gate counted. The allowlist change is itself gated twice — by
`checks.yml:103` (the self-test) and by `checks.yml:244` (the real report).

Nothing added here is behind a `live_infra` marker, and nothing added here is
ungated.
