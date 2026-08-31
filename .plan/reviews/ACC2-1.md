# RV review — ACC phase 2, the acceptance slice, round 1

- **Branch:** `feat/acc-scenarios`, head `88d5ace9` — *(ACC) step:12 the final tally*
- **Base:** `45b2b871` (merge-base with trunk). **Diff reviewed:** `git diff 45b2b871..88d5ace9` — 21 files, **+4731/−0**, entirely additive.
- **Scope declared:** `backend/tests/` additions and `.plan/` only, no production edits.
- **Reviewer:** RV — Date: 2026-08-31

## Verdict: CHANGES_REQUIRED

Four findings, all one-edit fixes, **none of them in the verification work itself**.
The tests are sound, the injections are real, and the three recorded misses are
drawn where they say they are. What is wrong is confined to what the branch
*says about itself* in three places and one piece of dead code — which on a
slice whose entire product is a claim about what is verified, is exactly where a
finding costs the most.

Nothing here is a contract violation, a scope breach, or an ownership breach. No
production file is touched: the diff is 21 new files under `backend/tests/` and
`.plan/`, zero deletions, zero modifications. Rule 11 clean. No skip, no xfail,
no weakened assertion anywhere in the diff.

---

## What I verified independently, and what held

### The gating claim (rule 13) — **confirmed exactly**

```
pytest tests/acceptance --collect-only -q      → 34/36 tests collected (2 deselected)
pytest tests/acceptance -q                     → 34 passed, 2 deselected in 21.94s
pytest tests/acceptance -m live_infra --collect-only
  → test_items_15_16_review_survives_a_kill_real_infra.py::test_a_kill_mid_review_…
  → test_items_15_16_review_survives_a_kill_real_infra.py::test_an_approval_after_…
```

The two deselected are precisely the live module's two tests and nothing else.
The other 34 are in the default suite, which `.github/workflows/checks.yml:121`
runs as `pytest tests` on every push. The two are **honestly labelled, not
counted as gated**: `STATUS.md:17-28` heads the tally with the rule-13 finding
against the gate itself, and the module's own docstring (lines 40-45) states its
only gate is a person. That is the correct posture and it is stated before the
tally, not after it.

### The suite numbers — **corroborated**

Full default suite in a clean worktree: **5218 passed, 3 failed, 10 skipped, 514
deselected** (5745 total, matching ACC's 5220+1+10+514). Two of my three failures
(`test_runner_default_dotenv_path_is_repository_root`,
`test_no_module_under_src_writes_a_fact_name_as_a_string_literal`) are
path-sensitive to the worktree root and **pass on the same source in the
canonical checkout**; neither is reachable from a diff that adds only new files.
The remaining failure is the allowlisted
`test_a_rejected_return_still_opens_no_work_item`. ACC's report is accurate.

### The finding that changes what anyone may claim — **verified independently**

I did not take this on ACC's word.

- `tests/test_return_case_workflow_real_infra.py::_Probe` registers **10**
  activities (`grep activity.defn` → lines 132-222). `workflows/worker.py`
  registers **15**, including all five gate activities (lines 86-90).
- `return_case_workflow.py:444` defaults `template_review_enabled: bool = True`,
  and the real-infra file **never sets it**, so every execution that reaches the
  gate schedules `record_template_draft` against a worker that does not have it.
  The failure mode is structural, not incidental.
- `git log -1 -- backend/tests/test_return_case_workflow_real_infra.py` →
  `5b7d60f6 fix(tests): stale workflow doubles wedged the live-infra suite,
  silently`, dated **2026-08-23**. Same file, same class of defect, fixed once.

**ACC's conclusion is sustained: "all 26 items green against live infra" cannot
be asserted from a live suite in this state, independently of anything this slice
wrote.** And the second half of the finding is equally correct —
`test_return_case_workflow_replay_compatibility.py:314` derives the registered
set from `worker.py`'s AST alone (lines 336-351) and never reads a
`Worker(..., activities=…)` constructed under `tests/`, which is why it passes
while the probe rots.

**ACC was right to report rather than repair.** Its brief grants `backend/tests/`
**additions**; editing an existing test file owned by another slice is a rule-11
ownership breach, and the extended guard would be red on arrival against the
stale probe, so it belongs in one change with the repair. The reasoning at
`items-14-17-review-across-a-kill.md:184-190` is correct and I adopt it.
`feat/live-harness-registration` is out of scope here and was not reviewed.

### The tally, sampled against the tests that exist

I sampled all three columns and looked specifically for a "verified" entry
resting on a test that cannot fail. **I found none.** Every module carries an
explicit non-vacuity guard, and they are not decorative:

- item 18's `_scanned_files()` asserts `len(files) > 100` — written after a
  `parents[3]` root made `rglob` walk a missing directory and both absence scans
  report clean having read nothing;
- item 10's `test_no_branch_can_route_to_a_rung…` asserts `targets` non-empty
  before asserting what is absent from it, and `test_the_three_places_agree`
  states one six-read identity so a partial re-wiring cannot leave a half-truth;
- item 13's cadence test asserts `undisturbed[0] > 0` and `woken` non-empty,
  because "reminders ≤ max" is unfalsifiable when the cap clamps;
- items 13/19 assert `calendar_applied is True` on **every** resolution and `not
  desk.is_continuous`, with a 24/7 control that itself asserts it fired —
  dispatch condition 1 met, both halves;
- AMENDMENT-4's crash scenario asserts the **gap exists** (`mirrors == []` after
  the crash) before asserting convergence, which is what makes it a test of
  *eventually* once rather than of atomically once, and it has a clean-path
  control so convergence is to the same two rows.

The half-claims split where they say they do. Item 14's row names the workflow
half and says the HTTP panel is not exercised; item 17's row names the omc half
and category C names the relay half as not attempted; item 18's two rows are the
ordered drain (verified) and the outbound half (deferred per AMENDMENT-9,
asserted absent). Item 7's "ingress half" is the most generous label in the
table — what is verified is AMENDMENT-3's coexistence requirement across all four
published documents, by `operationId` handler prefix, not that an NL message
binds to the correct record — but the evidence column says exactly that, and
items 7-8's binding behaviour sits in categories B and C where a reader will find
it. Not a finding.

Spot-checks of the underlying claims held: `ordered_command_fields`
(`operations/integrations/outbox.py`) validates a predecessor by
`{aggregateId, eventId}` with **no stream constraint**, so item 18's "the
machinery would accept a cross-stream predecessor" is a real demonstration, not a
vacuous one. `tests/operations/test_support_ingress_store.py` carries
`test_every_enqueued_event_carries_its_causation` and
`test_the_dispatcher_drains_the_inbound_stream_in_order` as **separate** tests,
which is what makes the measured separability meaningful. `.plan/reviews/`
holds 24 documents; a naive verdict parser mis-reads `ACC1-2.md` and `V3-2.md`
(both PASS, with the word `CHANGES_REQUIRED` appearing earlier in prose) —
STATUS.md says the item-26 audit was done by reading rather than by a parser
"that would have to accept both", and that is now demonstrated rather than
asserted.

### The three recorded misses — my rulings

**INJ-15a (worker kill ≠ `continue_as_new`) — the limit is drawn correctly, and
the tally row is right.** A replacement worker replays history, so
`resolve_business_deadline` returns from history and the resumed-deadline field
is never read; the path guards continuation, not restart. The narrowed claim —
that INJ-15b proves the *deployment's wiring* (activities registered, gate
reachable, state queryable and correct after the process is gone) — is exactly
what INJ-15b can prove and no more. *"Claiming more would be claiming the
framework's guarantee as the platform's"* is the right sentence. **The tally is
not over-reaching. The test file is** — see F3.

**INJ-16a — the final instrument tests the guarantee, not its neighbourhood.**
Confirmed by reading. The third form calls `gate.deliver_approved` directly
(lines 693-700) after the review is `SENT`, which puts a genuine second delivery
in front of the gate's own short-circuit — the code that owns "exactly one". The
first two forms are retained and are worth retaining (a redelivered signal is the
ordinary at-least-once case and its absorption is an assertion in its own right),
but only the third makes the count a claim. The diagnosis of why INJ-16c passed —
the gate has closed, no wait exists to wake, so nothing reaches the deciding code
— is correct against the module as written.

**The two races — both closed.** `reached()` fires from `_record` at the top of
the activity; it is replaced by `first_review_id()` (lines 205-220), which polls
`self.review_ids`, appended only after `gate.record_draft` **returns**. The
second race — `template_review_deadline_iso` set inside `_await_template_reviews`
*after* the draft activity returns — is closed by `_open_gate()` (lines 725-736),
which polls until the deadline is non-`None` rather than reading it once. Both
waits are now on the thing actually wanted. Closed. But the racy primitive is
still in the file — see F4.

### Items 15/16 live evidence, and item 20

The live module is the strongest thing in this diff. Its `_GateProbe` carries all
fifteen activities with the five gate ones running the **real**
`SupportTemplateGateService` over a real Mongo database, and the docstring states
plainly why (lines 169-180): a probe that remembered the draft in a Python list
would remember it across a kill for reasons that have nothing to do with
durability. The deadline is asserted **equal**, not merely present. The
`_SupportSpy` counts the real gate's own `post_support_message` calls, so
"exactly one" is a count of sends production decided to make. The
`test_return_case_workflow_real_infra.py` history it declines to depend on is the
right call given that file's state.

Item 20's audit (INJ-20a → `test_a_legacy_history_opens_support_instead_of_wedging`
reds with `unexpected activity record_template_draft`; INJ-20b → 19 gate tests
red) flips the decision each way and shows both patch branches load-bearing. I
re-ran `test_return_case_workflow_replay_compatibility.py` on the clean tree: 15
passed. Verified.

---

## Findings

### F1 — rule 13, against this branch's own new guard: `posix_signal_proof.py` has no gate, and the record does not name one

**File:** `backend/tests/harness/posix_signal_proof.py` (new, 238 lines);
`.plan/acceptance/safety-nets.md:63-91`; `.plan/acceptance/STATUS.md:17-28`.
**Rule:** RV rule 13 — *for every guard a branch adds, name the gate that runs
it; if nothing invokes it in CI or in a suite CI invokes, that is a finding.*

The file is deliberately not named `test_*` so pytest cannot collect it (its own
docstring, lines 22-24). Nothing else invokes it. Its only invocation is the
`docker run` line in `safety-nets.md`, typed by hand, once. STATUS.md's rule-13
section audits the acceptance modules — *"**Every** acceptance module ACC has
written is in the default suite and therefore gated"* — and that sentence is
true, but this file is not an acceptance module and is not covered by it. The
branch's central theme is applied to every module it wrote and not to the one
guard it added outside that set.

There is a second half, and it is the part that matters more than the
bookkeeping. **CI runs on `ubuntu-latest`** (`.github/workflows/checks.yml:90`)
and runs `pytest tests` (line 121). `tests/harness/test_chaos_restart.py` carries
**no** `live_infra` marker, and
`test_stop_lets_the_worker_handle_its_signal_and_kill_does_not` is skipped only
on `os.name == "nt"`. So the behavioural pin this script substitutes for **is
gated — by CI, on Linux, on every push.** Nothing in `safety-nets.md`,
`STATUS.md` or the ledger says so. The record presents the SIGTERM link as
closed by a manual Docker run, which understates the standing coverage and
overstates the standing risk, in the one document a future reader will consult to
decide whether that link is still watched.

**Why it matters:** rule 13 exists because four defects turned out to be one
pattern — the correct mechanism existed and was bypassed. A one-off proof script
sitting permanently in `tests/harness/` reads to the next maintainer as a
standing guard. It is not one. Either it is named as a one-off dev-platform proof
whose standing gate is CI-on-Linux, or it is wired into something.

**Fix:** one line naming the gate. State in `safety-nets.md` and in STATUS.md's
rule-13 section that `posix_signal_proof.py` is a one-time proof for a
Windows-only blind spot, that it is invoked by nothing, and that the standing
gate for the same link is `test_chaos_restart.py`'s behavioural pin running under
CI on `ubuntu-latest`. No test change is required.

### F2 — STATUS.md's "Rulings owed" is stale and contradicts the tally it sits under

**File:** `.plan/acceptance/STATUS.md:141-151`.
**Rule:** the honesty of the tally — the slice's entire product.

The section says item 18's cross-stream half is *"either the ruling already made,
or **AMENDMENT-8's situation exactly** … **ACC does not get to pick**"*, and
closes with *"the tally must not record item 18 as fully green until this is
ruled."* **The ruling has been made.** AMENDMENT-9 (`contracts.md:39`, 2026-08-31)
defers that half explicitly, corrects §7's scoping line as ambiguous rather than
defending it, and names the business consequence. The branch merged it at
`6985fa50` and step:12 rewrote the two item-18 rows to cite it (lines 43-44) —
but left this section untouched.

The outcome is not wrong (item 18 is not recorded as fully green), so this is not
a false claim. It is a live document telling a reader that a ruling is
outstanding when it is on file, in the same file that cites the ruling eighty
lines earlier. On a deliverable whose value is that its statements about what is
settled can be trusted, that is a finding rather than a typo.

**Fix:** replace the section with the disposition — AMENDMENT-9 ruled it, the
deferral is asserted checkably by
`test_item_18_causal_ordering_and_the_half_that_is_unreachable.py`, and the
assertion fails the day a call site populates a cross-stream predecessor.

### F3 — the item-15 test docstring claims a guard the test provably cannot provide

**File:** `backend/tests/acceptance/test_items_15_16_review_survives_a_kill_real_infra.py:546-549`.
**Rule:** test integrity — a test that describes a property it cannot exercise.

> The deadline is asserted **equal**, not merely present. "A deadline exists"
> passes for a gate that restarted its own clock on resume, which is the failure
> this is written against: the reviewer's fifteen minutes silently becoming
> thirty because a worker bounced.

ACC's own INJ-15a measured that this is not so **for the scenario this file
stages**. Making `_await_template_reviews` ignore
`resumed_template_review_deadline_iso` left both tests green, because a worker
kill replays history and the resumed path is never taken; a gate that
re-resolves its deadline on resume would show up across a `continue_as_new`, not
across the kill this file performs. So the assertion cannot catch the failure the
docstring names it against.

The limit **is** recorded — at `items-14-17-review-across-a-kill.md:78-91` and in
STATUS.md's injection tally. It is not recorded where the next reader of the test
will be. That reader will take the docstring at face value and believe a
kill-and-restart is guarded against clock resets. This is the shape the run keeps
punishing, one level up: not a green-but-blind test, but a correct test carrying
a blind rationale.

**Fix:** carry INJ-15a's limit into the docstring — the equality assertion holds
by Temporal's replay for a kill, is the right assertion to keep, and would only
become falsifiable-by-edit across a `continue_as_new`. Two sentences. No
assertion changes.

### F4 — `_GateProbe.reached()` is dead code, and it is the exact primitive that caused both races

**File:** `backend/tests/acceptance/test_items_15_16_review_survives_a_kill_real_infra.py:197-203`
(with `self._reached`, line 189, and the `set()` in `_record`, line 195).
**Rule:** `engineering:code-review` dimension — dead code; and rule 13's spirit.

`reached()` is defined and **never called**. Every call site was replaced by
`first_review_id()` and `_open_gate()` when the two latent races were closed —
correctly. But the method that fires *when an activity starts* is still sitting
in the probe, still wired to `_record`, one autocomplete away from the next
author who needs to wait for an activity. The module's own docstring at lines
206-214 explains at length why waiting on it is a race. Leaving the trap in place
with the explanation of why it is a trap beside it is not a resolution.

**Fix:** delete `reached()` and `self._reached` (and the `setdefault(...).set()`
in `_record`), or, if a start-of-activity wait is wanted later, rename it to say
so and document that it must not be used to wait for a result.

---

## Not findings, recorded so they are not re-raised

- **The ledger has no `step:12` entry.** `ACC.ledger.md` ends at step:11; step:12
  is carried entirely in `STATUS.md`, which is the delta report. The brief asks
  for "ledger and delta report complete" and the content exists; the placement is
  a process nit, not a finding.
- **Item 7's "ingress half" label is generous** — see above. The evidence column
  is precise enough that no reader can mistake it for the binding claim.
- **Item 18's machinery demonstration runs against the Mongo double**, not real
  Mongo. Acceptable: `ordered_command_fields`'s predecessor check is a
  `find_one` the double serves faithfully, and the test would raise
  `UnknownPredecessorError` if it did not.
- **The Definition of Done is not met** — "all 26 items green against live infra"
  — and this slice says so in its own first table. With the live-infra workflow
  suite in the state verified above, that outcome was not achievable by anyone
  this round, and the brief's own line that stopping and
  reporting *"is the expected outcome, not a failure"* covers it. Incompleteness
  against a DoD the environment made unreachable is not a review finding; the
  claim that it *had* been met would have been.

---

## Closing

This is the most carefully-instrumented slice I have reviewed on this run. Seven
defects found by ACC in its own instruments, each one the same family — *green
because the inputs could not exercise the property* — and each one found because
the injection was run rather than the green trusted. The INJ-4a episode
(refusing to conclude "the test is blind" from a green produced by an env var
this repo does not define, and proving the fault real against the worker first)
is the discipline this run has been trying to install, applied without being
asked. The three misses are the most valuable content in the branch, and all
three limits are drawn correctly.

The four findings are, between them, roughly six lines of prose and one deletion.
Fix them and this is a PASS.

Re-review will cover the complete updated diff, not only the changed lines.
