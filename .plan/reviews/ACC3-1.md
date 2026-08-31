# ACC3 round 1 — `feat/acc-audit-b`

**Verdict: CHANGES_REQUIRED** — one finding, documentation-only, on an
acceptance-gate claim. Everything else in this branch verified and holds.

Base `63744f2abb1ff186617dee0c7d541fd6f4870db2`; tip `0fe488c8`. Trunk
`refactor/unified-return-platform` has moved since the cut (CI-LINT merged);
`git merge-tree --write-tree` against current trunk resolves clean, no conflict
section. ACC3's own commits (`(ACC3)` prefix) touch only
`.plan/tracks/ACC3.ledger.md`, `.plan/acceptance/`, and four files under
`backend/tests/`. The `docs(merge)` and `review(CI-LINT-*)` commits riding along
are the orchestrator's and mine.

This review re-ran the audit's instrument rather than reading its conclusions.
Six injections were reproduced independently in the main worktree and reverted;
`git diff -- backend/src/` is empty at the end of this review.

---

## The finding

### F1 — the item-20 acceptance-gate consequence is stated narrower than it is

`.plan/acceptance/category-b-audit.md:176-178` and `.plan/acceptance/STATUS.md`
production finding 4:

> **one branch of item 20's deploy-replay pair is therefore unexercised in that
> module** — the structured-support-draft patch branch is never taken there,
> because the call raises before it can be.

The mechanism is right and the direction is right, but the scope is understated,
and this is the sentence an acceptance auditor will act on.

`_Runtime` (`backend/tests/test_cumulative_support_outcomes.py:1311`) has no
`patched` attribute *at all*. `backend/src/return_platform/workflows/return_case_workflow.py`
calls `workflow.patched` at **three** sites — line 1672
(`_PATCH_V3_CLARIFICATION_ROUND_TRIP`), line 2247
(`_PATCH_STRUCTURED_SUPPORT_DRAFT`), line 2294
(`_PATCH_SUPPORT_TEMPLATE_REVIEW_GATE`). Every one of them raises
`AttributeError` under that double.

Measured: `pytest tests/test_cumulative_support_outcomes.py` → `1 failed, 50
passed`. A passing test that reached any of the three would have failed. So
**no test in that module exercises any branch of any patch gate** — not one
branch of one pair. Both limbs of the structured-support-draft pair are
unexercised there, and so are the round-trip and review-gate gates.

Why it matters: as written, the record tells a reader that fixing one branch's
coverage closes the gap. It does not. The owner of the stale harness needs to
know that adding `patched` to `_Runtime` unblocks three gates, and that item
20's "both patch branches audited" is unsupported *by this module* for all of
them.

**Fix:** replace the "one branch" clause in both files with the measured scope.
No test or source change; the underlying finding stands as reported.

---

## What was verified independently

### 1. Sent ≠ frozen payload — production is CORRECT (verified, not accepted)

Read the delivery path rather than the report.
`SupportTemplateGateService.deliver_approved`
(`backend/src/return_platform/operations/support_template_gate.py:709`) composes
from `canonical_review_payload(review)`.
`canonical_review_payload` (`operations/review_aggregate.py:259-264`) returns
`canonicalEdit.canonical_payload` when one exists and falls back to
`draftPayload` only when it does not. `approve()` (`review_aggregate.py:758`)
freezes and hash-verifies that same function's result. Approval and delivery
read the same source. **Contracts §6/D3 holds: the workflow consumes the frozen
approved payload.**

Every other write path to Support was checked for a second door:

- `return_support/reply_gating.py:250-274` sends `composed.text` on the
  **auto-send** branch, reached only when `requires_review(intent)` is false —
  no review, therefore no canonical edit. Not a bypass.
- `return_support/clarification.py:234-241` is the clarification relay, a
  different payload entirely.
- `support_template_gate.py:916` (`_draft_of`) and
  `workflows/return_case_activities.py:3056` (`_gap_field_ids`) read
  `draftPayload` deliberately — both are *draft*-side projections (the panel's
  view, and the gaps the reviewer is looking at), not the outbound message.

So the reported failure mode does not exist in production. Confirmed as a
coverage defect.

**INJ-B11 reproduced.** Replaced line 709 with `dict(review.get("draftPayload")
or {})`; `pytest tests/operations/test_support_template_gate.py` →
`1 failed, 29 passed`. Only the new test reddens, with the assertion text the
report quotes. The other 29 in the file are blind to it exactly as claimed.

### 2. A refused request could write a durable command — production is CORRECT

`api/case_clarifications.py:197-243`. Ordering: `Depends(require_support_act)` →
422 for `map` without a record → `repository.get_case` + tenant comparison →
`_not_found()` → `_clarification_on_this_case` → `_not_found()` → **then**
`store.record_command`. All three 404 shapes precede the durable write. The
authorization check is genuinely before the write.

**INJ-B20 reproduced.** Deferred both refusals until after `record_command`;
`pytest tests/api/test_case_clarification_answer_route.py` → `3 failed, 18
passed`. The three failures are the three new parameters; the 18 pre-existing
tests stay green — the measurement that proves the injection was invisible to
them.

### 3. The seven added tests are not themselves blind

All seven pass on a clean tree (`14 passed` including neighbours). Each was
injected against here, not merely on report:

- `test_delivery_sends_the_frozen_canonical_edit_not_the_draft` — reddens under
  INJ-B11 (above).
- **Its premise assertion is real and live.** `assert frozen !=
  dict(review["draftPayload"])` at `test_support_template_gate.py:403`. I forced
  the decay it guards against by making `canonical_review_payload` ignore
  `canonicalEdit`: the test fails **on that line**, before reaching the delivery
  assertions. The test cannot silently become vacuous. This is the most
  important line in the branch and it works.
- `test_a_refused_answer_records_no_command[×3]` — all three redden under
  INJ-B20.
- `test_an_undeclared_attribute_degrades_rather_than_reaching[projection]` —
  under INJ-B15 (both AMENDMENT-2 guards removed) `[projection]` fails with
  `<class '…ReturnRecordProjection'>` reaching the message text while
  `[mapping]` stays green. Exactly the split reported.
- The two `TestBoundPersistence` two-record tests pass and assert the untouched
  neighbour, which is the half that makes cross-assignment expressible.

### 4. The mis-pointed-row finding — both sharp instances reproduced

**The transcript one, as instructed.** `_RecordingRelay.append_system_entry`
(`tests/operations/test_support_message_classification.py:168-180`) dedupes on
`(support_event_id, return_record_id, entry_kind)` **inside the double**. Deleted
the production append-once guard
(`operations/return_support/relay.py:163-164`, the `entryId` check):

- `tests/operations/test_support_message_classification.py` → **22 passed**,
  including `test_the_transcript_entry_is_appended_once_across_a_redelivery`,
  the test STATUS credits with the guarantee.
- `tests/operations/test_support_relay_and_wiring.py` →
  `test_the_same_entry_is_appended_once_however_often_it_is_delivered` **fails**
  (`assert True is False`).

A test whose double supplies the guarantee under test, credited by name with
that guarantee. Confirmed.

**The 96/96 one.** Deleted the `canonical_edit_version` mismatch raise
(`review_aggregate.py:751-757`):

- `tests/test_support_template_review_gate.py` + `tests/api/test_case_panel_and_reviews.py`
  → **96 passed**. Verbatim the claim.
- `tests/operations/test_review_aggregate.py` →
  `test_approval_refuses_a_stale_canonical_edit_version` fails.

The row is mis-pointed as reported.

### 5. Discarded injections — both rightly discarded

- **B4.** `_merge_bound_artifact._attempt`
  (`operations/artifact_binding.py:251-271`) searches for the named record but
  writes with `str(decision.return_record_id)`; only `expected_version` and the
  redelivery short-circuit come from the found document. Corrupting the search
  alone cannot mis-assign. The discard reasoning is correct, and B5 (search
  **and** write target) is the injection that expresses item 8. Recording the
  reasoning rather than the injection was the right call.
- **B13.** Reproduced: allowlist removed from `_record_attribute`
  (`support_template_renderer.py:263-264`) → `tests/operations/test_support_template_renderer.py`
  **60 passed**, including the new `[projection]` case. `binding_source`
  (`configuration/support_template_configuration.py:93`) refuses the binding
  first, so `_record_attribute` is never asked. Correctly discarded, and
  production finding 2 (defence in depth, deliberately kept) survives the new
  test — I checked, because the new test bypasses release validation and could
  plausibly have invalidated it.

  *Precision note, not a finding:* what actually held in that run was
  `binding_source` called at **render** time (`support_template_renderer.py:518`),
  not release validation — the test builds its template by `model_copy` and
  never validates a release. The report's phrasing is true of production (such a
  template cannot become a release) but compresses the mechanism of its own
  measurement.

### 6. Test integrity — clean

- `git diff refactor/unified-return-platform...feat/acc-audit-b -- backend/src/ scripts/`
  is **empty**. All 20 injections reverted.
- `scripts/ci/known_test_failures.json` byte-identical to trunk (empty diff).
- No `skip`, `xfail`, or deleted test in the diff. The renderer file's two
  deleted lines are the `def` line replaced by the parametrized signature.
- No assertion weakened. The renderer change *widens* an existing test from one
  record shape to two; the original mapping case is retained unchanged.
- No new imports of frozen modules (`operations/associate_flow`,
  `agents/order_discovery`, `api/associate_returns`, `api/return_agents`).
- Rule 13: the branch adds no guard. It adds tests, all under `backend/tests/`,
  all collected by the `backend` job in `.github/workflows/checks.yml`
  (`pytest tests`). Every added test has a gate that runs it.
- Hardcoding: test-local literals only. `_command_count`'s
  `"case_command_records"` literal mirrors the pre-existing `_only_command`
  helper in the same file; a rename would redden the siblings loudly rather than
  decay this test into a vacuous pass.

### 7. The red on the merge tip — production correct, harness stale

Confirmed in that direction, not the reverse.
`return_case_workflow.py:2247` guards a workflow-logic change with
`workflow.patched(_PATCH_STRUCTURED_SUPPORT_DRAFT)` — which is what blocking
rule 3 *requires* while histories can be in flight, and the surrounding comment
documents the permissive-decode correction the else-limb exists for. The defect
is that the test module's `_Runtime` double never grew the method. Reproduced:
`AttributeError: '_Runtime' object has no attribute 'patched'`.

It is already the single registered backend entry in
`scripts/ci/known_test_failures.json`, so CI's gate is not red on it; a raw
`pytest` run is, which is what the report says.

Not repaired here, correctly — it is another slice's harness.

The acceptance-gate consequence ACC3 draws from it is right in kind and too
narrow in scope. See F1.

### 8. Suite counts — reproduced exactly

```
1 failed, 5240 passed, 10 skipped, 514 deselected, 2 warnings in 273.97s
```

Matching the report's `5,240 passed`, one pre-existing failure, `514
deselected`. The 10 skips are pre-existing; none introduced by this branch.

---

## Not findings

- The dead `if case is not _MISSING: kwargs["case"] = case / else: kwargs["case"]
  = _MISSING` branch in `test_a_refused_answer_records_no_command` — both limbs
  assign the same value. Harmless; tidy it if the file is touched again.
- The `for client in _client(...) / break` idiom is this file's existing
  convention, not something the branch introduced.

---

## Round summary

The audit's instrument was applied honestly and its two headline production
claims are true — I established both from the source and from re-run injections
rather than accepting them. The premise assertion on the sent-payload test does
what it claims. The transcript double supplying its own guarantee is real and
reproduced. Nothing here weakens the suite.

One documentation correction stands between this and a PASS. Fix F1 in
`category-b-audit.md` and `STATUS.md` and resubmit; re-review will be short.
