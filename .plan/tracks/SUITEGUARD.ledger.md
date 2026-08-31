# SUITEGUARD ledger — a suite that shrinks must not report success

Append-only. One entry per step. Exact commands, exact output, pasted from the
terminal rather than transcribed.

Branch `feat/suite-size-guard`, base **`b7f07838d22d016021ca52c0c86f0b249e867049`**
— read from the repository by ref, not taken from the dispatch.

Scope: `scripts/ci/` and `.github/workflows/checks.yml` and this ledger.
`backend/src/` and `frontend/src/` are out of scope; a production defect found
here is a report, not a commit.

---

## step:00 — base verification, and the ref that is actually the tip

The dispatch says to verify the base by ref rather than by sha, and that is
what caught the interesting thing here: **`origin/refactor/unified-return-platform`
is NOT the tip.** The local ref is, by 329 commits.

```
$ git fetch --all --prune
$ git rev-parse refactor/unified-return-platform
b7f07838d22d016021ca52c0c86f0b249e867049
$ git rev-parse origin/refactor/unified-return-platform
a50c5500788f99e909f23099a81731b37c736b8c
$ git merge-base --is-ancestor b7f07838 a50c5500 && echo YES || echo NO
NO
$ git merge-base --is-ancestor a50c5500 b7f07838 && echo YES || echo NO
YES
$ git rev-list --count a50c5500..b7f07838
329
$ git rev-list --count master..b7f07838
591
```

So origin's tip is *contained in* the local ref and the local ref carries 329
commits that have never been pushed. Branching from `origin/` — the reflex, and
the thing a sha in a dispatch would have encouraged — would have been the tenth
stale-base incident on this run, and the stalest yet. The base is the local ref.

```
$ git worktree add -b feat/suite-size-guard K:/Projects/Ret/rmap-suite-size-guard refactor/unified-return-platform
Preparing worktree (new branch 'feat/suite-size-guard')
HEAD is now at b7f07838 fix(tests): trunk was red on both ruff gates -- my merge verification was wrong
$ git -C K:/Projects/Ret/rmap-suite-size-guard rev-parse HEAD
b7f07838d22d016021ca52c0c86f0b249e867049
```

**One correction to the dispatch, recorded because it changes nothing but would
have if I had trusted it.** The dispatch says `known_test_failures.json`
"currently has an empty backend list (a fix landed today)". On `b7f07838` it
does not — `suites.backend.known_failures` holds one entry:

```
tests.test_cumulative_support_outcomes::test_a_rejected_return_still_opens_no_work_item
```

Either the fix is not on this ref or the dispatch is describing a different
tree. I have not touched the file either way; the instruction that matters
("do not add to it") is unaffected.

**Environment.** Neither `frontend/node_modules` nor `backend/.venv` exists in
a fresh worktree. `npm ci` was run here. For Python the dispatch's warning
applies: the only venv is editable-installed against the MAIN worktree's `src`,
so every Python command below carries
`PYTHONPATH=K:/Projects/Ret/rmap-suite-size-guard/backend/src`. `.env` was
absent (gitignored) and `cp .env.example .env` was run, which is exactly what
`checks.yml`'s backend job does on a fresh runner.

**Anchors read before writing anything:**

| Anchor | Verdict |
|---|---|
| `.github/workflows/checks.yml` | read; `backend` and `frontend-tests` both `npm test`/`pytest` then call the comparator; `allowlist-self-test` is Gate 0 and both suite jobs `needs:` it |
| `scripts/ci/assert_known_failures.py` | read; already computes a `ran` set and already fails on `not ran` — it has the *shape* of a size check and no *size* |
| `scripts/ci/test_assert_known_failures.py` | read; seven negative controls, incl. "a suite that collapsed reports no failures, and must not read as success" |
| `scripts/ci/known_test_failures.json` | read; 1 backend entry, 2 frontend entries (see correction above) |
| `frontend/scripts/check-bundle.js` + `frontend/bundle-budget.json` | read; this is the ratchet idiom the dispatch says to follow — measured values in a data file, growth fails, and it is *self-pruning* so the baseline cannot rot |

**One finding that shapes the design.** The floor has to survive being measured
on one platform and enforced on another (I am on Windows; CI is
`ubuntu-latest`). So: is collection platform-dependent here? Searched:

```
$ grep -rn "collect_ignore\|sys.platform\|platform.system" backend/tests/ --include=*.py
(no collect_ignore; every hit is a module named platform.system_store or a print)
$ grep -rn "skipif" backend/tests/ --include=*.py | wc -l
8
```

All eight are `pytest.mark.skipif` — **runtime** skips. A skipped test is still
collected and still writes a `<testcase>` into the JUnit report. There is no
`collect_ignore` anywhere. So the *collected* count is platform-stable even
though the *passed* count is not, which is what makes a zero-slack floor
defensible below rather than a percentage guess.

---

## step:01 — the guard, and Gate 0's negative controls for it

**Shape chosen: extend `assert_known_failures.py`, not a new script.** The
dispatch allowed arguing for a different shape; I could not find the argument.
Both suite jobs already invoke this script, it already parses the JUnit report,
and it already computes the exact set — `ran` — that the floor needs. It even
already contains the degenerate case of this check (`if not ran: return 2`). A
separate script would have re-parsed the same file in a second step that could
be forgotten on a third suite; extending this one means a suite cannot be gated
by the allowlist *without* being measured, because it is one call.

**What is measured: two counts per suite, `cases` and `files`.** `cases` is
`<testcase>` elements; `files` is distinct `classname` values (test files for
vitest, test modules for pytest). Both, because they fail differently: a dead
worker takes whole files with it and moves `files` first and hardest — which is
the observed defect exactly — while `cases` is what notices a parametrised
family that quietly failed to expand inside a file that did start.

**Rejected: recording the file NAMES.** A name inventory would name precisely
which files are missing rather than only how many, which is better diagnostics.
It loses on rot. Several hundred backend modules, every rename requiring an edit
— and a list that must be edited routinely is a list that gets edited
reflexively, which is the failure mode the dispatch names ("would train people
to edit it without thinking"). Once the edit is reflexive, a deletion rides
through in the same diff and the guard is decoration. Two integers per suite get
read. The cost is honest and is stated in the error text: the guard says how
many did not report, and does not pretend to know which.

**Zero slack on the floor, deliberately, and unlike the bundle ratchet.**
`check-bundle.js` carries a 0.5% GROWTH_ALLOWANCE because gzip output is not
byte-identical across zlib versions — it is absorbing instrument noise. There is
no instrument noise here: these are integer counts, and step:00 established that
collection is platform-stable in this repository. Any allowance would therefore
not be absorbing error, it would be a hole of exactly that size for tests to
vanish into. 5% of 867 is 43 tests that could disappear in silence.

**A floor that is never re-staked is a floor at zero — `RESTAKE_ALLOWANCE`.**
This is the one place the check fires on the suite getting BIGGER, and it is
`check-bundle.js`'s SHRINK_ALLOWANCE reasoning transposed: a baseline that can
only move one way rots into a blanket excuse. Record 867 today, let the suite
reach 2,000, and a run executing 900 tests — a worse collapse than the one that
motivated this — clears the floor. So growth past 25% asks for a re-stake and
prints the number to write. 25% is loose on purpose: it is not absorbing
measurement error, it is choosing how often a human is asked to look, and a
quarter of a suite is rare enough to be a real event.

**Exit 2, not 1.** `checks.yml` already discriminates on that boundary — "0 =
clean, 1 = tests failed and the allowlist gets to rule on it. Anything else is
the run itself breaking, and no allowlist covers that." A suite two thirds of
which never started is the second thing. Filing it as 1 would put an
infrastructure failure in the queue marked "a test is failing", with the wrong
owner. The size verdict also OUTRANKS the allowlist verdict: if the suite did
not run, the allowlist's opinion of what it happened to contain is not evidence.

**It runs on red runs too.** The size check is evaluated before the allowlist
comparison and independently of it. The frontend suite exits non-zero on a
correct run (two allowlisted failures in `src/domains/registry.test.ts`), so a
size check that only ran on success would be gated by the very condition it
exists to doubt. There is a negative control for exactly this — "rejects a run
that is short AND legitimately red".

### Gate 0 extended (rule 13, first half)

The gate that runs the negative controls is unchanged and already named:
`checks.yml`'s `allowlist-self-test` job, `Negative controls` step, which both
suite jobs `needs:`. It gained 21 controls; the 8 that were there still pass.

```
$ PYTHONPATH=K:/Projects/Ret/rmap-suite-size-guard/backend/src \
    python scripts/ci/test_assert_known_failures.py
the accepted run: exactly the known failures, and they still fail
  [ok  ] accepts a run whose only failures are allowlisted

the regression it exists to catch
  [ok  ] rejects a failure that is not on the allowlist
  [ok  ] names the new failure

an error is a failure too -- a crash on the way to an assertion
  [ok  ] rejects an errored test that is not on the allowlist

the list must not rot
  [ok  ] rejects a run where an allowlisted test now passes
  [ok  ] asks for the stale line to be deleted
  [ok  ] rejects a run that never collected an allowlisted test

a suite that collapsed reports no failures, and must not read as success
  [ok  ] rejects a report containing no test cases
  [ok  ] rejects a missing report

a suite that came back SMALLER must not read as success
  [ok  ] accepts a run that is the size it should be
  [ok  ] says so in the log
  [ok  ] REJECTS an all-green run that is missing a fifth of its files
  [ok  ] names the shortfall in cases (50 recorded, 30 ran)
  [ok  ] names the shortfall in files (10 recorded, 6 ran)
  [ok  ] says the suite shrank
  [ok  ] exits 2 (the run broke), not 1 (a test failed)
  [ok  ] rejects a run that is short AND legitimately red
  [ok  ] the shortfall outranks the allowlist's verdict
  [ok  ] rejects a run missing a single file

but it is a FLOOR, not a pin -- growth is not a failure
  [ok  ] accepts a suite that grew (55 cases against a floor of 50)

and a floor the suite has outgrown is a floor at zero
  [ok  ] rejects a floor the suite has left far behind
  [ok  ] prints the number to re-stake it at

a floor that cannot fail is not a floor
  [ok  ] rejects a missing floor file
  [ok  ] says a check with no floor is not a check
  [ok  ] rejects a floor of zero
  [ok  ] rejects a floor with a missing count
  [ok  ] rejects a floor that is not a number
  [ok  ] rejects a suite gated with no floor recorded for it

all negative controls passed
EXIT=0
```

The central control is `REJECTS an all-green run that is missing a fifth of its
files`: a planted report of 6 files / 30 cases against a recorded 10 / 50, in
which **every one of the 30 passes**. There is no failure anywhere in it for the
allowlist to see. Before this change the comparator accepted it.

One pre-existing control needed a floor of its own rather than the standard one:
`vanished.xml` deliberately drops a case, so judged against the shared floor it
would answer 2 where that assertion wants 1. Each control now states the size it
is measured against, which reads better than inheriting one anyway.

---

## step:02 — the defect reproduced live, and what it forced me to change

I did not have to manufacture the first piece of evidence. The frontend suite
was run for a baseline while the backend suite was running in another process on
the same machine, and **the defect happened**:

```
$ cd frontend && npm test -- --reporter=default --reporter=junit --outputFile.junit=junit-frontend.xml
...
⎯⎯⎯⎯⎯⎯ Unhandled Error ⎯⎯⎯⎯⎯⎯⎯
Error: [vitest-pool]: Failed to start forks worker for test files K:/Projects/Ret/rmap-suite-size-guard/frontend/src/domains/support/RmaTicketsPage.test.tsx.
 ❯ node_modules/vitest/dist/chunks/cli-api.BK8pd4xc.js:3465:94
 ❯ Pool.schedule node_modules/vitest/dist/chunks/cli-api.BK8pd4xc.js:3465:5

Caused by: Error: [vitest-pool-runner]: Timeout waiting for worker to respond
 ❯ Timeout.<anonymous> node_modules/vitest/dist/chunks/cli-api.BK8pd4xc.js:3041:58

 Test Files  1 failed | 46 passed (47)
      Tests  2 failed | 583 passed (585)
     Errors  14 errors
   Duration  229.48s
JUNIT report written to K:/Projects/Ret/rmap-suite-size-guard/frontend/junit-frontend.xml
```

47 files where an unloaded machine reports 62; 585 tests where it reports 867.
The JUnit report it wrote:

```
$ python -c "...count testcase elements and distinct classnames..."
testcases: 585
distinct classnames: 47
failures: 2
```

**Two failures — and they are exactly the two allowlisted ones.** So this real
artifact is the defect in its pure form, and the pre-change comparator was run
against it to prove the point rather than assert it:

```
$ git show b7f07838:scripts/ci/assert_known_failures.py > /tmp/old_comparator.py
$ python /tmp/old_comparator.py --suite frontend --report frontend/junit-frontend.xml \
      --allowlist scripts/ci/known_test_failures.json
577 tests ran, 2 failed, 2 allowlisted
only the 2 known, still-failing tests failed
EXIT=0
```

**Exit 0.** `frontend-tests` would have been green having lost 15 of 62 files
and 282 of 867 tests. That is the whole defect, on trunk, in one command.

The artifact is kept at
`<scratchpad>/junit-frontend-TRUNCATED-real.xml` — it is gitignored
(`.gitignore:103`) and is evidence rather than a fixture.

### What this run changed in the design

Look at the old comparator's line: `577 tests ran`, against a report holding
**585** `<testcase>` elements. `ran` is a set of `classname::name` ids and this
suite genuinely contains duplicated ids — 585 collapsing to 577.

So `len(ran)` was the wrong thing to floor. Against distinct ids, one of a
duplicated pair can stop running without moving the number, and the blind spot
is the size of every duplicated name in the suite. `_read_report` now returns a
COUNT of elements alongside the id set, and the floor uses the count.

Two controls added for it: a report of 55 elements collapsing to 46 ids passes a
floor of 55, and the same report with five of the duplicates removed fails.

Gate 0 also caught my own error while adding them — I wrote the fixture's floor
as 10 files when the duplicate block reuses a file `suite_of` already emits,
making it 9:

```
  [FAIL] counts elements, not distinct ids (55 elements, 46 ids) -- ::error::THE SUITE SHRANK: 9 test files reported, but the recorded floor is 10 -- 1 did not report.
```

Which is the negative controls doing exactly the job they exist for, on the
first day, against the person writing them. Corrected; all 31 controls pass:

```
$ python scripts/ci/test_assert_known_failures.py
...
all negative controls passed
EXIT=0
```

---

## step:03 — the floor recorded, the gate named, both suites proved

**Rebased first.** Trunk moved during the work: `refactor/unified-return-platform`
went `b7f07838` -> `80c280f9`. The delta is five `docs(merge)` commits and
touches nothing under `scripts/ci`, `.github/workflows`, `frontend/src` or
`backend/src`, so it changed no assumption here, but the rebase was done anyway
rather than finishing on a base known to be stale.

```
$ git rev-list --count b7f07838..80c280f9
5
$ git diff --stat b7f07838 80c280f9 -- scripts/ci .github/workflows frontend/src backend/src
(empty)
$ git rebase 80c280f9
Successfully rebased and updated refs/heads/feat/suite-size-guard.
```

Re-checked on the new tip, because two separate messages have now told me the
backend allowlist is empty: **it is not.** It still holds the one
`test_a_rejected_return_still_opens_no_work_item` entry. Recorded and left
alone. It matters only because it is the accidental partial guard discussed
below, and the guard here does not depend on it either way.

### Measuring the frontend floor took three attempts, which is itself evidence

The first two `npm test` runs on this machine were **truncated** — the defect,
twice:

| run | files | cases | note |
|---|---|---|---|
| default pool, backend suite running alongside | 47 | 585 | `Failed to start forks worker`, 14 errors |
| `--maxWorkers=2` | 53 | 586 | 8 errors |
| `--maxWorkers=1` | **61** | **860** | complete |

So the condition RV could not reproduce reproduces readily here. That is
recorded as an observation and is deliberately NOT what the guard's
justification rests on — see below.

The complete run was corroborated by a second, independent instrument that does
not execute anything:

```
$ npx vitest list --json    # collection only, no test bodies run
entries: 860
distinct files: 61
```

860/61 from collection, 860/61 from the JUnit report of the executed run. Two
instruments, same numbers — which is the discipline `frontend/bundle-budget.json`
sets out ("Measure with the gate, not with the build log"), except that here the
two agree, so there is no gate-versus-log discrepancy to resolve.

`scripts/ci/suite_size_floor.json` records:

```
backend  : 5251 cases / 441 files   (1 failed, 5240 passed, 10 skipped, 514 deselected)
frontend :  860 cases /  61 files   (2 failed -- both allowlisted)
```

### The justification was rewritten to stand on the mechanism

Two coordinator messages landed mid-step, and both improved the work. The first
warned that "unreproduced" must not become "not a problem", and that a guard
justified by an anecdote is deleted by the first person who cannot reproduce the
anecdote. The second supplied a sharper structural reading, which is now the one
written into all three files:

* Against a run that dropped whole files, `unexpected` (`failed - allowed`) and
  `repaired` (`allowed & (ran - failed)`) are **structurally empty** — a dropped
  file yields neither a failure nor a pass, so those rules are not unlikely to
  fire, they are incapable of it.
* `missing` (`allowed - ran`) is the only rule that can fire, and only if a
  dropped file happened to carry an allowlisted id.
* `if not ran` catches **total** collapse and nothing short of it.
* And the framing that matters most, because it protects the guard from being
  deleted by someone who reads the existing gate and finds it reasonable:
  **an allowlist comparator can only notice failures already on its list.** It
  is the right instrument for "did anything new break" and the wrong one for
  "did the suite actually run". The allowlist is not defective at its own job;
  nothing was asking the second question.

All of that is checkable by reading `checks.yml` and the comparator. None of it
depends on reproducing anything.

### Rule 13 — the gate that runs the guard

| Guard | Gate that runs it |
|---|---|
| the size floor, backend | `checks.yml` job `backend`, step **"The suite ran in full, and only the known failures failed"** |
| the size floor, frontend | `checks.yml` job `frontend-tests`, step **"The suite ran in full, and only the known failures failed"** |
| the floor's negative controls | `checks.yml` job `allowlist-self-test`, step **"Negative controls"** — Gate 0, which both suite jobs `needs:` |

The steps were renamed. The old name, "Only the known failures failed", was true
of the collapsed run that motivated this, which is exactly why it needed to say
"ran in full" first. No new step was added: the floor rides the call both jobs
already make, so a suite cannot be gated by the allowlist without also being
measured.

### What was NOT done, and who should own it

**No `maxWorkers` cap was added.** `--maxWorkers=1` was used *by me, on the
command line, to obtain a baseline* and is not written into
`frontend/vitest.config.ts` or into `checks.yml`. Capping the pool would make
truncation rarer without making it visible, which on a gate whose job is to
report what did not run is the worse of the two outcomes. The cap addresses
vitest's pool behaviour; the floor addresses this workflow being unable to tell.
A cap may well be worth setting and is **proposed, not folded in** — it belongs
to whoever owns `frontend/vitest.config.ts`.

### Injection, both directions, both suites

Truncated reports were MANUFACTURED by trimming the real ones — whole modules
removed from the tail of collection order, every surviving case left
byte-identical, and the `testsuite` totals rewritten so the report stays
internally consistent. That last part matters: the difficulty of this defect is
that the headline agreed with itself. Script at
`<scratchpad>/truncate_report.py`.

**Frontend.** 20 of 61 files removed, and `registry.test.ts` — which carries
both allowlisted failures — SURVIVES. So this is precisely the case the
accidental catch cannot see:

```
$ python truncate_report.py junit-frontend-FULL-real.xml junit-frontend-TRUNCATED.xml 0.34
modules     : 61 -> 41 (20 dropped)
cases       : 860 -> 551 (309 removed)
failures    : 2
$ python -c "...registry.test.ts present?..."
registry.test.ts present: True
failures in truncated report: 2

$ python /tmp/old_comparator.py --suite frontend --report junit-frontend-TRUNCATED.xml --allowlist scripts/ci/known_test_failures.json
543 tests ran, 2 failed, 2 allowlisted
only the 2 known, still-failing tests failed
EXIT=0                                    <-- green, over 41 of 61 files

$ python scripts/ci/assert_known_failures.py --suite frontend --report junit-frontend-TRUNCATED.xml
::error::THE SUITE SHRANK: 551 test cases reported, but the recorded floor is 860 -- 309 did not report.
   ...
   in scripts/ci/suite_size_floor.json:  "cases": 551
::error::THE SUITE SHRANK: 41 distinct test files/modules reported, but the recorded floor is 61 -- 20 did not report.
   ...
   in scripts/ci/suite_size_floor.json:  "files": 41
543 tests ran, 2 failed, 2 allowlisted
EXIT=2

$ python scripts/ci/assert_known_failures.py --suite frontend --report junit-frontend-FULL-real.xml
suite size held: 61 test files/modules, 860 test cases (floor 61 / 860)
851 tests ran, 2 failed, 2 allowlisted
only the 2 known, still-failing tests failed
EXIT=0                                    <-- and this run is legitimately RED
```

The last one is the case the dispatch singled out: the frontend suite exits
non-zero on a correct run, and the floor still returns a clean verdict about its
size. The size check runs before the allowlist comparison and independently of
it, so it is never gated by the condition it exists to doubt.

**Backend.** No suite was re-run — the coordinator asked for machine quiet, and a
saved report is the better instrument anyway because it is fixed and replayable.
The report from step:02's run was trimmed:

```
$ python truncate_report.py junit-backend-FULL-real.xml junit-backend-TRUNCATED.xml 0.20
modules     : 441 -> 353 (88 dropped)
cases       : 5251 -> 4155 (1096 removed)
failures    : 1

$ python /tmp/old_comparator.py --suite backend --report junit-backend-TRUNCATED.xml --allowlist scripts/ci/known_test_failures.json
4155 tests ran, 1 failed, 1 allowlisted
only the 1 known, still-failing tests failed
EXIT=0                                    <-- green, over 353 of 441 modules

$ python scripts/ci/assert_known_failures.py --suite backend --report junit-backend-TRUNCATED.xml
::error::THE SUITE SHRANK: 4155 test cases reported, but the recorded floor is 5251 -- 1096 did not report.
::error::THE SUITE SHRANK: 353 distinct test files/modules reported, but the recorded floor is 441 -- 88 did not report.
4155 tests ran, 1 failed, 1 allowlisted
EXIT=2

$ python scripts/ci/assert_known_failures.py --suite backend --report junit-backend-FULL-real.xml
suite size held: 441 test files/modules, 5251 test cases (floor 441 / 5251)
5251 tests ran, 1 failed, 1 allowlisted
only the 1 known, still-failing tests failed
EXIT=0
```

Both new-comparator invocations above use the DEFAULT `--floor`, i.e. exactly
the command line `checks.yml` runs. Nothing is passed that CI would not pass.

### A misclassification found and fixed while writing the controls

A malformed `suite_size_floor.json` would have raised out of `json.loads`, and an
uncaught exception exits **1** — the code this script uses for "a test failed".
A typo in a JSON file would have been filed as a failing test: the exact
misclassification the exit-code discipline exists to prevent, arriving through
the guard added to enforce it. Reading the floor is now fully defended, and four
controls cover it.

### Final state

```
$ python scripts/ci/test_assert_known_failures.py
all negative controls passed          (35 controls: the original 8, plus 27)
$ python -m ruff check ../scripts/ci/
All checks passed!
$ python -c "import yaml; yaml.safe_load(open('.github/workflows/checks.yml'))"
checks.yml parses
```
