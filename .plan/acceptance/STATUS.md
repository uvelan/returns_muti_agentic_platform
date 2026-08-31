# ACC phase 2 — where the 26-item gate actually stands

Read against the brief's grouping. The distinction this run has been strict about
is kept: **an unexecuted scenario is not a green one**, and a scenario covered by
another slice's suite that ACC has not audited is not an ACC verification either.

Three categories, and nothing is promoted between them by inference.

* **A — verified here, with fault injection.** ACC wrote or audited the scenario,
  injected the fault it exists to catch, and recorded before/after evidence.
* **B — in-slice coverage located, not audited.** Tests exist and were found by
  name; ACC has **not** read their bodies or injected against them. Every slice
  on this run shipped at least one green-but-blind test, so "a test exists" is
  not a finding.
* **C — not reached.** No ACC work, with the reason stated.

## ⚠ RULE 13, APPLIED TO THE GATE ITSELF — CI runs no live-infra test

`.github/workflows/checks.yml`'s backend job is `pytest tests`, and
`pyproject.toml`'s `addopts` carries `-m "not live_infra and not browser"`. So
the **512 deselected tests are gated by nothing in CI** — their only gate is a
human running `scripts/dev/run_real_infra_suite.sh`, which until `9587e3a7`
refused to run at all against a healthy stack.

That is rule 13's exact statement about the acceptance gate's own instruments,
and it governs how the remaining durability items should be written: a
`_real_infra` scenario is a guard whose gate is a person remembering. **Every
acceptance module ACC has written is in the default suite and therefore gated.**

---

## A — verified here

| item(s) | scenario | evidence |
| --- | --- | --- |
| safety net (a) | live-infra chaos smoke, first execution ever, re-verified from cold with a different fault | `safety-nets.md` |
| safety net (b) | `killpg(getpgid(pid), SIGTERM)` through the session, proved under Linux, re-injected | `safety-nets.md` |
| **10** | DEFERRED per AMENDMENT-8; unreachability asserted in the three places that must agree, plus a six-read agreement identity | `item-10-deferral.md` |
| **13** | per-case cadence, cap a case total across N reviews; no duplicate reminders across a wake | `items-13-19-business-time.md` |
| **19** | a wait spanning a closed weekend fires no retroactive burst | `items-13-19-business-time.md` |
| **17** (omc half) | eventually once, **never** atomically — the crash gap observed as a gap, then closed by redelivery | `amendment-4-eventually-once.md` |
| **7** (ingress half) | AMENDMENT-3: three surfaces coexist in all four published documents, by handler | `amendment-3-coexistence.md` |
| **18** (ordered-drain half) | chain + drain audited by injection; **the chain/drain separability measured** — dispatch condition 3, and the deciding evidence for AMENDMENT-9 | `item-18-causal-ordering.md` |
| **18** (outbound-ordering half) | **DEFERRED per AMENDMENT-9**, checkably — asserted absent, and the assertion is the deferral's guard | `item-18-causal-ordering.md` |
| **14** (workflow half) | `execution_state` queryable and correct after a worker kill; the panel's HTTP composition is **not** exercised | `items-14-17-review-across-a-kill.md` |
| **15** | kill mid-review, live: draft, edit rows and remaining timeout survive; the resumed worker does not re-draft | `items-14-17-review-across-a-kill.md` |
| **16** | one approval, one message, one delivery identity — across a restart, and under a genuine second delivery | `items-14-17-review-across-a-kill.md` |
| **20** | both patch branches audited by flipping the decision each way | `items-14-17-review-across-a-kill.md` |
| **21** | byte-identical across two interpreters with different hash seeds, including under eviction | `items-21-22-context-and-pinning.md` |
| **22** | compaction clauses audited; the release pin across a promotion, covered by nothing before | `items-21-22-context-and-pinning.md` |
| **26** | every merged branch has a recorded `PASS`; the calibration bait was caught | below |
| AMENDMENT-5 (partial) | a weekend close leaves **no review without a legal exit** | `items-13-19-business-time.md` |

## Injection tally

**Twenty-six landed.** **Seven discarded or recorded as misses** for being red —
or green — for the wrong reason: an invalid released binding that failed schema validation before any
assertion ran; a `$set` upsert the Mongo double refuses; disabling
`pin_routing_decision`'s early return, which is not the mechanism; and a wake
scenario whose wake could not wake.

**Seven defects found in ACC's own instruments**, all one family — *green because
the inputs could not exercise the property*:

1. a wake that left the predicate false, so the harness could not tell it from a
   timeout, **and** a ceiling assertion the cap clamps anyway;
2. a document fixture parametrised by basename where three of four snapshots
   share one — four reported, two read. Tell: one injected file reddened three
   parameter sets;
3. a source scan rooted one directory too high — `rglob` on a missing directory
   yields nothing and raises nothing, so both scans reported no violations having
   read no source. An absence assertion would have passed *for the reason the
   finding claims*;
4. a squeezed-budget test at a budget that omitted nothing, the projection alone
   accounting for the drop;
5. (INJ-22a) an injection aimed at the branch that reads like the guard rather
   than the CAS filter that is one;
6. an item-16 scenario that counted one send after requesting one send — and
   whose second form, signalling twice, stayed green **even with both guards
   removed together**, because by then the gate has closed and no wait exists to
   wake. Only calling `deliver_approved` again put a real duplicate in front of
   the guard that owns the guarantee;
7. two latent races in the live module — `reached()` fires when an activity
   *starts*, and the review deadline is set *after* the draft activity returns —
   where fixing the first exposed the second it had been masking.

**One injection miss recorded as a limit rather than smoothed over:** ignoring
the resumed deadline changes nothing across a *worker kill*, because the
replacement worker replays history. For a kill, most of item 15's claims hold by
Temporal rather than by the platform, and the scenario claims only what INJ-15b
proves — that the deployment's wiring is right and the state is there.

**One production finding of the same shape, in `src/`:** `pin_routing_decision`'s
early return carries the docstring about keeping the first pin and enforces
nothing; the `{… field: None}` CAS filter is what does.

**One expectation of ACC's own was wrong and the code was right** — a stage that
crashes mid-invocation keeps the release pinned *before* the call. Found by
reading the source instead of reporting a defect, and recorded in the test rather
than quietly amended.

## Item 26, audited

`merge.md`'s table names a final `PASS` for every merged branch. Checked one by
one against `.plan/reviews/`: twenty-four documents, fifteen branches, **no
merged branch without a `PASS`**, every round count matching, and
`calibration-1.md` recording `CHANGES_REQUIRED` on a branch that never merges.
One formatting note, not a finding: `V1p1-1.md` writes its verdict as a bullet
where every other file uses a heading — which is why the audit was done by
reading rather than by a parser that would have to accept both.

Deliberately not a test: a `backend/tests/` module parsing `.plan/reviews/` would
make the application suite fail on a planning document.

## B — in-slice coverage located, not audited

| item(s) | where |
| --- | --- |
| 1–2 config-only rendering | V1's template/renderer suites |
| 3–6 review gate | `tests/test_support_template_review_gate.py` (33), `tests/api/test_case_panel_and_reviews.py` (60) |
| AMENDMENT-5's retry-409 | `test_a_retry_after_the_gate_closed_is_refused_and_changes_nothing`, `test_a_retry_we_cannot_adjudicate_is_503_not_409`, `test_every_state_that_cannot_retry_says_what_can_be_done_instead`, `test_every_state_the_gate_can_close_over_ends_with_a_legal_exit` |
| 7–8 relay + multi-RMA | `tests/operations/test_support_message_classification.py` (22) — `test_an_unmatched_artifact_never_creates_a_record` (DR-11), `test_an_ambiguous_artifact_asks_rather_than_guesses` |
| 9, 11–12 resolver | `tests/operations/test_support_resolution_ladder.py` (23), `tests/operations/test_support_clarification_roundtrip.py` (18) |
| 17 (relay half) | `test_the_transcript_entry_is_appended_once_across_a_redelivery` |
| 20 (deploy replay) | `tests/test_return_case_workflow_replay_compatibility.py` (15) |

## C — not reached

| item(s) | why |
| --- | --- |
| 1–2 | time. Reachable in the normal suite. |
| 3–6 beyond AMENDMENT-5's exit assertion | time. Reachable in the normal suite. |
| 8 (multi-RMA cross-assignment, prompt-injection fixture) | time. |
| 9, 11–12 (resolver disclosure line, budget, authz) | time. The fact rung and the clarification path are reachable; only item 10's tool rung is deliberately absent. |
| **14 (panel HTTP composition)** | not attempted. The workflow half is verified; composing `GET /panel` after a reload needs the API surface up as well as a worker. |
| **17 (relay half)** | not attempted. The omc half is verified (`amendment-4-eventually-once.md`); "relayed once" through the transcript is covered in-slice (category B) and was not audited. |
| 24–25 (frontend) | **outside this dispatch's scope as written** — "backend tests only". Needs the frontend suite (`npm test`, `contracts:check`, MSW conformance) and a widened brief or a different owner. |

Nothing in C is claimed as green.

## Rulings owed

**Item 18's cross-stream half.** Its text names "outbound waits for its inbound's
classification, unrelated approval does not". Measured: only two of §7's four
streams have a producer, no call site anywhere populates a cross-stream
predecessor, and the machinery *would* accept one. §7's sentence *"Acceptance 18
applies to the inbound stream"* is either the ruling already made, or
**AMENDMENT-8's situation exactly** — a separately frozen acceptance item
narrowed by one line of a contract section against something nothing can reach.
ACC does not get to pick. Nothing is blocked; **the tally must not record item 18
as fully green until this is ruled.**

## Production defects

1. **`scripts/dev/run_real_infra_suite.sh` preflighted the wrong SQL Server
   port** — reported at step:01, **fixed on trunk at `9587e3a7`**, and confirmed
   fixed here: the script now prints *"live-infrastructure suite: all five
   datastores reachable"* and proceeds.
2. **`pin_routing_decision`'s early return is not the guard its docstring
   describes** (above). Not a defect in behaviour — the CAS filter is correct and
   sufficient — but the branch a reader would cite is inert, which is the shape
   rule 13 was written for. Reported, not touched.

No defect found in `backend/src` behaviour. `git diff` against the merge base
touches nothing outside `backend/tests/` and `.plan/`.


## Findings handed to their owners (reported, not repaired)

1. **`tests/test_return_case_workflow_real_infra.py` is broken against live
   infrastructure — 12 of 13 tests.** Its `_Probe` predates V1's review gate and
   lacks the five gate activities `workflows/worker.py` registers. Production is
   correct; the harness is stale, and has been since V1 phase 2 merged. **The
   same file was fixed for the same class of defect on 2026-08-23**
   (`5b7d60f6 fix(tests): stale workflow doubles wedged the live-infra suite,
   silently`) and rotted again, because nothing runs it. One edit fixes it.
   **"All 26 items green against live infra" cannot be asserted from a live
   suite in this state.**
2. **The guard for exactly that defect exists, is gated, and does not reach it.**
   `test_every_activity_the_workflow_calls_is_registered_on_the_worker` derives
   the called set from the workflow and the registered set from `worker.py`, and
   passes because `worker.py` is right. It does not read the workers the *tests*
   construct. Extending it over every `Worker(..., activities=…)` under `tests/`
   closes the class — and is **red on arrival** against the stale probe, so it
   belongs with the repair, in one change, owned by that probe's slice.
3. **`pin_routing_decision`'s early return enforces nothing** — the
   `{… field: None}` CAS filter does. Behaviour correct; the branch a reader
   would cite is inert.
