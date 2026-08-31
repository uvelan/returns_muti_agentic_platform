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
