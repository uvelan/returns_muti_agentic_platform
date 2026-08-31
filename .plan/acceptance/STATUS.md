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

## ⚠ RULE 13, APPLIED TO THE GATE ITSELF — and to this branch

**CI runs no live-infra test.** `.github/workflows/checks.yml`'s backend job is
`pytest tests`, and `pyproject.toml`'s `addopts` carries
`-m "not live_infra and not browser"`. The **514 deselected tests are gated by
nothing in CI** — their only gate is a human running
`scripts/dev/run_real_infra_suite.sh`, which until `9587e3a7` refused to run at
all against a healthy stack. That is why the stale live-infra probe (below) could
rot twice.

**And the audit had to be turned on this branch, where it found two errors in
opposite directions.** The scope of a rule-13 audit is every guard added, not
only the ones in `tests/acceptance/`.

* **`tests/harness/posix_signal_proof.py` was a guard with no gate.**
  Deliberately uncollectable so pytest could not silently skip it on Windows,
  correct in what it proved, and **run once by hand**. Now invoked by
  `tests/acceptance/test_the_posix_signal_proof_is_gated.py`, which runs it as a
  subprocess and asserts exit 0, four `PASS` lines and the closing statement —
  the last two because a script that stopped running its checks also exits 0.
  One implementation of the proof, two callers.
* **The residual risk was overstated in the other direction.**
  `.github/workflows/checks.yml` runs every job on **`ubuntu-latest`**, so the
  behavioural pin
  `test_chaos_restart.py::test_stop_lets_the_worker_handle_its_signal_and_kill_does_not`
  is **executed on every push** — it is skipped on this Windows workstation and
  nowhere else. ACC's earlier records said "it has never run", which was true of
  the dev machine and false of the pipeline. A `skipif(os.name == "nt")` guard is
  not the "skipped on the platform that runs it" shape when the platform that
  runs it is Linux; that criticism belongs to a guard whose *only* runner skips
  it.

**Gating of every module this branch adds**, re-confirmed after each trunk merge
by collecting rather than by counting from memory: at step:13,
`36/38 tests collected (2 deselected)`. The two deselected are the live
review-gate module's, which cannot be otherwise because a worker kill needs a
worker, and which says so in its own docstring rather than being counted as
coverage. **Everything else this branch adds runs on every push**, including the
POSIX signal proof, which the pipeline executes and only a Windows workstation
skips.

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
| **7–8** (DR-11) | ACC3: unmatched never creates a record — injected by making it create one through the outcome signal; ambiguous never guesses | `category-b-audit.md` |
| **8** (cross-assignment) | ACC3: both the decision and the persistence layer, the latter blind until a two-record case was added | `category-b-audit.md` |
| **3–6** (four guarantees) | ACC3: `hold` never auto-sends; `draft_version`, `canonical_edit_version` and the payload hash broken **one at a time**; autosave after `APPROVING` refused; sent payload = frozen payload (that one had no test at all) | `category-b-audit.md` |
| **1–2** (zero hardcoding) | ACC3: facts resolved by `field_id` instead of the configured binding reds 23 tests; AMENDMENT-2's reach, both layers | `category-b-audit.md` |
| **17** (relay half) | ACC3: append-once under a genuine redelivery, and the write-vs-call count | `category-b-audit.md` |
| **9, 12** | ACC3: disclosure line dropped; budget checked after the call instead of before | `category-b-audit.md` |
| **11–12** (authz) | ACC3: 403/404 **with no fact written** — the second half had no test | `category-b-audit.md` |

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

**ACC phase 3 audited most of this category by fault injection.** What moved is
marked below; the full record is `category-b-audit.md` and `.plan/tracks/ACC3.ledger.md`.

The phase-3 headline is **not** that these tests were blind — most were not. It
is that **six guarantees were pinned by tests in files these rows never named**,
and in two cases the *named* test survives the removal of the guarantee it is
credited with. A mis-pointed row is worse than a blind test: it makes the real
coverage invisible in both directions.

| item(s) | where | phase-3 verdict |
| --- | --- | --- |
| 1–2 config-only rendering | V1's template/renderer suites | **→ A** for `case_fact` hardcoding and AMENDMENT-2. Row was mis-pointed: the AMENDMENT-2 guard lives in `tests/configuration/test_support_template_configuration.py`. One blind test closed. `graph:`/`literal:` **remain B**. |
| 3–6 review gate | `tests/test_support_template_review_gate.py` (33), `tests/api/test_case_panel_and_reviews.py` (60) | **→ A** for the four named guarantees. Row mis-pointed twice: `canonical_edit_version` and autosave-after-`APPROVING` are pinned only in `tests/operations/test_review_aggregate.py` — these 93 stay 96/96 green when either check is deleted. **One hole closed** (sent ≠ frozen payload). The other ~85 tests **remain B**. |
| AMENDMENT-5's retry-409 | `test_a_retry_after_the_gate_closed_is_refused_and_changes_nothing`, `test_a_retry_we_cannot_adjudicate_is_503_not_409`, `test_every_state_that_cannot_retry_says_what_can_be_done_instead`, `test_every_state_the_gate_can_close_over_ends_with_a_legal_exit` | **remains B** — not reached. |
| 7–8 relay + multi-RMA | `tests/operations/test_support_message_classification.py` (22) — `test_an_unmatched_artifact_never_creates_a_record` (DR-11), `test_an_ambiguous_artifact_asks_rather_than_guesses` | **→ A.** Both DR-11 tests are load-bearing under three injections, including one that makes UNMATCHED genuinely create a record. Row incomplete: item 8's cross-assignment lives in `tests/operations/test_artifact_binding.py`, never named here. One blind test closed (persistence half). The prompt-injection fixture **remains B**. |
| 9, 11–12 resolver | `tests/operations/test_support_resolution_ladder.py` (23), `tests/operations/test_support_clarification_roundtrip.py` (18) | **→ A** for the disclosure line and budget exhaustion. **One hole closed** (403/404 wrote a durable command; only the status half was tested). Remaining ladder/roundtrip scenarios **remain B**. |
| 17 (relay half) | `test_the_transcript_entry_is_appended_once_across_a_redelivery` | **→ A**, by two tests together. The named test **stays green when the append-once guard is deleted** — it drives a double with its own dedupe. The guarantee is pinned in `tests/operations/test_support_relay_and_wiring.py`. The named test does pin that `relayed_entries` counts writes, not calls. |
| 20 (deploy replay) | `tests/test_return_case_workflow_replay_compatibility.py` (15) | **remains B** — not reached. See production finding 3 below: one branch of the pair is unexercised in `test_cumulative_support_outcomes.py`. |

## C — not reached

| item(s) | why |
| --- | --- |
| 1–2 | **partly closed by ACC3** (`case_fact` hardcoding, AMENDMENT-2). `graph:` batching and `literal:` bindings still not injected against. |
| 3–6 beyond AMENDMENT-5's exit assertion | **partly closed by ACC3** (four guarantees). AMENDMENT-5's four retry-409 tests still not reached. |
| 8 (multi-RMA cross-assignment, prompt-injection fixture) | **cross-assignment closed by ACC3**, both layers. The prompt-injection fixture (`test_the_clarification_question_is_composed_never_quoted`) was read but **never injected against** — still not verified. |
| 9, 11–12 (resolver disclosure line, budget, authz) | **closed by ACC3** — all three. The ladder's other 20 scenarios and the roundtrip's remainder are still not injected against. Item 10's tool rung remains deliberately absent. |
| **14 (panel HTTP composition)** | not attempted. The workflow half is verified; composing `GET /panel` after a reload needs the API surface up as well as a worker. |
| **17 (relay half)** | not attempted. The omc half is verified (`amendment-4-eventually-once.md`); "relayed once" through the transcript is covered in-slice (category B) and was not audited. |
| 24–25 (frontend) | **outside this dispatch's scope as written** — "backend tests only". Needs the frontend suite (`npm test`, `contracts:check`, MSW conformance) and a widened brief or a different owner. |

Nothing in C is claimed as green.

## Rulings owed — none. Item 18's was made.

**Item 18 — RULED, by AMENDMENT-9.** ACC stopped and reported that item 18's
second half names a behaviour production does not implement, and asked whether
§7's *"Acceptance 18 applies to the inbound stream"* was the ruling or an
AMENDMENT-8-shaped gap. **It was the gap.** AMENDMENT-9 defers the
"classified before any new outbound send" half, corrects §7's line as an
ambiguity rather than defending it, and names the business consequence: after an
outage the platform can send Support a message composed without regard to what
Support said during it. ACC's checkable assertion is the deferral's guard — if a
call site ever populates a cross-stream predecessor, that test must be revisited
and item 18 returns to full scope.

The tally above records item 18 as **half green, half deferred** accordingly.
This section previously said the ruling was outstanding and that "ACC does not
get to pick"; that was written before the ruling and step:12 updated the rows
without reconciling it. Corrected rather than deleted, because what the branch
asked for and what it got are both part of the record.

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
4. **(ACC3) The merge tip is red, and it is finding 1's class recurring.**
   `tests/test_cumulative_support_outcomes.py::test_a_rejected_return_still_opens_no_work_item`
   fails on a clean tree at `63744f2a` with
   `AttributeError: '_Runtime' object has no attribute 'patched'`. That module's
   `_Runtime` double (line 1311) never grew a `patched` method when production
   grew a `workflow.patched` call. Production correct, harness stale — exactly
   what findings 1 and 2 predicted would recur, in a third file.
   **The acceptance-gate consequence, not previously stated: one branch of item
   20's deploy-replay pair is unexercised in that module**, because the call
   raises before the branch can be taken. Item 20's "both patch branches
   audited" holds for the branches ACC-2 flipped directly, not for this module's
   coverage of them. Any full-suite run on this branch is red before an auditor
   starts. Reported, not repaired.
5. **(ACC3) AMENDMENT-2 is defended twice; only one layer is reachable by test.**
   `support_template_renderer._record_attribute`'s allowlist can be deleted with
   the whole suite green, because `binding_source()` refuses the same binding at
   release validation first. Legitimate defence in depth, and the docstring says
   so — recorded so a future reader does not delete it as dead code.
