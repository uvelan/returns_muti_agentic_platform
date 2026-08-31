# ACC ledger

Append-only. One entry per step (contracts.md sect. 3).

**Phase 1 only** — brief items 1, 2 and 7 (harness scaffolding, business-calendar
fixture, fabrication-guard extension). Items 3–6 and 8–10 are acceptance
scenarios over code that does not exist until V3 merges and are deliberately not
attempted here.

Branch `feat/acc-harness`, cut from `e0a5f6c` — the trunk head of
`refactor/unified-return-platform`, `(T0) step:s1-merged pipeline bases
confirmed against approved head`. The brief names `feat/acc-acceptance` off the
post-V3 commit; this phase-1 branch is the orchestrator's direction and is
recorded here as the deviation it is.

Test harness: no venv in this worktree, so the main checkout's
`backend/.venv/Scripts/python.exe` runs with
`PYTHONPATH=<worktree>/backend/src`, from `<worktree>/backend`, with the
gitignored root `.env` copied in (`tests/conftest.py::pytest_configure` requires
it and it is untracked, so it stays untracked here).

---

## step:00 — anchor verification

**Anchors verified (all present at `e0a5f6c`, none adapted):**

| anchor | state |
| --- | --- |
| `backend/src/return_platform/operations/fact_names.py` | present; 2 constants, `SUPPORT_ARTIFACT_AMBIGUOUS` / `SUPPORT_ARTIFACT_UNMATCHED`, both imported by `operations/artifact_binding.py` |
| `backend/tests/test_frozen_modules_gain_no_new_callers.py` | present — backend source-guard shape |
| `frontend/src/domains/returns/ReturnCopilotFabrication.test.ts` | present — frontend source-scan idiom |
| `backend/src/return_platform/operations/business_calendar.py` | present; exports `BusinessCalendar`, `WorkingPeriod`, `advance_business_time`, `is_working_time`, `MAX_HORIZON_DAYS` |
| `configuration/return_configuration.py` `BusinessCalendarConfiguration` / `BusinessWorkingPeriodConfiguration` | present; `ReturnPlatformConfiguration.business_calendars` defaults `()` |
| `backend/tests/conftest.py` `_LIVE_INFRA_MODULE_SUFFIXES = ("_real_infra.py", "_docker.py")` | present (line 48); `_SUITE_MARKERS = ("live_infra", "browser", "integration", "unit")`; `pytest_itemcollected` marks every item |
| `backend/pyproject.toml` | markers `unit/integration/live_infra/browser`; `addopts` carries `-m "not live_infra and not browser"` |
| `scripts/dev/run_real_infra_suite.sh` | present |
| `backend/config/returns/production.yaml` `business_calendars:` | present; `default` is the 24/7 dev calendar (`America/New_York`, every day 0..1440) — cannot exercise business-time behaviour, which is item 2's premise |
| `backend/tests/platform/test_the_normal_suite_never_needs_live_infrastructure.py` | present — AST-scans **every** `.py` under `tests/` except `conftest.py`, so a helper module that constructs a driver must itself be live-classified. Shapes item 1's design. |

No mismatch, no halt.

**Next step:** step:01 — fact-name literal guard (brief item 7).

---

## step:01 — fact-name literal guard (brief item 7)

RV's standing grep (contracts.md sect. 3), made durable.

**Files:** `backend/tests/test_fact_name_literals_live_only_in_fact_names.py` (new).

**Shape.** The vocabulary is *discovered* by importing
`return_platform.operations.fact_names` and reading its public upper-case string
constants — never a copy of the list, which would be a second home for the very
strings the rule keeps in one home, and which would leave a later slice's
constant silently unguarded. Appending a constant there extends this guard in
the same commit.

Scanning is AST rather than text: contracts.md sect. 4 bans the *string
literal*, and prose is not a literal, so docstrings are exempted by node
identity while every other `str` constant is examined — which also catches
shapes a text grep reads past (a name inside `Literal[...]`, a dict key).
`fact_names.py` itself is exempt, resolved from `module.__file__` so moving the
module moves the exemption.

Three tests: the vocabulary is non-empty (a guard over nothing passes forever);
the scanner is proved against a source that *does* carry a literal, and against
the two forms that must stay legal (docstring prose, importing the constant);
and the rule itself over `backend/src/**/*.py`.

**Command:** `python -m pytest tests/test_fact_name_literals_live_only_in_fact_names.py -q`
**Result:** 3 passed.

**What it currently catches: nothing.** The tree is clean — the only occurrences
of either fact name in `backend/src` are lines 19 and 24 of `fact_names.py`, and
`operations/artifact_binding.py` imports both constants. Correct: this guard is
a ratchet against the next slice, not a finding against this one.

**Anchors verified:** `fact_names.py` constants read at runtime (2 discovered);
`test_frozen_modules_gain_no_new_callers.py` source-guard shape followed
(module-level constants, `rglob` over `BACKEND_SRC`, `__pycache__` skipped,
failure message names the sanctioned replacement).

**Next step:** step:02 — Mon–Fri business-calendar fixture (brief item 2).

---

## step:02 — Mon–Fri 09:00–17:00 business-calendar fixture (brief item 2)

**Files (all new):** `backend/tests/harness/__init__.py`,
`backend/tests/harness/business_calendars.py`,
`backend/tests/harness/conftest.py`,
`backend/tests/harness/test_business_hours_calendar_fixture.py`.

**Premise, verified.** `production.yaml`'s `business_calendars.default` declares
every day `0..1440` — `BusinessCalendar.is_continuous` is true, and
`advance_business_time` short-circuits a continuous calendar to
`start + timedelta(seconds=…)`. So against the shipped configuration every
overnight and weekend gap is zero seconds wide and items 13/19 would be
asserting plain addition. Confirmed by reading the file, not assumed.

**Shape.** `nine_to_five_configuration()` returns a
`BusinessCalendarConfiguration` — the configuration model, not the arithmetic's
model, because the real path is workflow → activity →
`ReturnPlatformConfiguration.business_calendars`, and a fixture handing out a
`BusinessCalendar` directly would skip the half of the seam most likely to
break. `as_business_calendar()` converts; `with_business_calendar()` installs a
calendar into a configuration *and* points `return_case.business_calendar_id` at
it (either half alone is a silent no-op — an unnamed calendar is never
consulted, and an id naming nothing falls back to wall clock and logs
`business_calendar_not_configured`, which is legitimate production behaviour and
therefore an invisible way to test nothing). Same-id entries are replaced, not
appended, because `_business_calendar` returns the first match.

Calendar id is `acceptance-business-hours`, deliberately not `default`: a
scenario that meant to install it and did not would otherwise silently get the
24/7 one and pass. Zone is `America/New_York` — the desk's real zone, so the
`fold` / local-wall-clock-day construction stays exercised.

No Mon–Fri constant is imported from anywhere; the pattern is built here,
because `business_calendar.py` deliberately has none.

**Recorded duplication.** `as_business_calendar` re-derives the config→domain
mapping that lives, private, in `ReturnCaseActivities._business_calendar`. Held
to account by `test_the_fixture_maps_to_the_calendar_production_would_build`,
which runs the real activity over a configuration carrying this calendar and
asserts the same instant — so a change to the production mapping fails here
rather than leaving acceptance asserting against a desk the platform does not
have. No production edit made.

**Coverage** (over the pure `operations/business_calendar.py`): overnight gap
(Mon 16:00 + 2h → Tue 10:00); weekend gap (Fri 16:00 + 2h → Mon 10:00);
deadline landing after a weekend with its remainder intact (Fri 16:30 + 8h →
Mon 16:30, the audit scenario); weekend start waits for the opening rather than
bursting (item 19); `is_working_time` at both edges of 09:00/17:00 plus
overnight and weekend; declared holiday; the weekday identity of the dates
themselves, since every expectation is "…because that day is a Saturday".

**Commands:**
- `python -m pytest tests/harness -q` → **10 passed**
- `python -m pytest tests/harness tests/test_fact_name_literals_… -m unit --collect-only` → **13 collected**, i.e. every new test lands in the `unit` suite (none live, none browser)
- `ruff check` / `ruff format` → clean

**Anchors verified:** `BusinessCalendarConfiguration`,
`BusinessWorkingPeriodConfiguration` (weekday 0–6, `end_minute` ≤ 1440),
`ReturnPlatformConfiguration.business_calendars`, `ReturnCaseTimingConfiguration
.business_calendar_id`, `advance_business_time` / `is_working_time` /
`BusinessCalendar.is_continuous`, `ReturnCaseActivities(repository, support_service,
configuration)` + `ResolveBusinessDeadlineInput(from_iso, working_seconds,
business_calendar_id, timezone)` — all as declared, none adapted.

**Next step:** step:03 — kill/restart harness scaffolding (brief item 1).

---

## step:03 — kill/restart harness scaffolding (brief item 1)

**Files (all new):** `backend/tests/harness/chaos_restart.py`,
`backend/tests/harness/test_chaos_restart.py`,
`backend/tests/harness/test_chaos_restart_smoke_real_infra.py`.

**Primitives only.** No scenario tests: items 14–18, 20 and 23 are assertions
about code that does not exist until V3 merges, and a scenario written ahead of
its subject is a scenario written against a guess.

- `WorkerSpec` / `WorkerProcess` — start, `kill`, `stop`, `restart`, `pid`,
  `is_running`, context manager. `RETURN_WORKFLOW_WORKER` and
  `ORDER_DISCOVERY_WORKER` point at `backend/scripts/run_*_worker.py`, the same
  entry points `compose.yaml` (`command: ["python", "/app/scripts/…"]`) and
  `scripts/run_worker_host.sh` launch. A real subprocess, never an in-process
  `Worker(...)`: an in-process kill leaves the interpreter, its `finally`
  blocks and its buffers intact, which is not the failure being tested.
- `kill()` is abrupt and takes the tree — `SIGKILL` to the process group on
  POSIX (`start_new_session=True` at launch), `taskkill /F /T` on Windows
  (`CREATE_NEW_PROCESS_GROUP`). `stop()` is the graceful one and is named apart
  so a scenario cannot reach for it by accident and silently test the drain path
  instead of crash recovery. Both idempotent, so teardown runs unconditionally
  after a failed scenario without burying the real error.
- `wait_until` / `wait_for_workflow` — polling waiters that treat **a raising
  probe as "not yet"**. This is the crux: a Temporal query against a workflow
  whose only worker has just been killed raises rather than returning pending,
  so a waiter that let the first exception through would fail inside the very
  window every restart scenario opens on purpose. `wait_for_workflow` is built
  on `describe()` (answered by the service from history) rather than a query
  (answered by a worker), for the same reason. Timeouts raise `ChaosTimeout(
  AssertionError)` naming `what` and carrying either the last observation or the
  last error.
- `assert_once` / `assert_remains_once` — the effectively-once observable from
  contracts.md §7. Both failure directions are reported distinctly (none = the
  send was lost; several = receiver dedupe did not hold) with the records
  printed. `assert_remains_once` holds the assertion open across a window
  because delivery is at-least-once and the retry lands *after* the restart — a
  single snapshot taken as the worker comes back tests the timing, not the
  guarantee.

**Suite classification.** `chaos_restart.py` constructs no driver: clients and
handles arrive as arguments and `DescribableWorkflow` is a `Protocol`. That is
deliberate — `test_the_normal_suite_never_needs_live_infrastructure` AST-scans
every `.py` under `tests/` except `conftest.py`, so a helper that built a client
would force *every scenario importing it* into the live suite. Pinned by
`test_the_harness_opens_no_connection_of_its_own`.

- `test_chaos_restart.py` → **`unit`**. Proves the primitives against a trivial
  subprocess and fake probes: start/kill idempotence, restart yields a new pid,
  **the kill takes orphan children with it** (measured by a child heartbeat
  going stale — an orphaned worker still polling a task queue would poison every
  later scenario), env overlay rather than replacement, waiter tolerance of
  raising probes, timeout messages, and the once/remains-once assertions.
- `test_chaos_restart_smoke_real_infra.py` → **`live_infra`** by the
  `_real_infra.py` suffix *and* an explicit `pytestmark`, deselected from the
  default run by `addopts`. Starts the real `run_return_workflow_worker.py`,
  lets it settle 20s (a worker that dies during startup reports `is_running` a
  millisecond after `start()`), kills it, starts it again, asserts a new pid.
  Three assertions and no fourth — it smoke-tests the harness and says nothing
  about return behaviour.

**Commands:**
- `python -m pytest tests/harness -q` → **31 passed, 1 deselected**
- `python -m pytest tests/harness -m live_infra --collect-only` → **1 collected,
  31 deselected** — the deselected one is the smoke test, confirming the
  boundary in both directions
- `ruff check` / `ruff format` → clean (`wait_until`/`assert_once`/
  `assert_remains_once` converted to PEP 695 type parameters; the async waiters'
  `timeout` renamed `timeout_seconds` for ASYNC109)

The live smoke test has **not** been executed — the five datastores are not
running in this worktree and `run_real_infra_suite.sh` preflights them by
design. Recorded as unverified-against-live; it is deselected from every run
this phase makes, and item 9 executes the live suite after V3.

**Anchors verified:** `backend/scripts/run_return_workflow_worker.py` and
`run_order_discovery_worker.py` exist (asserted in-test, not assumed);
`compose.yaml` service commands; `_LIVE_INFRA_MODULE_SUFFIXES`; `_SUITE_MARKERS`
precedence (explicit marker beats suffix beats fixtures); `addopts -m "not
live_infra and not browser"`; `run_real_infra_suite.sh` port preflight + `-m
live_infra` selection.

**Next step:** step:03a — close the orphan-child gap the harness's own tests found.

---

## step:03a — the graceful teardown was leaving orphans

**Files:** `backend/tests/harness/chaos_restart.py`,
`backend/tests/harness/test_chaos_restart.py`.

A test written for `stop()` (`test_stop_reaps_the_children_too`) failed on
first run, and the failure was real: `taskkill /T` walks the tree **from the
parent**, so reaping after `terminate()` had already killed the parent
enumerated nothing. Teardown was handing the next scenario a live child still
polling the task queue — the exact state the whole harness exists to prevent,
arriving through the polite path rather than the violent one.

**A job object was tried and rejected on evidence.** `CreateJobObjectW` +
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` + `TerminateJobObject` would remove the
"parent must be alive" constraint entirely, since job membership is inherited
and outlives the parent. It does not work here: `AssignProcessToJobObject`
lands *after* `CreateProcess` returns, and the worker spawns its own children
inside that window. Measured, not assumed — `IsProcessInJob` on the grandchild
returned "in no job at all", and the child survived `TerminateJobObject`. The
~60 lines of `ctypes` were removed rather than kept as decoration: a mechanism
that silently reaps less than the one it replaces is worse than the one it
replaces.

**Resolution.** One `_signal_tree(pid, force=…)` used by both paths, always
called while the parent is still alive. `kill()` forces immediately;
`stop()` asks politely, waits a short grace, then forces. The constraint is
documented at the method rather than left to be rediscovered.

**Second finding, from the clock.** The polite step made the harness suite
3s → 50s. On Windows `taskkill` without `/F` posts `WM_CLOSE`, which a console
process has no message loop to receive, and `Popen.terminate()` is
`TerminateProcess` — already un-catchable. So the polite step terminated
nothing and cost a full grace period per teardown. It is now skipped on Windows
with the reason stated: there is no graceful stop to attempt, and pretending
otherwise bought only latency. `GRACE_SECONDS = 3.0` on POSIX, where `SIGTERM`
to the process group means something.

**Commands:**
- `python -m pytest tests/harness -q` → **32 passed, 1 deselected in 5.31s**
  (was 2 failed / 30 passed before the fix, then 32 passed in 50.61s before the
  latency fix)
- `ruff check` / `ruff format` → clean

**Next step:** step:04 — full-suite verification and delta report.

---

## step:04 — delta report (ACC phase 1 complete)

Branch `feat/acc-harness`, cut from `e0a5f6c`. Four commits, nothing uncommitted.

### Suite

| run | result |
| --- | --- |
| base (`e0a5f6c`, before any ACC work) | 4 373 passed, **2 failed**, 9 skipped, 511 deselected |
| final (`feat/acc-harness` head) | 4 408 passed, **2 failed**, 9 skipped, 512 deselected, 3:20 |

**+35 passing tests, +1 deselected, zero new failures.** The two failures are the
known pre-existing pair, unchanged and untouched:
`tests/platform/test_main_is_composition_only.py::test_router_mounting_is_the_bulk_of_create_app_and_that_is_allowed`
and
`tests/test_cumulative_support_outcomes.py::test_a_rejected_return_still_opens_no_work_item`.

The +1 deselected is `test_chaos_restart_smoke_real_infra.py`, which is the
point: it is mandatory and it runs in the suite that has what it needs.

### Delivered (brief items 1, 2, 7)

| item | files | tests |
| --- | --- | --- |
| 7 — fabrication guard, backend half | `tests/test_fact_name_literals_live_only_in_fact_names.py` | 3 (unit) |
| 2 — business-calendar fixture | `tests/harness/{__init__,business_calendars,conftest}.py`, `tests/harness/test_business_hours_calendar_fixture.py` | 10 (unit) |
| 1 — kill/restart harness | `tests/harness/chaos_restart.py`, `tests/harness/test_chaos_restart.py`, `tests/harness/test_chaos_restart_smoke_real_infra.py` | 22 unit + 1 live_infra |

**What the fact-name guard catches today: nothing, and it must.** The tree is
clean — `support_artifact_ambiguous` and `support_artifact_unmatched` appear in
`backend/src` only at lines 19 and 24 of `fact_names.py`, and
`operations/artifact_binding.py` imports both constants rather than spelling
them. The guard is a ratchet against the next slice, not a finding against S1.
It reads the vocabulary from `fact_names.py` at runtime, so every constant a
later slice appends is guarded by the same commit that adds it.

### Not attempted, by instruction

Brief items 3–6 and 8–10 are acceptance scenarios and sweeps over code that does
not exist until V3 merges (review aggregate, panel endpoint, resolver, template
config, the V1–V3 UI). Nothing here asserts anything about them. Item 7's
frontend half — extending `ReturnCopilotFabrication.test.ts`-style guards — has
no phase-1 subject either: that guard is complete for the returns Copilot as it
stands, and the panel and template-config surfaces it would be extended to are
V1–V3 work.

### Deviations

1. **Branch name and base.** The brief says `feat/acc-acceptance` off the
   post-V3 RV-approved commit. This is `feat/acc-harness` off the current trunk
   head `e0a5f6c`, per the orchestrator's phase-1 direction. Recorded at the
   head of this ledger.
2. **Worktree started on the wrong commit.** It was on `0448d32` (`feat: refine
   conversational returns experience`) with no `.plan/` at all. Checked out
   `e0a5f6c` explicitly before reading anything. No adaptation — the contract
   and brief were read from the correct tree.
3. **`as_business_calendar` duplicates a private production mapping**
   (`ReturnCaseActivities._business_calendar`). Deliberate: no production edits
   are permitted to ACC, and the duplication is held to account by an
   equivalence test that runs the real activity. Recorded in step:02.
4. **The Windows job object was tried and removed** (step:03a) after measuring
   that it reaps less than `taskkill /T` does. The residual constraint — the
   tree must be signalled while the parent is alive — is documented at
   `_signal_tree`.

### Halts

None. Every anchor in the brief and in contracts.md §§3–4 verified as declared.

### Not verified

`test_chaos_restart_smoke_real_infra.py` has **not been executed against live
infrastructure** — the five datastores are not running in this worktree and
`run_real_infra_suite.sh` preflights them by design. It is deselected from every
run this phase makes. Brief item 9 executes the live suite after V3.

### Production-code issues spotted, not fixed

**None.** No production defect was found in the surfaces this phase read
(`operations/fact_names.py`, `operations/artifact_binding.py`,
`operations/business_calendar.py`, `configuration/return_configuration.py`
calendar models, `workflows/return_case_activities.py::resolve_business_deadline`,
`tests/conftest.py`, `backend/pyproject.toml`,
`scripts/dev/run_real_infra_suite.sh`).

Two observations for the orchestrator, neither a defect:

- `backend/config/returns/production.yaml` carries an explicit **BEFORE LIVE**
  obligation on `business_calendars.default` (24/7 dev calendar) and
  `return_case.support_response_wait_seconds: 1800`. Already tracked in the file
  and coupled by an existing test
  (`test_the_shipped_calendar_and_the_support_sla_agree_about_which_clock_they_use`).
  ACC's fixture does not touch it and does not depend on it.
- The config→domain calendar mapping is private to `ReturnCaseActivities`.
  Promoting it to a shared function on `operations/business_calendar.py` would
  let ACC drop its duplicate. **Not requested and not done** — it is a
  production edit, and it belongs to whichever slice owns that module.

---

## RV round 1 — `CHANGES_REQUIRED` (`.plan/reviews/ACC1-1.md`, `ba19fd8`)

Two findings, both rule 10 (test integrity), both the same species: a guard
whose prose claims a protection its assertions do not provide. Both accepted
without dispute — RV proved each by fault injection, and each reproduced here
before being fixed. Items 1, 2 and 7 were otherwise verified sound; the
fact-name guard was independently re-proved end-to-end and judged to exceed the
brief.

## step:05 — F1: the calendar pin now declares a holiday

**File:** `backend/tests/harness/test_business_hours_calendar_fixture.py`
(one line: `nine_to_five_configuration()` →
`nine_to_five_configuration(holidays=(MONDAY,))`, plus the docstring paragraph
that explains why).

**The finding, reproduced before fixing.** The pin is the whole justification
for `as_business_calendar` duplicating the private
`ReturnCaseActivities._business_calendar`, and its docstring claimed to catch
"a dropped holiday set". It did not. It ran against a calendar whose `holidays`
default is `()`, and an empty set maps to an empty set whether the mapping
copies it or drops it on the floor.

**Fault-injection evidence** (production edited in the working tree, run,
reverted with `git checkout` — never committed; `git status` confirmed clean
after each):

| injected drift in `return_case_activities.py::_business_calendar` | before fix | after fix |
| --- | --- | --- |
| `holidays=frozenset(declared.holidays)` → `frozenset()` | **10 passed** (miss) | **1 failed, 9 passed** |
| `timezone=declared.timezone or fallback_timezone` → `fallback_timezone or declared.timezone` | 1 failed (already caught) | **1 failed, 9 passed** |
| none (clean tree) | 10 passed | **10 passed** |

The failure now reads as a whole day of disagreement — production
`2026-08-17T20:30Z`, fixture `2026-08-18T20:30Z` — because the Friday-16:30
probe crosses `MONDAY`, which is the declared holiday. Chosen deliberately over
a subtler value: a pin whose failure is one day wide cannot be misread as a
rounding artefact.

**The general lesson, written into the docstring:** every field the mapping
carries has to be non-default in this pin, or it is not being compared at all.

**Commands:** `python -m pytest tests/harness/test_business_hours_calendar_fixture.py -q`
→ **10 passed** on the clean tree.

**Next step:** step:06 — F2.

---

## step:06 — F2: the stop/kill distinction is now observed, not named

**File:** `backend/tests/harness/test_chaos_restart.py`.

**The finding, reproduced before fixing.** RV deleted the graceful block from
`WorkerProcess.stop()` outright — making it identical to `kill()` on every
platform — and the file stayed green at **22 passed**. Reproduced here exactly:
the old `test_stop_is_the_graceful_one_and_kill_is_not` asserted only
`not worker.is_running`, which an alias satisfies just as well.

**Three tests where there was one.**

1. `test_stop_leaves_nothing_running` — the old body, renamed to what it
   actually checks, with the false claim removed from its docstring and the
   history of that claim left in it.
2. `test_stop_lets_the_worker_handle_its_signal_and_kill_does_not` — the
   behavioural pin RV suggested. A worker installs a `SIGTERM` handler that
   writes a file; `stop()` must produce that evidence and `kill()` must not.
   **Skipped on Windows**, with the reason spelled out in the `skipif`: there is
   no polite step there to observe, by design.
3. `test_stop_and_kill_do_not_collapse_into_one_path` — a source-level pin, in
   the same spirit as `test_the_harness_opens_no_connection_of_its_own`, which
   RV recorded as the right construction for a consequence that lands out of
   sight. `stop()`'s source must contain `force=False` and `force=True`;
   `kill()`'s must contain only `force=True`.

The third one exists because the second is skipped here. RV's own words name the
risk — collapsing the distinction is "an obvious tidy-up, since the Windows
branch already skips the polite step" — so on this dev platform the behavioural
test could never object. A pin that only runs on a platform CI may not be on is
not a pin.

**Fault-injection evidence** (`chaos_restart.py` edited in the working tree,
run, reverted with `git checkout`; `git status` confirmed clean after):

| state | before fix | after fix |
| --- | --- | --- |
| `stop()`'s graceful block deleted (`stop()` == `kill()`) | **22 passed** (miss) | **1 failed, 22 passed, 1 skipped** — `test_stop_and_kill_do_not_collapse_into_one_path`, message: "it is now kill() under another name, and every POSIX teardown just became a SIGKILL" |
| clean tree | 22 passed | **23 passed, 1 skipped** |

**Honest limitation, stated rather than papered over.** The behavioural test has
**never executed** — Windows is the dev platform and it is skipped there. Its
script was verified to compile and to install `signal.SIGTERM` in a live
subprocess without error, which is the most this platform can say. Its first
real run is on POSIX CI or during the acceptance run. The structural pin is what
actually guards the invariant here, and it is the one that caught RV's
injection.

**Commands:** `python -m pytest tests/harness -q` → **33 passed, 1 skipped,
1 deselected**. `ruff check` / `ruff format` clean.

---

## Advisory recorded for phase 2 (RV round 1, not a finding)

**A forgotten calendar fixture is still silent.** Choosing the id
`acceptance-business-hours` over `default` prevents a scenario from *shadowing*
or *being shadowed by* the shipped entry — but it does nothing about a scenario
that simply never calls `with_business_calendar`. That scenario inherits
`business_calendars.default`, which is the 24/7 dev calendar, and runs on wall
clock silently: every gap zero seconds wide, every weekend assertion reduced to
addition, green throughout.

What makes it loud is an assertion, not an id. **Every phase-2 scenario that
depends on business time must assert one of:**

- `resolved.calendar_applied is True` (as
  `test_the_fixture_maps_to_the_calendar_production_would_build` does), or
- `not calendar.is_continuous` (as
  `test_the_fixture_declares_a_desk_that_closes` does).

RV suggests a harness-level assertion so it cannot be forgotten. Deferred to
phase 2 deliberately: the right shape of that guard depends on how the scenarios
obtain their configuration, which is V1–V3 surface that does not exist yet.
Building it now would be guessing at an interface. **Carry this entry into the
phase-2 brief.**

---

## step:07 — RV round 1 resubmission

Branch `feat/acc-harness`, seven commits, nothing uncommitted. Both findings
fixed, each reproduced by fault injection before the fix and re-injected after.

### Suite

| run | result |
| --- | --- |
| base (`e0a5f6c`) | 4 373 passed, **2 failed**, 9 skipped, 511 deselected |
| RV round 1 (`f433237`) | 4 408 passed, **2 failed**, 9 skipped, 512 deselected |
| resubmission | 4 409 passed, **2 failed**, 10 skipped, 512 deselected, 3:15 |

**+1 passed, +1 skipped** against the reviewed commit — F2 turned one test into
three, of which the behavioural one is skipped on Windows. Failures unchanged:
the same known pre-existing pair, still untouched. **Zero new failures.**

### Fault-injection evidence, both findings

| finding | injected drift | before fix | after fix |
| --- | --- | --- | --- |
| F1 | production `_business_calendar` drops the holiday set | 10 passed (**miss**) | 1 failed, 9 passed |
| F1 | production inverts timezone precedence | 1 failed | 1 failed, 9 passed |
| F2 | `stop()`'s graceful block deleted → alias of `kill()` | 22 passed (**miss**) | 1 failed, 22 passed, 1 skipped |

Every injection was made in the working tree, run, and reverted with
`git checkout`; `git status` was confirmed clean after each. No production file
is modified by this branch — `git diff --name-only e0a5f6c..HEAD -- backend/src
frontend/src backend/config scripts` returns nothing.

### Carried forward, unchanged from the round-1 report

- `test_chaos_restart_smoke_real_infra.py` still unexecuted (no live stack).
  RV's note stands: it should be the first thing run when the stack comes up,
  ahead of any scenario built on it.
- The POSIX graceful teardown path is still unexercised on this platform. Now
  *asserted* structurally on every platform, and behaviourally on POSIX when
  something runs it — but the behavioural test itself has never run.
- No production-code defect found. The two advisory observations
  (`production.yaml`'s BEFORE LIVE obligation; the private config→domain mapping
  that ACC duplicates) are unchanged and still not acted on, both being
  production edits owned elsewhere.
- Advisory above — the forgotten-fixture gap — must reach the phase-2 brief.

---

# phase 2 — acceptance scenarios and the 26-item gate

Branch `feat/acc-scenarios`, based on trunk `39fd7c08`.

## step:01 — the two never-executed safety nets, executed and re-verified

**Files:** `backend/tests/harness/posix_signal_proof.py` (new),
`.plan/acceptance/safety-nets.md` (new).

Dispatch condition 2: run the never-executed safety nets **first**, before
anything is built on them. Both of the harness's guarantees were arguments
rather than observations. Both are now observations.

This step was written across a session break — an API session limit killed the
run after the work existed in the working tree but before its commit landed. On
resume the two files were read in full and **both nets were re-run from cold and
re-injected with faults different from the recorded ones**, because a claim
re-proved by repeating its own evidence is a claim compared with itself.

**(a) `test_chaos_restart_smoke_real_infra.py` — first execution ever, then a
second.** The real `run_return_workflow_worker.py` starts from the harness,
survives the 20s settle window, is killed, and comes back with a different pid:
**1 passed in 40.66s** against the live stack. Independent injection: an
unreachable `PLATFORM_TEMPORAL_TARGET` overlaid on `RETURN_WORKFLOW_WORKER.env`
— **proved to be a real fault first** by running the worker script directly with
that env (`RuntimeError: Failed client connect … ConnectionRefused`) — takes the
test to **1 failed in 7.93s**. The test is sighted.

**(b) The SIGTERM link.** RV narrowed the single unproven link to whether
`os.killpg(os.getpgid(pid), SIGTERM)` reaches the child through the session
`start()` establishes. `posix_signal_proof.py` executes exactly that under
`python:3.13-slim` in Docker (a script, not a `test_`, so the dev platform
cannot silently skip it): **all four links proved**, exit 0. Independent
injection: `_signal_tree`'s `SIGKILL if force else SIGTERM` reduced to `SIGKILL`
— one line, confirmed the only occurrence by `git diff -U0` — takes check 3 to
**FAIL** while checks 1, 2 and 4 stay **PASS**. That asymmetry is itself the
verification that the injection did what it claims rather than breaking the file.

Both injections reverted with `git checkout`; `git status` clean after each.
Full evidence tables in `.plan/acceptance/safety-nets.md`.

**Production defect found — reported, not fixed.**
`scripts/dev/run_real_infra_suite.sh:56` preflights `"SQL Server:14330"` while
`compose.yaml:192` publishes `${PLATFORM_SQLSERVER_PORT:-11433}` and `.env:94`
sets `11433`. The only sanctioned entry point for the live-infra suite therefore
**refuses to run against a stack that is fully up**, with the one message
guaranteed not to lead anyone to the port number. One line, in `scripts/`, which
is outside ACC's `backend/tests/`-only scope. Every live-infra run recorded here
was invoked with `pytest -m live_infra` directly, ports verified by hand.

**Next step:** step:02 — merge trunk (`bc434f72` resolving dispatcher, `c6c15256`
CI workflow + known-failure allowlist) into the branch, then AMENDMENT-8's
unreachability assertion.

---

## step:02 — trunk merged; acceptance item 10's deferral made checkable

**Merge:** `refactor/unified-return-platform` (`c5419fcf`) merged into
`feat/acc-scenarios`, clean, 7 files. Brings V3's resolving dispatcher wired
into the outbox worker (`bc434f72`) and the new CI workflow (`c6c15256`,
`.github/workflows/checks.yml` + `scripts/ci/assert_known_failures.py` +
`known_test_failures.json`). Noted and obeyed: CI now runs the full suites and
fails on **any** failure not in the allowlist, and also on a named failure that
starts passing — so every test this branch adds must be green.

**Files:** `backend/tests/acceptance/__init__.py`,
`backend/tests/acceptance/test_item_10_the_tool_rung_is_unreachable.py`,
`.plan/acceptance/item-10-deferral.md` (all new).

AMENDMENT-8 defers item 10 and rules the deferral must be checkable. The tool
rung is **not exercised**; its absence is asserted across **three places that
must agree** — the released `production.yaml` read from disk, the compiled
graph's node set, and the **target map** of every conditional branch — plus a
fourth test stating the agreement of six reads as one identity, because each
place can be green while another disagrees.

**The target map is the read nothing else in the suite performs.** LangGraph
raises for a map naming an absent node and for a router returning a name absent
from the map; neither fires for the thing item 10 turns on, which is a branch
that *can route to* a rung. The node set says what exists; the map says what is
reachable.

V3's `test_support_resolver_composition.py` already covers places 1 and 2, so
this file does not re-derive them: it builds through **the same production
factory** (importing that module's `_built` rather than copying its doubles) and
adds place 3 and the agreement.

**Injections — three, mirror-imaged, each read back before running:**

| # | fault | result |
| --- | --- | --- |
| INJ-10a | tool ports wired in `build_support_resolution_ladder` | 3 failed, 1 passed — config test correctly stays green |
| INJ-10b | one valid `tool_bindings` entry released in `production.yaml` | 2 failed, 2 passed — topology tests correctly stay green |
| INJ-10c | `tool_bindings` key deleted from the document | 1 failed, 3 passed — the presence assertion fires |

The complementary asymmetry between 10a and 10b is the verification that each
injection landed where it claims: a generic breakage would have taken all four
tests down together.

**An invalid injection was caught and discarded rather than recorded.**
INJ-10b's first form used `input_schema_ref: shipment_status.v1`, which is not
in this build's schema allowlist — the release failed Pydantic validation and
all four tests **errored** instead of failing. No assertion ran, so the red said
nothing about the test. Re-injected with a real allowlist entry
(`graph.shipment_status.v1`) before any evidence was written down. INJ-10c also
improved the agreement test: it indexed the document directly, so a deleted key
raised a bare `KeyError` instead of the assertion's own message.

**Commands:** `python -m pytest tests/acceptance -q` → **4 passed**.
`tests/platform/test_the_normal_suite_never_needs_live_infrastructure.py` plus
both resolver suites → **51 passed** (the new module is normal-suite classified
and opens nothing). `ruff check` / `ruff format` clean.

**Next step:** step:03 — the business-time scenarios (items 13, 19), each
asserting `calendar_applied is True` / `not …is_continuous` per dispatch
condition 1.

---

## step:03 — items 13 and 19: the cadence, on a calendar that actually shuts

**Files:** `backend/tests/acceptance/conftest.py`,
`backend/tests/acceptance/test_items_13_19_reminder_cadence_in_business_time.py`,
`.plan/acceptance/items-13-19-business-time.md` (all new).

**The gap, stated exactly.** `tests/test_support_template_review_gate.py`
already drives this loop, but its `resolve_business_deadline` is a double that
does `start + working_seconds` and returns `calendar_applied=True` **as a
literal**. A grep for `with_business_calendar` outside `tests/harness/` returned
nothing, so before this step **no test in the repository had run the reminder
cadence on a calendar that closes**. Dispatch condition 1's failure mode was not
hypothetical here; it was the state of the suite.

This module keeps that harness — same runtime substitution, same real
`SupportTemplateGateService` over the real `ReviewAggregateStore` — and replaces
one thing: the **real** `resolve_business_deadline` activity over a release
carrying the Mon–Fri 09:00–17:00 desk from ACC phase 1's fixture.

**Assert, not assume.** `_assert_business_time_was_actually_used` runs in every
business-time scenario: `not desk.is_continuous`, the run points at *this*
calendar, and `calendar_applied is True` on **every** resolution rather than the
first — a fallback taken on one leg would leave the early ones true.

**Measured.** From Friday 16:30 local, the legs land Monday 10:30/12:30/14:30/16:30
and three reminders fire. Nothing on Saturday, nothing bunched. A **control**
scenario runs the identical wait on the shipped 24/7 calendar and asserts its
first reminder lands the same Friday evening — without it, the weekend
assertions could be passing on the calendar rather than because of it.

**Injections — four, each read back before running:**

| # | fault | result |
| --- | --- | --- |
| INJ-19a | wall-clock reminder tick in `_await_template_reviews` | 1 failed — "a reminder was scheduled for **Friday 18:30** — outside desk hours"; the 24/7 control stays green, correctly |
| INJ-13a | cap multiplied by the open-review count (a per-review cadence) | 2 failed — both two-review scenarios; every one-review scenario green |
| INJ-13b | a reminder charged on the satisfied-predicate path | 1 failed — "6 reminders with wakes against 3 without" |
| INJ-13c | the calendar fixture forgotten (`desk_configuration` returns the release unchanged) | 4 failed — every business-time scenario, loudly |

**INJ-13b caught a scenario of mine that could not fail, and it was rewritten.**
Its first form asserted a *ceiling* (`sent <= max_reminders`) and its "wake" left
the gate's predicate false — so the harness raised `TimeoutError` exactly as if
nothing had arrived, and the injection left **all nine tests green**. Two defects
at once: the wake path was never entered, and a ceiling is unfalsifiable when the
cap clamps the count whatever charges it. Rewritten as a **comparison** — the
same case run twice, identical but for the wakes, counts must match — with a wake
that puts a notice for a review the case does not hold into
`pending_template_notices`, which genuinely satisfies the predicate, is drained,
is ignored, and leaves the review open. The re-run then failed with its own
message. Both `merge.md` shapes ("green because the inputs can't exercise the
property"; "proves the right thing is asked for, not that the wrong thing is
refused"), found in my own instrument by my own injection.

Also changed for a reason worth keeping: the reminder instant is read from the
**runtime clock at the moment the reminder is logged**, not from the recorded
`resolve_business_deadline` answers. The latter does not survive INJ-19a — a
wall-clock implementation stops asking for legs, so the list empties and the red
reads "nothing was resolved" instead of "a reminder fired outside desk hours".
Not the instrument flattening into the answer: the test never chooses how far the
clock moves; the runtime advances by the timeout the gate passes, computed from
the deadline the gate resolved.

**AMENDMENT-5 rule 2 is re-asserted on this path** — a weekend-spanning wait that
closes leaves every review in `HELD_FOR_OPERATIONS`, and the *absence* of a
stranded state is asserted against the set of states that have a legal exit
(`OPEN` and `APPROVING` deliberately excluded — that is the trap the amendment
closed).

**Commands:** `python -m pytest tests/acceptance -q` → **9 passed**.
`python -m pytest tests -q` → **5195 passed, 1 failed, 10 skipped, 512
deselected** (4:20) — the failure is `test_a_rejected_return_still_opens_no_work_item`,
the known pre-existing one named in `scripts/ci/known_test_failures.json`. **Zero
new failures**, which the new CI gate requires. `ruff check` / `ruff format` clean.

**Next step:** step:04 — the review-gate group (items 3–6) including
AMENDMENT-5's two, verifying in-slice coverage rather than duplicating it.

---

## step:04 — AMENDMENT-3: the three surfaces coexist *in the published document*

**Files:** `backend/tests/acceptance/test_amendment_3_three_support_surfaces_coexist.py`,
`.plan/acceptance/amendment-3-coexistence.md` (both new).

**In-slice coverage read first, and not duplicated.**
`tests/api/test_api_route_paths_are_unique.py` covers the declaration side
thoroughly — exact path, no `(method, path)` declared twice across every router
with parameters normalised, and the associate path claimed **by name**.
`tests/test_openapi_contract_drift.py` pins the four committed documents to the
code. **Neither asserts the document carries all three operations**, and the
failure AMENDMENT-3 actually produced was a *document* describing neither
surface. Code declares them + document matches code is a transitive argument
across two suites — `merge.md`'s "nobody stands at the seam". The integration
agent checked it by hand; this makes it permanent.

Asserts the **handler** behind each operation, not just path presence: a
document answering POST on the associate path with the ingress handler is the
amendment's exact state, and presence cannot distinguish them. A fourth test
pins the snapshot list against `test_openapi_contract_drift.JSON_SNAPSHOTS` so
the chain has no free end.

**Injections:**

| # | fault | result |
| --- | --- | --- |
| INJ-A3a | the amendment's failure reproduced in all four documents | 9 failed, 5 passed |
| INJ-A3b | one document only, one `operationId` swapped | 1 failed, 13 passed — the injected document named in the failure id |

**INJ-A3b found a defect in my own instrument.** The document fixture was
parametrised by `path.name`, and **three of the four snapshots share the
basename `return-platform.openapi.json`** — so the ids collapsed and every
lookup resolved to the first file. The test reported four documents and read
two. The tell was that a fault written into one file failed *three* parameter
sets, which is impossible if they are distinct documents. Re-parametrised on the
repository-relative path; INJ-A3b then failed exactly one, and INJ-A3a was
re-run so the numbers above are the corrected instrument's. Same shape as
step:03's INJ-13b, in a different costume: green because the inputs could not
exercise the property.

**Commands:** `python -m pytest tests/acceptance -q` → **23 passed**.
`ruff check` / `ruff format` clean. No production file modified.

**Next step:** step:05 — AMENDMENT-4's "never atomically" half, then the
remaining groups. See the halt/scope note at the end of this ledger.
