# HARNESS-1 — RV review

**Branch** `feat/live-harness-registration`
**Head** `00471116` — *"(harness) live-infra probes register what the workflow calls, and a gate that says so"*
**Base** `7a898cf9`
**Trunk at review** `d6a08097` (moved from `63744f2a` mid-review; both checked)

## Verdict: `CHANGES_REQUIRED`

Three findings, **all in `.plan/tracks/HARNESS.ledger.md`**. No code change is
required and none is asked for: every mechanism this branch ships was executed
and holds under adversarial mutation. The findings are that the ledger records
three things the evidence does not support — one number, one limit, one
attribution — and on a run whose entire cost has been spent separating *observed*
from *asserted*, a record that overstates is the defect this branch was written
to fix, one level up.

A separate **ruling on the acceptance run** follows the findings. It is not a
finding against this branch.

---

## Base verification (contracts.md §3)

    git merge-base feat/live-harness-registration refactor/unified-return-platform
    -> 7a898cf9   (== the branch's stated base)
    git merge-base --is-ancestor 7a898cf9 refactor/unified-return-platform  -> true

`7a898cf9` is a **genuine ancestor** of trunk and was trunk's ref at branch time.
Trunk has since advanced (79 commits at the time of writing, including a move
during this review). Not a stale-base incident: the branch is behind, not
orphaned, and the ledger's step:00 records the arrival worktree at `0448d32a`
(837 behind) and the decision to branch from the ref instead. That is the §3
protocol executed correctly, and reported rather than adapted.

## Scope and ownership

Files changed vs base — five, all inside the slice:

    .plan/tracks/HARNESS.ledger.md
    backend/tests/activity_probe.py                             (new)
    backend/tests/test_return_case_policy_gate_real_infra.py
    backend/tests/test_return_case_workflow_real_infra.py
    backend/tests/test_return_case_workflow_replay_compatibility.py

**No production file touched.** No ownership breach (rule 11). No frozen-module
imports introduced. No fact-name literals outside `operations/fact_names.py`.
Rule 1 (hardcoding) does not engage: the diff is test-side only, and the
activity names it introduces are `@activity.defn(name=...)` declarations that
must match `worker.py`'s literals by construction.

## Test integrity (rule 10) — clean

    added skips / xfails ....... none
    removed assertions ......... none
    removed test definitions ... none

`scripts/ci/known_test_failures.json` is **byte-identical** across base, branch
and trunk:

    7a898cf9 : cb4d565ef4824d4eacc2edd380e296c711d60670
    00471116 : cb4d565ef4824d4eacc2edd380e296c711d60670
    d6a08097 : cb4d565ef4824d4eacc2edd380e296c711d60670

The allowlist is not widened. Verified rather than read: the default suite at
head produces exactly one failure, and it is the one already named in that file.

---

## 1. The derivation is real — verified by breaking the correspondence

`declared_activities(probe)` reads `@activity.defn` off the class's MRO. Two
mutations, run against the live class at head:

**Forward** — a brand-new `@activity.defn` method attached to `_Probe` with **no
edit to `all()`** was picked up in both the name set and the bound registration
tuple.

**Reverse** — the same method removed, and separately a *real* activity method
(`record_template_draft`) removed, each vanished from the derivation
immediately. A removed method **cannot leave a stale entry**, because there is no
store for one to be stale in: the tuple is computed on every call.

This is not a second hand-written list. `all()` is a one-line delegation with no
enumeration anywhere in it, and the mutations confirm the correspondence is
structural rather than coincidental.

**Measured coverage at head** — 22 test-built `ReturnCaseWorkflow` worker sites
(14 + 8 across the two files), 18 activity calls in the workflow, and **18
declared on each probe, with an empty symmetric difference against the call set**:

    workflow activity calls: 18
    workflow_real_infra._Probe : declared 18, missing set(), extra set()
    policy_gate_real_infra._Probe : declared 18, missing set(), extra set()

Matches the ledger's step:03 figures exactly.

## 2. The stated limit — honest, and better than recorded

`activity_probe.py`'s docstring separates the two layers correctly and does not
imply the derivation covers both:

> "Deriving the tuple closes the gap between *the methods a probe defines* and
> *the methods it registers*. It cannot close the gap between the methods a probe
> defines and the activities the workflow calls — nothing here can... That second
> gap is closed by a gate rather than by a mechanism."

Both halves check out. Layer one is closed by construction (§1 above). Layer two
is genuinely not closable by the same mechanism — the probe's *method bodies*
carry per-activity result types that no generic stub can supply — and the
docstring names the gate that closes it instead, by test name and by file, and
states it fails in the **default** suite. That last claim is the one that could
have been a comment. It is not: see §3.

**A limit honestly recorded is a point in the branch's favour, and this one is
recorded better than it needed to be.** Layer two is not merely "unclosed with an
excuse" — it is closed, by a gate I ran and mutated. No finding here.

## 3. Rule 13 — the gate is named, and it runs

**The gate is CI's own backend job.** `.github/workflows/checks.yml:129` runs
`poetry run python -m pytest tests`; `pyproject.toml` `addopts` carries
`-m "not live_infra and not browser"`. The question is which side of that line
the new guard falls on, and the answer is the safe one:

`test_return_case_workflow_replay_compatibility.py` carries **no `live_infra`
marker**, does not end in `_real_infra.py`, and requests none of the live
fixtures — so `tests/conftest.py::_suite_of` classifies it `integration`, and CI
collects it. Executed at head with the real `addopts` in force:

    pytest tests/test_return_case_workflow_replay_compatibility.py
    -> 17 passed in 6.98s              (no stack; both new tests among them)

**And it bites.** I deleted the `hold_unsettled_reviews` activity from the
workflow probe in the source file and re-ran the guard alone:

    FAILED ...::test_every_test_worker_registers_every_activity_the_workflow_calls
    test_return_case_workflow_real_infra.py:519 (_Probe): {'hold_unsettled_reviews'}

Red, naming the file, the line, the class and exactly the one activity removed —
nothing else. Restored, green again.

The anti-vacuity pin
(`test_a_test_worker_for_the_case_workflow_exists_to_be_checked`) is real and
correctly built as an **equality** on the filename set rather than a superset, so
a file that stopped being seen fails rather than passing quietly. The walker
found 22 sites where the pin's floor is 20.

One noted fragility, **not a finding**: the guard resolves probes via
`importlib.import_module(f"tests.{path.stem}")`, which assumes the file sits
directly under `tests/`. Moving a probe file into a subpackage would keep the
filename (so the pin passes) and then raise `ModuleNotFoundError` in the guard.
That fails red, not silently, which is the correct direction.

**Rule 13 satisfied.** This is the first branch on this run where the guard's
gate was the answer to the question rather than the question.

## 4. The 17-vs-12 claim — re-measured, and it is 16

Run against the live stack at **base** `7a898cf9`:

    pytest -m live_infra tests/test_return_case_workflow_real_infra.py
    -> 12 failed, 1 passed in 306.37s

    pytest -m live_infra tests/test_return_case_policy_gate_real_infra.py
    -> 4 failed, 4 passed in 145.84s   (run 1)
    -> 4 failed, 4 passed in 132.50s   (run 2)

    both files, one process
    -> 16 failed, 5 passed in 438.94s  — exactly the union, no extra

Every failure carried the same cause, verbatim:

    NotFoundError: Activity function record_template_draft ... is not registered
    on this worker, available activities: [the ten in the old hand-written tuple]

**The substantive claim is confirmed and is the branch's to keep:** the sibling
policy-gate probe was stale too, the dispatch's 12 understated it, and the
harness defect was wider than dispatched. **The number is not.** It is 16, not
17 — see Finding F1.

**The stated reason it looked healthy is confirmed, and is category-B.** The four
policy-gate tests that pass at base are exactly:

    test_a_rejected_return_opens_no_support_work_item
    test_an_absent_policy_parks_the_case_and_asks_nobody
    test_an_unanswered_review_parks_without_asking_support
    test_cancelling_during_review_stops_the_case

— a rejection, a park, a park, and a cancellation. Not one of them reaches an
approved case, so not one of them reaches the eight activities the probe was
missing. The four that fail are the approval and routing paths. This is a
textbook **category B — green because the inputs could not exercise the
property**, and it is worth recording as such on `merge.md`'s list: the probe was
equally broken in both files, and one file's scenario mix hid it.

## 5. The repair, executed

At head, against the live stack, each file alone on a fresh server:

    tests/test_return_case_policy_gate_real_infra.py -> 8 passed in 22.56s
    tests/test_return_case_workflow_real_infra.py    -> 13 passed in 73.60s
    both files, one process                          -> 21 passed in 81.10s
    both files, one process                          -> 21 passed in 90.18s

All 16 base failures pass. Observed, not inferred.

Default suite at head, the one CI gates:

    pytest tests -> 1 failed, 5188 passed, 10 skipped, 512 deselected in 259.78s

The single failure is `test_a_rejected_return_still_opens_no_work_item`, already
in `known_test_failures.json`. Reproduces the ledger's step:06 figure exactly.
`ruff check` clean on all four files.

---

# Findings

## F1 — the ledger's "17" is 16 (`HARNESS.ledger.md`, step:02)

The ledger records:

> `pytest -m live_infra tests/test_return_case_policy_gate_real_infra.py @HEAD`
> `-> 5 failed, 3 passed` … So **17** live tests were failing on this defect,
> not 12.

Measured at that same commit, three times (twice alone, once combined with the
sibling file): **4 failed, 4 passed**, the same four names each time. The total
attributable to the registration defect is **16**.

**Why it matters, and it is not pedantry.** The file is demonstrably unstable
under load (F2). The most likely explanation for the ledger's fifth failure is
that a flake was counted into a defect total — which is precisely the ambiguity
this branch's own argument objects to when it appears anywhere else, and the
ambiguity this whole run has been spent eliminating. A defect count that silently
absorbs a flake is the same class of error as a green that silently absorbs one.

**Resolution:** correct to 16 (12 + 4), or keep 17 and record the run count and
the fifth test's name so the discrepancy is adjudicable rather than invisible.

## F2 — the residual is materially worse than recorded (`HARNESS.ledger.md`, "Residual")

The ledger records:

> Each passes in full alone; run together, 1–2 tests fail per run and a
> *different* one each time, passing in isolation.

**"Each passes in full alone" is not what the file does.** Three consecutive runs
of `test_return_case_workflow_real_infra.py` **alone**, at head, once the shared
Temporal server had carried the preceding runs:

    run 1 -> 5 failed,  8 passed in 487.64s
    run 2 -> 4 failed,  9 passed in 409.07s
    run 3 -> 1 failed, 12 passed in 302.96s

against `13 passed in 73.60s` for the first run on a fresh server — a 6.6x wall
time degradation and up to **five** spurious failures, in a single file, alone.
Different tests each run, with `test_a_graph_sync_failure_parks_the_case_loudly`
recurring. Under a wider load the same file kept failing:

    five files (53 tests), one process -> 3 failed, 50 passed in 328.87s
    five files (53 tests), one process -> 1 failed, 52 passed in 206.28s

The residual is understated on both axes: **blast radius** (it is not confined to
"two files in one process" — one file alone is enough) and **magnitude** (up to
five, not one to two). The mandate's rule applies directly: a limit worse than
recorded is a finding.

**Resolution:** restate the residual to what is observed — that the module is
unstable in proportion to accumulated server state, alone as well as in company,
at up to five failures per run.

## F3 — "pre-existing" is asserted, not established (`HARNESS.ledger.md`, "Residual")

The ledger states, as fact:

> it is not this repair, and it is not new.

**Neither half is established, and one of them cannot be.** The experiment that
would settle it — reproduce the flakiness before this branch's changes — **does
not exist**: at `7a898cf9`, 16 of these 21 tests cannot reach completion at all,
so there is no pre-branch population to observe flaking. The control is
structurally unavailable, which is not the same as a control that came back
negative. I ran it anyway (base, both files, one process): the failure set was
exactly the deterministic 16 with no extra flake among the five eligible tests —
one run, five tests, far too small to carry the claim.

The ledger's cited authority does not carry it either. The docstring in
`test_return_case_workflow_real_infra.py` diagnoses this **signature**, but the
**cause** it names — a per-test `Client.connect` never closed, and executions
started and never terminated — is already remediated in both files, at base and
at head: both carry the module-scoped client fixture and the autouse
`_terminate_started_executions`. So the docstring explains why a run *used to*
flake; it does not diagnose the residual that remains after its own fix.

**The repair is nonetheless not implicated, and I record that as the finding's
other half.** Evidence, all measured here:

- The diff adds no client connection, no task queue and no un-terminated
  execution. It adds eight in-process probe methods, none of which touches
  infrastructure.
- Across every run in this review — 5 multi-file runs, 6 single-file runs — the
  four **untouched** live-infra modules (`test_case_concurrency_real_infra.py`,
  `test_durable_interception_real_infra.py`,
  `test_case_confirmation_starts_workflow_real_infra.py`, 32 tests) were green in
  **every** execution, including under the heaviest load. Three dedicated runs of
  those files alone at base: `32 passed` three times.
- **Every spurious failure observed in this review, without exception, was in
  `test_return_case_workflow_real_infra.py`** — the module whose own docstring
  says it is the one that stresses this server (long timers, business time,
  worker restarts, graph-sync retries).

So the mechanism is environmental and localized, and the repair is exonerated by
the evidence — but "pre-existing" remains the label the evidence cannot supply.

**Resolution:** relabel. "Not caused by this repair — the diff adds no
infrastructure load, and every untouched live module stayed green throughout;
whether it predates the repair is unestablished, because at base these tests do
not run" is fully supported, and is a stronger sentence than the one written,
because it survives being checked.

---

# Ruling: the acceptance run

**No. As things stand, `scripts/dev/run_real_infra_suite.sh` cannot be trusted,
and its output must not be read as an acceptance result.**

The sanctioned entry point runs all **512** live tests in one process. My
measurements of that same server, at head, with the repair in place:

| load | tests | spurious failures |
|---|---|---|
| 1 file, fresh server | 13 | 0 |
| 2 files | 21 | 0, 0, 1 |
| 1 file, loaded server | 13 | 5, 4, 1 |
| 5 files | 53 | 3, 1 |

Every one of those failures was non-reproducible in isolation and named a
different test. A 512-test single-process run creates strictly more of the
accumulated state that drives this, and will therefore return a **non-empty,
non-repeating failure set essentially every time**. Each entry in it will be
indistinguishable from a real regression without individual adjudication — which
is exactly the ambiguity this run has spent itself eliminating, and exactly the
condition under which "all 26 acceptance items green against live infra" gets
asserted from a run nobody could read.

Note the asymmetry that makes this urgent rather than merely untidy: a flaky gate
does not just produce false alarms. It produces a standing incentive to re-run
until green, and a green obtained that way is indistinguishable from a green that
means something.

**What would have to change — any one of these makes the gate readable:**

1. **Quarantine the unstable module.** Run
   `test_return_case_workflow_real_infra.py` as its own process and its own
   reported step. On the evidence it is the sole contributor, and the other ~499
   tests then produce a clean signal.
2. **Run the live suite per-module.** A fresh process per file (or
   `pytest-xdist --dist loadfile` with fresh workers) stops accumulated server
   state from crossing module boundaries. This is the smallest change to the
   script and addresses the measured mechanism directly.
3. **Adjudicate on re-run, and report it.** A scoped `--reruns 1` for
   `live_infra` where a test that passes on re-run is recorded **as a flake, not
   as a pass** — so the acceptance record shows how many greens it had to buy.
4. **Failing all of the above:** the acceptance record must state that each
   failure was re-run in isolation and adjudicated individually. A bare
   "512 passed" from one process is not, today, an artifact this repository can
   produce or a reader can believe.

This is scoped out of the branch correctly and is **not** a finding against it.
It goes to the orchestrator, and it should be settled **before** the acceptance
run, not by it.

---

# What I could and could not verify

**Live tests were run.** The docker stack was up and reachable throughout
(`scripts/infra.sh start` not needed; all six containers healthy). Everything
recorded above as a run result was executed by me in a clean detached worktree of
each commit, against the live stack, with the repository's own `addopts` in
force. Nothing here is inferred from a run I did not observe.

**Not verified.** The ledger's step:05 fault injections (b) — the synthetic
`test_zz_injection_probe.py` — was not reproduced; I ran my own two injections
instead (one deletion, one method-level mutation), both of which bit correctly.
The remaining ~490 live tests are unexecuted by this review and are not claimed
by it, in line with the ledger's own closing statement, which is correctly scoped.

---

# Summary

The engineering is sound and, unusually for this run, it is sound in the way it
claims to be: the derivation is structural and survives mutation in both
directions, the guard extension is real, it is wired to a gate CI actually runs,
and it goes red on the exact defect it was written for. The category-B diagnosis
of the sibling probe is correct and worth recording. The repair is executed
end-to-end against live infrastructure.

`CHANGES_REQUIRED` rests entirely on the ledger: a count that absorbed a flake
(F1), a residual smaller on paper than in the machine (F2), and an attribution
stated as settled that its own evidence cannot settle (F3). Three edits to one
file. Resubmit and I will re-review the complete updated diff.
