# ACC phase 3 — category B audit by fault injection

**Base sha: `63744f2abb1ff186617dee0c7d541fd6f4870db2`** — `63744f2a docs(merge): ACC-2 merged -- RV PASS after three rounds`.
Branch `feat/acc-audit-b` cut from that commit.

## step:00 — base verification, and a correction to the dispatch

The dispatch said the base "must be the current tip of `master` (should be around
`63744f2a docs(merge): ACC-2 merged`)". Those two identifiers name different
commits, so the check was run rather than assumed:

```
$ git log --oneline -1 master
0448d32a feat: refine conversational returns experience
$ git rev-parse HEAD
63744f2abb1ff186617dee0c7d541fd6f4870db2
$ git branch --show-current
refactor/unified-return-platform
```

`63744f2a` is **not** on `master`:

```
$ git merge-base --is-ancestor 63744f2a master  -> NO
$ git merge-base --is-ancestor 0448d32a HEAD    -> YES
$ git branch -a --contains 63744f2a
* refactor/unified-return-platform
$ git rev-list --count 0448d32a..63744f2a
872
```

So the integration branch is `refactor/unified-return-platform`, and `master` is
**872 commits behind it** — it omits every merged slice, which is precisely the
trap the dispatch warns about ("branched from an ancestor that compiled, passed,
and silently omitted every merged slice"). Had the ref label been followed instead
of the sha, this run would have been the ninth.

**Resolution:** the sha is the load-bearing identifier and it matches HEAD exactly,
including its subject line. Branched from `63744f2a`. The dispatch's *"tip of
master"* wording is wrong and is recorded here rather than quietly worked around.

```
$ git checkout -b feat/acc-audit-b 63744f2a
Switched to a new branch 'feat/acc-audit-b'
$ git rev-parse HEAD
63744f2abb1ff186617dee0c7d541fd6f4870db2
$ git status --porcelain
(empty)
```

---

## step:01 — items 7–8, DR-11. Three injections, three caught.

The mechanism: `backend/src/return_platform/operations/artifact_binding.py`
`bind_artifact()` is a pure classifier (BOUND / AMBIGUOUS / UNMATCHED);
`persist_binding_decision()` writes a scoped fact for the latter two and
**never touches a record**. The only path that can create a return record is
`message_classification._record_support_outcome`, reached only from
`record_bindings_from_extraction(source)` — record *groups*, never artifacts.

Baseline:

```
$ ./.venv/Scripts/python.exe -m pytest tests/operations/test_support_message_classification.py -q \
    -k "unmatched_artifact_never_creates_a_record or ambiguous_artifact_asks_rather_than_guesses"
..                                                                       [100%]
2 passed, 20 deselected in 0.59s
```

### INJ-B1 — the ambiguous path guesses the first candidate

`artifact_binding.py:155-159`, the terminal `AMBIGUOUS` return replaced with
`BOUND` to `records[0]`. Target: `test_an_ambiguous_artifact_asks_rather_than_guesses`.

```
>       assert outcome.bound_artifacts == 0
E       AssertionError: assert 1 == 0
E        +  where 1 = AnalysisOutcome(..., bound_artifacts=1, clarifications=(), relayed_entries=1, ...)
tests\operations\test_support_message_classification.py:553: AssertionError
FAILED tests/operations/test_support_message_classification.py::test_an_ambiguous_artifact_asks_rather_than_guesses
1 failed, 21 deselected in 0.73s
```

**CAUGHT**, on the first assertion, for the right reason — the guess bound.

### INJ-B2a — an unknown reference binds to the first record

`artifact_binding.py:138-142`, the `UNMATCHED` return replaced with `BOUND` to
`records[0]`. Target: `test_an_unmatched_artifact_never_creates_a_record`.

```
>       assert outcome.bound_artifacts == 0
E       AssertionError: assert 1 == 0
tests\operations\test_support_message_classification.py:587: AssertionError
FAILED tests/operations/test_support_message_classification.py::test_an_unmatched_artifact_never_creates_a_record
1 failed, 21 deselected in 0.48s
```

**CAUGHT.**

### INJ-B2b — the literal DR-11 violation: UNMATCHED *creates* the record

B2a is a mis-bind, not a creation, so it does not yet prove the test's headline
claim. B2b injects the creation itself, in `message_classification.py` between
`extracted_artifacts` and `_persist_artifacts`: every artifact naming a
reference the case does not hold is turned into a `ReturnRecordBinding` and put
through `_record_support_outcome` — the one path that can mint a record.

```
        assert parts["facts"].named(SUPPORT_ARTIFACT_UNMATCHED)
>       assert parts["events"].calls == [], "no record group means no outcome signal"
E       AssertionError: no record group means no outcome signal
E       assert [{'case_id': ..., ...}], ...}] == []
E         Left contains one more item: {'case_id': 'case-5150', 'work_item_id': 'wi-5150',
E           'support_event_id': 'sev-5150', 'records': [{'return_reference': 'RMA-99', ...}], ...}
tests\operations\test_support_message_classification.py:591: AssertionError
FAILED tests/operations/test_support_message_classification.py::test_an_unmatched_artifact_never_creates_a_record
1 failed, 21 deselected in 0.50s
```

**CAUGHT**, and by the assertion that owns the guarantee rather than by a
neighbour — `events.calls == []` is load-bearing, and the diff names the
invented `RMA-99` precisely.

### Verdict

Both DR-11 tests move **B → A**. They are not green-but-blind: each headline
claim has an assertion that reddens when the production mechanism behind it is
removed. Note also `_StubRecordStore` exposes no create method at all, so a
creation attempted through the record port (rather than through the signal
chain, as B2b did) would `AttributeError` rather than pass silently — a second,
structural guard.

Reverted; suite re-run from the reverted tree:

```
$ git status --porcelain
(empty)
$ ./.venv/Scripts/python.exe -m pytest tests/operations/test_support_message_classification.py -q
......................                                                   [100%]
22 passed in 2.43s
```

---

## step:02 — item 8, cross-assignment. One blind spot found and closed; one injection discarded.

Item 8's shape: two records on one case, an artifact for record B — can any path
bind it to A? There are **two** mechanisms, and the suite covered only one.

### INJ-B3 — the decision layer cross-assigns (`bind_artifact`)

`artifact_binding.py:132-137`, `return_record_id=str(matched[...])` replaced with
`records[0][...]`. Identical whenever the case holds one record; cross-assigns
whenever it holds several. Run against the **whole backend suite**:

```
$ ./.venv/Scripts/python.exe -m pytest tests -q -p no:randomly
FAILED tests/operations/test_artifact_binding.py::TestBindingRules::test_an_artifact_naming_a_known_reference_binds_to_that_record
FAILED tests/test_cumulative_support_outcomes.py::test_a_rejected_return_still_opens_no_work_item
2 failed, 5232 passed, 10 skipped, 514 deselected, 2 warnings in 266.23s (0:04:26)
```

**CAUGHT** — by `tests/operations/test_artifact_binding.py:59`, which is the real
cross-assignment case (`records = [rec-1/RMA-1, rec-2/RMA-2]`, artifact bound to
`RMA-2`, asserts `rec-2`). STATUS listed items 7–8 only against
`test_support_message_classification.py`; **S1's own module suite was never in
the category-B row**, and it is where this property actually lives. The decision
half of item 8 is therefore **A**.

(The second failure is unrelated — see step:03.)

### INJ-B4 — DISCARDED, green for the wrong reason

`_merge_bound_artifact._attempt`, the record search replaced with
`next(iter(await records.list_return_records(case_id)), None)`. Intended as the
persistence-layer cross-assignment. Whole-suite run:

```
$ ./.venv/Scripts/python.exe -m pytest tests -q -p no:randomly
FAILED tests/test_cumulative_support_outcomes.py::test_a_rejected_return_still_opens_no_work_item
1 failed, 5233 passed, 10 skipped, 514 deselected, 2 warnings in 267.42s (0:04:27)
```

Nothing caught it — and reading the source explains why the injection was *not
the one I thought I had written*. `update_return_record` is called with
`str(decision.return_record_id)` (line 263), not with the found document's id;
only `expected_version` and the redelivery check come from `stored`. So B4
corrupts the version read, **not the write target**, and produces no
cross-assignment at all. Re-run against the strengthening below, it left the
cross-assignment test green and reddened only the `LookupError` one — with
`RuntimeError: coroutine raised StopIteration`, a shape that is not the
business failure either.

**Discarded**, in the phase-2 sense: an injection aimed at a line that reads like
the mechanism rather than the line that is one. Recorded because the reasoning
is the finding.

### The blind spot it exposed anyway

Every test in `TestBoundPersistence` (lines 203-241) stores **exactly one
record**. `records[0]` and "the record the decision names" are then the same
document, so the selection step in `_merge_bound_artifact` cannot be wrong and
the `LookupError` branch cannot be reached other than by an empty case. This is
the category-B family exactly: *green because the inputs could not exercise the
property*. Two tests added at `tests/operations/test_artifact_binding.py`:

* `test_a_bound_artifact_merges_onto_the_named_record_not_the_first` — two
  records, artifact bound to the second; asserts the write **and** the untouched
  neighbour, because asserting rec-2 alone would pass if rec-1 were written too;
* `test_a_decision_naming_a_record_the_case_does_not_hold_refuses` — the merge
  raises rather than falling back to a neighbour, which is only a meaningful
  claim when a neighbour exists.

Green on the clean tree:

```
$ ./.venv/Scripts/python.exe -m pytest tests/operations/test_artifact_binding.py -q
....................                                                     [100%]
20 passed in 0.97s
```

### INJ-B5 — the faithful cross-assignment, against the strengthening

Search picks `records[0]` **and** the write targets `str(stored["returnRecordId"])`
— the record the broken search found. This one really does cross-assign.

```
FAILED tests/operations/test_artifact_binding.py::TestBoundPersistence::test_a_bound_artifact_merges_onto_the_named_record_not_the_first
FAILED tests/operations/test_artifact_binding.py::TestBoundPersistence::test_a_decision_naming_a_record_the_case_does_not_hold_refuses
2 failed, 18 passed in 1.29s
```

and the assertion that reddens is the business failure, named:

```
>       assert store.updates == [("rec-2", {"trackingReference": "TRK-9"})]
E       AssertionError: assert [('rec-1', {'...e': 'TRK-9'})] == [('rec-2', {'...e': 'TRK-9'})]
E         At index 0 diff: ('rec-1', {'trackingReference': 'TRK-9'}) != ('rec-2', {'trackingReference': 'TRK-9'})
tests\operations\test_artifact_binding.py:266: AssertionError
```

Support's tracking number for RMA-2 written onto RMA-1. The strengthening is
itself injected-against, so it is not another blind test.

Reverted; `git diff` touches **no `src/`**:

```
$ git diff --stat
 backend/tests/operations/test_artifact_binding.py | 45 +++++++++++++++++++++++
 1 file changed, 45 insertions(+)
$ ./.venv/Scripts/python.exe -m pytest tests/operations/test_artifact_binding.py tests/operations/test_support_message_classification.py -q
..........................................                               [100%]
42 passed in 3.99s
```

---

## step:03 — a red test on the merge tip, and it is ACC-2's predicted class

Both whole-suite runs in step:02 carried a second failure, present with `src/`
clean and with no injection applied:

```
$ git status --porcelain
(empty, src/)
$ ./.venv/Scripts/python.exe -m pytest tests/test_cumulative_support_outcomes.py -q
FAILED tests/test_cumulative_support_outcomes.py::test_a_rejected_return_still_opens_no_work_item
1 failed, 50 passed in 1.58s
```

```
    async def _open_support(self, timings: ReturnCaseTimings) -> None:
>           if workflow.patched(_PATCH_STRUCTURED_SUPPORT_DRAFT):
E           AttributeError: '_Runtime' object has no attribute 'patched'
src\return_platform\workflows\return_case_workflow.py:2247: AttributeError
```

`tests/test_cumulative_support_outcomes.py:1311`'s `_Runtime` substitutes the
`temporalio.workflow` module functions the run loop calls, and it **does not
define `patched`** — production grew a `workflow.patched` call (the structured
support-draft patch branch) and the double did not follow. Production is
correct; the harness is stale.

This is **the same class ACC-2 handed to its owner and predicted would recur**
(STATUS "Findings handed to their owners" 1 and 2: stale workflow doubles, and a
registration guard that reads `worker.py` but not the workers the *tests*
construct). Here it is again, in a different file, and this time not silent —
it is red on the merge tip that ACC-2 recorded as `PASS`.

**Not repaired** (ACC does not edit another slice's harness, and the audit rule
forbids touching a failing test). Reported. Consequence: one of the two branches
of item 20's deploy-replay pair is unexercised in this module, and any full-suite
run on this branch is red before an auditor starts.

Only 1 of the 51 tests in the file reaches `_open_support`, which is why it
presents as a single failure rather than a wedged module.
