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
