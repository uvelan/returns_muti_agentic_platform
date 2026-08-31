# HARNESS — live-infra probe registration, and the gate that keeps it

Append-only. One slice, one commit: the repair and the guard extension ship
together, because either alone is worse than neither.

---

## step:00 — base verification (contracts.md §3)

The worktree arrived checked out at `0448d32a` on branch
`worktree-agent-af79f912fcfd95e05`. `refactor/unified-return-platform`'s ref is
`7a898cf9`.

    git merge-base --is-ancestor HEAD refs/heads/refactor/unified-return-platform  -> true
    git rev-list --count HEAD..refs/heads/refactor/unified-return-platform         -> 837

**The arrival HEAD is an ancestor of trunk, 837 commits behind its head.**
Branching there would have compiled, passed, and silently omitted every merged
slice — the failure §3 says has now happened six times. Branched from the ref
instead: `feat/live-harness-registration` off `7a898cf9`. Reported rather than
adapted.

Ironically `7a898cf9` is itself *"(T0) protocol: verify the named base is not an
ancestor of trunk"*.

## step:01 — reproduce, against live infrastructure

Stack up: all five datastores reachable. Ports are **not** the defaults —
Temporal is published on `17233` (Windows reserves 7147–7246) and Neo4j on
`17687`; `.env` sets `PLATFORM_TEST_TEMPORAL_TARGET=localhost:17233`.

    pytest -m live_infra tests/test_return_case_workflow_real_infra.py
    -> 12 failed, 1 passed in 429.32s

    NotFoundError: Activity function record_template_draft ... is not registered
    on this worker, available activities: draft_support_request,
    evaluate_case_eligibility, open_support_work_item,
    record_case_customer_identity, record_case_status, record_support_outcome,
    request_bay_assignment, resolve_business_deadline, send_support_reminder,
    synchronize_return_records

Ten available. Exactly the ten in `_Probe.all()`.

## step:02 — the real cause

**Duplication, in two layers, and the second one is the one that rots.**

1. `_Probe.all()` was a hand-written tuple re-listing methods the class already
   declares with `@activity.defn`. A tuple that can disagree with its own class.
2. The `_Probe` *class* is a second copy of the activity set `worker.py` already
   owns — the set `ReturnCaseWorkflow` calls. V1 phase 2 added five gate
   activities and V3 phase 2 two clarification activities to `worker.py`; the
   probes were never touched.

`5b7d60f6` (2026-08-23) fixed exactly this defect by **re-syncing the list by
hand**. It rotted again the day V1 phase 2 merged. A list re-synced by hand rots
again — which is why the fix is not a third re-sync.

**Nothing ran it.** CI's backend job is `pytest tests`; `addopts` carries
`-m "not live_infra and not browser"`. Those 512 tests were gated by a person
running a script that until `9587e3a` refused to start. Rule 13's purest
instance: *a guard with no gate is a comment*, and here the guard was 512 tests.

**The staleness was wider than the dispatch stated.** Measured at HEAD, the
*sibling* probe is stale too:

    pytest -m live_infra tests/test_return_case_policy_gate_real_infra.py @HEAD
    -> 5 failed, 3 passed

So **17** live tests were failing on this defect, not 12. The policy-gate file
looked healthier only because most of its scenarios stop at the gate and never
reach an approved case — green because the scenarios could not exercise the
property, a shape already on `merge.md`'s list.

> **Corrected in step:07 (RV HARNESS-1 / F1). The number is 16, not 17.** The
> `5 failed, 3 passed` above was a single run and is not reproducible; the file
> measures 4 failed / 4 passed. The fifth failure was a flake, counted into a
> defect total. The clause it supports — that the sibling probe was stale and the
> dispatch's 12 understated the defect — stands unchanged.

## step:03 — the repair

New `backend/tests/activity_probe.py`. `declared_activities(probe)` derives the
registration tuple from the probe's own `@activity.defn` declarations, so layer 1
is closed by construction: there is no second place to forget. Both probes'
`all()` now return `declared_activities(self)`.

Layer 2 — the probe's *method* set — cannot be closed by a mechanism; it is
genuinely hand-written. It is closed by step:04's gate instead, and the module
docstring says so rather than implying the derivation covers both.

Both probes gained the eight missing activities. The gate answers
`template_available=False`, so a case takes the composed path — which is exactly
what these scenarios exercised before the gate existed, and is a released
deployment state (`TemplateReviewDraftSet`'s own docstring names it), not a
fiction. The clarification pair is unreached by every scenario in either file and
is registered anyway, on `worker.py`'s own stated reasoning: registering only
what your scenarios happen to call is the shape of the defect.

**No production file touched.** ACC established `worker.py` is correct; measured
here as 18 workflow calls against 18 `worker.py` registrations. No halt.

## step:04 — the guard extension

`test_every_activity_the_workflow_calls_is_registered_on_the_worker` read the
workflow's calls against `worker.py`. It did not read the workers the *tests*
build. Extended, in the same file, over every `Worker(..., activities=…)` under
`tests/`:

- `test_every_test_worker_registers_every_activity_the_workflow_calls` — AST-walks
  every `tests/**/*.py` for `Worker(` / `worker.Worker(` calls carrying both
  `workflows=` and `activities=`, filters to those registering
  `ReturnCaseWorkflow` (the order-discovery and reasoning suites build their own
  workers for other workflows and are none of this rule's business), resolves
  `activities=<name>.all()` back to the probe class, and asserts the workflow's
  call set is covered by that class's declared activity names.
- `test_a_test_worker_for_the_case_workflow_exists_to_be_checked` — the
  anti-vacuity pin, separate and first. An assertion over an empty list passes;
  this pins the population (22 worker sites, both filenames, every `activities=`
  expression resolvable) so a rename or a move cannot silently empty the walk.

Checked against each probe's **declared** names rather than a constructed
instance: the policy-gate probe takes a required constructor argument, and a
check that silently skipped probes it could not build would be the same vacuum.

**It runs in the default suite** — the file carries no `live_infra` marker, so
`pytest tests` collects it and CI runs it. The whole defect is visible from
source; no stack needed. That is the gate this guard was missing.

## step:05 — fault injection (both directions, each verified for cause)

Per `merge.md`'s newest shape — *an injection red for the wrong reason* — each
injection was checked for having done what it claims, not merely for going red.

**Direction 1 — does it catch this exact defect?**
`git checkout HEAD -- tests/test_return_case_workflow_real_infra.py`, guard re-run:

    1 failed, 16 passed
    test_return_case_workflow_real_infra.py:342 (_Probe): {record_template_draft,
      record_template_revision, rerender_template_draft, hold_unsettled_reviews,
      snapshot_sent_template, record_clarification_answer,
      relay_clarification_to_support, case_has_return_details}
    ... and 13 more worker sites, same set

*Tell that it is red for the right reason:* it names the probe by file, line and
class; the eight names are exactly the complement of the ten in the live
`NotFoundError`'s "available activities"; the policy-gate file is **absent**
(only the reverted file was reverted); and the population pin still **passed**,
so the walker still saw 22 sites — the red is the assertion firing, not a
collapsed walk. Restored → 17 passed.

**Direction 2 — is it vacuous? Red on a *newly* under-registered worker?**
Two independent injections, neither touching the file repaired above.

(a) Removed one `@activity.defn` decorator from the *policy-gate* probe:

    test_return_case_policy_gate_real_infra.py:332 (_Probe): {record_case_status}
    ... 8 sites

Different file, different activity, and exactly the one decorator removed —
nothing else named, so the red tracks the injection precisely.

(b) Added a brand-new `tests/test_zz_injection_probe.py` declaring one activity
and building a `ReturnCaseWorkflow` worker from it:

    test_zz_injection_probe.py:24 (_FreshProbe): {17 activities}

The walker **discovered a file that did not exist when the guard was written** —
so the guard covers new workers, not a hardcoded pair. The population pin fired
too, correctly: the set changed. Injection file deleted; 17 passed.

## step:06 — results

**Live, against the running stack** (each file in full, on its own):

    pytest -m live_infra tests/test_return_case_workflow_real_infra.py
    -> 13 passed in 93.98s        (was 12 failed / 1 passed, 429s)

    pytest -m live_infra tests/test_return_case_policy_gate_real_infra.py
    -> 8 passed in 22.10s         (was 5 failed / 3 passed)

All 12 named failures pass. Executed, not assumed.

**Default suite** (`pytest tests`, the one CI gates):

    1 failed, 5188 passed, 10 skipped, 512 deselected in 250.82s

The one failure is the named `test_a_rejected_return_still_opens_no_work_item`.
Zero new failures and no newly-passing named failure, so
`scripts/ci/known_test_failures.json` is correct unchanged and is not touched.

`ruff check` clean on all four files. (The repo has five pre-existing `ruff check`
findings under `tests/`, all in files this slice does not touch. `ruff format` is
not a gate — `test_return_case_workflow_real_infra.py` is format-dirty at HEAD —
so the two cosmetic reflows `ruff format` introduced outside the change were
reverted to keep the diff purely additive.)

## Residual — recorded, not claimed

**Running the two real-infra files in one process is flaky.** Each passes in full
alone; run together, 1–2 tests fail per run and a *different* one each time,
passing in isolation. That is the load signature
`test_return_case_workflow_real_infra.py`'s own docstring describes and diagnoses
(shared dev Temporal, task-queue contention) — it is not this repair, and it is
not new. It matters for the acceptance gate because the sanctioned entry point
runs all 512 live tests in one process, so the cross-module cost is real and
larger than either file measured alone. Not fixed here: it is outside this
slice's scope and its diagnosis (namespace isolation) was already tried and
rejected on evidence in that docstring.

> **Corrected in step:07 (RV HARNESS-1 / F2 and F3).** Two of the three claims in
> the paragraph above are wrong. "Each passes in full alone" is false — one file
> alone flakes, at up to five failures per run. "It is not new" is unestablished
> and cannot be established, because at base 16 of these 21 tests never finish, so
> the control run does not exist. "It is not this repair" is *correct*, but not for
> the reason given: it is carried by other evidence, set out in step:07.

**The dispatch's wider consequence stands, and is now discharged.** *"All 26
acceptance items green against live infra"* could not have been asserted from the
suite in its arrival state — 17 live tests were failing on a stale harness, and
nothing would have said so. It can be asserted now for these two files; the
remaining ~490 live tests are unexecuted by this slice and are not claimed.

*(17 → 16, per step:07 / F1. The consequence is unchanged by the number.)*

---

## step:07 — RV HARNESS-1 corrections (F1, F2, F3)

RV returned `CHANGES_REQUIRED` on `.plan/reviews/HARNESS-1.md`. Three findings,
**all in this ledger, none in the code.** The code was verified by RV's own
injections and held; rule 13 is satisfied by the gate itself, since
`test_return_case_workflow_replay_compatibility.py` carries no `live_infra`
marker and CI's `pytest tests` therefore collects it.

**Form of the correction.** contracts.md §3 makes this ledger append-only, so the
three wrong statements are not rewritten. Each keeps its original wording, with a
block-quoted forward pointer inserted beneath it, and the corrected record is
here. What was believed and what is now known are both legible; that is the point
of an append-only record, and rewriting the three lines would have destroyed
exactly the evidence this branch exists to protect.

**Provenance of the numbers below.** Every measurement in this step was made by
RV against a healthy stack (~3 minutes per run) and is reproduced here as RV
reported it. **I did not re-derive them**, and I am not claiming them as my own
runs. Step:08 onward records runs I executed myself.

### F1 — the count is 16, not 17

The step:02 figure `5 failed, 3 passed` for the policy-gate file was one run.
RV measured the same file at the same base three times:

    pytest -m live_infra tests/test_return_case_policy_gate_real_infra.py   @base 7a898cf9
    -> 4 failed, 4 passed   (run 1)
    -> 4 failed, 4 passed   (run 2)
    -> 4 failed, 4 passed   (run 3, in-process with the sibling file; the union, no extra)

    pytest -m live_infra tests/test_return_case_workflow_real_infra.py      @base 7a898cf9
    -> 12 failed, 1 passed

**12 + 4 = 16 live tests were failing on the registration defect.** Every one of
them carried the same `NotFoundError: Activity function ... is not registered`.
The four that passed are the rejection/park/cancel scenarios, unchanged from
step:02's category-B reading, which stands.

**State it plainly: a flake was counted into a defect total.** The fifth failure
was not a fifth defect; it was the module's instability, recorded as damage. And
it is not a separate mistake from F2 — **it is the residual, at n=1.** The same
mechanism that makes a 512-test acceptance run unreadable made a 8-test
measurement wrong by one, and in both cases the error has the same shape: a
non-reproducing failure absorbed into a total that reads as deterministic. A
defect count that silently absorbs a flake and a green that silently absorbs one
are the same error with the sign flipped. This ledger did the first while arguing
against the second.

### F2 — the residual, restated to what was observed

"Each passes in full alone" is **false**. RV ran the workflow file **alone, in its
own process, six times** at head:

    pytest -m live_infra tests/test_return_case_workflow_real_infra.py   @head 00471116
    -> 13 passed
    -> 1 failed
    -> 13 passed
    -> 3 failed
    -> 13 passed
    -> 1 failed

and the failing tests **differ every time**. Across those runs and RV's earlier
loaded-server set the names seen were:

    test_the_support_wait_survives_a_worker_restart
    test_a_bay_result_arriving_before_the_wait_is_kept
    test_a_graph_sync_failure_parks_the_case_loudly
    test_the_case_completes_when_support_answers

One file. One process. No sibling module in the room. Under a loaded server the
same single file has produced **5, 4 and 1** spurious failures on consecutive
runs, against `13 passed in 73.60s` on a fresh one.

The sibling is not implicated:

    pytest -m live_infra tests/test_return_case_policy_gate_real_infra.py @head
    -> 8 passed, clean every time

Full load table, all RV's runs at head:

| load | tests | spurious failures observed |
|---|---|---|
| 1 file (workflow), fresh server | 13 | 0 |
| 1 file (workflow), loaded server | 13 | 5, 4, 1 |
| 1 file (workflow), six isolated runs | 13 | 0, 1, 0, 3, 0, 1 |
| 1 file (policy gate), any | 8 | 0 |
| 2 files, one process | 21 | 0, 0, 1 |
| 5 files, one process | 53 | 3, 1 |

**Every spurious failure in every run was in
`test_return_case_workflow_real_infra.py`.** Without exception.

So the residual was understated on both axes. **Blast radius:** it is not "two
files in one process" — one file alone is enough. **Magnitude:** it is not "1–2
tests" — it is up to five. The correct statement is that
`test_return_case_workflow_real_infra.py` is unstable in proportion to
accumulated Temporal server state, alone as well as in company, at up to five
failures per run, with a non-repeating failure set.

### F3 — the attribution, relabelled

The ledger asserted "it is not new". **That cannot be established, and the reason
is structural rather than a matter of effort: at base, 16 of these 21 tests never
finish, so there is no pre-repair population that could be observed flaking. The
control run does not exist.** RV ran base anyway and got exactly the deterministic
16 with no extra flake among the five eligible tests — one run, five tests, far
too small to carry anything. An unavailable control is not a control that came
back negative, and the ledger wrote it as though it had.

The docstring the ledger cited does not close the gap either: it diagnoses this
*signature*, but the *cause* it names — an unclosed per-test `Client.connect`,
executions started and never terminated — is already remediated at base and at
head by the module-scoped client fixture and the autouse
`_terminate_started_executions`. It explains a flake that was fixed, not the one
that remains.

**The repair is nonetheless exonerated — on other evidence, which is a different
and stronger claim than the one it replaces.** Three items, all measured:

1. **The diff adds no infrastructure load.** It adds one module
   (`activity_probe.py`) and eight in-process probe methods. No client
   connection, no task queue, no workflow execution, nothing that touches a
   datastore.
2. **Four untouched live modules stayed green throughout.**
   `test_case_concurrency_real_infra.py`, `test_durable_interception_real_infra.py`,
   `test_case_confirmation_starts_workflow_real_infra.py` and their siblings —
   **32 tests — were green in all eleven of RV's runs**, including the heaviest
   loads, and `32 passed` three times in dedicated runs at base.
3. **Localisation.** Every spurious failure observed anywhere in this review was
   in the one module whose own docstring says it is the one that stresses this
   server: long timers, business time, worker restarts, graph-sync retries.

The distinction matters and is the whole of this finding. *"We could not test it
and nothing broke"* is an absence of evidence dressed as evidence. *"The diff
adds no infrastructure load, and 32 tests in four untouched live modules were
green across every run"* is a positive result about the repair, and it survives
being checked. **The residual is not caused by this repair. Whether it predates
the repair is unestablished, because at base these tests do not run.**

**Files touched:** `.plan/tracks/HARNESS.ledger.md` only. No code change; none
was asked for and none is made.

**Command and output:**

    $ git diff --stat 00471116 -- .
     .plan/tracks/HARNESS.ledger.md | 168 +++++++++++++++++++++++++++++++++++++++++
     1 file changed, 168 insertions(+)

Insertions only — the append-only property is machine-checkable here, and this is
the check.

**Next step:** step:08 — RV's acceptance-run ruling. Per-module execution in
`scripts/dev/run_real_infra_suite.sh`.
