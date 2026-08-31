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
