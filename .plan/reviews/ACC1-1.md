# RV review — ACC phase 1, round 1

- **Branch:** `feat/acc-harness` (final commit `f433237`)
- **Base:** `e0a5f6c` (trunk head incl. merged S1)
- **Diff reviewed:** `git diff e0a5f6c..f433237` — 9 files, +1945/-0, entirely test-side
- **Dispatched scope:** ACC brief items **1, 2, 7 only**. Items 3–6 and 8–10 were out of scope by orchestrator instruction; the absence of acceptance scenarios is therefore not a finding and is not treated as one below.
- **Reviewer:** RV — Date: 2026-08-30

## Verdict: CHANGES_REQUIRED

Two findings. Both are the same species — a guard whose own prose claims a protection the assertions do not provide — and both were confirmed by fault injection, not by reading. Both fixes are one line. Nothing else in the diff is contested; the rest of the branch is strong work and is recorded as verified below.

---

## Findings

### F1 (BLOCKING — rule 10, test integrity) — the calendar pin does not catch the drift it names

- **File:** `backend/tests/harness/test_business_hours_calendar_fixture.py`
- **Lines:** 176 (`declared = nine_to_five_configuration()`), against the docstring claim at 168–174; the duplication it licenses is `backend/tests/harness/business_calendars.py:110-131` (`as_business_calendar`).
- **Rule violated:** Rule 10 (test integrity) — a test written so that it passes rather than catches; the assertion is narrower than the property it is the sole guarantor of.
- **What the code claims:** `test_the_fixture_maps_to_the_calendar_production_would_build` is the entire justification for `as_business_calendar` duplicating the private production mapping `ReturnCaseActivities._business_calendar` (`return_case_activities.py:853-883`). Its docstring: "A change to the production mapping — a different timezone precedence, **a dropped holiday set** — fails here as a disagreement about one instant."
- **What is actually true (verified by RV, fault injection in a throwaway worktree):**
  - Timezone precedence drift → **caught.** Inverting `timezone=declared.timezone or fallback_timezone` to `fallback_timezone or declared.timezone` in production fails this exact test (1 failed, 9 passed).
  - Holiday drift → **not caught.** Replacing `holidays=frozenset(declared.holidays)` with `holidays=frozenset()` in the production mapping leaves the entire file green (**10 passed**).
- **Why:** the pin runs against `nine_to_five_configuration()`, whose `holidays` default is `()`. An empty set maps to an empty set under either the correct implementation or a dropped one, so the comparison of instants cannot distinguish them.
- **Why it matters:** this pin is the only thing standing between a knowingly-duplicated production mapping and silent divergence, and it is the reason the duplication was accepted rather than routed back to the owning slice. The uncovered field is not hypothetical: `nine_to_five_configuration(holidays=...)` is already exercised by the sibling test `test_a_declared_holiday_is_skipped_like_a_weekend`, whose own docstring names item 13's cadence scenarios as "the natural place for a holiday to appear". If production's holiday handling drifts, every phase-2 cadence scenario declaring a holiday would assert against a desk the platform does not have — and would pass while doing it.
- **Fix:** pin against a configuration that declares a holiday, e.g. `declared = nine_to_five_configuration(holidays=(MONDAY,))` (the module already defines `MONDAY`, and the Friday-16:30 + 8h probe already crosses it — the sibling test shows the instant moves to Tuesday). Either that, or narrow the docstring to claim only what it checks; the first is preferred, since the claim is the right one.

### F2 (rule 10, test integrity) — `stop()`'s graceful path is asserted by name only

- **File:** `backend/tests/harness/test_chaos_restart.py`
- **Lines:** 152–164 (`test_stop_is_the_graceful_one_and_kill_is_not`)
- **Rule violated:** Rule 10 — the test's name and docstring assert a property its body does not check.
- **What the code claims:** "…this pins that they are implemented differently rather than aliased."
- **What the body does:** `start()`, `stop()`, `assert not worker.is_running`. That is satisfied identically by `kill()`, by an alias, and by any implementation that stops the process at all. Nothing distinguishes the two paths.
- **Verified by RV:** deleting the graceful block from `WorkerProcess.stop()` outright (`chaos_restart.py:236-241`), making `stop()` behaviourally identical to `kill()` on every platform, leaves `tests/harness/test_chaos_restart.py` fully green (**22 passed**).
- **Why it matters:** the harness's stated design rests on the two being different — the module docstring and both method docstrings argue at length that a scenario reaching for `stop()` instead of `kill()` "would be exercising the drain path while claiming to test crash recovery, and would pass". A future edit that collapses that distinction (an obvious tidy-up, since the Windows branch already skips the polite step) would silently turn every POSIX teardown into a SIGKILL, and no test would object. Note also that the polite path exists only on POSIX, so it is currently unexercised on this dev platform in any form.
- **Fix:** assert the distinction on the POSIX path — e.g. a spec whose child installs a `SIGTERM` handler that writes a file, then assert `stop()` produces the handler's evidence and `kill()` does not (skipped on `os.name == "nt"`, where the docstring correctly says there is no polite step). Or, if that is judged not worth the harness complexity, rename the test and reduce the docstring to the claim it does support ("stop leaves nothing running"); the aliasing claim must not stand unbacked either way.

---

## Verified and not contested

**Item 7 — fact-name guard (`tests/test_fact_name_literals_live_only_in_fact_names.py`). Sound; exceeds the brief.**

- Vocabulary is **read from `fact_names.py` at runtime** (`_declared_fact_names` over `vars(fact_names)`), not copied — so a constant added by a later slice is guarded by the same commit that adds it. The exemption path is resolved from `fact_names.__file__` rather than written as a string, so moving the module moves the exemption.
- **AST-based**, not textual. Docstrings are exempted **by node identity** (`_docstring_nodes`), which is the correct construction: a module whose docstring and code both carry the same string still has only the docstring forgiven.
- **The scanner is proved against a violation** — the failure mode named in the dispatch. `test_the_scanner_finds_a_literal_where_one_exists` checks all three directions (literal caught, prose forgiven, sanctioned import forgiven), and `test_the_vocabulary_is_discovered_rather_than_copied` guards against the empty-vocabulary tautology.
- **RV verified this end-to-end, independently of the author's fixture:** planting `_rv_probe.py` under `backend/src/return_platform/operations/` containing both a bare literal and one inside a `Literal[...]` annotation turned the guard red, naming the file and both names. Removing it returned it to green. The `Literal[...]`/dict-key claim holds.
- Residual (advisory, not a finding): the guard scans `backend/src` only, whereas contracts.md §4 bans the literals "anywhere else". Scoping to `src` is correct here — the guard's own fixture must write one, in `tests/` — but it means a test-side literal is still RV's manual grep. Noted for future rounds.

**Item 2 — business-calendar fixture (`tests/harness/business_calendars.py`).** Mon–Fri 09:00–17:00 in `America/New_York` (the desk's real zone, DST included), built rather than imported — correct, since `business_calendar.py` deliberately declares no Mon–Fri anywhere. `with_business_calendar` does both halves (declare + repoint `return_case.business_calendar_id`), and **replaces** rather than appends on id collision — correct, since `_business_calendar` returns the first match and an appended duplicate would be a calendar that never wins. That replacement behaviour is itself tested. The seven behavioural tests (overnight gap, weekend gap, remainder-survives-the-weekend, weekend-start-waits-for-opening, `is_working_time` boundaries in both directions, holiday-as-shut-day) are real assertions about the pure arithmetic, and the load-bearing dates are themselves asserted to be the weekdays the tests assume.

**Item 2 — the "not `default`" id choice: reasoning confirmed against `production.yaml`, with one qualification.** `backend/config/returns/production.yaml:1356-1363` ships `business_calendars.default` as a 24/7 dev calendar and `return_case.business_calendar_id: default` (line 1346), so `advance_business_time`'s continuous short-circuit makes every business-time gap zero seconds wide. Using a distinct id (`acceptance-business-hours`) is right and prevents the specific silent failure of shadowing or being shadowed by the shipped entry. **Qualification for phase 2:** the id choice alone does not make a *forgotten* fixture loud — a scenario that never calls `with_business_calendar` still inherits `default` and silently runs on wall clock. What actually makes it loud is asserting `calendar_applied is True` (as the pin test does) or asserting `not …is_continuous`. Phase-2 scenarios must do one of those; consider a harness-level assertion so it cannot be forgotten. Advisory, not a finding on this diff.

**Item 1 — `chaos_restart.py` primitives: sound enough for scenarios to rest on.**

- The orphaned-children fix is correct and the reasoning is right: `taskkill /T` and `os.killpg` both enumerate *from* the live parent, so reaping must happen before the parent is released — which is what both `kill()` and `stop()` now do. The `/F` flag is inserted correctly (`taskkill /F /T /PID`). The process group / new session is established at launch, which is the only time it can be. The claim is tested behaviourally via a heartbeating grandchild going stale, rather than by a pid liveness check — the right call, given pid reuse.
- Skipping the polite step on Windows is honest and correctly reasoned (`taskkill` without `/F` posts `WM_CLOSE`; a console process has no message loop; `Popen.terminate()` is already `TerminateProcess`). The cost saved is real. Recorded as a deliberate platform asymmetry, documented at the site where it is felt. See F2 for the coverage consequence.
- `kill()` uses no `terminate()` first — correct for the question these scenarios ask. `start()`/`kill()` are idempotent in both directions, so teardown runs unconditionally without burying the assertion that explains a failure. `restart()` is asserted to produce a different pid.
- The waiters treat a raising probe as "not yet" — correct and necessary: across a kill window a Temporal query has no poller to answer it, and a first-exception-fatal waiter would fail in exactly the interval the scenario opens on purpose. `wait_for_workflow` correctly uses `describe()` (answered by the service from history) rather than a query (answered by a worker), which is the only thing that can observe a workflow while its worker is gone. Timeout messages carry both the last observation and the last error, and `ChaosTimeout` subclasses `AssertionError` so a durability failure reads as a failure rather than as retriable infrastructure noise.
- `assert_once` distinguishes the two failure directions (none = send lost; several = receiver dedupe did not hold), matching contracts.md §7's effectively-once wording and its "exactly one message on B" observable. `assert_remains_once` correctly holds the assertion open across the window in which an at-least-once retry actually lands — the difference between testing the guarantee and testing the timing — and is proved against a fetch that plants the duplicate after the first look.
- The suite-boundary discipline is right and is itself pinned: nothing in the harness constructs a client, everything takes handles as arguments, and `test_the_harness_opens_no_connection_of_its_own` greps the module for driver constructions so the consequence cannot land two files away unnoticed.

**Standing greps (this round):** fact-name literals outside `operations/fact_names.py` — none in `src` (now machine-enforced; independently re-verified). New imports of frozen modules (`operations/associate_flow`, `agents/order_discovery`, `api/associate_returns`, `api/return_agents`) — none. Template/section/intent/tool literals in code — none; this branch adds no production code at all. No production file is touched by the diff, so the ACC ownership constraint holds.

**Other charter rules:** no production code, so rules 1–9 and 11–12 have no surface here beyond the above. No credentials appear in the diff; `WorkerSpec.env` overlays `os.environ` rather than replacing it (tested), and no secret is written into a spec, a log line, or an assertion message.

---

## Test verification (run by RV, not taken from the ledger)

- New tests in isolation at `f433237`, run from `backend/`: **35 passed, 1 deselected** — the deselected one being the live smoke test, which confirms both the count the orchestrator reported and the deselection below.
- Full default-deselection suite at `f433237`, run from `backend/`: **4408 passed, 2 failed, 9 skipped, 512 deselected**. The 2 failures are exactly the known pre-existing pair (`test_main_is_composition_only.py::test_router_mounting_is_the_bulk_of_create_app_and_that_is_allowed`, `test_cumulative_support_outcomes.py::test_a_rejected_return_still_opens_no_work_item`), which RV confirmed failing on the untouched `a50c550` during the S1 round. Deselection count rises 511 → 512, matching the one added live-infra module exactly. Reported base `e0a5f6c` = 4373 passed/2 failed; +35 with zero new failures is confirmed.
- Note for successors: several tests in this repo, including the new calendar pin, resolve `config/returns/production.yaml` relative to the working directory and therefore require pytest to be run from `backend/`. This matches the established repo convention and is not a finding.

## Residual risk — recorded, not a finding

- **`test_chaos_restart_smoke_real_infra.py` has not been executed.** The datastores are not up, so the one assertion that the *deployment's* worker actually launches from these specs — script paths, working directory, inherited environment — is unproven. Classification is correct and was verified: the `_real_infra.py` suffix puts it in `live_infra` via `tests/conftest.py:48,118`, `pytestmark = pytest.mark.live_infra` states the same fact explicitly, and it is deselected from the default run (the +1 in the deselected count above). Non-execution is not treated as a finding per the dispatch. **It is a live risk for the acceptance run:** the first phase-2 durability scenario to run against real infra will discover any spec error as a worker that exits during startup, and `_running_after_settling`'s 20-second settle plus its message is what will make that readable. This smoke test should be the first thing run when the stack comes up, ahead of any scenario written on top of it.
- **The POSIX graceful teardown path is unexercised on the current dev platform** (Windows skips it by design), and per F2 nothing asserts it anywhere. Compounding, not separate.
