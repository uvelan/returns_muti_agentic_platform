# RV review — RUNTIME slice (`_Runtime.patched` double + the defect underneath), round 1

- **Branch:** `feat/runtime-patch-double`, head `54b269fa`
- **Base:** `2d0e3d65` — verified a genuine ancestor (`git merge-base --is-ancestor` → 0).
- **Trunk:** `refactor/unified-return-platform` @ `c8eac86d` (read from the ref, not from the dispatch).
- **Diff:** `git diff 2d0e3d65..54b269fa` — 3 files, +982/−10:
  `.plan/tracks/RUNTIME.ledger.md` (new), `backend/tests/test_cumulative_support_outcomes.py`,
  `scripts/ci/known_test_failures.json`.
- **Reviewer:** RV — Date: 2026-08-31
- **Method note:** every backend command in this review ran with
  `PYTHONPATH=<worktree>\backend\src`. The single venv is installed editable against the
  main worktree, so a bare interpreter call imports whichever branch the main tree is on.

## Verdict: PASS

Zero findings. I treated every claim as unproven and re-derived it. The two that carry the
branch — **the second defect underneath the `AttributeError`**, and **the faithfulness of the
double** — both hold, and both reproduce exactly. The merge onto current trunk is clean and
the new suite-size floor is not disturbed.

The interesting claim is true, and it is the right shape of true: the fix addresses the
defect the `AttributeError` was hiding, not merely the missing method.

---

## 1. The second defect — the test's name had been false for as long as it had been red

**Verified at source, and then confirmed by running the base tip.** This is the most
important thing on the branch and it is correct in every part.

The chain, each link checked in `backend/src/return_platform/workflows/return_case_workflow.py`
(unmodified by this branch):

1. `backend/config/returns/production.yaml:1449-1454` ships `policy_evaluation.enabled: false`,
   with `disabled_reason` "Suspended on this development host while order-discovery turns are
   answered through the MANUAL provider".
2. The gate answers `SKIPPED_BY_CONFIGURATION` (`PolicyGateState`, :328).
3. At `:1991`, `SKIPPED_BY_CONFIGURATION` **`return True`** — it *clears* the gate. The comment
   there is explicit that no status is set, because "`POLICY_APPROVED` would be a verdict nobody
   reached". So the case proceeds.
4. `test_a_rejected_return_still_opens_no_work_item` bound `SHIPPED_CONFIGURATION`, so no return
   was ever rejected. Its three assertions ("no work item", "no drain", "gate evaluated") were
   all true of a case that had **simply not been judged**.

**The runtime proof is the base tip's own failure location.** I ran the module at `2d0e3d65`:

```
E           AttributeError: '_Runtime' object has no attribute 'patched'
src\return_platform\workflows\return_case_workflow.py:2247: AttributeError
FAILED tests/test_cumulative_support_outcomes.py::test_a_rejected_return_still_opens_no_work_item
1 failed, 50 passed
```

Line 2247 is inside `_open_support` (`async def _open_support` begins at :2231) — the one method
this test must never reach. The `AttributeError` was raised *by the work-item-opening path*,
executing inside a test whose entire claim is that that path does not run. That is the defect,
stated by the stack trace itself. It could not be seen while the line was on the allowlist,
because an allowlisted failure is a line in a log nobody reads the trace of.

**The fix addresses it.** `POLICY_ENABLED_CONFIGURATION` (test file :120-141) is
`SHIPPED_CONFIGURATION.model_copy` overriding exactly `enabled`/`disabled_reason` and nothing
else — derived from the release, not hand-built, so the rule set under test is the one a
container would activate. The test now also asserts the gate's *verdict*, not just its
downstream silence:

```python
assert harness.instance._state.policy.state == PolicyGateState.EVALUATED.value
assert harness.instance._state.policy.decision == PolicyDecisionName.REJECT.value
assert harness.runtime.patch_ids == []
```

The last line is the positional guarantee restated through the new instrument: `_open_support`
holds the only two `workflow.patched` calls on this path, so an empty marker log proves nothing
past the gate ran. Correct, and stronger than the assertions it supplements.

The precedent it cites is real and quoted accurately: `backend/tests/policy/test_case_policy_gate.py:365-376`
overrides the same single value for the same stated reason, and the skip *itself* is covered
there. The reasoning was copied, not re-derived — which is the right call.

## 2. The double is faithful, not convenient — mutation re-run

`patches: bool | Mapping[str, bool] = True` (test file :1355), answered from the constructor at
:1396. Not hardcoded.

**I re-ran the mutation.** Replaced the body with `self.patch_ids.append(patch_id); return True`
on a green merged tree:

```
E       AttributeError: 'str' object has no attribute 'text'
src\return_platform\workflows\return_case_workflow.py:2304: AttributeError
E       Failed: DID NOT RAISE KeyError
FAILED tests/test_cumulative_support_outcomes.py::test_an_unmarked_history_decodes_the_bare_string_the_activity_used_to_return
FAILED tests/test_cumulative_support_outcomes.py::test_an_unmarked_history_never_reaches_the_review_gate_at_all
FAILED tests/test_cumulative_support_outcomes.py::test_a_patch_marker_this_module_does_not_know_about_fails_loudly
3 failed, 53 passed in 2.38s
```

Both un-patched-limb tests redden, and the draft one fails with **exactly** `AttributeError:
'str' object has no attribute 'text'` — the production wedge itself, the typed branch taken
against a bare string. This is the direct evidence the dispatch asked for: an always-`True`
double would have left the un-patched limb as unreachable as the `AttributeError` did, and it is
detected.

**One imprecision, not a finding.** The ledger's prose at :323 says "Exactly the two un-patched-limb
tests"; three tests redden, the third being the `KeyError` guard. The ledger's own pasted
transcript immediately above that sentence lists all three and reports `3 failed, 53 passed`, so
the evidence is complete and honest and my run reproduces it line for line. The summary sentence
undercounts in the *safe* direction — the mutation is caught more thoroughly than claimed. Noted,
not charged.

The mutation was reverted (`git checkout --`) and the tree confirmed clean before the suite run
below. It was applied in a throwaway merge worktree, never in the branch worktree or the main tree.

## 3. Coverage of what it unblocks — including site 1672, the one resting on another file

Three `workflow.patched` sites confirmed by grep at :1672, :2247, :2294 for
`_PATCH_V3_CLARIFICATION_ROUND_TRIP`, `_PATCH_STRUCTURED_SUPPORT_DRAFT`,
`_PATCH_SUPPORT_TEMPLATE_REVIEW_GATE`. Sites :2247 and :2294 are both inside `_open_support`
(:2231); :1672 is not, and is not on this module's path.

**Site 1672, verified specifically.** `backend/tests/test_support_template_review_gate.py:1265`
is `test_an_unmarked_history_keeps_the_behaviour_it_recorded`, wired with `patches=False` and
asserting against `_PATCH_V3_CLARIFICATION_ROUND_TRIP` by importing the production constant. Its
`_Runtime.patched` (:152) answers from the same constructor flag. The patched limb is the default
for the rest of that class. The claim was checked rather than assumed, and the check holds — the
file is untouched by this branch, so the coverage is pre-existing and real.

The four new limb tests here cover :2247 and :2294 both ways, and they are separable only because
the argument is a *mapping*: each pins the neighbouring gate so a wrong answer next door cannot
supply this test's failure. The `result_type` assertion via `self.options` is the right
instrument — the two draft limbs call the same activity with the same input and differ only in
whether the type is pinned, so the call list alone genuinely cannot tell them apart.

Repo-wide the un-patched limbs were not entirely dark: `test_return_case_workflow_replay_compatibility.py::_LegacyRuntime`
(:372, `patched` at :393) drives `_open_support` as an old history. The branch's claim is correctly
scoped — "neither gate had ever been evaluated **from this module**" — and its survey of the
sibling doubles (ledger :124-136) I spot-checked at every line number cited, including the
acceptance subclass at `test_items_13_19_reminder_cadence_in_business_time.py:148`. All accurate.

## 4. The allowlist removal — both directions, and the new floor

`suites.backend.known_failures` is now `[]`. Trunk's copy of the file is byte-identical to the
base's for this key, so there is no conflict on the allowlist itself.

**Direction A — the fixed suite passes the gate.** Merged tree (`refactor/unified-return-platform`
+ `feat/runtime-patch-double`), full default backend run:

```
5245 passed, 11 skipped, 514 deselected, 2 warnings in 322.57s
```

exit 0 — no failures at all, matching the claimed `5245 passed, 0 failed`. (The branch tip alone,
run separately, reports the same `5245 passed, 11 skipped, 514 deselected`.) `assert_known_failures.py
--suite backend` against that real report:

```
suite size held: 441 test files/modules, 5256 test cases (floor 441 / 5251)
5256 tests ran, 0 failed, 0 allowlisted
only the 0 known, still-failing tests failed
```

exit 0. The gate passes with an empty allowlist, and the size floor is measured in the same call.

**Direction B — the base tip's report is now rejected.** Feeding the base tip's report to the
branch's empty allowlist (floor isolated so the allowlist verdict is not masked by the size check):

```
suite size held: 1 test files/modules, 51 test cases (floor 1 / 51)
51 tests ran, 1 failed, 0 allowlisted
::error::NEW FAILURE (not on the allowlist): tests.test_cumulative_support_outcomes::test_a_rejected_return_still_opens_no_work_item
```

exit 1. The removal is load-bearing in both directions, as claimed.

`scripts/ci/test_assert_known_failures.py` — run in the merged tree as CI runs it (`python
scripts/ci/test_assert_known_failures.py`; it is a standalone script, not a pytest module):
`all negative controls passed`, exit 0.

**The suite-size floor does not conflict.** Trunk gained `scripts/ci/suite_size_floor.json` and
231 lines of `assert_known_failures.py` after this branch's base; the branch touches neither, so
the merge is clean by disjointness (confirmed by an actual `git merge` — no conflicts, 3 files).
The backend figure is `cases: 5251, files: 441`.

It is a **floor, not a pin** — the script fails only on a run coming back *smaller*, plus a
re-stake demand at 25% above. Adding five tests therefore cannot breach it, and measurably does
not. The script's own line on the merged report:

```
suite size held: 441 test files/modules, 5256 test cases (floor 441 / 5251)
```

5256 ≥ 5251, and well inside the 25% re-stake band. The five new tests live in an existing module
and add no new `classname`, so `files` is unmoved at exactly 441. **The floor is not wrong by
five, no edit to it is required, and none is made.** Correct.

## 5. Test integrity

Clean. The whole removed side of the test diff is three lines:

```
-from collections.abc import Awaitable, Callable
-    async def execute_activity(self, name: str, argument: Any, **_options: Any) -> Any:
-    harness = _harness(monkeypatch)
```

An import widened, a signature that now records its options instead of discarding them, and a
harness call that now binds the policy-enabled release. Each replacement is strictly more capable
than what it replaced. No test deleted, no assertion weakened, no `skip`/`xfail`/`skipif` added
anywhere in the module, no mock standing in for a live-infra path. Five tests added (module went
51 → 56 collected, measured at both tips).

`backend/src/` untouched — `git diff --name-only 2d0e3d65 54b269fa -- backend/src frontend config`
returns nothing. No production code changed, by me or by the branch.

Standing greps clean on the diff: no fact-name string literals, no imports of frozen modules, no
template/section/intent/tool literals. The patch ids are read off production
(`workflow_module._PATCH_...`) rather than restated, so a renamed marker fails these tests instead
of quietly answering a string nobody consults — which is the correct direction.

## 6. Rule 13 — the gate that runs it

Confirmed. `backend/tests/test_cumulative_support_outcomes.py` carries `pytestmark =
pytest.mark.asyncio` (:98) and no `live_infra` or `browser` marker, so it is selected by the
default `-m "not live_infra and not browser"` in `backend/pyproject.toml:139-140`.
`.github/workflows/checks.yml:306` runs `poetry run python -m pytest tests --junitxml=junit-backend.xml`
in the backend job, and :327 gates the resulting report with `assert_known_failures.py`. Every
guard this branch adds — the five tests, the `KeyError` refusal, the empty allowlist — is run by a
step that exists and that I have executed.

---

## Merge

Clean. `git merge feat/runtime-patch-double` onto `c8eac86d` applies 3 files with no conflicts and
no manual resolution. No sequencing precondition, no trunk guard armed against it.

## Observations (not findings, no action required)

- Ledger :323 "Exactly the two un-patched-limb tests" undercounts its own transcript by one.
  Safe direction; evidence complete.
- `backend/tests/policy/test_case_policy_gate.py:373` says `policy_evaluation.enabled` is overridden
  "only here". This branch adds a second such override, so that comment is now slightly stale. The
  branch does not edit that file and the new override cites it correctly; flagging only so it is
  not mistaken later for a contradiction.
