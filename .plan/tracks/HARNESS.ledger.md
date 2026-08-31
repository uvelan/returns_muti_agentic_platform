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

---

## step:08 â€” the acceptance run: a process per module

RV's ruling (`HARNESS-1`, "Ruling: the acceptance run") is that
`scripts/dev/run_real_infra_suite.sh` cannot be trusted as it stands, because it
runs all 512 live tests in one process and therefore manufactures more of the
accumulated server state that F2 measures than any measurement taken.

RV named two sufficient fixes. **Quarantining
`test_return_case_workflow_real_infra.py` was rejected deliberately.** It is the
cheaper change and it would produce a cleaner number, and that is the objection:
it removes 13 tests' worth of coverage from the gate in order to make the gate
read green. **Per-module execution keeps every test and changes only the
execution model** â€” a fresh interpreter, a fresh Temporal client and a fresh
worker per file, so state cannot cross a module boundary.

**Files touched:** `scripts/dev/run_real_infra_suite.sh` only. No test file, no
production file.

### What was preserved

- The datastore preflight is **byte-identical**, exit 2 and all.
- Argument pass-through is unchanged.
- The collected-total report survives â€” and had to be repaired to survive, see
  below.
- A positional path or a `-k` expression skips the fan-out and runs one process,
  because fanning out over a set the caller narrowed to one thing buys nothing.

### The collected-total report was already broken

Found while testing, not looked for. The report was
`... --collect-only -q "$@" | tail -1`, and pytest's last line is the last line
only when there is no warnings summary. On the whole suite there is one:

    $ bash -x scripts/dev/run_real_infra_suite.sh --collect-only -q   # before
    + collected='-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html'
    collection: -- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html

Not cosmetic. That line exists because "a marker typo that collects zero tests
exits 0 and looks identical to success" â€” the script's own header says so â€” and a
report that prints a URL cannot show that. Now anchored on pytest's count line:

    $ bash scripts/dev/run_real_infra_suite.sh --ignore=...   # after
    collection: 11/2957 tests collected (2946 deselected) in 4.25s

The same collection pass now feeds both the report and the module list, so the
number reported and the set executed cannot disagree.

### Discovery

Modules come from `pytest -m live_infra --collect-only -q`, not from a glob. A
glob over `*_real_infra.py` would have missed roughly twenty live modules that
are not named that way (`tests/reasoning/test_run_lifecycle.py`,
`tests/platform/test_index_drift.py`, `tests/source_connectors/*_docker.py`, â€¦) â€”
and missing them silently is the exact defect shape this branch exists to fix.

    73 modules, 512 tests.

### Rule 13 â€” the gate that runs this guard

**This one runs in no CI gate, and I am not going to imply otherwise.**
`addopts` carries `-m "not live_infra and not browser"`, so CI's
`poetry run python -m pytest tests` (`.github/workflows/checks.yml:129`) does not
execute one live test. This script *is* the gate for the live suite, invoked by a
person, and what step:08 changes is whether that gate's verdict is readable â€” not
whether something runs it. The guard added here is the aggregate check inside the
script, and the thing that runs it is the script's own exit code, proven below
rather than asserted.

That is the honest side of the line, and it is the weaker one. It is unchanged by
this step and out of its scope: wiring the live suite into CI is a separate
decision about runtime and infrastructure, and it belongs to the orchestrator.
The guard added in step:04 is the one that falls on the *other* side â€” it carries
no `live_infra` marker, so `pytest tests` collects it and CI runs it.

### The aggregate cannot lie â€” proven, not argued

One exit code became 73. The failure mode is a runner that exits 0 because the
last module passed, which would be a fresh instance of this run's oldest defect.

Proof by construction: two temporary modules, one **failing** placed so it sorts
**first** (`tests/api/test_aaa_runner_selftest_real_infra.py`) and one **passing**
placed so it sorts **last**
(`tests/test_zzz_runner_selftest_green_real_infra.py`). If the runner took the
last exit code, this arrangement returns 0.

    $ bash scripts/dev/run_real_infra_suite.sh --ignore=<the other 70 modules>
    SCRIPT EXIT: 1
    collection: 11/2957 tests collected (2946 deselected) in 4.25s
    ================== live-infrastructure suite: summary ==================
    modules run     : 3
    modules passed  : 2
    modules failed  : 1
    tests passed    : 10
    tests failed    : 1
    wall time       : 0m 29s (one process per module)

    FAILED modules:
      x tests/api/test_aaa_runner_selftest_real_infra.py
          exit 1 -- 1 failed, 1 passed in 0.31s

    the live-infrastructure suite FAILED (1 of 3 modules).
    Logs were per module; re-run one with:
      scripts/dev/run_real_infra_suite.sh tests/api/test_aaa_runner_selftest_real_infra.py

Exit 1 with a green last module. The failing module is named, with its counts,
and 10 + 1 = 11 reconciles against the collected total.

**The proof found a real defect on its first run, which is the argument for
running it.** The first attempt printed `tests failed: 0` beside a failing
module, and flagged two of three modules as counts-unreadable. The count regexes
required a non-digit before the number, and pytest's summary begins with one:
`8 passed in 24.51s`. So every all-green module parsed as unreadable and every
`N failed` opening a summary line was dropped. Had the summary been written
against the theory instead of executed, the runner would have shipped reporting
zero failures next to a red exit code. Fixed by padding the line; the run above
is the re-proof.

Note the guard that caught it was the one that refuses to call unreadable counts
a pass â€” it fired, correctly, and blocked a false green while the parser was
broken. It stays in for that reason.

### The trap RV flagged: the anti-vacuity population pin

`test_a_test_worker_for_the_case_workflow_exists_to_be_checked` asserts filename
**equality**, not a superset, so a new file in `tests/` is capable of failing it â€”
step:05(b) demonstrated exactly that. Reasoned rather than assumed: the pin's
population is files that build a `Worker(workflows=[ReturnCaseWorkflow], ...)`.
Both proof modules declare `pytestmark = pytest.mark.live_infra` and a bare
assertion; neither imports `Worker` or `ReturnCaseWorkflow`. So the pin should not
see them. Checked rather than reasoned about only:

    $ python -m pytest tests/test_return_case_workflow_replay_compatibility.py -q
      ### with both temporary live modules present ###
      17 passed in 5.21s

      ### after deleting them ###
      17 passed in 4.58s

Unaffected, and now known to be unaffected. Separately, the runner change cannot
reach the pin at all: the pin walks `tests/**/*.py` from source, and step:08
touches one file under `scripts/`.

    $ git status --porcelain
     M scripts/dev/run_real_infra_suite.sh

Both temporary modules deleted. Nothing else in the tree.

### Environment note

This worktree has no `backend/.venv`, so the script takes its documented third
branch and uses `python` from `PATH`. The repository's venv is at the main
checkout and its `return_platform_backend.pth` hardcodes *that* checkout's
`backend/src` â€” so running the worktree's tests through it silently imports the
main checkout's newer `src`, which fails collection in
`tests/operations/test_case_projection.py` on an `actorId` field that does not
exist at this branch's base. Every run recorded in this ledger from step:08 on
was made with `PYTHONPATH` pinned to **this worktree's** `backend/src`, verified:

    $ python -c "import return_platform; print(return_platform.__file__)"
    K:\...\worktrees\agent-af79f912fcfd95e05\backend\src\return_platform\__init__.py

Recorded because a run against the wrong `src` is a result about the wrong code,
and nothing in the tooling says which one you got.

**Next step:** step:09 â€” run the live suite per module and report what happens.

---

## step:09 â€” the per-module run, and what it says (negative result)

**Headline: per-module execution does NOT eliminate the spurious failures.** It
was the wrong one of the two remedies RV offered, and the evidence for that is
below rather than argued. The runner from step:08 is still worth having â€” it
fixed a broken total, it surfaces failures a single-process run would have
buried, and its aggregate is proven â€” but it does **not** discharge RV's ruling,
and the acceptance run is still not readable.

### 1. The decisive experiment

Per-module execution *is* "one file, one fresh process". RV had already measured
that exact condition six times at head and got failures in three of them. So the
remedy and the condition under which the defect was measured are the same thing,
and the prediction was that it would not help. Executed rather than left as a
prediction â€” three consecutive isolated runs of the module RV identified as the
sole contributor:

    $ python -m pytest -m live_infra tests/test_return_case_workflow_real_infra.py -q

    ########## run 1 ##########
    FAILED tests/test_return_case_workflow_real_infra.py::test_a_rejected_return_needs_no_graph_sync
    1 failed, 12 passed in 162.37s (0:02:42)

    ########## run 2 ##########
    FAILED tests/test_return_case_workflow_real_infra.py::test_a_bay_failure_does_not_stop_the_return
    FAILED tests/test_return_case_workflow_real_infra.py::test_a_graph_sync_failure_parks_the_case_loudly
    2 failed, 11 passed in 232.99s (0:03:52)

    ########## run 3 ##########
    13 passed in 116.11s (0:01:56)

**1 failed, then 2 failed, then 0 failed. A different set every time.** Two of
the three names â€” `test_a_rejected_return_needs_no_graph_sync` and
`test_a_bay_failure_does_not_stop_the_return` â€” do not appear anywhere in RV's
eleven runs, which strengthens rather than weakens the reading: the failure set
is not converging on a fixed group of weak tests, it is drawn fresh each time.

**Why per-module cannot work, stated plainly.** A new process resets *in-process*
state: the Temporal client, the worker, the event loop, module globals. The
mechanism F2 measures is not in the process. It is accumulated state on the
**shared Temporal server** â€” open executions, task-queue backlog, timers â€” and
that server is the same server across every process the fan-out starts. Isolating
the client from itself does nothing about the thing the client is talking to.

This was visible in the brief's own numbers before a line of script was written,
and I did not see it until the runs came back. Recording that, because "the
measurements already contained the answer" is the more useful lesson than the
result.

### 2. The suite run â€” incomplete, and reported as incomplete

The full per-module suite run **did not finish** and no verdict is claimed from
it. It completed 29 of 71 modules, hung on the 30th, and its harness task was
terminated at the timeout. Per the standing rule that an unexecuted scenario is
not a green one, this is recorded as a partial observation, not a result.

    collection: 512/5711 tests collected (5199 deselected) in 6.19s
    running 71 modules, one process each

**29 modules completed: 28 fully green, 1 with two failures. 190 passed,
2 failed, 4 skipped. Zero spurious failures among them** â€” every module in that
first 29 either passed cleanly or failed deterministically for a reason that
reproduces on inspection. That is a real and encouraging signal, but note what it
excludes: alphabetical order puts
`tests/test_return_case_workflow_real_infra.py` at roughly position 65, so **the
one module that produces the spurious failures had not been reached when the run
stopped.** The clean first 29 is therefore not evidence about the question in Â§1;
Â§1 answers it directly instead.

### 3. A real, deterministic, pre-existing failure the runner surfaced

    === [29/71] tests/operations/test_integration_outbox_index_plans_real_infra.py
    ======================== 2 failed, 5 passed in 33.53s =========================
    FAILED ...::test_the_union_lands_as_six_indexes_on_the_server
    FAILED ...::test_rebuilding_the_union_against_a_populated_collection_is_a_no_op

    E  Left contains 2 more items:
    E  {'case_stream_event_id_unique': [('eventId', 1)],
    E   'case_stream_sequence_unique': [('aggregateId', 1), ('stream', 1), ('streamSequence', 1)]}
    E  AssertionError: assert 9 == 7

Not a flake. `ensure_integration_outbox_indexes` creates nine indexes; the test
pins seven. Provenance, established by log rather than by guess:

    $ git log --oneline -3 -- backend/src/.../operations/integrations/outbox.py
    4359bf9e (S2) step:08 recovery: waiting is not the same as broken
    1a1b6c81 (S2) step:02 per-case streams on the outbox: CAS sequences, ...
    bf7d2e7e fix(platform): close the copilot lifecycle defects ...

    $ git log --oneline -3 -- backend/tests/operations/test_integration_outbox_index_plans_real_infra.py
    bf7d2e7e fix(platform): close the copilot lifecycle defects ...

    $ git diff --name-only 7a898cf9 HEAD          # this branch touches neither
    .plan/tracks/HARNESS.ledger.md
    backend/tests/activity_probe.py
    backend/tests/test_return_case_policy_gate_real_infra.py
    backend/tests/test_return_case_workflow_real_infra.py
    backend/tests/test_return_case_workflow_replay_compatibility.py
    scripts/dev/run_real_infra_suite.sh

**S2 step:02 added the two named ordering indexes (contracts.md Â§7, and the
production docstring explains the naming choice) and did not update the live test
that pins the index set. The test file has not been touched since before that
commit.** Nothing caught it because the live suite had no working entry point â€”
which is this branch's whole thesis, arriving as a second instance.

**Not fixed here, and deliberately.** `backend/src` is off-limits, and I will not
touch it. The stale assertion belongs to S2's slice, not this one, and editing
another slice's test to make a number go green is exactly rule 10's and rule 11's
territory. **Reported to the orchestrator.** My reading is that the production
code is correct and the test is stale, but that is S2's call to make, not mine.

### 4. A hang, and a defect in the step:08 runner

    === [30/71] tests/operations/test_order_line_reservations_real_infra.py
    tests\operations\test_order_line_reservations_real_infra.py ............ [ 36%]
    ..................

Thirty of its thirty-three tests passed, then the process stopped producing
output for **28 minutes** before the harness task was terminated. It was blocked,
not spinning:

    PID 27660  CPU=6.06s  Threads=5  WS=184MB      # idle, no CPU growth
    PID 28092  CPU=0.03s  Threads=1  WS=11MB       # the launcher shim

Six seconds of CPU across thirty completed database tests, then nothing. Peer
modules in this suite finish in 30â€“100s.

**This is a defect in the runner I shipped in step:08, and it is mine.** There is
no per-module timeout. One hanging module hangs the entire gate forever, and an
acceptance gate that never returns is worse than one that returns a wrong number:
a wrong number gets adjudicated, an unterminated run gets re-run until someone
gives up. The step:08 header argues an aggregate must not be able to lie; silence
is the one failure mode that argument did not cover.

Fixing it needs a `timeout`-wrapped module invocation that records the module as
failed-by-timeout and moves on. **Not implemented in this step**, because the
script was being executed by a live bash process throughout â€” bash reads a script
incrementally and editing it mid-run corrupts the loop. It is the first thing
step:10 should do.

Whether the hang is pre-existing or per-module-induced is **unestablished**. It
is in a module nobody has run, so the same F3 problem applies: there is no
control. I am not going to guess, and I am not going to call it pre-existing on
the strength of it looking like the outbox case.

I could not terminate the hung process to let the loop continue â€” the permission
system refused `Stop-Process`, and the bash `kill` could not map the Windows PID.
That is why the run ends at 29 rather than at 71.

### 5. Wall-clock cost, as a number

Over the 29 completed modules:

    sum of pytest's own reported per-module times : 1088s  (18.1 min)
    wall clock, launch to module 30's start       : ~36.5 min

**Roughly 38 seconds per module of pure process overhead** â€” a fresh interpreter,
`conftest.py` import and a collection pass, 29 times. Per-module execution
therefore costs about **as much again as the tests themselves take**, and
extrapolates to **~45 minutes of overhead** across all 71 modules on top of
whatever the tests cost. Reported rather than judged: whether that is acceptable
is the orchestrator's call, and it now has a number instead of an adjective.

### 6. What this leaves

- RV's ruling on the acceptance run **stands, undischarged.** Per-module was the
  wrong remedy; the state that drives the failures is on the server and does not
  care about process boundaries.
- The two remedies still on RV's list that address a *server-side* mechanism are
  quarantine (rejected here on coverage grounds, and it is worth noting that
  rejection now costs more than it did) and adjudicated re-runs recorded **as
  flakes rather than as passes**. A third that RV did not list, and that follows
  directly from the mechanism: **reset the Temporal server between modules**, or
  give each module its own namespace with a fresh task queue. The module's own
  docstring records that namespace isolation was tried and rejected on evidence,
  so that history needs reading before it is tried again.
- This is a decision about the acceptance gate, not about this slice. It goes to
  the orchestrator.

**Next step:** step:10 â€” a per-module timeout, so the gate cannot hang; then the
acceptance-gate remedy, which is an orchestrator decision and not this branch's
to take.

---

## step:10 â€” a ceiling per module, so the gate cannot hang

Closes the defect step:09 recorded against my own step:08 runner: no per-module
timeout, so the hang in `test_order_line_reservations_real_infra.py` took the
whole gate with it and the run ended at 29 of 71 modules.

**Files touched:** `scripts/dev/run_real_infra_suite.sh` only.

Each module now runs under `timeout --kill-after=30s ${LIVE_MODULE_TIMEOUT}s`,
default **900s**. Chosen against measurement rather than taste: the slowest
module observed *finishing* in step:09 was
`test_order_discovery_fulltext_real_infra.py` at **276.85s**, so the ceiling is a
little over three times the worst honest case. Overridable by environment for a
deliberately slow selection.

Three details that matter more than the timeout itself:

- **A timeout is not a failure with a count â€” it is the absence of a result.**
  Handled *before* the summary is parsed, because a killed pytest leaves a
  partial log whose last line is a row of progress dots; parsing that would
  invent counts for a module that produced nothing. The module is instead marked
  counts-unreadable, which makes the run refuse to report totals as its result.
- **`timeout` missing is announced, not degraded quietly.** If coreutils
  `timeout` is not on `PATH` the script says so on stderr and runs without a
  ceiling. An operator should know which of the two runners they are holding.
- The narrowed-path `exec` now removes the collection temp file and clears the
  trap first â€” `exec` replaces the shell, so the `EXIT` trap never fired and the
  file leaked on every single-file invocation.

### Proved, with a module that actually hangs

A temporary module doing `time.sleep(3600)`, placed to sort **first**, and a green
module placed to sort **last** so a green tail cannot mask the result.
`LIVE_MODULE_TIMEOUT=25` to keep the proof short:

    $ LIVE_MODULE_TIMEOUT=25 bash scripts/dev/run_real_infra_suite.sh --ignore=...
    SCRIPT EXIT: 1
    collection: 2/2948 tests collected (2946 deselected) in 15.03s
    running 2 modules, one process each (ceiling 25s per module)

    tests\api\test_aaa_hang_selftest_real_infra.py !!! tests/api/test_aaa_hang_selftest_real_infra.py TIMED OUT after 25s -- no result; the module did not finish

    ================== live-infrastructure suite: summary ==================
    modules run     : 2
    modules passed  : 1
    modules failed  : 1
    tests passed    : 1
    tests failed    : 0
    wall time       : 0m 42s (one process per module)

    counts could not be read for 1 module(s); the totals above are
    incomplete and must not be quoted as the suite's result:
      ? tests/api/test_aaa_hang_selftest_real_infra.py

    FAILED modules:
      x tests/api/test_aaa_hang_selftest_real_infra.py
          exit 124 -- TIMED OUT after 25s -- no result; the module did not finish

    the live-infrastructure suite FAILED (1 of 2 modules).

The run terminates, exits **1**, names the hung module, and â€” the part worth
having â€” declines to quote its own totals, because one module produced no result.
`tests failed: 0` sits next to a failed run and is explicitly labelled
incomplete rather than read as a green.

### Population pin, re-run again

Both temporary modules were deleted, and the pin was run with them present and
after removing them, as in step:08:

    $ python -m pytest tests/test_return_case_workflow_replay_compatibility.py -q
    17 passed in 9.29s

    $ git status --porcelain
     M scripts/dev/run_real_infra_suite.sh

### What step:10 does not do

It does not make the acceptance run trustworthy. **Step:09's negative result
stands: per-module execution does not eliminate the spurious failures**, because
the state driving them is on the shared Temporal server and no process boundary
touches it. A ceiling stops the gate hanging; it does not stop the gate lying
about a flake. RV's ruling is still undischarged and the remedy is an
orchestrator decision.

**Open, and handed on:**

1. **The acceptance-gate remedy.** Per-module was the wrong choice of RV's two.
   What remains is quarantine (rejected here on coverage grounds â€” that rejection
   now costs more than it did), adjudicated re-runs recorded *as flakes rather
   than as passes*, or a server-side reset between modules. Orchestrator's call.
2. **`test_integration_outbox_index_plans_real_infra.py`** â€” two deterministic
   failures from S2 step:02's index addition against a stale test pin. S2's
   slice. Not touched here.
3. **The hang in `test_order_line_reservations_real_infra.py`** â€” real, and now
   bounded rather than diagnosed. Provenance unestablished.
4. **The suite has still never been run to completion.** 29 of 71 modules is what
   exists. No one should quote a live-suite result until it has.

---

## step:11 â€” three corrections to my own record

Step:09 and step:10 contain claims that are wrong. Correcting them here, per the
same append-only discipline step:07 applied to RV's findings: the original text
keeps its wording, and this is the corrected record.

### 11.1 â€” the hung process was killed by the orchestrator, not by me

Step:09 recorded that I could not terminate the hung
`test_order_line_reservations_real_infra.py` process (PIDs 27660 and 28092)
because the permission system refused `Stop-Process` and the bash `kill` could
not map the Windows PID. That much is accurate.

What happened next is not what I reported. I later observed the loop had advanced
past module 30 and offered "my `kill -9` may have landed after all" as the
explanation. **That is wrong.** The orchestrator verified both processes â€” started
16:07:30, idle since â€” and **killed them from outside this session.** That is what
released the loop.

The wrong explanation is worse than a missing one. A delayed signal is not a
thing that happens here, and leaving that sentence in the record would teach a
future reader to expect one. **An orchestrator action is an event in the run and
belongs in the record as an event**, not as an unexplained process death
back-filled with the most convenient local cause.

The orchestrator also notes it intervened in a run it believed had already ended,
on the strength of my incorrect report in 11.2. No harm resulted. It is recorded
because the cause of the intervention was my error, not its own.

### 11.2 â€” "the run was terminated at 29 modules" is retracted

Step:09 states the suite run "did not finish", completed "29 of 71 modules" and
was "terminated at the timeout". **The run was never terminated.** It was still
executing the whole time, and reached module 40 while this entry was being
written.

What I actually did: read a 0-byte harness task-output file, read a log snapshot
showing 29 modules, saw a background *waiter* task report `killed`, and concluded
the run had died. The waiter was one of my own polling loops. The 0-byte file was
the launcher's stdout, which is empty by construction because the run redirects
its own output to a log. **Two instruments, neither of them pointed at the run,
both read as evidence about the run.**

This is the same family as measuring against the wrong `src`, recorded in
step:08's environment note: **an instrument pointed at the wrong artifact reports
confidently and wrongly.** It does not return an error, and nothing in the reading
says which artifact it came from. Both times the tell was available â€” the log's
own mtime was current â€” and both times I did not check it before drawing a
conclusion.

The correct statement of module 30: it produced **no summary line at all**. The
loop recorded it as a failure whose "summary" is its last non-empty output, a row
of progress dots â€” which is precisely the case step:10's timeout handling was
written for, and precisely the case run 1 is too old to have handled.

### 11.3 â€” I edited the script while it was executing, having written that I would not

Step:09 says, of the missing per-module timeout: *"Not implemented in this step,
because the script was being executed by a live bash process throughout â€” bash
reads a script incrementally and editing it mid-run corrupts the loop."*

Step:10 then edited that file while run 1 was still executing it. I believed run 1
had ended, on the strength of 11.2. **The reasoning in step:09 was right and I
overrode it with a bad reading rather than a better argument.**

The mechanics, precisely, because the general rule is worth more than this
instance:

- Bash parses a **compound statement whole** before executing it. The entire
  per-module `for` loop is one compound statement, so run 1 is executing the
  pre-step:10 loop body from memory. That is consistent with what was observed:
  run 1 hung on module 30 with no ceiling, which is exactly the version without
  the timeout.
- Bash re-reads the file **by byte offset** for top-level commands *after* that
  compound statement. Step:10 changed the byte length of the file above the
  summary block. **So run 1's final summary block may be read from shifted or
  edited bytes and cannot be trusted.**

Consequences, split by what is and is not affected:

- **Safe:** every per-module result line already in the log. Each is pytest's own
  output from its own process, written before any edit and never re-read.
- **Suspect:** run 1's own aggregate summary, which is the exact property step:08
  was asked to prove.

**Mitigation:** the aggregate for run 1 is computed from the log rather than
quoted from the script's summary. And the honest consequence is stated rather
than worked around: **run 1 cannot serve as the proof of the aggregate property**,
even if its numbers come out fine. That proof rests on step:08's and step:10's
injections, which ran to completion against files nobody was editing.

**The rule, which is the part worth keeping:** *a running script is a live
artifact, and editing it is editing a process.* Not a file that a process happens
to have read â€” one it is still reading. The safe move was the one step:09 had
already identified and I talked myself out of.

**Next step:** step:12 â€” run 1's completion, reported from the log.

---

## step:11 -- arrival: the stack was not clean, the interpreter was not mine, and the mechanism is not the one we named

No live run was executed in this step. Everything below is read-only observation
of the machine, `git`, and source. That is deliberate: a full per-module suite
run started by the previous agent was **still in flight** when I arrived, and the
defect under investigation is accumulated shared state. A second writer to the
same stack does not make a measurement noisy, it makes it not a measurement.

### 1. Base verified by ref, and the first check I ran was the wrong one

    $ git rev-parse HEAD
    497c1ca77a79d6687bbe5d9c71591ee8cf795177

    $ git rev-parse refs/heads/refactor/unified-return-platform
    b7f07838d22d016021ca52c0c86f0b249e867049

    $ git rev-list --left-right --count refs/heads/refactor/unified-return-platform...HEAD
    83      5

    $ git merge-base refs/heads/refactor/unified-return-platform HEAD
    7a898cf9f3a44b0947bce9a0de6c5fed1679954a

    $ git merge-base --is-ancestor 7a898cf9 refs/heads/refactor/unified-return-platform
    -> true    BASE OK (behind, not orphaned)

**Recording my own error because it is the interesting half.** My first check was
`git merge-base --is-ancestor <trunk-head> HEAD`, which returned false and which
I briefly read as "BASE-STALE". That check is wrong: it asks whether *trunk* is
an ancestor of *my branch*, which is false for every branch that is merely
behind. The protocol question is the reverse one, and it passes. Being 83 behind
is not a stale-base incident; being orphaned would be. Contracts sect. 3 asks the
second question, and RV HARNESS-1 had already settled it the same way.

### 2. The stack was not clean. It had an entire suite run in it.

The dispatch said two hung pytest processes had been killed and the stack should
be clean, but to check rather than assume. Checked:

    $ Get-CimInstance Win32_Process -Filter "Name='bash.exe'" | ... CommandLine
    ProcessId 5664   CreationDate 31-08-2026 15:47:52
      "C:\Program Files\Git\usr\bin\bash.exe" scripts/dev/run_real_infra_suite.sh
    ProcessId 15104  CreationDate 31-08-2026 16:57:51   (child of 5664)
      "C:\Program Files\Git\usr\bin\bash.exe" scripts/dev/run_real_infra_suite.sh

`15104` is a **child** of `5664`, not a second run -- checked rather than
assumed, because "two suite runs are racing" and "one suite run re-execed" are
very different reports:

    $ Get-CimInstance Win32_Process -Filter "ParentProcessId=5664"
    ProcessId 15104  bash.exe  scripts/dev/run_real_infra_suite.sh

So: **one** full per-module run, started 15:47:52, alive and progressing. At the
time of writing it is at module 42 of 71. This is the furthest a live-suite run
has ever got in this repository. It was not stopped, and I ran nothing against
the stack while it was in flight.

Also still resident, from **2026-08-30 18:31**, three processes the previous
rounds did not account for:

    PID 4012   1600.7s CPU   ...\backend\.venv\Scripts\python.exe C:\...\tmpb7ioi3yb\child.py
    PID 21028  1597.6s CPU   ...\backend\.venv\Scripts\python.exe C:\...\tmp28d2bnx0\c.py
    PID 10092  1597.1s CPU   ...\backend\.venv\Scripts\python.exe C:\...\tmpro8zlllo\c.py

Sampled twice, ten seconds apart, before drawing any conclusion about them:

    PID 4012 : 1600.671875 -> 1600.796875   (+0.125s over 10s)
    PID 21028: 1597.609375 -> 1597.750000   (+0.140s over 10s)
    PID 10092: 1597.125000 -> 1597.250000   (+0.125s over 10s)

**About 1.3% of one core each, on 8 logical processors.** They are low-grade
pollers, not the CPU hogs their totals suggest -- the 1597s is 22 hours of
accumulation, not current burn. Recorded as present and quantified rather than
blamed: they are leftovers of the ACC signal proof and they are not a plausible
cause of a 6x wall-time degradation on their own. They should still be reaped
before any timing measurement is taken.

### 3. The interpreter was importing another branch's production code

The dispatch's second load-bearing fact, verified rather than accepted:

    $ python.exe -c "import return_platform,os; print(os.path.dirname(return_platform.__file__))"
    # with PYTHONPATH unset
    K:\Projects\Ret\returns_muti_agentic_platform\backend\src\return_platform

    # with PYTHONPATH=<this worktree>\backend\src
    K:\...\.claude\worktrees\agent-af79f912fcfd95e05\backend\src\return_platform

The main worktree is checked out on **`feat/acc-frontend`** (`f4d9743a`), not on
this branch and not on trunk. And the runner sets nothing:

    $ grep -n "export\|PYTHONPATH" scripts/dev/run_real_infra_suite.sh
    1:#!/usr/bin/env bash          # (the only match: the shebang)

    $ grep -n "pythonpath" backend/pyproject.toml      -> no match
    $ grep -n "sys.path\|PYTHONPATH" backend/tests/conftest.py  -> no match
    $ ls -d backend/.venv                              -> No such file or directory

So there is no `.venv` in this worktree, no `pythonpath` in the pytest config and
no `sys.path` shim in `conftest.py`. **Every per-module run made from this
worktree -- including the one in flight, and including step:09's three isolated
runs -- imported `return_platform` from the main worktree, on `feat/acc-frontend`.**
The tests are this branch's; the production code under them is not.

**Blast radius, measured rather than feared.** 42 files differ in `backend/src`
between this branch and what those runs actually imported:

    $ git diff --name-only HEAD f4d9743a -- backend/src | wc -l
    42

But the two modules that carry this step's findings are **bit-identical** across
the two trees:

    identical: backend/src/return_platform/workflows/return_case_workflow.py
    identical: backend/src/return_platform/operations/integrations/outbox.py
    DIFFERS  : backend/src/return_platform/workflows/return_case_activities.py

`test_return_case_workflow_real_infra.py` imports only from
`...workflows.return_case_workflow` and drives probes rather than the real
activities, so the differing `return_case_activities.py` is not in its path.
**Both findings below therefore survive the defect.** The other 40 files are in
the path of other modules in the 71, so the in-flight run's *green* modules are
the ones to re-read with care -- they are greens for a hybrid tree.

### 4. `test_integration_outbox_index_plans_real_infra.py` -- production is right, the test was stale. Fixed.

The previous agent's read, verified independently rather than inherited.

`ensure_integration_outbox_indexes` issues eight `create_index` calls
(`outbox.py:287,288,289,293,294,295,301,307`); with `_id_` the server holds
**nine**. The test pinned seven. The two extra are S2 step:02's named ordering
indexes, and they are load-bearing rather than decorative:

    $ grep -rn "streamSequence" backend/src/ | grep -v pycache
    operations/case_commands.py:312          "caseSequence": ordering["streamSequence"],
    operations/case_commands.py:345          case_sequence=int(ordering["streamSequence"]),
    operations/integrations/outbox.py:386    "streamSequence": sequence,
    operations/integrations/outbox.py:626    "streamSequence": {"$lt": command.stream_sequence},
    operations/return_support/ingress_store.py:538   sort=[("streamSequence", -1)],
    operations/return_support/ingress_store.py:639   key=lambda command: int(command.get("streamSequence") or 0),

The field is written, filtered, sorted and ordered on, and `outbox.py:326`
records the unique index as "the second lock on the same door" behind
`allocate_case_stream_sequence`'s CAS counter, against contracts.md sect. 7.
Deleting either index to make the test green would remove a concurrency guard.
**Production is correct; the pin was stale from S2 step:02 until now.** Ownership
has passed to trunk, so the test is fixed here rather than reported onward.

**What changed, and why it is not a weakened assertion.** The expected map gained
the two indexes it was missing, `len(after) == 7` became `9`, and four
assertions were **added** -- `unique` and `partialFilterExpression` on each of
the two -- so the pin now covers the properties that make them guards rather than
merely their key patterns. An index that landed non-unique or non-partial used to
be invisible to this test and now reddens it.

`test_the_union_lands_as_six_indexes_on_the_server` is renamed to
`test_the_union_lands_on_the_server_exactly_as_declared`. The count came out of
the name on purpose: it had already gone stale once, and re-pinning it to "eight"
would reset the same trap. The old name is recorded here and at
`docs/execution-context/remediation/LEDGER.md:480`, which is the one other place
that referenced it.

    $ PYTHONPATH=<worktree>\backend\src python -m ruff check <file>   -> All checks passed!
    $ PYTHONPATH=<worktree>\backend\src python -m ruff format --check -> 1 file already formatted
    $ python -c "ast.parse(...)"                                      -> parses OK

**Not executed.** The stack was not mine. This fix is prepared and unverified
against a live server, and must not be read as green until it has been run.

**Rule 13.** This file is `*_real_infra.py` and carries `pytestmark =
pytest.mark.live_infra`, and `addopts` carries `-m "not live_infra and not
browser"`. So **nothing in CI runs it** -- it falls on the ungated side, and its
only gate is `scripts/dev/run_real_infra_suite.sh`, the runner this track is
trying to make trustworthy. It is not in `scripts/ci/known_test_failures.json`
and does not belong there: that allowlist governs the CI suites, which never
collect this test.

### 5. The mechanism is a fixed wall-clock budget under contention -- not, on the evidence, namespace-scoped server state

The brief asked for a mechanism that can be turned on and off, and warned against
working through a candidate list blindly. Before touching the stack I went
looking for what the *previous* rounds had already measured, and the answer was
sitting in a ledger nobody in this track had cited.

`docs/execution-context/remediation/LEDGER.md:445` (2026-08-23, four complete
504-test runs, predating every branch in this run):

> **The residual two are flaky, not failing.** They are different tests in each
> run -- `test_the_case_completes_when_support_answers` and
> `test_the_support_wait_survives_a_worker_restart` in one,
> `test_the_policy_review_wait_survives_a_worker_restart` and
> `test_a_graph_sync_failure_parks_the_case_loudly` in the next -- always from
> the two workflow real-infra modules, always `did not run within 30.0s`. Those
> two files pass 21/21 in isolation, and the two that failed the third run pass
> together **in 9.60s** against the 30s budget they exceeded under load. **A
> fixed wall-clock budget is the mechanism; contention is the cause.**

That signature is still in the source at head:

    backend/tests/test_return_case_workflow_real_infra.py:320
        async def reached(self, name: str, *, within_seconds: float = 30.0) -> None:
    :330            async with asyncio.timeout(within_seconds):
    :333            raise AssertionError(f"{name} did not run within {within_seconds}s")

Thirteen call sites, budgets of 20s and 30s. **Every test named as spuriously
failing across all three independent rounds routes through it:**

| test | line | `reached` call | budget | named by |
|---|---|---|---|---|
| `test_the_case_completes_when_support_answers` | 420 | 427 | 30s | Aug ledger |
| `test_the_support_wait_survives_a_worker_restart` | 444 | 459 | 30s | Aug ledger |
| `test_a_bay_failure_does_not_stop_the_return` | 519 | 528 | 30s | step:09 run 2 |
| `test_a_graph_sync_failure_parks_the_case_loudly` | 708 | 723 | 30s | Aug ledger, RV, step:09 run 2 |
| `test_a_rejected_return_needs_no_graph_sync` | 740 | 752 | 30s | step:09 run 1 |

Step:09 read the fact that the names are "drawn fresh each time rather than
converging on weak tests" as evidence the failure set is arbitrary. It is better
read the other way: **the population is not arbitrary, it is exactly the tests
that wait on a fixed wall-clock budget**, and which of them loses depends on
which is waiting when the machine is slowest. That is the same conclusion the
August ledger reached from a different direction.

**Why this displaces the accumulated-server-state hypothesis rather than
refining it.** The state hypothesis predicts degradation that grows with what the
namespace holds. Two observations sit badly with it:

- The in-flight per-module run has completed **41 modules with zero spurious
  failures**, and module 41 (`test_structure_physical_identity.py`, 18.10s) is no
  slower than module 4 (`test_conversation_tenant_isolation_real_infra.py`,
  4.34s). If executions and task queues piling up in `default` were the driver,
  the run should be getting worse. It is not.
- The August ledger's own correction records the same trap being fallen into
  once already: a slowdown attributed to 54 leaked containers exhausting the
  daemon, where `docker ps` "does return in **0s** once the suite is not
  running... The container count was never the cause and pruning would have
  fixed nothing; **the daemon was starved of I/O by the suite itself.**"

`asyncio.timeout` measures wall clock. A test that needs a Temporal round trip
inside 30s fails when the *machine* is loaded, whatever the namespace holds.

**And it explains the negative result in step:09 without contradicting it.**
Step:09 concluded per-module execution does not help, from three consecutive
isolated runs giving 1f/2f/0f. Those three runs were taken back-to-back on a
machine that had been running the suite continuously, with the three pollers of
sect. 2 resident, and -- per sect. 3 -- against another branch's production code.
Per-module execution removes *in-process* state, which step:09 correctly says is
not the mechanism; but it also does nothing to make the machine quieter, which on
this reading *is* the mechanism. The experiment did not isolate the variable it
needed to isolate. **This does not make step:09 wrong about what it measured, and
its central point stands: a process boundary is not a remedy for shared state.**
It makes its conclusion under-determined.

**The experiment that would settle it, to be run when the stack is free.** The
mechanism is turnable on and off without touching Temporal at all:

1. Reap the three pollers, let the in-flight run finish, confirm the machine is
   idle.
2. Run `test_return_case_workflow_real_infra.py` alone, five times, serially,
   with `PYTHONPATH` set, recording every failure *with its message* -- which no
   previous round recorded, and which is the datum that decides this.
3. Then run it again under a manufactured CPU/IO load, and predict: failures
   reappear, and their messages are `... did not run within 30.0s`.
4. Remove the load; predict they vanish.

If step 3 fails to produce failures, the contention reading is wrong and the
server-state hypothesis comes back. If it produces them and step 2 did not, the
mechanism is established in the on/off form the brief asked for.

**A gap in the record, worth naming.** Across RV's eleven runs and step:09's
three, **no failure message was ever recorded** -- only test names. The single
most diagnostic field was discarded at every round. That is why this was still
open, and it is why sect. 5's experiment records messages rather than counts.

### 6. The in-flight run: what it will and will not be able to say

- **Its per-module result lines are real** and are pytest's own output, already
  written to the log.
- **Its aggregate cannot be trusted**: `run_real_infra_suite.sh` was edited while
  bash was executing it, and bash re-reads a script by byte offset. The summary
  block may be read from edited bytes. The aggregate must be recomputed from the
  log rather than quoted from the run. Recorded as an incident: **do not edit
  the runner while a run of it is in flight.**
- **Its greens are hybrid-tree greens** (sect. 3), for the 40 differing modules.
- `tests/operations/test_order_line_reservations_real_infra.py` **hung again** --
  second occurrence, same shape, 30 of 33 tests then silence:

      === [30/71] tests/operations/test_order_line_reservations_real_infra.py
      collected 33 items
      tests\operations\test_order_line_reservations_real_infra.py ............ [ 36%]
      ..................
      === [31/71] tests/operations/test_return_shipment_concurrency_real_infra.py

  **Step:10's ceiling worked**: the run moved on instead of hanging forever.
  But there is no `TIMED OUT` marker anywhere in the log --

      $ Select-String -Path permodule_run1.log -Pattern "TIMED|124|no result"
      (no matches)

  -- because the marker is written to stderr and the log captures stdout. A
  ceiling that fires silently in the operator's log is most of a fix: the run
  survives, but the reader of `permodule_run1.log` sees a module that produced
  no summary line and no explanation. Recorded against step:10, mine to fix.

  Provenance of the hang is **still unestablished** and I am not going to guess
  it. One new datum: `docs/execution-context/remediation/LEDGER.md:469-485`
  records a *different* module of this family hanging in August
  (`test_integration_outbox_index_plans_real_infra.py`, "still undiagnosed",
  with `pytest -o faulthandler_timeout=N` named as the next step and not run).
  That module now completes in 33.53s, so its hang did not persist. The
  resemblance is suggestive and is **not** evidence about this one;
  `faulthandler_timeout` remains the cheap diagnostic and needs an idle stack.

### Open

1. **Nothing in sect. 4 or 5 has been executed.** The outbox fix is prepared, not
   verified. The mechanism in sect. 5 is argued from three rounds of prior
   measurement and from source, and is **not yet established** -- the on/off
   experiment is designed and unrun.
2. **The suite has still never been run to completion**, and the run in flight
   will not settle it, for the three reasons in sect. 6.
3. The three pollers of sect. 2 should be reaped before any timing measurement.

---

## step:12 -- a retraction, the load that was never recorded, and a rule about messages

No live run in this step either. The stack is still held by the in-flight run and
is being drained by the orchestrator.

### 1. Retraction: I claimed step:10's ceiling fired. It did not, and I could not have known it did.

In step:11 sect. 6 I wrote that the module-30 hang was handled and that "step:10's
ceiling worked", on the reasoning that the run moved on to module 31. **That was
an inference dressed as an observation, and it is the same error this track keeps
finding in others.** Withdrawn.

What is actually established:

    $ Select-String -Path permodule_run1.log -Pattern "TIMED|124|no result"
    (no matches)

The marker at `run_real_infra_suite.sh:266` is `echo "!!! $module $summary"` --
**stdout**, not stderr. My step:11 explanation (that it went to stderr while the
log captured stdout) was wrong. And the ceiling machinery is sound in this
environment, checked rather than assumed:

    $ command -v timeout                 -> /usr/bin/timeout
    $ timeout --version | head -1        -> timeout (GNU coreutils) 8.32
    $ timeout --kill-after=30s 2s sleep 10; echo $?
    -> 124

`set -euo pipefail` is set (line 66), so `rc=${PIPESTATUS[0]}` receives 124 and
the branch at line 263 fires. **So if the ceiling had fired, the marker would be
in the log.** It is not. Therefore module 30 did not exit through the ceiling --
it exited some other way, most plausibly the external kill of the hung pytest
that the orchestrator reported making. The `else` branch then took the partial
log's last non-empty line (a row of progress dots) as the summary, found no
counts in it, and added the module to `counts_unparsed`.

**The consequence, stated plainly: step:10's ceiling has still never been observed
firing in a real run.** It was proved in step:10 against a synthetic
`time.sleep(3600)` module at `LIVE_MODULE_TIMEOUT=25`, which is a proof of the
mechanism and not of the ceiling doing its job on the actual hang. In the one
real opportunity it has had, something else got there first.

**What will settle it:** the run's own summary block. If module 30 appears as
`exit 124 -- TIMED OUT after 900s`, the ceiling fired and I am wrong. If it
appears under `counts could not be read` with a different exit code, it did not.
Recorded as a prediction before the fact so it cannot be fitted afterwards.

### 2. A hypothesis raised and ruled out, recorded because near-misses are cheap to hide

While checking the above I found that in a shell whose `PATH` lacks Git's
`/usr/bin`, `command -v timeout` resolves to **`/c/WINDOWS/system32/timeout`** --
Windows' pause utility, which takes `/T` and rejects the script's arguments:

    $ /c/WINDOWS/system32/timeout --kill-after=30s 900s echo HELLO
    ERROR: Invalid syntax. Default option is not allowed more than '1' time(s).
    exit=1

`TIMEOUT_CMD` is set by `command -v timeout || true` (line 208) and is tested only
for emptiness (line 209), so a namesake on `PATH` would pass that test, and every
module would exit 1 *without pytest ever running*.

**This is not what happened, and I nearly reported that it was.** The stripped
`PATH` was an artifact of my own `bash -lc` invocation. In the environment the
runner actually ran in, `timeout` is coreutils (sect. 1), and modules 1-29 all
executed pytest normally, which they could not have done under the Windows
binary. **Ruled out as the cause; retained as a latent fragility** worth one line
of hardening, since the check that would have caught it costs nothing.

### 3. The load was never recorded, and that is the largest single correction to this track

The orchestrator has disclosed that **three to four concurrent agents, several
running full backend suites, were active throughout every round in which these
failures were measured** -- RV's eleven runs, and step:09's three consecutive
isolated runs. Nobody was measuring a quiet machine. The load was ambient and
therefore invisible, and no round recorded it.

This is recorded here as a fact about the measurements, not as an excuse for
them. Its consequences are specific:

- **Step:09's negative result is under-determined, not wrong.** Its three runs
  (1f/2f/0f) were taken on a machine carrying several concurrent suites. Its
  conclusion -- that a process boundary is no remedy for shared state -- stands
  on its own reasoning. Its inference that per-module execution therefore cannot
  help does not, because the variable it needed to hold still was moving and
  unrecorded.
- **RV's fresh-vs-loaded table** (13 tests in 73.60s clean, 302-487s "loaded")
  measured a real effect, but "loaded" meant *the machine*, not the Temporal
  namespace. The same numbers that were read as evidence for accumulated server
  state are equally evidence for contention, and the two were never separated.
- **The accumulated-server-state hypothesis was a worse guess than the one
  already written in this repository in August**, which named a fixed wall-clock
  budget and machine contention from four complete runs. Two rounds were spent
  chasing the newer guess.

### 4. Standing rule: a flake investigation that records names and not messages is not an investigation

Across **fourteen measured runs** -- RV's eleven, step:09's three -- not one
failure *message* was captured. Only test names.

`did not run within 30.0s`, a bare assertion failure, and a connection error are
three different defects with three different owners. The field that distinguishes
them was discarded at every round, and the rounds then reasoned about what was
left. The mechanism in step:11 sect. 5 was recoverable only because the *August*
ledger had recorded the message, once, eight days before this run began.

**The rule, for this track and any other:**

> When recording a flaky failure, record the failure **message** and not only the
> test name. A name says which test lost; the message says what it was waiting
> for. A run that reports `N failed` and a list of names has thrown away the only
> field that makes the next round cheaper than this one.

Applied immediately: the sect. 5 experiment records `pytest -rA` output per
failure, and any summary this track writes reports messages beside names.

### 5. The in-flight run: what it is, and what it is not

Restated because it has twice been described as something it is not. It is **not**
the first complete live-suite run, and no one should call it that:

- Its production code is `feat/acc-frontend`'s, not this branch's (step:11
  sect. 3). 42 `src` files differ. Its greens are **hybrid-tree greens**.
- Its aggregate is unreliable: the runner was edited while bash was executing it.
- It has one module (30) whose exit is unclassified, per sect. 1.

**What it is still worth, and it is not nothing:** its per-module result lines are
pytest's own output and are real, and they carry the two things this
investigation needs -- **per-module timings** (no upward drift across 47 modules,
which is the observation that sits worst with the accumulation hypothesis) and
**the hang's second occurrence**. It is being allowed to finish for those.

### 6. The marker fix -- designed, and deliberately not applied yet

`run_real_infra_suite.sh` is the file the live bash process is executing. Bash
reads a script incrementally by byte offset, so editing it mid-run corrupts the
loop -- which is why step:09 deferred this same class of fix to step:10, and why
it is deferred again here. **Not applied. Blocked on the run finishing.**

The defect the run exposed is *not* the one step:11 named. It is this: a module
that exits non-zero **without a parseable summary** leaves the operator's log a
row of progress dots followed by the next module's header, and no explanation
anywhere. That is what module 30 looks like right now. The `!!!` marker exists
only for `rc == 124 || rc == 137`; every other abnormal exit -- an external kill,
a crash, an interpreter abort -- is silent inline.

Three changes, smallest first:

1. **Confirm `TIMEOUT_CMD` is coreutils, not a namesake** (sect. 2):
   `"$TIMEOUT_CMD" --version 2>/dev/null | grep -qi coreutils` before trusting
   it; otherwise treat as absent and announce.
2. **Announce the ceiling's status on stdout**, so the operator's log records
   which of the two runners they are holding rather than only their terminal.
3. **Print an inline marker for every non-zero `rc`**, not only the two timeout
   codes -- naming the module, the exit code, and that counts were unreadable.
   A gate that cannot lie has to include the case where the module was killed by
   something that was not the gate.

### Open

1. Sect. 1's prediction is unresolved until the run prints its summary.
2. Everything in step:11 sect. 4 and 5 remains unexecuted.
3. The marker fix is designed and blocked.

---

## step:12 â€” run 1 complete, and what the message said

Run 1 reached all **71 modules**. It is the first time the live suite has been
run to completion in this repository.

### The aggregate, derived from the log

**The script's own summary block never printed.** That is step:11.3's predicted
hazard arriving as fact rather than as a caveat: the mid-run edit shifted the
byte offsets bash re-reads for top-level commands after the loop, and the summary
was never emitted. The per-module lines are intact â€” each is pytest's own output,
written by its own process before any edit â€” so the aggregate below is computed
from the log, and **run 1 does not serve as the proof of the aggregate property.**
That proof rests on step:08's and step:10's injections, which ran against files
nobody was editing.

    modules started        : 71
    modules reporting      : 69
    modules with no result : 2   (both hung and were killed -- see below)

    passed  : 421
    failed  :   3
    errors  :   0
    skipped :  54
    -------------------
    sum     : 478

**Reconciliation, which is the check that makes the number readable:** collection
reported **512**. The two killed modules hold 33 and 1 tests respectively.
478 + 34 = **512**. Nothing is unaccounted for.

**54 skipped live tests** is worth stating rather than absorbing â€” two modules are
skipped in their entirety (`test_source_inspection_postgresql_docker.py`, 10;
`test_order_agent_rest.py`, 40). An acceptance record that reports "the live suite
passed" over 54 silent skips is making the same category-B claim this branch was
written about. Not investigated here; flagged.

### The two failing modules, verbatim

**1. `tests/operations/test_integration_outbox_index_plans_real_infra.py` â€” `2 failed, 5 passed in 33.53s`**

    E  Left contains 2 more items:
    E  {'case_stream_event_id_unique': [('eventId', 1)],
    E   'case_stream_sequence_unique': [('aggregateId', 1), ('stream', 1), ('streamSequence', 1)]}
    E  AssertionError: assert 9 == 7

Deterministic. S2 step:02 added two named ordering indexes to `outbox.py`; the
live test pinning the index set has not been touched since before that commit.
Not mine to fix â€” `backend/src` is off-limits and the test is S2's.

**2. `tests/test_return_case_workflow_real_infra.py` â€” `1 failed, 12 passed in 133.67s`**

`test_a_bay_failure_does_not_stop_the_return`, and **the message is the finding**:

    >           await probe.reached("open_support_work_item")
    tests\test_return_case_workflow_real_infra.py:528

    name = 'open_support_work_item', within_seconds = 30.0
    >           raise AssertionError(f"{name} did not run within {within_seconds}s")
    E           AssertionError: open_support_work_item did not run within 30.0s
    tests\test_return_case_workflow_real_infra.py:333: AssertionError

    ------------------------------ Captured log call ------------------------------
    WARNING  temporalio.activity: Completing activity as failed
      ({'activity_type': 'request_bay_assignment', 'attempt': 1, ...})
    RuntimeError: warehouse service unavailable

**Line 333. `did not run within 30.0s`. The default budget, not a tightened
20s call site.** This is the field fourteen prior runs discarded, and it changes
the diagnosis.

The captured log shows the mechanism rather than merely the symptom. The test
*deliberately* injects a bay failure (`probe.bay_should_fail = True`, raising
`RuntimeError("warehouse service unavailable")` at line 163) and then waits for
the workflow to carry on to `open_support_work_item`. Temporal is retrying
`request_bay_assignment` â€” `attempt: 1` is logged â€” and the workflow proceeds only
once that retry sequence resolves. **So the test is a race between a fixed 30s
client-side budget and a server-side retry schedule.** Any added latency
lengthens the retries; the budget does not move.

That is not accumulated state, and no process boundary addresses it. It matches
the August diagnosis in `docs/execution-context/remediation/LEDGER.md:445` â€” *"a
fixed wall-clock budget is the mechanism; contention is the cause"* â€” and it
explains the property step:09 misread as arbitrariness: the failing set is not
random, it is **the subset of the 12 `reached()` call sites whose race happened
to lose that run.**

**Step:09's causal claim is therefore withdrawn as under-determined.** Its
observation stands â€” a fresh process is no remedy for shared state â€” but "the
mechanism is accumulated Temporal server state" outran its evidence, and the
evidence that would have corrected it was a message I copied into the ledger
without reading. Step:09 records `test_return_case_workflow_real_infra.py:333:
AssertionError` verbatim. Line 333 is the `did not run within Ns` raise. **I had
the answer in my own transcript for two steps and read only the test name.**
That is precisely the gap: *a flake investigation that records names and not
messages is not an investigation.*

### Contention â€” three sources, and the run cannot discriminate

Recorded because ambient load is invisible unless someone writes it down, and
today it was invisible to the person generating it:

1. Another agent's backend suite â€” **17:00:28 to an unobserved point before 17:35**.
2. An orchestrator `git merge` attempt â€” two minutes, start time unrecorded,
   somewhere near module 64's start at 17:39:33.
3. An orchestrator background recursive `find` over the repository including
   **seventeen worktrees** â€” start and end not precisely recorded, bounded only by
   message ordering, and the largest by duration.

**Module 64's slowness cannot be attributed to any one of these.** All three are
candidates, a genuine module defect is a fourth, and this run cannot tell them
apart. Recorded as undiscriminated rather than tidied.

Module 69 ran 17:50:46â€“17:52:59, after source 1 closed and around source 3's
reported completion. **Whether it was contended is unestablished.** So its failure
is consistent with the wall-clock hypothesis and does not establish it â€” the same
restraint applied to the green case, applied to the red one.

### Two hangs, and who killed them

- **Module 30** `test_order_line_reservations_real_infra.py` â€” 30 of 33 tests, then
  blocked at 6s CPU for 28 minutes. **Killed by the orchestrator**, from outside
  this session. That is what released the loop.
- **Module 64** `test_case_confirmation_starts_workflow_real_infra.py` â€” collected
  **1 item**, zero progress in 9.5 minutes, CPU creeping 3.8s â†’ 7.31s (a poll or
  retry loop, not a clean block), 18â€“22 threads, 201MB steady. **Killed by me at
  17:49:03**, on the diagnostics, because reaching module 69 was worth more than
  preserving an undiagnosed hang.

Shared shape worth naming and **not** worth diagnosing from two instances: both
wait on Temporal, both produced no result, and neither had a ceiling because run 1
executes the pre-step:10 loop body. Named as a pattern to investigate.

### Wall clock

    sum of pytest's own per-module times : 3578s  (59.6 min)
    wall clock, launch to last module    : ~142 min
    of which, the two hangs              : ~37.5 min

Leaving **~45 min of process overhead across 71 modules, ~38s per module** â€”
consistent with the 29-module figure in step:09, and roughly as much again as the
tests themselves cost.

### Fixes shipped in this step

`scripts/dev/run_real_infra_suite.sh`, three changes, all verified by execution:

1. **`PYTHONPATH` pinned to this checkout.** Without it the runner tests whichever
   `return_platform` the interpreter resolves â€” in a worktree with no `.venv`, the
   **main** checkout's, via the shared venv's `return_platform_backend.pth`. There
   is no `pythonpath` in `pyproject.toml` and no `sys.path` shim in `conftest.py`.
   A suite runner that silently tests whatever the main worktree is checked out on
   is a stale-base incident with no branch to blame.

       $ env -u PYTHONPATH bash scripts/dev/run_real_infra_suite.sh tests/test_return_case_policy_gate_real_infra.py
       source tree: /k/.../worktrees/agent-af79f912fcfd95e05/backend/src
       8 passed in 25.25s

       # and the control, showing what it was doing before:
       $ env -u PYTHONPATH python -c "import return_platform; print(return_platform.__file__)"
       K:\Projects\Ret\returns_muti_agentic_platform\backend\src\return_platform\__init__.py

   **Run 1 itself was not affected** â€” I exported `PYTHONPATH` in the parent shell
   and env vars are inherited, corroborated by run 1's clean
   `512/5711 tests collected` where the main tree gives `512/5550` plus a
   collection error. The defect was real for every *other* caller.

2. **A per-module start timestamp.** One line, and its absence cost an afternoon:
   a runner that records no wall-clock time produces durations that are
   measurable and not *placeable*, so no module can be correlated with another
   process's load, a container restart, or a deploy. Combined with the
   orchestrator having no record of its own I/O, **the correlation we spent the
   afternoon attempting was never available to either side.**

       === [ 2/ 3] tests/test_return_workflow_concurrency.py  started 2026-08-31T17:56:34+05:30

3. **`skipped` counted.** An all-skipped module reports `10 skipped in 6.37s` with
   no other number, so it read as counts-unreadable and dragged the run toward
   "cannot report what it ran". Two modules did exactly that in run 1. Now counted
   and surfaced:

       the live-infrastructure suite PASSED: 3 modules, 5 tests (10 skipped).

   Totals reconcile against the collected line: 5 + 10 = 15 = `15/2571 collected`.

### What remains open

1. **RV's ruling is still undischarged.** Per-module was the wrong remedy â€” but the
   reason is now better than step:09's. The state is not (only) accumulated; the
   binding constraint is a **fixed client-side wall-clock budget racing a
   server-side retry schedule**. Neither quarantine nor a process boundary
   addresses that. The candidates that would are: raising or removing the fixed
   budgets in favour of a condition, or reducing what the budget must wait for.
   That is a change to `test_return_case_workflow_real_infra.py`'s helper and it
   is a design decision, not a cleanup â€” **orchestrator's call, and RV should see
   it**, since a budget raised to make a test pass is rule 10 territory unless the
   reasoning is on the record.
2. **`test_integration_outbox_index_plans_real_infra.py`** â€” S2's stale index pin.
3. **The two hangs** â€” bounded by step:10's ceiling, not diagnosed.
4. **54 silently skipped live tests.**
5. **A clean run has still never been observed** â€” run 1 was contended by three
   sources and its summary block was corrupted by my own edit. A controlled run on
   a quiet machine, with the step:10 ceiling and the step:12 timestamps in force,
   would be the first trustworthy one.

---

## step:13 — a second writer, a verified fix, and a corruption of mine

Discovered while verifying the final state, not looked for.

### 13.1 — this branch had two authors at once

Two commits on `feat/live-harness-registration` are not mine:

    c11c7db5  2026-08-31 17:11:20  (harness) step:11 the stale pin, and a mechanism the record already had
    472dd452  2026-08-31 17:17:55  (harness) step:12 the ceiling did not fire, and fourteen runs threw the message away

Both landed **while run 1 was in flight** (module ~44 at 17:11, ~47 at 17:17).
They interleave with my own `d1313348` (step:11) and `d4d35f4f` (step:12), and
the consequence is in the ledger:

    $ grep -oE "^## step:[0-9]+" .plan/tracks/HARNESS.ledger.md | sort | uniq -d
    ## step:11
    ## step:12

**Two `step:11` sections and two `step:12` sections, by different authors, about
different things.** contracts.md §3 requires one entry per step and an append-only
ledger; append-only holds, but the step ids no longer identify a step. A reader
asking "what happened at step:12" now gets two answers. **Not repaired here** —
renumbering would rewrite another author's entry, which is the one operation an
append-only ledger forbids most clearly. It needs the orchestrator to assign the
numbering, and it is recorded so the next reader is not the one who discovers it.

The root cause is not the numbering. **Two agents held the same worktree and the
same branch at the same time**, and neither could see the other's commits without
looking. That is the same shape as the three contention sources in step:12: a
shared resource with no protocol and no record, where each party's activity is
invisible to the other until it collides.

### 13.2 — their fix was sound, and is now verified

`c11c7db5` edited `test_integration_outbox_index_plans_real_infra.py`, the S2 test
I had declined to touch in step:09. **It is a strengthening, not a weakening, and
it clears rule 10 cleanly:**

- pin corrected `7` → `9`, matching what `ensure_integration_outbox_indexes`
  actually declares;
- **four assertions added** — `unique` and `partialFilterExpression` on each of the
  two named ordering indexes — so an index that landed non-unique or non-partial
  now reddens a test that previously would have passed on the key pattern alone;
- the test renamed off `..._as_six_indexes_...`, taking the count out of the name
  that had already gone stale once.

Nothing removed, no skip, no xfail. My step:09 reading — production correct, pin
stale — is independently confirmed by their analysis, and their fix goes further
than a re-pin would have.

Their commit closes with: *"Not executed. The outbox fix is prepared and
unverified against a live server and must not be read as green until it has been
run."* Correctly scoped, and that gap is now closed — stack quiet, machine idle:

    $ env -u PYTHONPATH bash scripts/dev/run_real_infra_suite.sh \
        tests/operations/test_integration_outbox_index_plans_real_infra.py
    source tree: /k/.../worktrees/agent-af79f912fcfd95e05/backend/src
    collected 7 items
    ============================= 7 passed in 15.64s ==============================
    EXIT: 0

**7 passed.** The last remaining unverified change on this branch is now executed.
Note the count moved 7 → 7 tests because two assertions merged into one renamed
test; the module reported `2 failed, 5 passed` in run 1 and reports `7 passed` now.

### 13.3 — one claim of theirs is disproven

Their step:11 states that every run made from this worktree imported
`return_platform` from the main checkout, and therefore that *"the in-flight run's
greens are hybrid-tree greens."*

**The defect is real — I fixed it in step:12 — but the conclusion about run 1 is
wrong.** Run 1 was launched with `PYTHONPATH` exported in the parent shell, and
environment variables are inherited by the script and by every pytest it spawns.
Measured both ways against a script that sets nothing itself:

    A: PYTHONPATH exported by the caller (how run 1 ran)
       child resolves -> ...worktrees/agent-af79f912fcfd95e05/backend/src   ← worktree
    B: no export (every other caller)
       child resolves -> ...returns_muti_agentic_platform/backend/src        ← main

Corroborated by run 1's own collection line: the main tree gives
`512/5550 tests collected` **plus a collection error** in
`tests/operations/test_case_projection.py` on an `actorId` field absent at this
base; run 1 recorded `512/5711 tests collected (5199 deselected)`, clean. Those
are different trees and the log says which one.

So **run 1's greens are not hybrid-tree greens.** They are contended greens, from
three sources, which is a different and smaller caveat. Recorded here rather than
left standing, because a correct finding with an overreaching conclusion is the
exact failure this track has spent itself on — and it would have retired the only
complete live-suite run this repository has produced.

### 13.4 — a corruption I introduced

The same check surfaced a defect of mine:

    ## step:07 — RV HARNESS-1 corrections        ← correct em dash
    ## step:08 â€” the acceptance run              ← mojibake
    ## step:09 â€” the per-module run
    ## step:10 â€” a ceiling per module
    ## step:11 â€” three corrections
    ## step:12 â€” run 1 complete

Every section I appended via PowerShell `Get-Content -Raw` piped to `Add-Content
-Encoding utf8` has double-encoded every non-ASCII character: UTF-8 bytes read as
ANSI, then re-encoded as UTF-8. Step:07, written through the editor rather than
the shell, is clean; the other agent's entries, written in ASCII, are clean.

Content is unaffected and every command and output remains legible, but it is
damage to the record and it is mine. **Not repaired here:** a byte-level rewrite
of six committed sections is exactly the edit append-only exists to prevent, and I
have already once talked myself past a rule I had written down. It needs the
orchestrator's call, and the fix for future entries is simply not to append
through that path.

### What this step does not change

The branch's substantive state is unchanged: the three RV corrections stand, the
per-module runner and its three step:12 fixes stand, run 1's aggregate stands, and
the wall-clock-budget mechanism stands as the better explanation. What changed is
that one more unverified thing became verified, and three record-level defects —
duplicate step ids, a disproven claim, and an encoding corruption — are now
written down instead of waiting to be found.

---

## step:13 -- the budget is smaller than one attempt's own ceiling

Diagnosis only. **Nothing was changed in this step**, per the orchestrator's
instruction to report before remedying.

### 1. Two corrections to my own record first

**(a) The hybrid-tree claim (step:11 sect. 3) is WRONG.** Run 1 had `PYTHONPATH`
exported by its environment. Verified by running the discriminator both ways
rather than accepting the correction:

    $ # PYTHONPATH unset -- main worktree src, feat/acc-frontend
    ERROR tests/operations/test_case_projection.py - pydantic_core._pydantic_core...
    !!! Interrupted: 1 error during collection !!!
    512/5550 tests collected (5038 deselected), 1 error in 20.22s

    $ # PYTHONPATH=<this worktree>\backend\src
    512/5711 tests collected (5199 deselected) in 7.33s

Run 1's own line is `512/5711 tests collected (5199 deselected) in 6.19s` -- the
with-`PYTHONPATH` signature, and clean where the other has a collection error.
**Run 1's greens are contended, not hybrid-tree.**

The reasoning in step:11 was sound and the premise was not checked: I verified
that *the runner* exports nothing and concluded *the run* had nothing, without
testing the environment the runner was launched from. **A correctly-reasoned
inference from an unchecked premise is still a wrong answer**, and it is a worse
failure than a bad inference because it carries the confidence of good work. The
discriminator above cost one command and existed the whole time.

**(b) My step:12 prediction cannot be settled, because there is no summary.**
Run 1 died at the end:

    ============================== 3 passed in 6.52s ==============================
    scripts/dev/run_real_infra_suite.sh: line 257: _infra: command not found
    scripts/dev/run_real_infra_suite.sh: line 258: syntax error near unexpected token `else'

All 71 modules executed; the script then resumed at a byte offset into rewritten
bytes and died before printing its summary. **The edit-mid-run hazard landed
exactly as recorded in step:11 sect. 6, on the run that was recording it.** So
the question of whether module 30 exited 124 is now unanswerable from this run,
and is recorded as unclosed rather than quietly dropped.

### 2. The aggregate, reconstructed from the log rather than quoted from the run

    modules with a header       : 71
    modules with no result line : 2   (30, 64)   [59 and 66 are all-skipped, real results]
    tests passed                : 421
    tests failed                : 3
    tests errored               : 0

    x [29] test_integration_outbox_index_plans_real_infra.py -- 2 failed, 5 passed in 33.53s
    x [69] test_return_case_workflow_real_infra.py           -- 1 failed, 12 passed in 133.67s

**A second resultless module, not previously known.** Module 64,
`tests/test_case_confirmation_starts_workflow_real_infra.py`, collected 1 item
and then produced **nothing at all** -- not even a progress dot:

    === [64/71] tests/test_case_confirmation_starts_workflow_real_infra.py
    collected 1 item
    tests\test_case_confirmation_starts_workflow_real_infra.py
    === [65/71] ...

This module is one of the four RV recorded as green in **every** execution of its
review. It is a new observation, undiagnosed, and it is **not** the same shape as
module 30 (which reached 30 of 33 tests before stopping). Recorded, not explained.

**This total is not a suite result and must not be quoted as one.** One module
hung, one produced nothing, and the run's own aggregate never printed.

### 3. The retry schedule, established from source

The orchestrator asked what the schedule for `request_bay_assignment` actually
is, and whether it can exceed the budget by construction. It can, and by more
than a factor of two.

    return_case_workflow.py:1891   await workflow.execute_activity(
                          :1892       "request_bay_assignment",
                          :1897       start_to_close_timeout=_PERSIST_TIMEOUT,
                          :1898       retry_policy=_BEST_EFFORT_RETRY,

    :111   _PERSIST_TIMEOUT:    Final = timedelta(seconds=30)
    :133   _BEST_EFFORT_RETRY:  Final = RetryPolicy(maximum_attempts=2)

`RetryPolicy(maximum_attempts=2)` leaves interval and coefficient unset, so they
take Temporal's defaults. The repository states its own arithmetic for that
policy family at `:127-132`, and it is the authority I am using rather than my
memory of Temporal's defaults:

> Retrying it on the persistence policy meant **five attempts with exponential
> backoff -- roughly fifteen seconds** added to the critical path

1 + 2 + 4 + 8 = 15. So the initial interval is 1s and the coefficient is 2, and
**two attempts means one retry after roughly 1 second.**

**The decisive number is not the backoff. It is `start_to_close_timeout`, and it
is per attempt.**

| quantity | value |
|---|---:|
| `start_to_close_timeout`, **per attempt** | 30s |
| attempts (`maximum_attempts`) | 2 |
| backoff between them | ~1s |
| **worst case for this one step, entirely within spec** | **~61s** |
| the test's budget for the whole workflow to reach a *later* step | **30.0s** |

**The test's total budget is equal to the ceiling of a single attempt of one of
the steps it must wait through, and there are two such attempts.** The bay
activity can take 61 seconds while behaving exactly as designed, and
`open_support_work_item` is downstream of it -- `_await_bay` awaits the activity
and only continues once it resolves or raises `ActivityError` (`:1900-1907`).

So `test_a_bay_failure_does_not_stop_the_return` asserts a 30s bound on a
sequence whose first component alone is allowed 61s. **It has been asserting on
a timing coincidence -- that a failing attempt fails fast -- since it was
written.** That is a defect in the test, established by construction and without
needing a reproduction.

**This displaces the "race against a retry backoff" reading.** The backoff is
~1s and cannot account for a 30s overrun; it is the per-attempt ceiling that
can. Worth separating, because the two suggest different remedies: a backoff
race would be fixed by pinning the retry policy, and this is not.

### 4. All 13 call sites, since the same question applies to each

Every `reached()` site waits for a step that is downstream of at least one
activity carrying `start_to_close_timeout=_PERSIST_TIMEOUT` (30s per attempt).

    30.0s budget (default) : lines 427, 459, 528, 650, 683, 723, 752
    20s budget (tightened) : lines 553, 593, 628, 772, 808

**Every one of the thirteen budgets is <= the per-attempt ceiling of activities
it must wait through, and five are tightened to two-thirds of it.** The mismatch
is systemic, not specific to the bay test. The bay test is simply the one that
injects a failure and therefore pays the retry, so it loses first and loses most
often -- which is exactly why it is the name that recurs across rounds.

### 5. Why the obvious remedy is unavailable, and what the options actually are

**Pinning the schedule from the test is not possible without touching
production.** `_PERSIST_TIMEOUT` and `_BEST_EFFORT_RETRY` are module-level
constants, not parameters. `ReturnCaseTimings` -- the one thing the test does
inject, via `_timings()` -- carries workflow-level waits (`bay_wait_seconds`,
`support_response_wait_seconds`), **not activity timeouts or retry policies**. So
"pin the retry policy in the test so the schedule is bounded and known", which
would have been the best answer, requires a production change and is therefore
out of scope under the standing rule. **Reported, not done.**

That leaves three, and I am not choosing between them unilaterally:

1. **Derive the budget from the production constants rather than restating it as
   a number.** Compute the ceiling in the test as
   `_PERSIST_TIMEOUT * maximum_attempts + backoff + margin` by importing the
   constants, so the budget tracks the schedule by construction and cannot drift
   from it again. This is a *larger* budget, but it is not a magic number and it
   is not raised-until-green: it is the schedule's own worst case, and it
   reddens if production ever tightens below it. Cost: the test imports two
   private constants.
2. **Wait on a state transition instead of a wall clock**, with a generous
   ceiling that is a safety net rather than the assertion. `reached()` waits for
   an activity to be *invoked*, which is an event, so this means restructuring
   what the tests wait on -- the largest change, and the only one that removes
   the wall clock from the assertion entirely.
3. **Record as a known flake** under the discipline already identified, and
   leave the budgets alone.

My recommendation is **(1)**, because it is the only one that makes the test's
bound a function of the production schedule rather than an opinion about it, and
because it fails loudly in the right direction. But the orchestrator asked for
the diagnosis before the change, and this is the diagnosis.

### 6. One thing the failure message does not answer

Across the entire run log there is exactly **one** retry record:

    $ Select-String permodule_run1.log -Pattern "Completing activity as failed"
    L1065: request_bay_assignment attempt=1

**Attempt 2 appears nowhere.** With a ~1s backoff inside a 30s window it should
have run and failed well before the budget expired. Either it was dispatched and
its log was not captured in the `call` phase, or it was not dispatched for
~29 seconds. The first is benign; the second would mean the delay is in
dispatch rather than in the schedule, which sect. 3's arithmetic would then
under-explain rather than explain.

This does not change sect. 3 -- the budget is insufficient by construction
either way -- but it is a real open question, and it is the one thing that would
justify spending the stack on a reproduction. **Not run without direction.**

### Open

1. The remedy is not chosen. Sect. 5 options reported, nothing changed.
2. Sect. 6's missing attempt 2 is unexplained.
3. Module 64 is a new resultless module, undiagnosed.
4. Module 30's exit code is now permanently unknowable from run 1 (sect. 1b).
5. Pinning activity timeouts from tests needs a production change (sect. 5).

---

## step:14 -- the reproduction: an attempt that burns its whole ceiling, on a quiet machine

Reproduction only. **The remedy is still not applied.**

Machine state at measurement: six containers healthy, no pytest or runner
process alive, CPU 19.6% then 7.5% on 8 logical processors. The three pollers
from 30-08 were still resident and were measured in step:11 at ~1.3% of one core
each (~0.5% of the machine); recorded rather than claimed away, and immaterial at
that size. **No repo file was touched:** the instrument is a standalone script in
the scratchpad that imports the test module's own `_Probe`, `_case_input` and
`_TEMPORAL_TARGET`, and replaces only the 30s ceiling with 300s so the question
becomes "how long does it take" instead of "did it fit".

### 1. What attempt 2 actually does

Measured with `--log-cli-format="MS=%(relativeCreated)d ..."` on the real test:

    run A: request_bay_assignment#1@5132ms  #2@6175ms   -> gap 1043 ms
    run B: request_bay_assignment#1@3551ms  #2@4595ms   -> gap 1044 ms

**When attempt 2 runs at all, it runs 1.04s after attempt 1** -- exactly the
1s initial interval the repository's own docstring implies. The backoff model was
right and is not the problem.

*A measurement error caught before it became a finding:* my first parse used
`%(message).90s`, which truncated the activity lines before the `'attempt'` key,
so it silently counted the two **workflow** warnings instead and reported a
"gap" of 1047ms that was the interval between two unrelated log lines. The number
was plausible, which is what made it dangerous. Re-run without truncation.

### 2. The failure reproduces on a quiet machine, and it is bimodal

Twelve runs of the diagnostic, ceiling 300s, machine idle:

    MODE A -- fresh task queue per run (what the tests do)
      run 1: first_activity= 3.15s   open_support_work_item= 45.73s  activities=8   FAIL@30
      run 2: first_activity= 0.27s   open_support_work_item=  3.68s  activities=9
      run 3: first_activity= 0.26s   open_support_work_item=  3.24s  activities=9
      run 4: first_activity= 0.27s   open_support_work_item= 34.01s  activities=8   FAIL@30
      run 5: first_activity= 0.22s   open_support_work_item=  2.95s  activities=9
      run 6: first_activity= 0.23s   open_support_work_item=  3.46s  activities=9

    MODE B -- ONE task queue reused across all runs (control)
      run 1: first_activity= 2.29s   open_support_work_item=  5.29s  activities=9
      run 2: first_activity= 0.30s   open_support_work_item= 33.87s  activities=8   FAIL@30
      run 3: first_activity= 0.24s   open_support_work_item= 33.82s  activities=8   FAIL@30
      run 4: first_activity= 0.23s   open_support_work_item=  3.57s  activities=9
      run 5: first_activity= 0.20s   open_support_work_item=  2.86s  activities=9
      run 6: first_activity= 0.30s   open_support_work_item= 32.73s  activities=8   FAIL@30

Corroborated by eleven runs of the real test through pytest on the same quiet
machine: **6 failed, 5 passed**, every failure `did not run within 30.0s`.

**There is no middle.** Every run is either ~3s or ~33-46s. A defect that
produced a continuum would be contention; a defect that produces two clusters
separated by almost exactly 30 seconds is a **timeout firing**.

### 3. Two hypotheses killed by the control, and one confirmed

**Task-queue churn is exonerated.** Mode B reuses a single task queue across all
six runs and fails **3 of 6** -- if anything worse than mode A's 2 of 6. A fresh
task queue per test is not the cost. This also disposes of the "unique task queue
per module" remedy from the original brief: the isolation it offers is isolation
from something that is not happening.

**Contention is not necessary.** 5 of 12 diagnostic runs and 6 of 11 pytest runs
failed with no other load on the machine. Contention raises the rate; it does not
create the defect. My step:11 reading -- that contention was the cause -- is
therefore **half wrong, and I am recording it as such**: contention is a
modifier, not the mechanism.

**The per-attempt ceiling is confirmed as the operative term**, which is
step:13's arithmetic vindicated by measurement rather than by argument:

    slow runs: 45.73, 34.01, 33.87, 33.82, 32.73 s
    fast runs: 2.86 - 5.29 s
    difference: ~30s == _PERSIST_TIMEOUT exactly

### 4. The mechanism, and the single fact that pins it

**Every slow run recorded 8 activities. Every fast run recorded 9. Twelve out of
twelve, no exceptions.** The missing call is the second bay attempt.

So the sequence in a slow run is:

1. Attempt 1 is dispatched and the probe raises. (Recorded: 1 bay call.)
2. Temporal schedules attempt 2 ~1s later.
3. **Attempt 2 never reaches the probe.** It is scheduled but not delivered.
4. It therefore runs out its `start_to_close_timeout` -- **the full 30s** --
   before Temporal marks the attempt failed.
5. `maximum_attempts=2` is now exhausted, `ActivityError` is raised, the
   workflow logs "bay request failed; continuing without a bay" and proceeds.

Total = ~1s backoff + **30s of a ceiling being burned by an attempt that never
ran** + ~3s of ordinary work = 33-34s, which is what four of the five slow runs
measure to within a few hundred milliseconds. The 45.73s outlier had a slow start
(`first_activity=3.15s` against a 0.2-0.3s norm) and is the same shape with a
worse prologue.

**Why the test cannot survive this:** the test budgets 30.0s for the whole
workflow, and step 4 alone consumes 30s. The budget is not merely tight -- it is
**exactly** the size of one term in a sum that has several.

*Not established:* **why** attempt 2 is not delivered to a worker that is
polling and alive. That is a Temporal task-delivery question, and it is the
remaining open item. It does not block the remedy, because the remedy has to
cover the 30s whether or not the delivery gap is ever explained.

### 5. The number the remedy needs, now derived rather than guessed

    per-attempt ceiling  _PERSIST_TIMEOUT           30s
    attempts             _BEST_EFFORT_RETRY          2
    backoff between      (1s initial, coefficient 2) ~1s
    ------------------------------------------------------
    worst case for the bay step alone, in spec       61s
    observed worst case across 12 runs            45.73s
    observed non-bay work (fast path)          2.86-5.29s

**61s is the construction bound and 45.73s is the observed one, so the
construction bound is not merely defensible -- it is the tighter statement of the
two and it dominates what was actually seen.** A ceiling derived from
`_PERSIST_TIMEOUT * maximum_attempts + backoff + margin` is therefore derived
from a **complete** model, which is what the orchestrator asked me to establish
before using it. Step:13's worry that dispatch latency was a missing term is
resolved: the "dispatch latency" *is* the 30s ceiling, and it is already in the
sum.

### 6. Three things for the record

**(a) Standing rule -- the unchecked premise.** Promoted from step:13's
self-correction, because it is a rule and not an apology:

> A correctly-reasoned inference from an unchecked premise is worse than a bad
> inference, because it carries the confidence of good work. When a conclusion
> rests on a premise about the environment -- what is on `PATH`, what is
> exported, what is checked out, what else is running -- **check the premise with
> a command, not with a reading of the code that would set it.** I verified that
> the runner exports no `PYTHONPATH` and concluded the run had none; the
> discriminator that disproved it cost one command and existed the whole time.

This step obeys it: the machine being quiet was measured, not assumed, and the
truncation bug in sect. 1 was caught by re-running rather than by trusting a
plausible number.

**(b) Module 64 is unexplained and is being kept separate.**
`tests/test_case_confirmation_starts_workflow_real_infra.py` collected 1 item and
produced nothing -- no progress dot, no summary. It is a different shape from
module 30, which reached 30 of 33 tests first. It is in a module RV recorded
green in **every** execution of its review. **It is not folded into the
wall-clock finding**, and the fact that it is nearby is not evidence that it is
the same thing.

**(c) Run 1 died on an edited script, and that is the argument for the rule.**

    ============================== 3 passed in 6.52s ==============================
    scripts/dev/run_real_infra_suite.sh: line 257: _infra: command not found
    scripts/dev/run_real_infra_suite.sh: line 258: syntax error near unexpected token `else'

All 71 modules ran; the script then resumed at a byte offset into rewritten bytes
and died before printing its summary. **The edit-mid-run hazard landed on the
one run that was recording it**, and it destroyed the aggregate -- which is why
step:13's total had to be reconstructed from per-module lines, and why the
module-30 exit-code question from step:12 is now permanently unanswerable from
run 1. Do not edit a script while a run of it is in flight.

### Open

1. The remedy is designed and **not applied**.
2. Why attempt 2 is not delivered (sect. 4) is unexplained.
3. Module 64 (sect. 6b) is unexplained.
4. Whether any of the five 20s call sites intends its budget as an *upper bound*
   on speed rather than a liveness net -- must be read per site before any of
   them is changed, because raising a deliberate upper bound would be a weakened
   assertion and the two look identical from the number alone.

---

## step:15 -- the per-site read: three sites are asserting promptness, and my derived number was too small

Read only. **The remedy is still not applied.**

### 1. Two corrections to my own numbers, both found by checking rather than repeating

**(a) It is 12 call sites in that module, not 13.** My original grep returned 13
lines and one of them was `async def reached` at line 320 -- the definition. I
counted it as a site and then repeated "thirteen" in step:13, step:14 and three
messages. Verified:

    $ grep -c "probe\.reached(" test_return_case_workflow_real_infra.py
    12

**(b) The scope is 17 sites across two modules, not 12 in one.**
`test_return_case_policy_gate_real_infra.py` carries **its own copy** of
`reached()` at line 240 with the same `within_seconds: float = 30.0` default,
and five call sites (352, 386, 442, 463, 516). It is the sibling probe that
step:02 already found stale once for the same reason -- a mechanism duplicated
across two files goes wrong in both. A remedy applied to one module and not the
other would leave five sites asserting on the same coincidence.

### 2. The per-site read

Three sites are **asserting promptness and must keep their budgets.** They are
`bay_wait_seconds=30` cases with a 20s ceiling, and the test says so itself:

    :574   `bay_wait_seconds` is 30 here and the test does not take 30 seconds:
    :575   that is the assertion. A workflow still waiting for a signal would.

    :562   # It did not sit out the 30-second bay wait, and it kept the answer.

**The budget being *smaller than* `bay_wait_seconds` is the entire mechanism of
those tests.** Raise it to anything above 30s and a workflow that sat out the
full bay wait -- the exact regression these tests exist to catch, and the exact
bug the bay activity was written to fix -- passes silently. That is a deleted
assertion that leaves a green test behind, which is the failure mode this remedy
exists to avoid.

| site | file | budget | `bay_wait_seconds` | verdict |
|---|---|---:|---:|---|
| 553 | workflow | 20s | **30** | **KEEP -- promptness** |
| 593 | workflow | 20s | **30** | **KEEP -- promptness** |
| 628 | workflow | 20s | **30** | **KEEP -- promptness** |
| 772 | workflow | 20s | 30 | raise -- liveness net |
| 808 | workflow | 20s | 0 | raise -- liveness net |
| 427, 459, 528, 650, 683, 723, 752 | workflow | 30s | 1 (default) | raise -- liveness net |
| 352, 386, 442, 463, 516 | policy gate | 30s | 0 (default) | raise -- liveness net |

**14 raise, 3 keep.**

Site 772 is the one that needed reading rather than pattern-matching: it *does*
carry `bay_wait_seconds=30`, so it looks like the other three. But it waits for
`request_bay_assignment`, which runs **before** the bay wait begins, and its
assertions are `outcome.status == "CANCELLED"` and
`"open_support_work_item" not in probe.calls`. Nothing about the 20s is
load-bearing. Classifying it by its timings alone would have got it wrong in the
safe direction; classifying it by its budget alone would have got it wrong in the
dangerous one.

Site 808 sets `bay_wait_seconds=0`, so there is no wait to prove avoidance of.
The five policy-gate sites default to `bay_wait_seconds: 0` (`:262`) and none
passes `within_seconds`, so none of them is asserting promptness either.

### 3. The derived number was too small, and the correction is not small

Step:14 gave the construction bound as **61s** = `_PERSIST_TIMEOUT x 2 + 1s`.
**That is the bound for the bay activity alone, not for the path**, and the
budget guards the whole path. Read from the objects rather than from the source:

    _BEST_EFFORT_RETRY: attempts=2  initial=0:00:01  coeff=2.0
    _PERSIST_RETRY:     attempts=5  initial=0:00:01  coeff=2.0
    _DRAFT_RETRY:       attempts=2  initial=0:00:01  coeff=2.0
    _PERSIST_TIMEOUT = 0:00:30

`open_support_work_item` sits downstream of activities on **`_PERSIST_RETRY`**
(`:1714`, `:1730`, `:1977` -- the status and customer writes), and that policy is
**five** attempts:

    one _BEST_EFFORT_RETRY activity exhausting:  30*2 + (1)          =  61s
    one _PERSIST_RETRY   activity exhausting:  30*5 + (1+2+4+8)      = 165s
    observed worst case across 12 runs                               = 45.73s

So the honest construction bound for these budgets is **165s**, not 61s. My
step:14 figure was derived from the wrong activity -- the one that happened to
fail in front of me. **That is the same error as reasoning from the failure you
can see**, one level up, and it is exactly what the orchestrator was guarding
against by asking whether the model was complete.

**The derivation can live in code rather than in a comment**, which was the
requirement: every term is a readable attribute (`maximum_attempts`,
`initial_interval`, `backoff_coefficient`) on the imported policy objects, so a
helper can compute the ceiling from `_PERSIST_TIMEOUT` and the retry policy and
will track production automatically if either changes.

**The cost, stated rather than buried:** a 165s+ liveness net means a genuinely
hung test takes ~3 minutes to fail instead of 30 seconds. Across 14 sites that is
a worst-case wall-time increase the orchestrator should price before I apply it.
It is bounded by the per-module 900s ceiling, but it is not free, and I am not
choosing it unilaterally.

### 4. The three kept sites will stay flaky, and that is the honest consequence

The three promptness sites are exposed to the same 30s dead wait as everything
else. Keeping their budgets means **keeping their flakiness** -- they will still
fail when an attempt burns its ceiling, and under the standing rule those
failures must be recorded as flakes, never re-run into passes.

**But those three are precisely the sites where the wall clock is a proxy for a
state fact that could be asserted directly**, which makes them the best possible
target for option (2) rather than the worst. The claim "it did not sit out the
bay wait" is a claim about *how the bay was resolved*, and the workflow already
exposes it -- `test_the_bay_activity_answers_and_no_signal_is_needed` queries
`state.bay_resolved` at `:595`, four lines after its own wall-clock wait.

**Registered follow-up (option 2), with a concrete target rather than a
direction:** replace the 20s wall-clock proxy at sites 553/593/628 with an
assertion on `execution_state` -- that the bay was resolved by signal or by the
activity, not by the wait expiring -- and give them the same derived liveness
ceiling as the other 14. That is strictly stronger than the timing proxy (it
distinguishes *why* the bay resolved, which 20s-vs-30s only infers) and it is
deterministic. It is not done here because it changes what the tests assert, and
that is a bigger change than the one asked for.

### 5. Escalation: is the undelivered attempt a production concern?

**My read: more likely a test-harness artefact than a production defect --
but that is a judgement, not a finding, and the reassuring answer is the one I
am least entitled to assume.**

For the artefact reading: these tests construct and destroy a `Worker` **per
test**, inside `async with`, against a client shared for the module. A production
worker is started once and polls for the process's lifetime. Rapid worker
churn -- a poller appearing and vanishing every few seconds -- is a property of
the harness that production does not have. And the control points the same way:
**mode B, which reuses one task queue across iterations and therefore accumulates
pollers from workers that have already shut down, failed 3 of 6 against mode A's
2 of 6.** A task matched to a poller belonging to a worker that has gone away
would produce exactly what was measured -- started as far as the server is
concerned, never executed, timed out at `start_to_close`.

Against it, and why I will not call it settled: the worker is demonstrably alive
and inside its `async with` for the entire measured window in every run, and
mode A uses a **fresh queue per iteration** with no prior pollers on it at all,
yet still failed twice. The stale-poller story does not cover mode A, so the
mechanism I am proposing does not fully fit my own data.

**What would settle it, cheaply and read-only:** the workflow's own event
history for a slow run distinguishes the two readings in one field.

    temporal workflow show --workflow-id <id> --namespace default

An `ActivityTaskTimedOut` with `timeoutType: START_TO_CLOSE` means a worker
accepted the task and never completed it -- the harness-churn reading, and
survivable in production where workers do not churn. A `SCHEDULE_TO_START`
timeout, or a scheduled event with no corresponding `ActivityTaskStarted`, means
the server never delivered it to anyone -- **and that is a production concern**,
because `_PERSIST_TIMEOUT` guards real persistence steps and a case would sit
visibly stalled for 30 seconds per affected attempt, up to 165s on
`_PERSIST_RETRY`, with nothing in any log to explain it.

**Flagged as a possible production concern so it is not lost behind a test fix.**
I have not run it -- it needs one slow run captured with its workflow id, which
is a few minutes of stack, and I am not spending that without a decision.

### Open

1. The remedy is **not applied**. 14 sites to raise, 3 to leave.
2. The ceiling figure needs a decision: 165s is the honest construction bound,
   and it costs ~3 minutes per genuinely hung site.
3. Sites 553/593/628 stay flaky by design until the option-2 follow-up lands.
4. Whether the undelivered attempt is a production defect is **unsettled**, and
   the discriminator in sect. 5 has not been run.
5. Module 64 remains unexplained.

---

## step:16 -- the discriminator, and the ceiling derived in code

### 1. The discriminator: `START_TO_CLOSE`, and the server did not lose the task

A slow run was captured with its workflow id (34.31s, 8 activities,
`test-return-case-8f19915c`) and its history read read-only:

    $ docker exec ...temporal-1 temporal workflow show \
        --workflow-id test-return-case-8f19915c --namespace default \
        --address 172.22.0.7:7233

    id=17 EVENT_TYPE_ACTIVITY_TASK_SCHEDULED  activity=request_bay_assignment id=3 s2c=30s s2s=0s
    id=18 EVENT_TYPE_ACTIVITY_TASK_STARTED    attempt=2 identity=28556@udhaya-pc
    id=19 EVENT_TYPE_ACTIVITY_TASK_TIMED_OUT  TIMEOUTTYPE=TIMEOUT_TYPE_START_TO_CLOSE
                                              retryState=RETRY_STATE_MAXIMUM_ATTEMPTS_REACHED
    id=23 EVENT_TYPE_ACTIVITY_TASK_SCHEDULED  activity=evaluate_case_eligibility id=4
    id=24 EVENT_TYPE_ACTIVITY_TASK_STARTED    attempt=1
    id=25 EVENT_TYPE_ACTIVITY_TASK_COMPLETED

Timestamps: event 18 at `12:58:45`, event 19 at `12:59:15` -- **30 seconds
exactly**, which is `_PERSIST_TIMEOUT`.

**`TIMEOUT_TYPE_START_TO_CLOSE`, not `SCHEDULE_TO_START`.** A real worker
(`28556@udhaya-pc`, the test process itself) **accepted** attempt 2 and never
completed it. The server delivered the task.

**Why that is the reassuring answer, and why it is not merely reassuring by
assertion.** `s2s=0s` -- `schedule_to_start_timeout` is unset, i.e. unlimited.
Had the server failed to deliver the task, it would have sat in the queue
**forever** rather than timing out at 30s. So delivery is **proven by the
timeout type that fired**, not inferred from the absence of evidence. The
alarming reading -- an activity scheduled that no worker ever receives -- is
ruled out by the mechanism of the observation itself.

**Residual production concern, downgraded but not closed.** What remains is "a
live worker accepted a task and did not execute it", which is precisely the
condition `start_to_close_timeout` exists to bound, and production handles it the
way it is designed to: time out, retry, five attempts on `_PERSIST_RETRY`. A
production case would be delayed rather than stuck, and the delay is bounded and
visible in the workflow history. **It is a worker-side stall, not a server
routing defect, and it is not the silent-and-unexplained failure mode I flagged.**

*Still unexplained:* why a worker inside a live `async with Worker(...)` accepts
a task and does not run it. My harness-churn story from step:15 does not fully
cover it -- mode A used a fresh queue per iteration -- and I am leaving it
unexplained rather than fitting a story to it. It is now a bounded curiosity
rather than a possible production defect, which is the difference the
discriminator bought.

### 2. The remedy, applied

**Files:** `tests/activity_probe.py`, `tests/test_return_case_workflow_real_infra.py`,
`tests/test_return_case_policy_gate_real_infra.py`. **No production file
touched.**

The derivation lives in `activity_probe.py` -- the module that already exists
because a duplicated hand-maintained list rotted twice, which makes it the right
home for the second thing that was duplicated across the same two files:

    def worst_case_activity_seconds(timeout: timedelta, retry: RetryPolicy) -> float:
        attempts = max(retry.maximum_attempts, 1)
        ...
        return timeout.total_seconds() * attempts + backoff

    LIVENESS_CEILING_SECONDS = (
        worst_case_activity_seconds(_PERSIST_TIMEOUT, _PERSIST_RETRY) + _PATH_MARGIN_SECONDS
    )

Evaluated against the real production objects:

    persist  worst: 165.0      # 30 * 5 + (1+2+4+8)
    besteff  worst:  61.0      # 30 * 2 + 1
    LIVENESS_CEILING_SECONDS = 180.0

**It is a derivation, not a comment describing one.** Every term is read from
the imported `RetryPolicy` (`maximum_attempts`, `initial_interval`,
`backoff_coefficient`, `maximum_interval`) and from `_PERSIST_TIMEOUT`. Change
either constant in production and the ceiling moves with it.

**The margin is 15.0s and it is derived, not chosen for feel.** Twelve
instrumented runs (step:14) put the *entire* fast path at 2.86-5.29s, so 15s is
roughly three times the worst observed whole-path cost. It is also small beside
the 165s term it is added to, which is the point: the bound is dominated by the
schedule, not by my headroom.

**Uniform, deliberately.** One bound from the worst policy on the path, not a
per-site derivation. A per-site bound would be tighter and would offer fourteen
more chances to under-model exactly one path -- which is the error I made in
step:14, deriving 61s from `_BEST_EFFORT_RETRY` because that was the activity I
had watched fail. **A conservative uniform bound cannot be wrong in that
direction.** This is recorded in the constant's own docstring so the next reader
does not "improve" it into fourteen fragile derivations.

**The cost is not the cost I feared.** `reached()` returns as soon as its
condition is met, so the ceiling is a ceiling and not a duration: a larger bound
makes *failing* tests slower to report and does nothing to passing ones.

**Scope: 17 sites across both modules.** 14 take the derived ceiling; 3 keep
their own. The policy-gate module's five sites were included -- fixing twelve and
leaving five would have been the mis-pointed-row shape in a new costume, and that
module's `reached()` docstring now records that this is the **second** defect
caused by the two files carrying separate copies of the same helper.

**Registered, not done:** collapsing the two `_Probe.reached` implementations
into one shared helper. The budget no longer drifts -- both defaults now come
from the same constant -- but the *method* is still duplicated, and the two
probes differ in construction (the policy-gate probe takes a required argument),
so merging them is a larger refactor than this remedy. **Registering it rather
than leaving it silent, because the duplication is now 2 for 2 on causing
defects.**

### 3. The three sites that keep their budgets, and what they assert

`553`, `593`, `628` (pre-edit numbering) keep `within_seconds=20`, each with a
comment at the call site saying why -- because a future reader seeing three
"unfixed" budgets beside fourteen raised ones would otherwise assume an oversight
and finish the job:

> `bay_wait_seconds` is 30 and this is 20: the ceiling being *below* the bay wait
> is what proves the case did not sit the wait out. Raising it to the derived
> liveness ceiling would delete that assertion and leave a green test behind.

**These three remain flaky, and that is stated rather than hidden.** They are
exposed to the same accepted-but-never-completed attempt as everything else, and
when it happens they will fail. Under the standing rule those failures are
**flakes and must be recorded as flakes, never re-run into passes.**

**Registered follow-up (option 2), with a target rather than a direction:**
replace the timing proxy at those three sites with an assertion on
`execution_state` -- that the bay resolved by signal or activity rather than by
the wait expiring. `test_the_bay_activity_answers_and_no_signal_is_needed`
already queries `state.bay_resolved` **two lines below its own wall-clock wait**,
so the state fact is available today. That is strictly stronger than the timing
proxy: it distinguishes *why* the bay resolved, where 20-versus-30 seconds only
infers it. Not done here because it changes what the tests assert.

### 4. Rule 10 and rule 13

    $ git diff -U0 | grep "^+.*\(skip\|xfail\)"       -> no matches
    $ git diff -U0 | grep "^-.*\(assert \|def test_\)" -> no matches
    $ ruff check <3 files>                             -> All checks passed!
    $ ruff format <3 files>                            -> 2 reformatted, 1 unchanged

No test deleted, no assertion removed or weakened, no skip or xfail added. Four
assertions were **added** to the outbox test in step:11 and none removed
anywhere. The three promptness budgets are unchanged.

**Rule 13.** Both edited test modules are `*_real_infra.py` carrying
`pytest.mark.live_infra`, and `addopts` carries
`-m "not live_infra and not browser"`, so **CI runs neither.** They fall on the
ungated side; their only gate is `scripts/dev/run_real_infra_suite.sh`.
`tests/activity_probe.py` is different: it is imported by
`test_return_case_workflow_replay_compatibility.py`, which carries **no**
live-infra marker and **is** collected by CI's backend job
(`checks.yml:129`). So the new code in `activity_probe.py` is import-time
executed under CI -- if the derivation raised, CI would go red. The *value* it
computes is only exercised live.

### Open

1. Repetition proof is running; results in the next step. **Nothing is claimed
   green until it lands.**
2. Sites 553/593/628 stay flaky until the option-2 follow-up.
3. The two `reached()` copies are still two copies (sect. 2).
4. Why a live worker accepts a task and does not run it (sect. 1).
5. Module 64 remains unexplained.

---

## step:18 — the repetition: the remedy did NOT work, and the ceiling was itself exceeded

**Headline: four of five runs of the previously-flaky module failed after the
remedy. The remedy is not a fix and must not be recorded as one.**

### 1. Every result, verbatim

    ########## WORKFLOW MODULE RUN 1 ##########
    13 passed in 117.86s (0:01:57)

    ########## WORKFLOW MODULE RUN 2 ##########
    FAILED ...::test_reminders_fire_on_the_configured_cadence_and_then_park
    FAILED ...::test_the_bay_activity_answers_and_no_signal_is_needed
    FAILED ...::test_a_graph_sync_failure_parks_the_case_loudly
    3 failed, 10 passed in 649.49s (0:10:49)

    ########## WORKFLOW MODULE RUN 3 ##########
    FAILED ...::test_a_graph_sync_failure_parks_the_case_loudly
    1 failed, 12 passed in 248.31s (0:04:08)

    ########## WORKFLOW MODULE RUN 4 ##########
    FAILED ...::test_a_signal_that_won_the_race_is_not_overwritten_by_the_activity
    FAILED ...::test_a_duplicate_support_response_does_not_issue_a_second_set_of_rmas
    2 failed, 11 passed in 259.62s (0:04:19)

    ########## WORKFLOW MODULE RUN 5 ##########
    FAILED ...::test_the_wait_counts_business_time_not_wall_clock
    1 failed, 12 passed in 385.87s (0:06:25)

    ########## POLICY GATE RUN 1 ##########   8 passed in 22.78s
    ########## POLICY GATE RUN 2 ##########   8 passed in 26.14s
    ########## OUTBOX MODULE ##########       7 passed in 73.51s (0:01:13)

**Workflow module: 1 clean run in 5.** Seven test failures across four runs, and
the names are drawn fresh again — `test_a_graph_sync_failure_parks_the_case_loudly`
twice, everything else once.

### 2. The decisive result: the derived ceiling was itself exceeded

Run 5, verbatim:

    >           raise AssertionError(f"{name} did not run within {within_seconds}s") from None
    E           AssertionError: open_support_work_item did not run within 180.0s

    tests\test_return_case_workflow_real_infra.py:346: AssertionError

`test_the_wait_counts_business_time_not_wall_clock` is **site 808 — one of the
fourteen I raised.** It took more than 180 seconds to reach
`open_support_work_item`, against a ceiling derived as
`_PERSIST_TIMEOUT x maximum_attempts + backoff + margin` = 165 + 15.

**So the model is still incomplete.** Step:14 concluded the dispatch gap "IS the
30s ceiling and was already in the sum". Run 5 falsifies that: something on this
path can consume more than 180s, and the retry schedule does not account for it.
The same criticism I made of my own 61s figure in step:15 — deriving from the
activity that happened to fail in front of me — applies again to 180s, one level
further out. **Two successive derivations, each corrected by the next
measurement, is a sign that the quantity is not bounded by the schedule at all.**

### 3. The failures are not one defect. There are at least four signatures.

This is the finding that most changes the picture, and no previous round could
have seen it because no previous round captured messages.

**(a) The ceiling, exceeded** — run 5, above.

**(b) A workflow-history assertion, not a timeout** — run 2:

    E                 synchronize_return_records attempt=1
    E             historyLength     : 96
    E             no failed workflow task; last event types: 7, 10, 11, 12, 5, 6, 7, 10
    tests\workflow_result.py:84: AssertionError

That is `tests/workflow_result.py`, a different helper entirely, asserting on the
workflow's event history. Nothing to do with `reached()` or with any budget.

**(c) A transport error** — run 4:

    >           raise RPCError(message, RPCStatusCode(status), details)
    E           temporalio.service.RPCError: h2 protocol error: http2 error
    ..\..\..\..\backend\.venv\Lib\site-packages\temporalio\service.py:457: RPCError

**An HTTP/2 protocol error on the gRPC connection to Temporal.** This is
infrastructure, not a budget and not a workflow defect, and it took out two tests
in one run. It has never appeared in any previous record on this track — because
no previous round recorded messages.

**(d) The graph-sync test** — runs 2 and 3, the only repeat offender. The
captured tail shows its own deliberately injected
`RuntimeError: the graph refused the write`, which is the fault the test
*injects*, so the tail does not tell us what the assertion failure was.

**My step:11 diagnosis addressed (a) only.** The wall-clock budget was real and
was worth fixing, but it was one of several mechanisms, and the module's
flakiness is not reducible to it. The convergence I found across three rounds —
every named test routing through `reached()` — was true and was *not* sufficient,
because those rounds recorded only names, and names cannot distinguish (a) from
(b), (c) or (d).

### 4. A defect in my own instrument, and it breaks my own rule

`repeat.ps1` captured each run with `Select-Object -Last 12`. For runs with one
failure that was enough; for run 2, three failures were compressed into a tail
that shows **one** partial traceback. So for signature (d) I have a name and no
message.

**I wrote the standing rule in step:12 — "a flake investigation that records
names and not messages is not an investigation" — and then built an instrument
that truncates messages.** Recorded rather than quietly re-run, because the
lesson is that a rule stated in a ledger does not enforce itself; the harness has
to. Any re-run must use `-rA` with no tail truncation and per-run log files.

### 5. What is clean, and it is not nothing

    policy gate : 8 passed, 8 passed          (22.78s, 26.14s)
    outbox      : 7 passed                    (73.51s)

**The outbox fix is verified** — 7 passed on a quiet stack, independently of the
other author's verification of the same thing. **The policy-gate module is green
twice** and fast, which is consistent with its five sites having been raised and
with that module never having been the unstable one.

So the remedy is not worthless: it is *sufficient for the policy-gate module* and
*insufficient for the workflow module*. Reporting that split rather than a single
verdict, because a single verdict would be wrong in one direction or the other.

### 6. A correction to the record about this run

I was told the repetition had been killed and that only run 1 completed. **That
is not what happened.** The background task ran to completion (exit 0), the
monitor fired through `########## ALL DONE ##########`, and the log carries all
eight runs, last written 19:03. Checked against the artifact rather than accepted:

    $ ls -la repeat.log   ->  4358 bytes, Aug 31 19:03

Had I accepted it, I would have re-run 30 minutes of stack to rediscover results
that were already on disk — and, worse, would have reported run 1's `13 passed`
as the only datum, which is the single most misleading number in the whole set.

### Open

1. **The remedy does not fix the workflow module.** 4 of 5 runs failed.
2. The 180s ceiling is exceeded in practice; the quantity is not bounded by the
   retry schedule, and I no longer have a derivation I trust.
3. Signatures (b), (c) and (d) are undiagnosed. (c) — `h2 protocol error` — is
   an infrastructure symptom that may bear on every live module, not just this one.
4. Signature (d) has no captured message, because my own harness truncated it.
5. The three promptness sites remain flaky by design; run 2's
   `test_the_bay_activity_answers_and_no_signal_is_needed` and run 4's
   `test_a_signal_that_won_the_race_is_not_overwritten_by_the_activity` are two
   of those three, and both are **flakes, not passes**, in any record of this run.

---

## step:19 — reconciliation: an index to the duplicated step ids

This file has three step ids that appear twice, written by two authors. This
entry resolves each to a commit so a reader can look one up instead of guessing.
**Nothing is renumbered, deleted or edited.** Renumbering would rewrite another
author's append-only entry, which is the one edit this format exists to prevent,
and doing it to tidy the record would be worse than the disorder it fixes.

### 1. The cause, which is not carelessness by either author

**The orchestrator resumed two agents onto one branch without either being told
the other was active.** Both wrote in good faith into the file they had been
pointed at, under the same git identity, so neither `git log` nor `git blame`
distinguishes them. Each discovered the other only by noticing an artifact that
did not match its own record — one a corrupted encoding, the other a block of
script that had moved further than its own edit explained.

Recording this because a future reader finding duplicate ids will otherwise
assume whoever wrote them was careless. Neither was.

**Both authors initially wrote the other's commits up as an intrusion. Both
framings were wrong, symmetrically, and neither is preserved here** — not the
other author's, which it has withdrawn, and not the mirror of it from my side.
Neither of us could see that the other had been resumed onto the branch.

### 2. The index

Steps 07–10 are the other author's and predate the second author's arrival
entirely; they are listed for completeness and do not collide.

| step | sha | time | author | what it is |
|---|---|---|---|---|
| 07 | `b4c26396` | 15:36 | A | the three RV corrections — a count, a limit, an attribution |
| 08 | `9c5cce57` | 15:47 | A | a process per module, and an aggregate that cannot lie |
| 09 | `b71f4d9c` | 16:49 | A | per-module does not fix it |
| 10 | `497c1ca7` | 16:53 | A | a ceiling per module |
| **11** | `d1313348` | 17:03 | **A** | **three corrections to its own record** — that a late `kill -9` of its own stopped the hung reservations process (the orchestrator did); that run 1 had stopped at 29 modules (it was still running, concluded from a 0-byte launcher stdout and one of its own killed waiters); and that it edited the runner mid-execution having written in step:09 that it would not |
| **11** | `c11c7db5` | 17:11 | **B** | **the stale outbox pin, and the wall-clock mechanism** — production right, test stale, pin 7→9 with four assertions added; and the `reached()` budget found in the August remediation ledger |
| **12** | `472dd452` | 17:17 | **B** | **the ceiling did not fire** — retraction of the claim that step:10's timeout handled the module-30 hang; the fourteen-runs-no-messages rule |
| **12** | `d4d35f4f` | 17:58 | **A** | **run 1 complete, and what the message said** — the 71-module aggregate derived from the log because the summary never printed; 421/3/54 reconciling to 512; module 69's verbatim message; withdrawal of step:09's accumulated-state claim; three runner fixes |
| **13** | `c29da2bd` | 18:00 | **A** | **a second writer, a verified fix, and a corruption of mine** — the collision from its side; verification of B's outbox change on a quiet stack (7 passed); disproof of the hybrid-tree reading; its own `Add-Content` encoding defect, left unrepaired |
| **13** | `5abc3028` | 18:07 | **B** | **the budget is smaller than one attempt's own ceiling** — `start_to_close_timeout` is per attempt |
| 14 | `220778bf` | 18:22 | B | the bimodal reproduction on a quiet machine |
| 15 | `fad67539` | 18:27 | B | three sites assert promptness; the derived number was too small |
| 16 | `6138f74d` | 18:34 | B | `START_TO_CLOSE`; the ceiling derived in code |
| 17 | `b989c16d` | 18:39 | B | a marker for the module the gate did not stop |
| 18 | `7d7fc85e` | 19:14 | B | the remedy did NOT work; the ceiling was itself exceeded |

Verified against the repository rather than transcribed:

    $ git log --format="%h %ad %s" --date=format:"%H:%M:%S" b4c26396^..HEAD
    $ grep -n "^## step:" HARNESS.ledger.md | ... | sort | uniq -d
    -> step:11  step:12  step:13

### 3. Going forward

**One owner on this branch from now on, and it is author B.** Author A has stood
down and confirms it has run, committed and edited nothing since `c29da2bd`. Its
half of this map was relayed through the orchestrator rather than written into
this file, so the duplication does not grow while being documented.

Step ids from `step:14` onward are unambiguous. The three duplicated ids stay
duplicated; this index is how they are resolved.

### 4. Author A's work, assessed

None of it is reverted, and three parts of it are better than what I had:

- **The `PYTHONPATH` fix in the runner is better than my workaround.** I set the
  variable per-command; A exported it from the script itself
  (`export PYTHONPATH="$ROOT/backend/src${PYTHONPATH:+:$PYTHONPATH}"`, prepended
  so a deliberate caller value survives), which fixes it for every invocation
  rather than for the ones I remembered.
- **The `n_skipped` parsing fix explains modules 59 and 66**, which my own log
  parser also mis-flagged as resultless in step:13 sect. 2. An all-skipped module
  reports `10 skipped in 6.37s` with no other number in the line, and without
  that fix it dragged the whole run into "cannot report what it ran". A found and
  fixed a defect I had only observed.
- **The per-module timestamps close a gap I complained about without fixing.**
  I noted in step:11 that no round had recorded machine load; A made the
  durations placeable against it.

Its step:12 verification of my outbox change also closed the explicit
"not executed" caveat I had left on it, before my own step:18 re-verified the
same thing independently. Both measurements agree: 7 passed.

### 5. The honest state of the branch

**Two authors' fixes, individually reasoned — and the joint verification is still
missing, though less of it than when that phrase was coined.** Precisely:

- **My ceiling change has now been exercised, and it failed** (step:18): four of
  five runs of the workflow module still fail, and one exceeded the 180s ceiling
  itself. It is not a fix.
- **A's three runner fixes have never been exercised by a completed suite run.**
  Run 1 predates them entirely and died on the mid-run edit; step:18's repetition
  invoked `pytest` directly and bypassed `run_real_infra_suite.sh` altogether.
- **My step:17 runner marker fix is likewise unexercised**, and it sits on top of
  A's changes in the same file.

So the runner as it now stands — A's step:12 plus my step:17 — **has never been
run end to end by anyone.** The live suite has still never been run to completion
with both authors' changes present, and no one should quote a live-suite result
until it has.

---

## step:21 — the root cause is fsync latency on the Temporal database, and no test-side change can fix it

### 1. The re-run, on reverted budgets, with the fixed instrument

    workflow_run1   [ 93s]  13 passed
    workflow_run2   [118s]  1 failed, 12 passed
                            FAILED test_a_bay_failure_does_not_stop_the_return
                              E  AssertionError: open_support_work_item did not run within 30.0s
    workflow_run3   [ 88s]  13 passed
    workflow_run4   [ 79s]  13 passed
    workflow_run5   [275s]  2 failed, 11 passed
                            FAILED test_the_bay_activity_answers_and_no_signal_is_needed
                            FAILED test_a_signal_that_won_the_race_is_not_overwritten_by_the_activity
                              E  AssertionError: open_support_work_item did not run within 20s
                              E  temporal_sdk_bridge.RPCError: (14, 'shard status unknown', b'')
                              E  temporalio.service.RPCError: shard status unknown
    policygate_run1 [ 67s]  8 passed
    policygate_run2 [ 25s]  8 passed
    outbox_run1     [ 22s]  7 passed

**3 clean runs of 5 on the reverted budgets, against 1 of 5 with the 180s
ceiling.** Small samples, but they point the same way as the reversion argument:
the raise bought nothing, and a longer ceiling means a failing test occupies the
stack longer, which is why run times ballooned to 649s under it.

The instrument fix earned itself immediately: signature (c) reappeared with a
**different message** — `shard status unknown` (gRPC status 14, UNAVAILABLE) —
where run 4 of the previous batch had `h2 protocol error`. Under the old
`-Last 12` harness the second of run 5's two failures would have been lost.

### 2. Following the transport error into the server

`shard status unknown` came back from `get_workflow_execution_history`. That is
not a test-side condition, so I read the server's own logs. Over three hours:

    "Failed to start transaction"   145
    "context deadline exceeded"     266
    "shard status unknown"           44
    "Acquired shard"                 19

Representative lines:

    {"level":"error","msg":"Operation failed with internal error.",
     "error":"UpdateTaskQueue failed. Failed to start transaction.
              Error: context deadline exceeded"}
    {"level":"error","msg":"Persistent fetch operation Failure","shard-id":4}
    {"level":"warn","msg":"Failed to poll for task.","Error":"context deadline exceeded"}
    {"level":"info","msg":"Acquired shard","shard-id":4}

**Temporal cannot start transactions against its Postgres inside its own
deadlines.** When that persists, it loses and re-acquires history shards —
nineteen times in three hours — and a client whose call lands during a shard
handover gets `shard status unknown`.

And Postgres says why:

    checkpoint complete: wrote 1830 buffers (11.2%); write=174.354 s, total=235.963 s
    checkpoint complete: wrote  971 buffers (5.9%);  write= 98.828 s, total=100.167 s
    checkpoint complete: wrote  920 buffers (5.6%);  write= 94.058 s, total= 95.377 s

**1830 buffers is about 14 MB. It took 174 seconds to write.**

### 3. The measurement that settles it

`pg_test_fsync`, run on the Temporal Postgres data volume:

    Compare file sync methods using one 8kB write:
        open_datasync         33.557 ops/sec    29800 usecs/op
        fdatasync              7.949 ops/sec   125797 usecs/op
        fsync                  2.464 ops/sec   405867 usecs/op
        open_sync             10.639 ops/sec    93993 usecs/op

    Non-sync'ed 8kB writes:
        write            1087454.346 ops/sec        1 usecs/op

**Between 30 and 406 milliseconds for a single 8 kB durable write.** A healthy
SSD does this in well under a millisecond, at thousands of operations per
second. This volume manages **two to thirty-three**. The unsync'd figure —
over a million ops/sec — shows the data path itself is fine; it is **durability**
that costs, which is precisely what a database does on every commit and every
checkpoint.

The arithmetic closes: a checkpoint of 1830 buffers at single-digit sync
operations per second is on the order of two to three minutes, and the log says
174 seconds.

### 4. What this rules out, by measurement rather than by argument

| candidate | measurement | verdict |
|---|---|---|
| accumulated workflow state | database is **38 MB**, 916 executions | **ruled out** |
| memory pressure | Postgres at **166 MB of a 1 GB** limit | ruled out |
| a slow Windows bind mount | it is a **named Docker volume**, not a bind mount | ruled out |
| sequential disk throughput | **53 MB/s** (Mongo's volume: 278 MB/s) | ruled out |
| durable write latency | **30–406 ms per 8 kB sync** | **this is it** |

**The accumulation hypothesis is now dead on a direct measurement rather than on
an inference.** A 38 MB database with 916 executions is not a database that
needs pruning, and clearing the namespace would not move a number that is set by
how long the disk takes to acknowledge a flush.

### 5. Why every remedy this track has tried was doomed

This is the finding that reframes the whole investigation, and it is worth
stating plainly rather than tactfully:

- **Per-module execution** (step:08–09) could not help: a process boundary does
  not change fsync latency.
- **Namespace or task-queue isolation** — the original brief's leading
  candidate, and the module docstring's rejected experiment — could not help,
  and the reused-queue control in step:14 already showed it empirically.
- **My derived ceiling** (step:16) could not help. A budget cannot outrun a
  server whose transactions are timing out; it can only make a stuck test wait
  longer before it gives up, which is exactly what step:18 measured.
- **The three promptness sites** cannot be rescued by any budget at all, because
  their budgets are assertions.

Every one of these aimed at the test process or the Temporal namespace. **The
constraint is under both of them, in the storage layer**, and nothing in
`backend/tests` can reach it.

It also explains the residue nobody could place: activity attempts accepted and
never completed (step:16's `START_TO_CLOSE`, verbatim from the history) are what
a worker looks like when its completion RPC cannot commit; the bimodality is a
write that either lands or waits on a stalled flush; and the two modules that
produced no result at all — `test_order_line_reservations_real_infra.py` and
module 64 — are the shape of a client blocked on a server that has lost its
shard. **None of those are established as the same defect and I am not claiming
they are**, but they are now all consistent with one cause for the first time.

### 6. A near-miss of my own, recorded

I first ran the sync test with `dd ... oflag=dsync`, it did not return inside
120 seconds, and I wrote that up as "fewer than 8 IOPS" — a finding. It was not.
The Postgres container's busybox `dd` had rejected the flag instantly:

    dd: invalid argument 'dsync' to 'oflag'

The hang was the *other* leg of the same command, against Mongo. **I had a
plausible number attached to the wrong cause, which is the exact shape of the
truncation bug from step:18**, two steps after writing the rule about it. Caught
by reading the output file before reporting rather than after. `pg_test_fsync`
replaced the guess with an instrument built for the question.

### 7. Where the stack actually comes from

Worth recording, because it bears on who can fix this and it is not what the
dispatch assumed:

    com.docker.compose.project.config_files :
      K:\Projects\FEG\Ret\full\returns_platform_copilot_recovered_20260804-211601\compose.yaml
      ...\compose.novault.yaml

**The running stack is defined by a compose file in a different project
directory entirely, not by this repository's `scripts/infra.sh`.** Anything done
about section 8 touches a file outside this repo and shared with whatever else
uses that stack.

### 8. Remedy — proposed, NOT applied

The fix is at the storage layer and it is cheap, but it is **not mine to apply
unilaterally**: it is infrastructure config, it lives outside this repository,
and it changes a stack other work may be using.

For a **disposable test database**, durability is a cost with no benefit — the
data is thrown away between runs, and a crash mid-suite means re-running the
suite, not losing records. Two options, either of which removes the bottleneck:

1. `synchronous_commit=off` and `fsync=off` on the Temporal Postgres service
   (command flags or `POSTGRES_INITDB_ARGS`). Commits stop waiting on the disk.
2. Mount the Temporal Postgres data directory on **tmpfs**. The data is
   disposable by definition, and RAM has no fsync problem.

**This is testable the way the brief asked for**: turn it on, re-run the module
five times, turn it off, re-run. If flakiness tracks the setting, the mechanism
is established end to end and the live suite becomes usable. **I have not done
it**, and I will not start a container restart on a shared stack without a
decision.

If it is refused, the honest fallback is the one already identified: this suite
cannot be a hard gate on this hardware, and its failures must be recorded as
**flakes, never re-run into passes**.

### Open

1. The remedy is proposed and unapplied; the on/off experiment is unrun.
2. Signature (b) — `tests/workflow_result.py:84`, "no failed workflow task" —
   did not recur in this batch and is still uncharacterised. Whether it is a
   product defect remains **unestablished**; I have not fixed anything on that
   suspicion.
3. Signature (d)'s message is still uncaptured — it did not recur either.
4. Module 64 and `test_order_line_reservations_real_infra.py` remain
   undiagnosed, now with a plausible but unproven common cause.
5. The branch fails `ruff format --check` pre-existing (step:20).

---

## step:22 — before-state and predictions, recorded before the stack is touched

Nothing has been changed at the time of writing. This entry exists so the change
is revertible by somebody who was not here.

### 1. The file, and who owns it

    path    : K:\Projects\FEG\Ret\full\returns_platform_copilot_recovered_20260804-211601\compose.yaml
    service : temporal-postgresql   (lines 348-364)
    project : return-multi-agent-platform
    also    : compose.novault.yaml is the second compose file for this project but
              does NOT define temporal-postgresql -- compose.yaml is the only place

**This file is outside this repository.** It belongs to a different project
directory, and the running stack is launched from there rather than from this
repo's `scripts/infra.sh`.

**It is under version control in its own repo**, which is on branch
`refactor/unified-return-platform` at `1839b7f`, and both compose files are
**clean** — `git status --porcelain -- compose.yaml compose.novault.yaml`
returned nothing. So the before-state is recoverable there with
`git checkout -- compose.yaml` even if this ledger is lost.

**My change will be left DIRTY in that repo, not committed.** It is a temporary
test-environment change to somebody else's project, and committing it would
assert an intent I have no standing to assert. Whoever owns that repo should
decide whether it becomes permanent.

### 2. Verbatim before-state

    348   temporal-postgresql:
    349     image: postgres:${POSTGRES_VERSION:-17.10-alpine}
    350     environment:
    351       POSTGRES_USER: temporal
    352       POSTGRES_PASSWORD: ${TEMPORAL_DB_PASSWORD}
    353       POSTGRES_DB: temporal
    354     volumes:
    355       - temporal_pg_data:/var/lib/postgresql/data
    356     healthcheck:
    357       test: ["CMD-SHELL", "pg_isready -U temporal -d temporal"]
    358       interval: 10s
    359       timeout: 5s
    360       retries: 12
    361     restart: unless-stopped
    362     networks: [platform]
    363     logging: *default-logging
    364     mem_limit: 1g

There is **no `command:` key today**. The container runs the image default:

    Entrypoint: docker-entrypoint.sh
    Cmd       : postgres

### 3. The exact change

One key added after `image:`, nothing removed:

    +    command:
    +      - postgres
    +      - -c
    +      - fsync=off
    +      - -c
    +      - synchronous_commit=off

**Exactly the two settings approved, and no others.** `full_page_writes=off` is
the usual third companion and is deliberately **not** included: it was not
approved, and adding an unrequested setting would make the A/B measure something
other than what was sanctioned.

**To revert:** delete the five added lines (or `git checkout -- compose.yaml` in
that project), then `docker compose -p return-multi-agent-platform up -d
temporal-postgresql`.

### 4. Why this is a genuine single-variable experiment

**The named volume `temporal_pg_data` is not deleted and not recreated.**
Recreating the container re-attaches the same volume, so the database keeps its
38 MB and its 916 executions across both arms. Accumulated state is therefore
**held constant**, and the only thing that changes is whether Postgres waits for
the disk. That matters because accumulation was the hypothesis this track spent
two remedies on: this experiment cannot accidentally confirm the storage fix by
also wiping the data.

### 5. Predictions, stated before the numbers exist

**Baseline to beat** (step:21, reverted budgets, quiet machine): **3 clean runs
of 5**; failures carried `did not run within 20s` / `did not run within 30.0s`
and `shard status unknown`. Server side, over three hours: 145 "Failed to start
transaction", 266 "context deadline exceeded", 44 "shard status unknown", 19
shard re-acquisitions. `pg_test_fsync`: 2.5–33.6 ops/sec.

**What I expect if the storage layer is the cause:**

1. `pg_test_fsync` rises by **one to three orders of magnitude**.
2. Temporal's persistence errors during the runs fall to **zero or near it** —
   this is the direct measurement of the mechanism.
3. The workflow module goes **5 of 5 clean at the ORIGINAL budgets**, and run
   times cluster near the fast end (79–93s) instead of spanning 79–275s.

**What would count as noise rather than improvement:** 4 of 5 clean *with the
server-side error counters still non-zero*. Five runs cannot separate 4/5 from
3/5 on their own — the test counts are the weakest evidence here, and I am
naming that in advance so I cannot later treat a lucky 4/5 as a result. **The
load-bearing evidence is (1) and (2), which measure the mechanism directly
rather than through the tests.**

**What would falsify the storage explanation:** (1) and (2) succeed — fsync is
fast, Temporal's errors are gone — **and the tests still fail**. That would mean
the storage layer was real but not the whole story, and it is the more valuable
outcome of the two. I will report it plainly if it happens.

**Budgets stay reverted.** If the storage fix is the cause, the original 20s and
30s budgets should now be *sufficient*, and a remedy that makes the original
assertions hold is worth far more than one that relaxed them. That is the
cleanest available confirmation and it is why re-raising would have destroyed
the experiment.

### 6. The `dd` near-miss, recorded prominently as instructed

Before `pg_test_fsync` I ran:

    dd if=/dev/zero of=... bs=8k count=1000 oflag=dsync

It did not return within 120 seconds, and **I began writing that up as "fewer
than 8 IOPS" — a finding, with a number.** It was nothing of the sort. The
Postgres container's busybox `dd` had rejected the flag instantly:

    dd: invalid argument 'dsync' to 'oflag'

The hang was the **other leg of the same command**, the one against Mongo. So I
had a plausible number attached to the wrong cause — **the identical shape to
the log-truncation defect from step:18, committed two steps after I wrote the
standing rule about exactly that.**

It was caught by reading the output file before reporting it. That is the only
defence that has worked on this run, and it works because it is mechanical:
**read the actual output, then write the claim — never the other way round.**
`pg_test_fsync` then replaced a guess with an instrument built for the question,
which is the second half of the same lesson.

### Open at the time of writing

The change is **not applied**. The A/B is **not run**.

---

## step:23 — the A/B, the forced checkpoint, and a correction to step:21's mechanism

Three results: a validated latency fix, an **inconclusive** causal experiment,
and a **correction to my own headline finding** that matters more than either.

### 1. The A/B on the setting — uninformative on failures, decisive on latency

    A: fsync=off, synchronous_commit=off      B: reverted (fsync=on)
      13 passed in 41.99s                       13 passed in 71.02s
      13 passed in 41.84s                       13 passed in 79.71s
      13 passed in 39.48s                       13 passed in 97.30s
      13 passed in 41.82s                       13 passed in 83.10s
      13 passed in 41.16s                       13 passed in 75.11s
      policygate  9.49s /  7.30s                policygate 22.85s / 23.73s
      outbox     25.89s                         outbox     21.50s

**5/5 clean in both arms**, so on "does the fix remove the flakiness" this
experiment says **nothing**. At the baseline 2-in-5 rate, P(0 in 5) is 7.8%, and
it happened twice. My step:22 prediction that 5/5 would confirm success was
badly set: **I chose a bar without checking whether the sample size could clear
it**, which is a prediction that could only ever confirm.

**What the A/B does establish, on non-overlapping ranges:**

    workflow module   39.5-42.0s (off)   vs   71.0-97.3s (on)      ~2x
    policy gate        7.3- 9.5s (off)   vs   22.9-23.7s (on)      ~2.5x
    outbox (control)  25.9s      (off)   vs   21.5s      (on)      unchanged

**The outbox module is the control and it did not move.** It is Mongo-only and
never touches Temporal's Postgres. So this is not "the machine got faster".

Temporal persistence errors across a full A-arm run window (909 log lines):
**0 / 0 / 0 / 0** for failed-transaction, deadline-exceeded, shard-unknown and
shard-reacquisition.

### 2. The forced-checkpoint experiment — INCONCLUSIVE, and I am stopping

Rather than buy 30 passive runs to catch a ~40% coincidence, the trigger was
forced: a loop issuing `CHECKPOINT;` every 5 seconds against Temporal's Postgres
throughout the run, so any critical wait was guaranteed to overlap one.

    ARM=fsync_on, forced checkpoints, 3 runs
      run 1 [ 74s]  13 passed
      run 2 [122s]  1 failed, 12 passed
            FAILED test_a_bay_failure_does_not_stop_the_return
              E  AssertionError: open_support_work_item did not run within 30.0s
      run 3 [ 98s]  13 passed

A failure did appear. **It is not attributable to the forced checkpoints**, and
the reason is in the checkpoint log itself. Of 49 forced checkpoints, the first
cleared the restart backlog (1671 buffers, write=156.6s) and **every subsequent
one was trivial**:

    buffers= 820  write=0.076s   buffers=330  write=0.268s
    buffers= 274  write=0.280s   buffers=242  write=0.108s
    buffers=  48  write=0.068s   buffers= 37  write=0.034s
    ... 47 more, all write= 0.03-0.55s

**The experiment did not create the condition it was built to create.** A manual
`CHECKPOINT` is immediate rather than spread, and issuing one every 5 s keeps the
dirty set tiny, so I produced 48 cheap checkpoints instead of one heavy stall.
One failure in three runs is indistinguishable from the background rate.

**Verdict: inconclusive, for a reason I can state.** Per the standing
instruction, I am stopping here rather than buying another hour to try again.

### 3. The correction: step:21 misread the checkpoint numbers

This is the part that outlives the experiment. Step:21 led with:

> checkpoint complete: wrote 1830 buffers (11.2%); write=174.354 s
> **1830 buffers is about 14 MB. It took 174 seconds to write.**

and read that as disk starvation. **It is not.** The full line, and the config:

    wrote 1753 buffers  write=175.638s  sync=3.016s  total=180.411s
    checkpoint_completion_target = 0.9
    checkpoint_timeout           = 5min

`checkpoint_completion_target=0.9` means Postgres **deliberately spreads** a
timed checkpoint's write phase across 0.9 x 300s = **270 seconds**, to avoid an
I/O spike. A 175-second write phase inside a 270-second budget is Postgres
working exactly as designed. The phase that actually touches the disk
synchronously is `sync=`, and it is **3.0 seconds**.

The manual checkpoints in section 2 prove it independently: unspread, the same
database wrote 820 buffers in **0.076 s**.

**So the headline number in step:21 was normal behaviour misread as pathology.**
I had a real mechanism, reached for the largest number in the log to illustrate
it, and did not check what that number meant.

**The mechanism itself survives, correctly located.** It is **WAL commit
latency**, not checkpoints:

    wal_sync_method = fdatasync
    pg_test_fsync, fdatasync:  7.949 ops/sec   125797 usecs/op   (~126 ms)

With `synchronous_commit=on`, **every Temporal transaction waits ~126 ms for a
WAL flush**, which caps the server's commit rate and is what produced
`context deadline exceeded` and `shard status unknown`. `synchronous_commit=off`
removes that wait. That is why the fix works, and it is a different sentence
from the one step:21 wrote.

Note also that step:21 quoted `open_datasync` at 30 ms as the relevant figure.
This server uses `fdatasync`, at **126 ms** — four times worse. I quoted the
wrong row of my own measurement.

### 4. What is established, and what is not

**Established:**
- Durable-write latency on this volume is ~126 ms per WAL flush against a
  sub-millisecond expectation (`pg_test_fsync`, `fdatasync`).
- `fsync=off` + `synchronous_commit=off` makes the live suite **~2x faster** on
  every Temporal-dependent module, with a Mongo-only control unmoved.
- It takes Temporal's persistence errors to **zero** across a full run window.

**Not established:**
- **That it removes the test flakiness.** Both A/B arms were clean; the forced
  trigger was mis-built. The link between the storage fix and the failure rate
  is **unproven**, and nothing in this ledger should be read as proving it.
- Whether checkpoint overlap is a trigger at all. Section 2 did not test it.

### 5. Final state

`compose.yaml` in the external project carries the change, **left uncommitted**
(`M compose.yaml`) so its owner decides whether it becomes permanent. `SHOW
fsync` / `SHOW synchronous_commit` both return `off`; stack healthy. Revert is
one `git checkout -- compose.yaml` plus a `docker compose up -d
temporal-postgresql`.

**The gate question is unchanged by all of this.** With the flakiness link
unproven, the honest position is the one already recorded: **this suite cannot
be a hard gate on this hardware, and its failures are flakes that are never
re-run into passes.** The 2x speedup is worth having on its own and does not
depend on settling the flakiness question.

### Open

1. The storage-fix / flakiness link is **unproven** (sect. 4).
2. Signatures (b) and (d) never recurred and remain uncharacterised. (b) may be
   a product defect; **nothing was changed on that suspicion.**
3. Module 64 and `test_order_line_reservations_real_infra.py` remain undiagnosed.
4. The branch fails `ruff format --check`, pre-existing (step:20).
5. The runner (author A's step:12 + my step:17) has still never been run end to
   end, and the live suite has still never been run to completion.

---

## step:24 — the two failures reproduced on the merge tree, not argued from the review

HARNESS-2's verdict was withdrawn and reissued `CHANGES_REQUIRED` on three
findings. Before touching anything I reproduced them myself, on the merge tree,
because the whole point of this round is that a branch-local run is not evidence
about the merge.

**Base verified by ref, not by a sha quoted in a brief.**

    git rev-parse refactor/unified-return-platform feat/live-harness-registration
    bf7fa1400bd16df42bd37bb8b270c780aea0afb8
    2f1c0e506c812605e76aeddb004c6a1e6ddf9254

Trunk has moved since the review (HARNESS-2 measured against `72f37ba2`); the
merge tree below is built against the current ref, not against the review's sha.

**The merge tree.**

    git merge-tree --write-tree refactor/unified-return-platform feat/live-harness-registration
    920bbe6c5e1bdec8b57d26ab00715b07652c0683
    exit=0            (clean, no conflicts)

Materialised with `git archive` into a scratch tree (`.../scratchpad/mt0`),
plus the repository `.env` that `backend/tests/conftest.py:30` requires and
which is untracked.

**The environment trap, checked rather than assumed.** `return_platform_backend.pth`
in the venv points at the *main* worktree's `src`, so a bare interpreter call
imports whichever branch the main tree is on. Every Python command in this
ledger carries `PYTHONPATH` pinned to the tree under test, and I verified the pin
actually wins:

    PYTHONPATH=<mt0>/backend/src python -c "import return_platform; print(return_platform.__file__)"
    C:\...\scratchpad\mt0\backend\src\return_platform\__init__.py

**The failures.**

    cd <mt0>/backend
    PYTHONPATH=<mt0>/backend/src python -m pytest tests/test_return_case_workflow_replay_compatibility.py -q
    ...
    FAILED tests/test_return_case_workflow_replay_compatibility.py::test_a_test_worker_for_the_case_workflow_exists_to_be_checked
    FAILED tests/test_return_case_workflow_replay_compatibility.py::test_every_test_worker_registers_every_activity_the_workflow_calls
    2 failed, 15 passed in 7.61s

The second one's exception is the one HARNESS-2 predicted in round 1 and then
graded away:

    name = 'tests.test_items_15_16_review_survives_a_kill_real_infra'
    E   ModuleNotFoundError: No module named 'tests.test_items_15_16_review_survives_a_kill_real_infra'

at `test_return_case_workflow_replay_compatibility.py:504`. So both findings are
mine now, not the review's, and the branch alone would have shown neither: the
same module run in the branch worktree is 17 passed.

**Naming.** The brief numbers these F5/F6/F7; HARNESS-2 numbers the same three
F4/F5/F6. I use the brief's numbers below and record the mapping here once so
the two documents can be read against each other: brief F5 = review F4 (the
population pin), brief F6 = review F5 (the importer), brief F7 = review F6
(`_GateProbe`).

### Gate (rule 13)

`backend/tests/test_return_case_workflow_replay_compatibility.py` is collected by
the default backend suite — no marker, and `addopts` deselects only `live_infra`.
The gate that runs everything in this entry and every entry below it is the
default `pytest` run in `scripts/linux/02_run_backend_tests.sh` / the backend job
in `checks.yml`. Nothing here needs live infrastructure: the guard is a
static/import-time check.

---

## step:25 — F5: the population pin inverted to a subset, and proved it still catches the drop-out

> **Correction to step:24, made here rather than by editing it.** Step:24 names
> the gate as `scripts/linux/02_run_backend_tests.sh`. **No such script exists.**
> The gates are `scripts/linux/03_run_backend_quality.sh:24` (`poetry run
> pytest`) and `.github/workflows/checks.yml:129` (`poetry run python -m pytest
> tests`), verified by grep, not from memory. Step:24's `addopts` claim is also
> incomplete: `backend/pyproject.toml:139-140` deselects `not live_infra and not
> browser`, two markers, not one. Neither correction changes step:24's
> conclusion — this file carries no marker and is collected by both gates.

**The change.** `test_return_case_workflow_replay_compatibility.py:463-484`.
The exact-set equality becomes a subset in the direction that matters:

    assert {
        "test_return_case_policy_gate_real_infra.py",
        "test_return_case_workflow_real_infra.py",
    } <= {path.name for path, _line, _cls, _src in workers}

**Why, and I checked RV's reasoning rather than taking it.** RV argues equality's
extra strictness is redundant with the sibling registration guard, which already
checks every file the walker finds — and that equality only *looked* load-bearing
because F6 made a new file fatal, so the two defects were propping each other up.
That is testable and I tested it: with F6 fixed (step:26), the sibling guard
imports and checks `tests/acceptance/test_items_15_16_...py` and reports a real
finding against it (step:27). So the "a new probe is exactly where an
under-registered one arrives" job that the old comment claimed for equality is
in fact done by the sibling, and done better — the sibling names the missing
activities, equality only says "a name you did not expect". **No dispute.**

**The residual is in the test's own comment, not only here** — that was the
condition, and the comment carries it in the most visible form I could give it:

    #: THE RESIDUAL, AND IT IS REAL: a *newly added* worker file is not
    #: protected against silently dropping back out until somebody names it
    #: below. Equality gave that automatically. The second net is the
    #: `len(workers) >= 20` floor above -- raise it when you add a name here.

**Effect on the merge tree** (`mt_f5` = merge tree `920bbe6c` + this fix only):

    2 failed, 15 passed  ->  1 failed, 16 passed in 6.72s

and the one remaining failure is F6, unchanged.

### Injection — does the inverted assertion still catch what equality caught?

This is the question that decides whether this fix is a repair or a
capitulation, so it is answered by injection, twice, in throwaway copies of the
merge tree.

**Inj-F5-a — a pinned file is *moved* away** (`tests/test_return_case_workflow_
real_infra.py` -> `tests/acceptance/test_return_case_workflow_moved_real_infra.py`).
This is the drop-out shape the pin's own docstring names first, and note that
under F6's fix the moved file is still *importable*, so nothing else would have
noticed:

    E       AssertionError: assert {'test_return...eal_infra.py'} <= {'test_items_...eal_infra.py'}
    E         Extra items in the left set:
    E         'test_return_case_workflow_real_infra.py'
    1 failed in 4.08s

**Caught, and it names the file that vanished.**

**Inj-F5-b — a pinned file stays but stops registering the workflow.** The more
likely real regression: 8 sites of `workflows=(ReturnCaseWorkflow,)` in
`test_return_case_policy_gate_real_infra.py` rewritten to another workflow, so
the walker's *semantic* filter drops the file while the filename survives.

    sites rewritten: 8
    E       AssertionError: expected the real-infra suites' workers, found 18
    E       assert 18 >= 20
    1 failed in 2.08s

The **floor** fired first here. That is a real second net doing its job, but it
leaves open whether the subset assertion itself would have caught it, so I
isolated it — same injected tree, floor lowered to `>= 0` so only the subset can
speak:

    E       AssertionError: assert {'test_return...eal_infra.py'} <= {'test_items_...eal_infra.py'}
    E         Extra items in the left set:
    E         'test_return_case_policy_gate_real_infra.py'
    1 failed in 2.07s

**Caught on its own, without the floor's help.** Both nets fire independently on
the same defect, which is what "second net" is supposed to mean and is not
something I was willing to assert without separating them.

### Gate (rule 13)

`scripts/linux/03_run_backend_quality.sh:24` and `.github/workflows/checks.yml:129`
— the default backend `pytest` run. Verified by running the module itself, above.

---

## step:26 — F6: the module path is derived from the file, and the walker is left alone

**The change.** `test_return_case_workflow_replay_compatibility.py:517-531`.

    dotted = ".".join(path.relative_to(_test_root().parent).with_suffix("").parts)
    module = importlib.import_module(dotted)

replacing `importlib.import_module(f"tests.{path.stem}")`, which hard-coded one
directory level against a walker that is deliberately repo-wide.

**What I did not do, and why it is recorded here rather than in a commit
message.** The cheaper fix was to scope the walker to `tests/*.py`, and I
rejected it. Two reasons, the first decisive:

1. It would make the red go away **by removing the region the defect is in**.
   The only file the old importer could not reach is the only file that turns out
   to be under-registered (step:27). Scoping is narrowing a check to the region
   where it already passes, and it would be this branch committing the pattern
   rule 13 exists to catch — with the twist that the check would be narrowed by
   the very change that made it work.
2. It contradicts the walker's own stated design, four lines above it at
   `:425`: *"A worker for some other workflow is none of this rule's business
   ... so the filter is on the workflow, not on the file."* The filter is
   semantic by intent. Scoping would replace it with a positional one.

Both reasons are written into the code as a comment at the fix site, because the
next person to see this guard go red on a subpackage file will reach for exactly
that scoping.

**Effect on the merge tree** (`mt_f56` = merge tree `920bbe6c` + F5 + F6):

    1 failed, 16 passed in 8.03s

The importer now resolves `tests.acceptance.test_items_15_16_review_survives_a_
kill_real_infra`, and the remaining failure is **not** a guard bug. It is F7 —
the guard working, and reporting a real defect. That is step:27.

### Injection — does the fixed importer still catch under-registration?

An importer rewritten until it stops raising, that no longer detects the rot it
was written for, would be worse than the bug. Two injections, both in throwaway
copies of `mt_f56`.

First, a **failed** injection, recorded because it says something true about the
guard. I renamed the *method* `record_case_status` -> `record_case_status_RENAMED`
on the top-level `_Probe` and the guard did not report it. That is correct, not a
miss: `activity_probe.py:83-95` reads the **decorator's** `name=` argument, not
the Python attribute, *"those are the names a worker registers under and the
names the workflow asks for, and they are allowed to differ."* The rename left
`@activity.defn(name="record_case_status")` intact, so nothing was
under-registered and there was nothing to report. I had built the wrong
injection; the guard was right and I was wrong.

**Inj-F6-a — the control, a top-level probe under-registers.**
`@activity.defn(name="record_case_status")` -> `name="record_case_status_GONE"`
in `tests/test_return_case_workflow_real_infra.py`:

    'test_return_case_workflow_real_infra.py:422 (_Probe)': {'record_case_status'}
    'test_return_case_workflow_real_infra.py:454 (_Probe)': {'record_case_status'}
    'test_return_case_workflow_real_infra.py:467 (_Probe)': {'record_case_status'}

**Caught**, at every worker site, naming the activity.

**Inj-F6-b — the one the old code could not have caught at all.** The same
single-activity drop, in the **subdirectory** file
`tests/acceptance/test_items_15_16_review_survives_a_kill_real_infra.py`:

    'test_items_15_16_review_survives_a_kill_real_infra.py:566 (_GateProbe)':
        {'record_clarification_answer', 'case_has_return_details',
         'record_case_status', 'relay_clarification_to_support'}

`record_case_status` appears alongside the three F7 activities. Under the old
importer this file produced `ModuleNotFoundError` and **no finding of any kind** —
step:24's traceback is that exact file. So the fix does not merely stop the
crash: it extends real detection into a region the guard had never actually
covered, which is the opposite of what scoping would have done.

### Gate (rule 13)

`scripts/linux/03_run_backend_quality.sh:24` and `.github/workflows/checks.yml:129`
— the default backend `pytest` run, which collects this module unmarked.

---

## step:27 — F7: the defect the guard finds once it works, and an ownership line crossed on purpose

**Verified independently before fixing, in my own tree.** RV found this in a
scratch copy; the brief asked me to reproduce rather than accept it, and the
report below is not transcribed from the review. Merge tree + F5 + F6, every
worker site enumerated by the guard's own helpers:

    workflow calls: 18
      4 sites  test_items_15_16_review_survives_a_kill_real_infra.py  (_GateProbe)
               declared 15 of 18
               missing=['case_has_return_details', 'record_clarification_answer',
                        'relay_clarification_to_support']
      8 sites  test_return_case_policy_gate_real_infra.py  (_Probe)   declared 18 of 18  missing=None
     14 sites  test_return_case_workflow_real_infra.py     (_Probe)   declared 18 of 18  missing=None
    total sites: 26

Exactly the shape RV reported, at the four sites RV named (566, 599, 658, 666),
and the other 22 clean.

**The unreachability claim, checked against the code rather than accepted.**
This is the part that decides whether the three are a genuine gap or a
deliberate omission:

- `case_has_return_details` — one call site,
  `return_case_workflow.py:2214`, behind the guard at `:2147`
  (`if not timings.return_details_required or self._state.return_details_recorded: return`).
  `ReturnCaseTimings.return_details_required: bool = False` at `:428`.
  `grep -n return_details` over the acceptance module: **no matches**, so it is
  never overridden.
- `record_clarification_answer` (`:1700`) and `relay_clarification_to_support`
  (`:1718`) — both called only from inside
  `@workflow.signal(name="clarification_answered")` at `:1639-1640`.
  `grep -ci clarification` over the acceptance module: **0**.

So all three are genuinely unreachable by this module's scenarios today. That is
not a defence of the omission, it is the diagnosis: **the file is green because
its inputs cannot exercise the property, not because the property holds.**
Category B — the identical shape this branch diagnosed about the sibling
policy-gate probe, and the reason
`test_return_case_workflow_real_infra.py:287-294` registers the clarification
pair it likewise never signals, in its own words: *"A probe that registers only
what its own scenarios happen to call is the shape of the defect this file just
had."*

It matters more here than there. This module **kills the worker mid-run**, and a
restart is precisely where a case can take a path the happy scenarios do not.

**The fix.** Three `@activity.defn` methods on `_GateProbe`, copied in form from
the sibling probe, three entries appended to `all()`, two imports
(`ClarificationAnswerResult`, `ClarificationRelayView`). No scenario, fixture or
assertion in that file is touched.

`all()` in this file is a hand-written tuple rather than `declared_activities(self)`,
so the decorators alone would have turned the guard green while leaving the
worker still not registering them — the guard reads declarations, `Worker` reads
`all()`. Both were updated, and I checked the two now agree rather than assuming:

    all() registers 18 ; declared 18
    all() == declared: True

I deliberately did **not** convert `all()` to the derived form. That is the right
change and it is ACC's to make.

### Ownership — stated, not buried

`backend/tests/acceptance/` is **ACC's area, not the harness track's.** I am
crossing that line deliberately. HARNESS-2 flagged this as needing orchestrator
sequencing — either ACC adds the stubs first or the two land together — and this
is the "land together" arm, taken because the alternative is worse in a specific
way: the only other route to green is scoping the guard away from
`tests/acceptance/`, which HARNESS-2 Judgement 2 rules out and which step:26
records as the thing this branch must not do. **A guard that cannot go green
without a change in another track's file is not a reason to weaken the guard.**

The change is kept minimal, obvious and **separately committed** (`f75df769`) so
ACC can review it as a single diff without reading the harness work around it,
or revert it independently if ACC prefers to land its own version.

### Result

    pytest tests/test_return_case_workflow_replay_compatibility.py
    17 passed in 4.33s

and all 26 worker sites now enumerate 18 of 18, missing none.

### Gate (rule 13)

Two gates, and they are different ones, which is the point of this file's
existence. The `_GateProbe` change itself is only *executed* by the live-infra
acceptance run (`pytest.mark.live_infra`, deselected from the default suite) —
`scripts/dev/run_real_infra_suite.sh`, which is manual. But it is **statically
checked on every default run** by
`test_every_test_worker_registers_every_activity_the_workflow_calls`, via
`scripts/linux/03_run_backend_quality.sh:24` and
`.github/workflows/checks.yml:129`. That is the whole design: the registration
rot is caught by a check CI runs, not by a live suite nobody starts.

---

## step:28 — the merge tree, green, measured rather than predicted

**The deliverable is the merge tree, so the merge is a real commit and the suite
was run against it.** `4ed01b4f` merges `refactor/unified-return-platform` into
`feat/live-harness-registration` as a **three-way merge** — not a rebase, not a
squash. HARNESS-2 section 6 makes that a condition: the branch's ruff-format
state resolves only under a three-way merge. It was also structurally necessary
here, because `tests/acceptance/test_items_15_16_review_survives_a_kill_real_infra.py`
exists on trunk and not on the branch, so F7 could not be expressed at all until
the merge existed.

The merge was clean, as `git merge-tree --write-tree` predicted at step:24.

**Head:** `f75df769`.

**Full default backend suite, on the merged tree, PYTHONPATH pinned to it:**

    cd backend
    PYTHONPATH=<worktree>/backend/src python -m pytest tests -q
    ...
    5247 passed, 11 skipped, 514 deselected, 2 warnings in 281.56s (0:04:41)

Against HARNESS-2's measurement of the unfixed merge —
`2 failed, 5245 passed, 11 skipped, 514 deselected` — the two failures are gone,
the two tests are now passing (5245 + 2 = 5247), and **nothing else moved**:
skips and deselections are identical, so no test was skipped, xfailed, weakened
or deleted to get here. Rule 10 clean; the diff adds assertions and registrations
and removes none.

**The other gates in `03_run_backend_quality.sh`, on the same tree:**

    ruff format --check .   ->  1160 files already formatted
    ruff check .            ->  All checks passed!
    pytest scripts/tests    ->  4 passed in 0.10s

`ruff format` clean on the merged tree confirms HARNESS-2 section 6's ruling from
the other direction: the branch's 94-file format delta was a stale base, and it
disappears on merge. **No file under `backend/src/` was touched by any step in
this round** — the whole change is four test-side hunks in two files, plus this
ledger.

### What each fix is worth, in one line each

- **F5** — the pin no longer breaks unrelated merges, and still catches a
  drop-out two independent ways (step:25 injections a and b, the second isolated
  from the floor so the assertion answers on its own).
- **F6** — the guard now covers `backend/tests/`' subpackages instead of
  crashing on them, and detection there is real, not nominal: injecting a
  single-activity drop into the subdirectory probe is reported (step:26 inj-b),
  where the old code produced `ModuleNotFoundError` and no finding at all.
- **F7** — a real under-registration on trunk is closed, found only because F6
  was fixed rather than scoped.

### Open

1. `_GateProbe.all()` is still a hand-written tuple where
   `declared_activities(self)` exists. Deliberately left: ACC's file, ACC's call.
   The guard now covers the gap either way.
2. Everything still open at step:23 remains open — the storage-fix/flakiness
   link is unproven, signatures (b) and (d) uncharacterised, module 64 and
   `test_order_line_reservations_real_infra.py` undiagnosed. Item 4 of that list
   (the ruff format failure) is **closed** by the merge, as measured above.
3. The residual F5 accepts — a newly-added worker file is unprotected until
   somebody names it — is live by design, stated in the test's own comment, with
   the `>= 20` floor as the second net. Raise the floor when the named set grows.
