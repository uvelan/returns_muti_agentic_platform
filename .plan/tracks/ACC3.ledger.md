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

---

## step:04 — items 3–6, the review gate. Four injections; the category-B row points at the wrong file.

**Note on provenance:** INJ-B6…B9 were first executed before a session
interruption and were *not* in the ledger. Rather than transcribe them from
recollection, all four were **re-executed from a clean tree** and the outputs
below are the re-runs. (The interruption arrived with `git diff -- backend/src/`
already empty, confirmed before anything was applied.)

Target set, and why it is three files rather than STATUS's two: step:02's lesson
— ask whether the suite STATUS names is the suite where the guarantee lives.
`GATE` = `tests/test_support_template_review_gate.py` +
`tests/api/test_case_panel_and_reviews.py` (the two STATUS names) +
`tests/operations/test_review_aggregate.py` (S2's own, which STATUS never names).

```
$ ./.venv/Scripts/python.exe -m pytest $GATE -q
147 passed, 1 warning in 22.93s
```

The mechanism: `operations/review_aggregate.py` `approve()` verifies three things
before a review may leave `OPEN` — `draft_version` (line 747),
`canonical_edit_version` (751), and the canonical payload hash (778). Broken
**one at a time**, per the rule that a test failing only when all three break is
pinning the conjunction rather than the parts.

### INJ-B6 — `draft_version` check removed

```
FAILED tests/api/test_case_panel_and_reviews.py::test_a_stale_draft_version_is_409_with_the_field_named
FAILED tests/api/test_case_panel_and_reviews.py::test_a_hash_from_a_panel_read_before_the_draft_moved_is_refused
FAILED tests/operations/test_review_aggregate.py::test_approval_refuses_a_stale_draft_version
3 failed, 144 passed, 1 warning in 17.40s
```

**CAUGHT**, individually, and by a test that names the field.

### INJ-B7 — `canonical_edit_version` check removed. **The category-B row is wrong here.**

Run first against **only the two files STATUS names**:

```
$ ./.venv/Scripts/python.exe -m pytest tests/test_support_template_review_gate.py tests/api/test_case_panel_and_reviews.py -q
96 passed, 1 warning in 11.09s
```

**All 96 green with the check gone.** Then with S2's own suite added:

```
$ ./.venv/Scripts/python.exe -m pytest $GATE -q
FAILED tests/operations/test_review_aggregate.py::test_approval_refuses_a_stale_canonical_edit_version
1 failed, 146 passed, 1 warning in 17.76s
```

The property **is** covered — and by exactly one test, in a file STATUS's
category-B row for items 3–6 does not mention. This is step:02's finding a
second time: *a category-B row pointing at the wrong file makes the real
coverage invisible in both directions.* Read literally, STATUS says "93 tests
cover the review gate"; those 93 do not contain this guarantee at all.

### INJ-B8 — payload hash check removed

```
FAILED tests/api/test_case_panel_and_reviews.py::test_a_wrong_payload_hash_is_409
FAILED tests/operations/test_review_aggregate.py::test_approval_refuses_a_hash_of_bytes_the_store_does_not_hold
2 failed, 145 passed, 1 warning in 18.34s
```

**CAUGHT**, individually, by both layers.

### INJ-B9 — `hold` takes the auto-send branch

`workflows/return_case_workflow.py:2759`, `if policy == "auto_send"` widened to
`if policy in ("auto_send", "hold")`, so a timed-out review under the default
`hold` policy is approved as `SYSTEM` and sent.

```
FAILED tests/test_support_template_review_gate.py::test_nobody_answering_parks_the_case_and_sends_nothing
FAILED tests/test_support_template_review_gate.py::test_a_revision_naming_an_unheld_review_is_still_ignored_without_supersedes
FAILED tests/test_support_template_review_gate.py::test_a_supersedes_naming_a_review_this_case_never_held_is_refused
FAILED tests/test_support_template_review_gate.py::test_an_approved_request_is_sent_while_another_is_still_being_read
FAILED tests/test_support_template_review_gate.py::test_a_notice_naming_another_review_is_ignored
FAILED tests/test_support_template_review_gate.py::test_the_wait_runs_on_the_deadline_in_state_not_the_one_it_started_with
FAILED tests/test_support_template_review_gate.py::test_every_state_the_gate_can_close_over_ends_with_a_legal_exit
7 failed, 140 passed, 1 warning in 19.41s
```

**CAUGHT**, headlined by `test_nobody_answering_parks_the_case_and_sends_nothing`
— the test that owns the guarantee, failing on the guarantee's own wording. The
six others are collateral from the default-policy path changing shape, which is
expected and is not what earns the verdict.

### Verdict so far on 3–6

`hold`-never-auto-sends and all three approval checks are **individually
load-bearing → A**. The correction to carry into STATUS is not about a blind
test but about a **mis-pointed category-B row**: one of the four guarantees is
pinned by a single test in a file the row omits.

Reverted after each; `git diff --stat -- src/` empty.

---

## step:05 — items 3–6 finished. One guarantee was unguarded by the entire backend suite.

### INJ-B10 — an autosave after APPROVING is accepted

`review_aggregate.upsert_draft_edit` line 520,
`self._require_state(review, "edit", (ReviewState.OPEN,))` widened to
`(ReviewState.OPEN, ReviewState.APPROVING)`.

```
--- the two files STATUS names, alone ---
96 passed, 1 warning in 11.34s
--- plus S2's own suite ---
FAILED tests/operations/test_review_aggregate.py::test_autosave_after_approving_is_a_409_and_the_row_survives
1 failed, 146 passed, 1 warning in 17.47s
```

**CAUGHT — but, exactly as with INJ-B7, only outside the files STATUS names.**
Second of the four review-gate guarantees whose only pin is invisible to the
category-B row.

### INJ-B11 — delivery sends the raw draft instead of the frozen canonical edit

`support_template_gate.py:709`, `payload = canonical_review_payload(review)`
replaced with `payload = dict(review.get("draftPayload") or {})`. Approval is
untouched: it still freezes and hash-verifies the canonical payload. Only the
bytes that leave the platform change.

```
--- the two files STATUS names, alone ---
96 passed, 1 warning in 11.43s
--- plus S2's own suite ---
147 passed, 1 warning in 17.30s
```

Green. So — a negative claim, therefore the whole suite:

```
$ ./.venv/Scripts/python.exe -m pytest tests -q -p no:randomly
FAILED tests/test_cumulative_support_outcomes.py::test_a_rejected_return_still_opens_no_work_item
1 failed, 5235 passed, 10 skipped, 514 deselected, 2 warnings in 315.27s (0:05:15)
```

The single failure is step:03's pre-existing red. **Nothing in 5,235 backend
tests caught it.** This is the audit's most serious coverage finding.

**Why it was blind, precisely.** `_approved_review`
(`tests/operations/test_support_template_gate.py:364`) creates a review with a
`draft_payload` and **never a canonical edit**. `canonical_review_payload` then
returns the draft (line 264, the `else` limb), so the frozen payload and the
draft are byte-identical in every delivery test in the file, and the choice
between them is unobservable. Same shape as step:02's single-record store:
*green because the inputs could not exercise the property.*

**Business consequence, named.** An associate edits a drafted reply — corrects a
refund amount, removes a commitment the case cannot honour — approval freezes
and hash-verifies *that* text, and delivery sends the **original draft**. Support
is told something no one approved, behind a valid approval receipt. The review
gate's entire purpose is that what a human approved is what gets sent; that
property had no test.

**Production is correct.** Line 709 reads the canonical payload. This is a
coverage defect, not a behaviour defect — but it is the class that lets a future
edit here ship silently.

### The strengthening

`test_delivery_sends_the_frozen_canonical_edit_not_the_draft` added to
`tests/operations/test_support_template_gate.py`: draft says
`"REJECTED -- do not refund"`, a canonical edit resolved through
`resolve_canonical_edit` says `"APPROVED -- refund issued"`, approval freezes the
canonical one, and delivery must send it. The test **asserts its own premise**
(`frozen != review["draftPayload"]`) so it cannot decay back into the shape it
replaces if the fixture ever stops writing a canonical edit.

Clean tree:

```
$ ./.venv/Scripts/python.exe -m pytest tests/operations/test_support_template_gate.py -q
30 passed in 2.81s
```

Injected against, with INJ-B11 re-applied:

```
E       AssertionError: assert 'APPROVED -- refund issued' in 'RETURN DETAILS\nREJECTED -- do not refund\n'
FAILED tests/operations/test_support_template_gate.py::test_delivery_sends_the_frozen_canonical_edit_not_the_draft
1 failed, 29 passed in 3.06s
```

The assertion output *is* the business failure: Support reading "REJECTED — do
not refund" on a review whose approved text said the opposite.

Reverted; `git diff -- backend/src/` empty; the four gate files 177 passed.

---

## step:06 — items 1–2, config-only rendering. Zero-hardcoding holds; AMENDMENT-2's test could not fail.

Baseline (renderer + draft + gate): `110 passed in 6.38s`.

### INJ-B12 — the renderer resolves facts by `field_id`, ignoring `source_binding`

The sharpest expression of "hardcoded field name": `support_template_renderer.py:540`,
`_resolve_case_fact(draft, path, …)` → `_resolve_case_fact(draft, field_configuration.field_id, …)`.
Production config makes these genuinely different (`field_id: customer_reference`
→ `case_fact:customer_id`; `workflow_status` → `case_fact:workflow_status_at_handoff`),
and so does the suite's own `_minimal_template` (`order_number` →
`case_fact:confirmed_order_reference`).

```
FAILED …TestComposedEquivalenceMatrix::test_the_default_variant_reproduces_the_composed_text[straight_through]
 … (23 failures across all 17 matrix scenarios and the binding tests)
23 failed, 87 passed in 7.08s
```

**CAUGHT**, loudly. The suite does **not** only render the default config — it
renders non-default bindings throughout (`return_record:returnReference`,
`case_fact:tracking_number`, `graph:<path>`, `literal:`), so a hardcoded field
name cannot survive it. **Non-negotiable #1 (zero hardcoding) → A.**

### INJ-B13 — DISCARDED. Aimed at the second layer while the first held.

`support_template_renderer.py:263`, AMENDMENT-2's allowlist in
`_record_attribute` deleted.

```
110 passed in 6.76s
```

Green — and reading the source says why: the **real** AMENDMENT-2 guard is
upstream, in `configuration/support_template_configuration.py:93`
(`binding_source`), which refuses `return_record:<undeclared>` at release
validation. `_record_attribute`'s allowlist is a documented second line of
defence, and its own docstring says so. **Discarded: the injection removed the
belt while the braces held** — the INJ-B4 shape again, and phase 2's
"a branch that reads like the guard".

### INJ-B14 — layer 1 (release validation) alone

```
FAILED tests/configuration/test_support_template_configuration.py::TestRecordAttributeReach::test_a_dunder_is_refused_at_release_validation
FAILED tests/configuration/test_support_template_configuration.py::TestRecordAttributeReach::test_a_method_on_the_projection_is_refused
FAILED tests/configuration/test_support_template_configuration.py::TestRecordAttributeReach::test_an_undeclared_attribute_is_refused
3 failed, 350 passed, 1 warning in 19.94s
```

**CAUGHT**, individually — in `tests/configuration/`, a **third** file the
category-B row for items 1–2 ("V1's template/renderer suites") does not name.

### The finding: `test_an_undeclared_attribute_degrades_rather_than_reaching` could not fail

The render-side AMENDMENT-2 test passed a **dict** as the return record.
`_record_attribute` branches on `isinstance(record, Mapping)`, and
`{...}.get("__class__")` is `None` **whether or not either guard exists**. The
`getattr` limb — the one AMENDMENT-2 was written against ("resolving it through
unconstrained `getattr` is forbidden") — was never reached, and
`ReturnRecordProjection` is a pydantic model, **not** a Mapping, so the shape
production actually renders is the shape the test omitted.

Parametrised over both shapes, and **INJ-B15 (both guards removed at once)** is
the injection that justifies it:

```
E       assert 16 == -1
E        +      where "Record:\n- RMA: <class 'return_platform.operations.case_projection.contract.ReturnRecordProjection'>\n" = RenderedTemplate(...).text
FAILED …TestPerRecordSections::test_an_undeclared_attribute_degrades_rather_than_reaching[projection]
1 failed, 1 passed, 58 deselected in 1.31s
```

`[projection]` reddens; **`[mapping]` stays green with AMENDMENT-2 removed
entirely.** The pre-existing test could not have detected the collapse of the
guarantee under any injection — the textbook category-B family, now closed. The
class repr reaching `.text` is the business failure: internal type names on the
message a Support-desk person reads.

**Production is correct and defended twice.** This is a coverage finding only.

Reverted; `git diff -- backend/src/` empty; renderer + configuration `332 passed`.

---

## step:07 — item 17's relay half. The named test does not test the named guarantee.

Baseline: `37 passed in 2.66s`.

Phase 2's lesson applied first: does the scenario put a **real** duplicate in
front of the guard? `test_the_transcript_entry_is_appended_once_across_a_redelivery`
does call `_analyse` twice — a genuine second delivery of the classify command,
the right shape, not a second signal into a closed gate. So the shape is fine.
The question is *which* guard the duplicate lands on.

### INJ-B16 — the real append-once guard removed

`operations/return_support/relay.py:163`,
`if any(str(item.get("entryId")) == entry_id for item in existing): return False`
disabled. This is the mechanism the docstring credits: *"Append-once is the
adapter's contract (the entry id is derived from the event and the record), so a
redelivered classify command appends nothing new."*

```
--- the file STATUS names, alone ---
22 passed in 2.67s
--- plus the relay adapter's own suite ---
FAILED tests/operations/test_support_relay_and_wiring.py::test_the_same_entry_is_appended_once_however_often_it_is_delivered
1 failed, 36 passed in 3.03s
```

**The test STATUS names for item 17's relay half stays green with the production
append-once guard deleted.** It drives `_RecordingRelay`
(`tests/operations/test_support_message_classification.py:164`), a double that
implements *its own* dedupe on `(support_event_id, return_record_id, entry_kind)`.
The second delivery is real, but the guard it meets is the double's, not
production's.

The guarantee **is** pinned — by
`test_the_same_entry_is_appended_once_however_often_it_is_delivered` in
`tests/operations/test_support_relay_and_wiring.py`. Fifth file in this audit
that holds a guarantee its category-B row does not name.

### INJ-B17 — what the named test *does* pin

`message_classification.py:700`, `if wrote: appended += 1` → count every call.

```
FAILED tests/operations/test_support_message_classification.py::test_the_transcript_entry_is_appended_once_across_a_redelivery
1 failed, 21 passed in 3.03s
```

**CAUGHT.** So the named test is not vacuous: it pins that `relayed_entries`
reports *writes* rather than *calls* — which is the propagation half, and is
what makes `second.relayed_entries == 0` meaningful. It simply is not the test
of "appended once"; that lives one layer down.

### Verdict

Item 17's relay half → **A**, by the two tests together. The correction is to the
pointer, not to the coverage: read literally, STATUS credits the guarantee to a
test that survives the guarantee's removal.

---

## step:08 — items 9, 11–12, the resolver. Two guarantees pinned; the authz half was unguarded.

Baselines: ladder + roundtrip `54 passed`; composition + gating + roundtrip +
ladder `96 passed`; clarification route `18 passed`.

### INJ-B18 — budget checked *after* the call (item 12)

`resolution_ladder.py:439`, `>=` → `>`, an off-by-one that spends the invocation
the budget existed to prevent.

```
E       AssertionError: the budget is checked before the call, not after
E       assert 2 == 1
FAILED tests/operations/test_support_resolution_ladder.py::test_budget_exhaustion_writes_the_fact_and_escalates
1 failed, 53 passed in 1.94s
```

**CAUGHT**, by the assertion whose message *is* the property. Note this test is
the shape phase 2's finding #4 was not: budget 1 against 2 queued answers, so the
budget genuinely bites, plus a negative twin
(`test_a_run_that_stays_within_budget_writes_no_exhaustion_fact`) that the
docstring explains. **Item 12 → A.**

### INJ-B19 — the disclosure line dropped (item 9)

`outbound_composition.py:_with_disclosure`, returns the bare body while still
reporting `discloses_agent=True` — an agent-authored message reaching Support
with no disclosure and the platform believing it disclosed.

```
FAILED tests/operations/test_support_reply_gating.py::test_an_auto_reply_is_delivered_with_system_provenance_and_disclosure
FAILED tests/operations/test_support_reply_gating.py::test_both_paths_compose_the_identical_message
 … 12 failed, 84 passed in 1.90s
```

**CAUGHT**, by 12 tests across both the reviewed and auto paths. **Item 9 → A.**

### INJ-B20 — the refusal is deferred until after the command is recorded (items 11–12)

The endpoint's 404 paths (`api/case_clarifications.py:225-233`) moved *after*
`store.record_command`, so the caller still gets the identical 404 and body while
the answer sits durably in `case_command_records`.

```
--- the route's own suite ---
18 passed, 1 warning in 3.08s
--- the whole backend suite ---
FAILED tests/test_cumulative_support_outcomes.py::test_a_rejected_return_still_opens_no_work_item
1 failed, 5237 passed, 10 skipped, 514 deselected, 2 warnings in 266.81s (0:04:26)
```

The single failure is step:03's pre-existing red. **Nothing in 5,237 tests
caught it.** The four sibling tests
(`test_another_tenants_case_is_a_404_not_a_403`, `test_a_missing_case_is_the_same_404`,
`test_an_answer_to_a_clarification_this_case_never_asked_is_a_404`, and the
capability one) assert **status and error code only** — never that the refusal
left nothing behind. The dispatch names the property as "403/404 **with no fact
written**"; only the first half was under test.

**Business consequence.** A principal who cannot see a case submits an answer;
the API correctly says 404, and the answer is on file against that case, queued
for delivery to Support under a clarification id the principal was never shown.
The 404 that exists so a caller cannot learn the case exists would be paired with
a durable write proving it does.

**Production is correct** — both refusals precede `record_command`. Coverage
finding only.

### The strengthening

`test_a_refused_answer_records_no_command`, parametrised over all three 404
shapes (another tenant, missing case, clarification never asked), asserting the
status **and** `case_command_records` empty.

Clean tree: `3 passed, 18 deselected`. Injected against:

```
E       assert 1 == 0
FAILED …test_a_refused_answer_records_no_command[another_tenant]
FAILED …test_a_refused_answer_records_no_command[missing_case]
FAILED …test_a_refused_answer_records_no_command[never_asked]
3 failed, 18 passed, 1 warning in 3.82s
```

All three redden; the pre-existing 18 stay green, which is the measurement that
the injection really is invisible to them.

Reverted; `git diff -- backend/src/` empty; the five suites `117 passed`.
