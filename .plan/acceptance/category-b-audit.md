# Category B, audited by fault injection — ACC phase 3

Base `63744f2abb1ff186617dee0c7d541fd6f4870db2`, branch `feat/acc-audit-b`.
Ledger with verbatim commands and output: `.plan/tracks/ACC3.ledger.md`.

Category B was *"tests exist, found by name, bodies never read, never injected
against."* The instrument here is fault injection, not reading: for each
guarantee, the production mechanism was **broken in `src/`**, the test re-run,
and the result recorded. Every injection was reverted immediately;
`git diff -- backend/src/` is empty at every commit boundary and at the end.

**No blocking defect was found against a non-negotiable.** DR-11 holds, and so
does zero-hardcoding. What the audit did find is a different failure than
expected, and it is systematic.

---

## The headline: STATUS's category-B rows point at the wrong files

The audit expected blind tests. It found a handful — but the dominant finding is
that **six of the guarantees named in category B are pinned by tests in files the
category-B rows never mention**, and in two cases the *named* test survives the
removal of the very guarantee it is credited with.

| guarantee | STATUS's row names | where the guarantee actually lives |
| --- | --- | --- |
| item 8 cross-assignment (decision) | `test_support_message_classification.py` | `tests/operations/test_artifact_binding.py` |
| 3–6 `canonical_edit_version` | the two review-gate files (93 tests) | `tests/operations/test_review_aggregate.py` |
| 3–6 autosave-after-`APPROVING` | the two review-gate files | `tests/operations/test_review_aggregate.py` |
| 1–2 AMENDMENT-2 reach | "V1's template/renderer suites" | `tests/configuration/test_support_template_configuration.py` |
| 17 relay append-once | `test_the_transcript_entry_is_appended_once_across_a_redelivery` | `tests/operations/test_support_relay_and_wiring.py` |

Read literally, STATUS credits `canonical_edit_version` and
autosave-after-`APPROVING` to 93 tests that stay **96/96 green** when either check
is deleted, and credits "the transcript entry is appended once" to a test that
stays green when the append-once guard is deleted. The coverage is real; the
map is wrong. **A mis-pointed row is worse than a blind test, because it makes
the real coverage invisible in both directions** — a future auditor deletes the
guard, sees the named suite green, and concludes the guard was dead.

---

## The two genuine coverage holes

Both were caught by nothing in the entire backend suite, and in both cases
**production is correct** — these are coverage defects, not behaviour defects.

### 1. The sent payload was never checked against the frozen one (items 3–6)

`INJ-B11`: delivery reads `review["draftPayload"]` instead of
`canonical_review_payload(review)`. Approval is untouched — it still freezes and
hash-verifies the canonical payload. **5,235 backend tests stayed green.**

*Why it was blind:* `_approved_review`
(`tests/operations/test_support_template_gate.py:364`) creates reviews with a
draft and **never a canonical edit**, so `canonical_review_payload` returns the
draft and the two candidate sources are byte-identical in every delivery test.
The choice between them cannot be observed.

*Business consequence:* an associate edits a drafted reply — corrects a refund
amount, removes a commitment the case cannot honour — approval freezes and
hash-verifies *that* text, and delivery sends the **original draft**. Support is
told something no one approved, behind a valid approval receipt. The review
gate's whole purpose is that what a human approved is what gets sent.

*Closed by* `test_delivery_sends_the_frozen_canonical_edit_not_the_draft`, which
asserts its own premise (`frozen != draftPayload`) so it cannot decay back into
the shape it replaces. Under INJ-B11 it reddens with the failure in the
assertion text: `assert 'APPROVED -- refund issued' in '… REJECTED -- do not refund'`.

### 2. A refused request could write a durable command (items 11–12)

`INJ-B20`: the clarification endpoint's 404 paths moved *after*
`store.record_command`. Identical status, identical body. **5,237 backend tests
stayed green.**

*Why it was blind:* the four sibling tests assert **status and error code only**.
The dispatch names the property as "403/404 **with no fact written**"; only the
first half was ever under test.

*Business consequence:* a principal who cannot see a case submits an answer, the
API correctly says 404, and the answer sits on file against that case queued for
delivery to Support under a clarification id the principal was never shown. The
404 exists so a caller cannot learn the case exists; it would be paired with a
durable write proving it does.

*Closed by* `test_a_refused_answer_records_no_command`, parametrised over all
three 404 shapes. All three redden under INJ-B20 while the pre-existing 18 stay
green — which is the measurement proving the injection is invisible to them.

---

## The two blind tests

### AMENDMENT-2's render-side test could not fail (items 1–2)

`test_an_undeclared_attribute_degrades_rather_than_reaching` passed a **dict** as
the return record. `_record_attribute` branches on `isinstance(record, Mapping)`,
and `{...}.get("__class__")` is `None` **whether or not either guard exists**. The
`getattr` limb — the one AMENDMENT-2 was written against, *"resolving it through
unconstrained `getattr` is forbidden"* — was never reached, and
`ReturnRecordProjection` is a pydantic model, **not** a Mapping, so the shape
production actually renders is the shape the test omitted.

With **both** AMENDMENT-2 guards removed (INJ-B15), the parametrised
`[projection]` case fails with
`"Record:\n- RMA: <class '…ReturnRecordProjection'>"` reaching the message text,
while `[mapping]` stays green. The original test could not have detected the
collapse of the guarantee under any injection.

### The persistence half of cross-assignment (item 8)

Every test in `TestBoundPersistence` stored **exactly one record**, so `records[0]`
and "the record the decision names" were the same document and the selection step
in `_merge_bound_artifact` could not be wrong. Closed by two tests on a
two-record case; under INJ-B5 the failure is the business one:
`('rec-1', {'trackingReference': 'TRK-9'}) != ('rec-2', …)` — Support's tracking
number for RMA-2 written onto RMA-1.

---

## Full injection table

| # | item(s) | what was injected | caught? | verdict |
| --- | --- | --- | --- | --- |
| B1 | 7 | ambiguous binding guesses `records[0]` instead of asking | ✅ named test | **A** |
| B2a | 7–8 | unknown reference binds to `records[0]` | ✅ named test | **A** |
| B2b | 7–8 | UNMATCHED artifact **creates** the record via the outcome signal | ✅ `events.calls == []` | **A** — DR-11 holds |
| B3 | 8 | matched reference binds to `records[0]` (decision layer) | ✅ but in `test_artifact_binding.py` | **A**, row mis-pointed |
| B4 | 8 | `_merge_bound_artifact` searches `records[0]` | — | **DISCARDED** — corrupts the version read, not the write target; not a cross-assignment at all |
| B5 | 8 | search **and** write target both `records[0]` | ✅ new test | **A** after strengthening |
| B6 | 3–6 | `draft_version` check removed | ✅ | **A** |
| B7 | 3–6 | `canonical_edit_version` check removed | ✅ only outside the named files (96/96 green there) | **A**, row mis-pointed |
| B8 | 3–6 | payload-hash check removed | ✅ both layers | **A** |
| B9 | 3–6 | `hold` takes the auto-send branch | ✅ `…parks_the_case_and_sends_nothing` | **A** |
| B10 | 3–6 | autosave accepted in `APPROVING` | ✅ only outside the named files | **A**, row mis-pointed |
| B11 | 3–6 | delivery sends the raw draft, not the frozen canonical edit | ❌ **5,235 green** | **hole closed** |
| B12 | 1–2 | facts resolved by `field_id`, ignoring `source_binding` | ✅ 23 tests | **A** — zero-hardcoding holds |
| B13 | 1–2 | `_record_attribute`'s allowlist removed | — | **DISCARDED** — removed the belt while the braces (release validation) held |
| B14 | 1–2 | AMENDMENT-2's release-validation guard removed | ✅ in `tests/configuration/` | **A**, row mis-pointed |
| B15 | 1–2 | **both** AMENDMENT-2 guards removed | ✅ only `[projection]`; `[mapping]` green | **blind test closed** |
| B16 | 17 | relay adapter's append-once guard removed | ✅ but the named test stays green | **A**, row mis-pointed |
| B17 | 17 | relay counts calls instead of writes | ✅ named test | **A** — the named test is not vacuous |
| B18 | 12 | budget checked after the call (`>=` → `>`) | ✅ | **A** |
| B19 | 9 | disclosure line dropped from agent-authored messages | ✅ 12 tests | **A** |
| B20 | 11–12 | 404 deferred until after `record_command` | ❌ **5,237 green** | **hole closed** |

**Discarded: 2** (B4, B13) — both aimed at a line that *reads* like the mechanism
rather than the line that is one. Recorded because the reasoning is the finding.

**Tests added: 7**, each injected against. Full suite after: `5,240 passed`,
one pre-existing failure (below), `514 deselected`.

---

## Production findings, reported not repaired

### 1. The merge tip is red, in the class ACC-2 predicted

`tests/test_cumulative_support_outcomes.py::test_a_rejected_return_still_opens_no_work_item`
fails on a clean tree at the base commit:

```
    if workflow.patched(_PATCH_STRUCTURED_SUPPORT_DRAFT):
E   AttributeError: '_Runtime' object has no attribute 'patched'
```

Its `_Runtime` double (line 1311) substitutes the `temporalio.workflow` module
functions the run loop calls and **never grew a `patched` method** when production
grew a `workflow.patched` call. Production is correct; the harness is stale.

This is exactly the class ACC-2 handed to its owner and predicted would recur
(STATUS "Findings handed to their owners" 1 and 2). **The acceptance-gate
consequence, which nobody had stated: one branch of item 20's deploy-replay pair
is therefore unexercised in that module** — the structured-support-draft patch
branch is never taken there, because the call raises before it can be. Item 20's
"both patch branches audited" holds for the branches ACC-2 flipped directly; it
does not hold for this module's coverage of them. And any full-suite run on this
branch is red before an auditor starts.

Not repaired: ACC does not edit another slice's harness, and the audit rule
forbids touching a failing test.

### 2. AMENDMENT-2 is defended twice, and only one layer is reachable by test

`_record_attribute`'s allowlist can be deleted with the whole suite green,
because `binding_source()` refuses the same binding at release validation first.
This is legitimate defence in depth and the docstring says so explicitly. Noted
so that a future reader who deletes it on "dead code" grounds knows it was
measured and kept deliberately.

No defect was found in `backend/src` behaviour.

---

## What was NOT reached — an unexecuted scenario is not a green one

| item(s) | why |
| --- | --- |
| **8, prompt-injection fixture** | not attempted. `test_the_clarification_question_is_composed_never_quoted` exists and was read but **never injected against**; its composed-not-quoted claim is unverified by this audit. |
| **AMENDMENT-5's retry-409 (4 named tests)** | not reached. Still category B, untouched. |
| **20, deploy replay** (`test_return_case_workflow_replay_compatibility.py`, 15) | not reached. Still category B — and see production finding 1, which bears on it. |
| **9, 11–12 resolver, remainder** | partially reached. Budget (B18), disclosure (B19) and the clarification route's authz (B20) are done; the ladder's other 20 tests and the roundtrip's remaining scenarios are **not** injected against. |
| **3–6, the rest of the 93** | the four guarantees the dispatch named are done. The remaining ~85 tests in those two files were not individually injected against. |
| **1–2, graph and literal bindings** | only `case_fact` (B12) and `return_record` (B13–B15) were injected. `graph:` batching and `literal:` were not. |
| **14 panel HTTP composition, 24–25 frontend** | unchanged from ACC-2's category C; outside this dispatch. |

Nothing above is claimed as green.
