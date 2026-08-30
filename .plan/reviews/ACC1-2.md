# RV review — ACC phase 1, round 2

- **Branch:** `feat/acc-harness`, head `bdf6a8cc9637028c0df13638ecab9966e065b3ec`
- **Base:** `e0a5f6c`. **Diff reviewed:** `git diff e0a5f6c..bdf6a8c` — complete updated diff, 9 files, +2219/-0, entirely test-side.
- **Previous round:** `f433237`, reviewed at `.plan/reviews/ACC1-1.md` (commit `ba19fd8`) — CHANGES_REQUIRED, two findings.
- **Dispatched scope:** ACC brief items **1, 2, 7 only**. Items 3–6 and 8–10 are out of scope by orchestrator instruction; their absence is not assessed.
- **Reviewer:** RV (second instance) — Date: 2026-08-30

## Verdict: PASS

Both round-1 findings are **withdrawn**, each verified by re-injecting the fault myself
rather than by reading the slice's evidence table. No new finding is sustained. Three
advisories and a carry-forward condition are recorded below; none is a finding and none
gates merge.

---

## Round-1 findings — disposition

### F1 (rule 10, calendar pin) — **WITHDRAWN**

- **Fix:** `backend/tests/harness/test_business_hours_calendar_fixture.py:186` —
  `declared = nine_to_five_configuration(holidays=(MONDAY,))`.

I re-injected the drift in a throwaway worktree at `bdf6a8c`, editing production
`return_case_activities.py::_business_calendar` and running the file from `backend/`:

| Injected into production `_business_calendar` | Round 1 (`f433237`) | Round 2 (`bdf6a8c`) |
| --- | --- | --- |
| `holidays=frozenset(declared.holidays)` → `frozenset()` | 10 passed (**miss**) | **1 failed, 9 passed** |
| `declared.timezone or fallback_timezone` → `fallback_timezone or declared.timezone` | 1 failed, 9 passed | **1 failed, 9 passed** |
| `end_minute=period.end_minute` → `+ 60` *(my own third probe)* | not tested | **1 failed, 9 passed** |

The holiday failure is exactly the instants claimed: production `2026-08-17T20:30+00:00`
against the fixture's `2026-08-18T20:30+00:00` — a whole day, which is the size of error
this pin exists to make unmissable. I added the third probe because the amended docstring
now claims *"every field the mapping carries has to be non-default here or it is not being
compared at all"*, and a claim of completeness should be tested rather than trusted: the
working-period arm holds too. `calendar_id` is covered by the `calendar_applied is True`
assertion, and the comparison is bidirectional — a drift in the *fixture's*
`as_business_calendar` fails the same assertion from the other side.

The docstring now states the previous defect in the open rather than quietly correcting
it, which is the right disposition for a guard whose credibility is the whole reason the
duplication of a private production mapping was accepted.

### F2 (rule 10, `stop()` vs `kill()`) — **WITHDRAWN**

- **Fix:** one test became three at `backend/tests/harness/test_chaos_restart.py:189`
  (`test_stop_leaves_nothing_running`, renamed and stripped of the claim it did not
  support), `:216` (behavioural SIGTERM pin, POSIX-only), `:262`
  (`test_stop_and_kill_do_not_collapse_into_one_path`, source-level, every platform).

I re-injected round 1's exact fault — deleting the graceful block from
`WorkerProcess.stop()` (`chaos_restart.py:273-277`), making `stop()` behaviourally
identical to `kill()` on every platform:

| | Round 1 (`f433237`) | Round 2 (`bdf6a8c`) |
| --- | --- | --- |
| `stop()`'s graceful block deleted | 22 passed (**miss**) | **1 failed, 22 passed, 1 skipped** |

The failure is `test_stop_and_kill_do_not_collapse_into_one_path`, and it fails **on this
platform**, which is the point — the behavioural pin beside it is skipped here. The hole
named in round 1 ("the obvious tidy-up passes, silently turning every POSIX teardown into
a SIGKILL") is closed on the platform the suite actually runs on. Finding withdrawn.

**On whether the source-level pin is a legitimate instrument or a brittle text assertion —
it is legitimate, with a boundary I measured.** It is the same species as the branch's
pre-existing `test_the_harness_opens_no_connection_of_its_own`, which I also probed this
round by appending an `AsyncMongoClient(...)` construction to `chaos_restart.py`: it went
red immediately, so that pin is not vacuous either and the precedent is real, not
rhetorical. The reasoning the slice gives — "a pin that only runs on a platform CI may not
be on is not a pin" — is correct, and a design invariant whose behavioural evidence exists
only on a platform this suite may not run on is exactly the case where reading the source
back is the honest remaining instrument. Its assertions are also well-chosen: it pins
`force=False` **and** `force=True` present in `stop()` (so a "fix" that drops the forceful
follow-up, leaving a worker that ignores SIGTERM to poison the next scenario, also fails)
and `force=False` **absent** from `kill()` (so the collapse is caught in both directions).
Failure messages name the operational consequence, not the token.

Its boundary, measured rather than asserted — see advisory A1.

---

## Judgements you asked for

**Do the three tests together close the hole on this platform? Yes, for deletion and
aliasing — the failure modes round 1 named.** `stop()` collapsing into `kill()` by
deletion, by aliasing, or by losing either half of its two-step now fails on Windows
through the structural pin. The renamed `test_stop_leaves_nothing_running` keeps its real
assertion and has stopped claiming anything else, which was the narrower half of F2 and is
also resolved.

**On the deferred harness-level guard: deferring is acceptable, and I am converting it
into a carry-forward condition rather than a finding.** The risk is real — a phase-2
scenario that simply never calls `with_business_calendar` inherits the shipped
`return_case.business_calendar_id: default`, whose 24/7 calendar makes
`advance_business_time`'s continuous short-circuit collapse every business-time gap to
zero seconds, and it does so in complete silence. But the slice's reasoning is sound on
its own terms: the guard's right shape (a fixture that fails on entry, an autouse
assertion, a scenario base class) depends on a phase-2 surface that does not exist, and
building it now is guessing at an interface that would then have to be rebuilt or worked
around. Two usable instruments already exist and are proven on this branch —
`resolved.calendar_applied is True` (asserted by the pin test, with a failure message that
names both ways it can go wrong) and `not …is_continuous` (asserted by the same-id
replacement test). Nothing more needs to exist now.

**Carry-forward condition for the phase-2 dispatch (orchestrator action, not the
slice's):** every phase-2 scenario depending on business time must assert
`calendar_applied is True` or `not …is_continuous`, and the phase-2 review must check that
it did. The slice has recorded this as an advisory in its ledger; recording it here so it
is a condition on the next dispatch rather than a note that can evaporate between briefs.

---

## Independent verification (run by RV, not taken from the ledger)

- **Harness tests in isolation** at `bdf6a8c`, from `backend/`: **36 passed, 1 skipped, 1
  deselected**. Round 1 was 35 passed / 1 deselected. The delta is **+1 passed
  (`test_stop_and_kill_do_not_collapse_into_one_path`) and +1 skipped (the POSIX-only
  behavioural pin)** — exactly F2's one-test-into-three, with the third being the rename
  of an existing body rather than an addition.
- **Full default-deselection suite** at `bdf6a8c`, from `backend/`: **4409 passed, 2
  failed, 10 skipped, 512 deselected** (221s). This reproduces the reported numbers
  exactly, and the +1 passed / +1 skipped against round 1's 4408/2/9/512 is accounted for
  by the line above. Deselected holds at 512, so no test silently left the default run.
- **The two failures are the known pre-existing pair** —
  `tests/platform/test_main_is_composition_only.py::test_router_mounting_is_the_bulk_of_create_app_and_that_is_allowed`
  and `tests/test_cumulative_support_outcomes.py::test_a_rejected_return_still_opens_no_work_item`
  — the same pair round 1 confirmed failing on untouched `a50c550`. Unrelated to this
  branch.
- **Zero production files touched:** `git diff --name-only e0a5f6c..bdf6a8c` outside
  `backend/tests/` and `.plan/` returns nothing. The ACC ownership constraint holds for
  the whole branch, not just round 2. Every injection above was made in a disposable
  worktree and reverted; the branch tree is clean.
- **Item 7, the fact-name guard, re-verified independently of round 1 and of the author's
  fixture:** I planted `backend/src/return_platform/operations/_rv2_probe.py` carrying
  `support_artifact_ambiguous` as a bare assignment, `support_artifact_unmatched` inside a
  `Literal[...]` annotation, and both spellings inside the module docstring. The guard went
  red naming the file and **both** names, and forgave the docstring occurrences; removing
  the file returned it to green (3 passed). The AST construction and the docstring-by-node-
  identity exemption hold.
- **Standing greps (this round):** fact-name literals outside `operations/fact_names.py` —
  none in `src`, now machine-enforced and independently re-proved above. New imports of
  frozen modules (`operations/associate_flow`, `agents/order_discovery`,
  `api/associate_returns`, `api/return_agents`) — none in the diff. Template / section /
  intent / tool literals in code — none; the branch adds no production code at all.
- **Rule 10, weakening check across the round-2 delta:** no test deleted, no assertion
  loosened, no `xfail`. One `skipif` added (`test_chaos_restart.py:245`) — it gates a
  **newly added** test on a platform where the behaviour it observes does not exist, with
  the reason spelled out at the site, and the structural pin beside it exists specifically
  to cover that skip. That is the opposite of a skipped scenario standing in for coverage.
- **Rules 1–9, 11–12:** no production code on the branch, so no surface beyond the above.
  No credential appears in the diff; `WorkerSpec.env` overlays `os.environ` rather than
  replacing it, and no secret reaches a spec, a log line, or an assertion message.
- Round 1's verified-and-not-contested section — the chaos primitives' orphan reaping,
  the waiter semantics, `assert_once` / `assert_remains_once`, the calendar fixture's
  behavioural tests and the "not `default`" id choice — was re-read against the current
  diff. Nothing in the round-2 delta touches any of it, and I found no reason to reopen
  any part of it.

---

## Advisories — not findings

- **A1 — the structural pin covers deletion, not disablement.** Measured, not assumed: I
  inverted the platform guard at `chaos_restart.py:273` from `os.name != "nt"` to
  `os.name == "nt"`, which leaves the graceful block present but unreachable on POSIX —
  the same behavioural collapse, reached differently. The suite stayed green (23 passed, 1
  skipped), because `force=False` is still in the source. The behavioural pin would catch
  it on POSIX; on Windows nothing does. This is a strictly smaller and more contrived hole
  than the one round 1 found (which any tidy-up would have hit), and no text assertion can
  close it in general — asserting on control flow rather than tokens would mean parsing
  the method, which is a worse trade. Recorded so the pin's reach is on the record rather
  than assumed to be total; not a finding, and I am not asking for a change.

- **A2 — the behavioural SIGTERM pin has still never executed its own assertion, but its
  plumbing now has.** The slice states this plainly and claims only that the generated
  script compiles and installs `signal.SIGTERM` in a live subprocess. I verified that
  claim and pushed it two steps further:
  1. Running the test on this platform with the `skipif` removed, it reaches
     `assert handled.exists()` and fails there — not earlier. So `WorkerSpec` construction,
     script generation, `Popen` launch, `stop()` and `kill()` all execute correctly; the
     only thing that fails is the platform-specific signal delivery the `skipif` documents.
     A first execution on POSIX will not error for a construction reason.
  2. Executing the byte-identical generated body with `signal.raise_signal(SIGTERM)`
     appended: the handler is confirmed installed (`getsignal(SIGTERM) is _drain`), it
     writes `drained` to the evidence path, and `sys.exit(0)` returns 0.
  The single unproven link is now narrow and precisely nameable: whether
  `os.killpg(os.getpgid(pid), SIGTERM)` reaches the child through the new session
  established at launch. That is POSIX-only and unverifiable here.

- **A3 — the fact-name guard scans `backend/src` only.** Carried forward from round 1
  unchanged. Correct for this guard (its own fixture must write a literal, in `tests/`),
  but contracts.md §4 bans the literals "anywhere else", so a test-side literal remains a
  manual RV grep. Unchanged in round 2; still not a finding.

## Residual risk for the acceptance run — recorded, not a finding

Two unexecuted paths compound, and both land on the first live run rather than here:

1. **`test_chaos_restart_smoke_real_infra.py` has never run** (datastores are down;
   correctly classified `live_infra` and deselected — the 512 count above is that one
   module). Nothing has proved that the *deployment's* worker launches from these specs:
   script paths, working directory, inherited environment. **Run this first when the stack
   comes up, ahead of any scenario built on it** — a spec error will present as a worker
   that exits during startup, and `_running_after_settling`'s 20-second settle and its
   message are what will make that readable rather than mysterious.
2. **The POSIX graceful teardown path has never run anywhere**, per A2. It is now asserted
   two ways (structurally on every platform, behaviourally on POSIX), so the first CI run
   on a POSIX runner is the moment it is genuinely proved. If that run is red, the failure
   is in `_signal_tree`'s POSIX arm or the session setup, not in the test.

Neither is a defect in this diff, and neither is treated as one. They are the two places
where this branch's guarantees are currently arguments rather than observations, and they
should be the first two things the acceptance run resolves.
