# SUITEGUARD — round 1

**Branch** `feat/suite-size-guard` @ `579bef8e5d4b378503f7ea19e7e2e31a5c5cc282`
**Base** `80c280f9` — verified: `git merge-base --is-ancestor 80c280f9 refactor/unified-return-platform` → yes, and
`git rev-list --count 80c280f9..refactor/unified-return-platform` → **0**. The base is the trunk head itself,
not a stale ancestor. (It is not an ancestor of `master`; trunk for this work is
`refactor/unified-return-platform`, and against that it is exact.)

**Verdict: CHANGES_REQUIRED** — three findings, one of them squarely rule 13 turned on the
guard's own most load-bearing line.

This is a good branch. The mechanism is right, the evidence is real rather than asserted, and
almost everything the ledger claims reproduced exactly when I re-ran it myself. The findings are
narrow and none of them is a design disagreement.

---

## What I verified, and how

I ran no backend suite and no live-infrastructure test. Everything below comes from the saved
JUnit artifacts, the branch's manufactured trims, and the standalone negative-control script
(`python scripts/ci/test_assert_known_failures.py` — no pytest, no infrastructure).

### 1. The injection evidence — reproduced, both directions, and the trap avoided

I restored the pre-change comparator (`git show 80c280f9:scripts/ci/assert_known_failures.py`)
and ran old and new against all four reports:

| report | cases / files | old exit | new exit | new says |
|---|---|---|---|---|
| frontend FULL (real) | 860 / 61 | 0 | **0** | `suite size held` |
| frontend TRUNCATED | 551 / 41 | **0** | **2** | 309 cases, 20 files `did not report` |
| backend FULL (real) | 5251 / 441 | 0 | **0** | `suite size held` |
| backend TRUNCATED | 4155 / 353 | **0** | **2** | 1096 cases, 88 modules `did not report` |

Every number matches the ledger. The full reports match the recorded floors **exactly**
(860/61 and 5251/441), so the floor was staked from the artifact rather than from a narrative.

**The trap was avoided.** I checked directly rather than taking the claim:

- frontend TRUNCATED still contains `src/domains/registry.test.ts` → `True`, and still reports
  exactly 2 failures, both allowlisted. So `missing` is empty and the accidental catch
  **cannot** fire. The exit 2 is the floor talking and nothing else.
- backend TRUNCATED still contains `tests.test_cumulative_support_outcomes::test_a_rejected_return_still_opens_no_work_item`,
  1 failure. Same conclusion.

**Internal consistency of the manufactured reports** — I checked this rather than trusting it,
since a trim whose totals still claim the original count would prove nothing:

- Sum of `<testsuite tests="...">` equals the actual `<testcase>` count in all four files
  (551 = 551, 4155 = 4155). Totals were genuinely rewritten.
- Every surviving `<testcase>` is byte-identical to its counterpart in the full report
  (`byte_differing_survivors = 0`), and no survivor is absent from the full report
  (`not_in_full = 0`). Nothing was fabricated or reshaped.

**The strongest piece is the one that was not manufactured at all.** The real truncation
artifact (`junit-frontend-TRUNCATED-real.xml`) measures 585 cases / 47 files / 577 distinct ids,
2 failures — both allowlisted, `registry.test.ts` survived. Old comparator: **exit 0**, printing
`only the 2 known, still-failing tests failed` over a suite that lost 14 of 61 files. New
comparator: **exit 2**. That single artifact is the whole defect and the whole fix, and it owes
nothing to a fixture the author wrote.

It also retro-justifies commit `24ddeb68`: 585 elements collapsing to 577 ids in that artifact,
860 collapsing to 851 in the full one. Flooring `len(ran)` would have carried a blind spot the
size of every duplicated name in the suite. Flooring element count is correct.

### 2. It does not fire on legitimate runs — including the red one

frontend FULL exits **0** under the new comparator while containing its two allowlisted
failures. This is the check that mattered most, and it passes: the size floor is not gated by
the condition it exists to doubt. Confirmed against `checks.yml` itself — both suite steps run
`set +e` and bail only on `status -gt 1` (lines 305/311 and 394/400), so exit 1 is genuinely the
tolerated path and a truncated run exiting 1 would sail through. Exit 2 is the correct choice
against this file's own boundary.

### 3. Design choices — all four sustained

- **Zero slack: correct.** `check-bundle.js`'s allowance absorbs zlib non-determinism; there is
  no instrument here and no noise to absorb, so any allowance is a hole of exactly that size.
  Sustained.
- **Exit 2, outranking the allowlist verdict: correct**, on the `-gt 1` boundary above.
  (But see Finding 1 — correct and untested are different things.)
- **Runs before and independently of the allowlist comparison: correct**, and necessary for §2.
- **25% restake — cannot be used to walk the floor down.** I checked this specifically. No code
  path in the script ever names a lower floor except the shrink branch, and that branch is only
  reached *after the run has already failed with exit 2* — so adopting its number is a human
  edit in the same commit, never something CI accepts silently. The restake branch prints only a
  higher number. There is no automated descent. The honest cost is a standing blind spot of
  `S − F ≤ 0.25F`, i.e. up to 20% of a maximally-outgrown suite, and `suite_size_floor.json`
  states that cost in those terms rather than hiding it. Sustained.

### 4. Rule 13 on the guard itself — gate named, controls run, and I broke it to check

Gate 0 (`allowlist-self-test`) runs `python scripts/ci/test_assert_known_failures.py`
(checks.yml:103), and both `backend` and `frontend-tests` declare `needs: allowlist-self-test`.
The gate is real and it blocks. I ran the script: **35 controls, all green.**

Counting controls is not evidence, so I mutation-tested the guard — eight mutations of
`assert_known_failures.py`, self-test re-run against each:

| mutation | caught? |
|---|---|
| M1 shrink check disabled | ✅ 9 controls red |
| M2 shrink returns 0 not 2 | ✅ 9 controls red |
| M3 restake rule removed | ✅ 2 controls red |
| M4 floors distinct ids not elements | ✅ 1 control red |
| M6 `json.loads` unguarded | ✅ 2 controls red |
| M7 missing floor file tolerated | ✅ 1 control red |
| **M5 size check demoted below the allowlist verdict** | ❌ **survives, 0 controls red** |
| **M8 `baseline <= 0` → `baseline < 0`** | ❌ **survives, 0 controls red** |

Six of eight caught, with specific and well-named failures. The two survivors are Findings 1
and 2.

### 5. The self-caught misclassification — fixed and defended

Verified. `_check_size` wraps `json.loads` in `except (OSError, ValueError)` returning 2, and
mutation M6 (removing the guard) reddens both `rejects an unparseable floor file with 2, not a
traceback` and `no traceback escaped`. The four floor-integrity controls
(unparseable, non-object entry, non-object root, no floor recorded for the suite) all run and
all pin real behaviour. The reasoning is right too: an uncaught exception exits 1, which is this
script's code for "a test failed", so a typo in a JSON file would have been filed to the wrong
queue and the wrong owner.

### 6. Windows-measured, Linux-enforced — both halves hold

This is the claim the branch could not test on a real runner, so I checked the mechanism rather
than the measurement:

- **Backend.** All 8 `skipif` occurrences in `backend/tests` are **runtime** skips, including
  the two module-level `pytestmark` forms (`test_compose_topology.py:40`,
  `test_source_inspection_postgresql_docker.py:55`). Module-level `pytestmark` still collects
  the items and skips at setup, so each still writes a `<testcase>`. There is **no**
  `collect_ignore`, no `importorskip`, no `pytest.skip(allow_module_level=True)`, and no
  `__test__ = False` anywhere in `backend/`. Collection is therefore platform-invariant.
- The deselection is also platform-invariant: `addopts` pins a static `-m "not live_infra and
  not browser"`, and `conftest.py::pytest_itemcollected` assigns the suite marker from
  fixture names and in-process composition — neither consults the platform. So the 514
  deselected on Windows are the same 514 on ubuntu-latest.
- **Frontend.** No `process.platform`, `os.platform`, `skipIf`, `runIf`, `.skip(`, `.todo(`,
  `.only` anywhere in `src/**/*.test.ts{,x}`; `vitest.config` `include` is a static glob. There
  is no platform-conditional collection to drift.

I did not re-run `vitest list --json`, and did not need to: the corroboration that matters for
the *Linux* question is the absence of platform gates, not a second Windows measurement. The
floor is set at the measured executed size with zero slack, and nothing in either suite can
collect differently on Linux. It is sound rather than lucky. Agreed.

### 7. Test/config integrity — clean

- `scripts/ci/known_test_failures.json` is **byte-identical** to trunk — same blob
  `cb4d565ef4824d4eacc2edd380e296c711d60670` on both commits.
- **The backend allowlist is not empty.** It carries
  `tests.test_cumulative_support_outcomes::test_a_rejected_return_still_opens_no_work_item`.
  I verified this against trunk directly rather than accepting either account. (See Finding 3.)
- Files touched: `checks.yml`, the ledger, `assert_known_failures.py`, `suite_size_floor.json`,
  `test_assert_known_failures.py`. **No production source.** No skips, no xfails, no weakened
  assertions, no deleted tests. Standing greps clean — no fact-name literals, no frozen-module
  imports.

### On the two judgment calls put to me

**Argument from mechanism, not incident — genuinely done.** All three artifacts lead with the
three checkable facts about `checks.yml` and demote the reproduction to "an observation, not the
load-bearing argument", and each explicitly says why: a guard justified by an anecdote is deleted
by the first person who cannot reproduce it. I verified fact (1) by reading the workflow and
facts (2) and (3) by running the old comparator. The justification stands without the incident,
which is exactly what makes it survivable — the previous reviewer's failure to reproduce is now
irrelevant to whether the guard is warranted. This is the right way round and it was done
properly.

**Declining the `maxWorkers` cap — right call.** A cap lowers the incidence of truncation
without restoring anyone's ability to see it, and on a gate whose entire job is reporting what
did not run, a defect that now fires seldom enough that nobody notices is the worse outcome. The
floor is cause-agnostic: it catches truncation from a dead worker, from OOM, from a future
vitest regression, and from a cause nobody has thought of. Separating them, and naming an owner
for the cap rather than folding it in, is correct.

---

## Findings

### Finding 1 — the precedence of size over the allowlist verdict is not pinned by any control
**Rule 13.** `scripts/ci/assert_known_failures.py:~315` (`if size != 0: return size` preceding
`if unexpected or repaired or missing: return 1`); controls in
`scripts/ci/test_assert_known_failures.py`.

Reorder those two blocks and **all 35 negative controls still pass.**

This is not hypothetical, and it is not a case the guard can afford to leave unguarded. The
ordering only changes behaviour when a run is short **and** the allowlist has a finding — and
the most likely real truncation is exactly that: a dead worker takes `registry.test.ts` with it,
so `missing` becomes non-empty. I built that report (the branch's own frontend trim with
`registry.test.ts` removed: 537 cases / 40 files) and ran both:

```
shipped code           -> exit 2   (blocked by `status -gt 1`)
size-demoted mutation  -> exit 1   (TOLERATED — CI green over 40 of 61 files)
```

The mutation reinstates the precise hole this branch exists to close, and the branch's own gate
waves it through.

The reason the existing control misses it is worth stating, because it looks like coverage:
`rejects a run that is short AND legitimately red` plants a report whose failures are all
*allowlisted*, so the allowlist verdict is **clean** and the comparator falls through to the
size result under either ordering. It tests "short + red", not "short + the allowlist has
something to say" — and only the latter distinguishes the orderings.

The ledger and three separate comments argue this precedence at length ("a short run outranks a
clean verdict", "2 wins over 1, and over 0"). By this branch's own standard that argument is a
comment until something runs it.

**Fix:** add a control planting a short report that also drops an allowlisted id, asserting
exit 2 — and ideally a second asserting exit 2 for short + a genuinely *new* failure.

### Finding 2 — `rejects a floor of zero` passes through the wrong branch
`scripts/ci/test_assert_known_failures.py` (the `floor={"cases": 0, "files": 10}` control);
guard at `assert_known_failures.py` `baseline <= 0`.

Weakening `baseline <= 0` to `baseline < 0` leaves all 35 controls green. The control passes not
because the zero-floor rejection fires, but because with `baseline = 0` the *restake* rule
(`50 > 0 * 1.25`) fires instead and also returns 2.

Behaviourally this is contained — a zero floor still fails the job either way — so severity is
low. But the control does not test what it is named after, and a later change to the restake
rule would silently remove the only thing making it pass. Assert on the message
(`has no usable demo.cases floor`), not just the exit code.

### Finding 3 — `suite_size_floor.json` asserts the backend allowlist is empty; it is not
`scripts/ci/suite_size_floor.json`, `$comment`:

> "On the backend that accident is not even available in principle: a suite whose allowlist is
> empty has no named test whose absence could be noticed."

The backend allowlist on trunk holds one entry
(`test_a_rejected_return_still_opens_no_work_item`), so the accidental catch **is** available on
the backend today. It is emptied only on the unmerged `feat/runtime-patch-double`.

`checks.yml` and `assert_known_failures.py` both state this correctly — the workflow says the
accident "turns entirely on the single name that list happens to hold; empty it and the accident
is gone outright", which is exactly right. The JSON retained the earlier, incorrect premise.

This is a documentation defect, but not a trivial one: this file's whole value is that its
argument can be checked by reading the repository, and here a reader who checks will find the
premise false. That is the failure mode the branch correctly identified for anecdote-based
justifications, reappearing as a stale fact. Restate it conditionally, as the workflow does.

### Minor note (not a finding)
`assert_known_failures.py` says it "borrows `frontend/scripts/check-bundle.js`'s
SHRINK_ALLOWANCE exactly" and then contrasts 25% with "0.5%". It borrows the *mechanism*
exactly, not the value, and 0.5% is that file's `GROWTH_ALLOWANCE`; its actual `SHRINK_ALLOWANCE`
is `0.03`. The apt comparison for a 25% restake is 3%, not 0.5%. The reasoning is unaffected —
the zero-slack argument is about the growth side and is correct as written.

---

## To clear this round

1. Add the control(s) in Finding 1 — short + allowlist finding must assert exit 2.
2. Tighten the zero-floor control to assert its message (Finding 2).
3. Correct the backend-allowlist claim in `suite_size_floor.json` (Finding 3).

Nothing here requires a design change, and the 25% restake, the zero slack, the exit code, the
ordering, and the decision to leave `maxWorkers` alone are all sustained as correct.
