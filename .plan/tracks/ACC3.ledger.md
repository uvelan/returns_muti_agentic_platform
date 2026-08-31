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
