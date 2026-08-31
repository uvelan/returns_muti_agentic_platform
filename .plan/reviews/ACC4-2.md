# ACC4-2 — `feat/acc-frontend` @ `5635052a`

**Verdict: `PASS`** — zero unresolved findings. F1 is closed at the mechanism.

Round 1 (`ACC4-1.md`, `9b1901fc`) was `CHANGES_REQUIRED` on one finding. Its
substance — four injections, the seeding proven load-bearing by deletion, F7a's
discard, rule 12, test integrity — was established there and is not re-derived
here. This round is the diff and one judgement.

Escalations **E1** (AMENDMENT-6 unexecuted, rule 2, blocking) and **E2**
(FE-DEFECT-5, rule 13) stand with the orchestrator, unchanged and still not
charged to this branch.

---

## No code

```
$ git diff f4d9743a 5635052a -- frontend/ backend/
(empty)

$ git diff --name-only f4d9743a 5635052a
.plan/acceptance/STATUS.md
.plan/acceptance/frontend-audit.md
.plan/tracks/ACC4.ledger.md
```

Three planning documents, nothing else. `scripts/` diff is zero lines, so
`known_test_failures.json` is byte-identical; `.github/` and `frontend/src/`
are untouched; `git grep -nE "(it|test|describe)\.(only|skip|todo)\("` at
`5635052a` over `frontend/src/` returns nothing. Suite figures reused from round
1 as dispatched: 62 files / 867 tests / 865 passed, 78.87 s. No backend or
live-infra test was run by this review.

## F1 — fixed at the mechanism

**Superseding marker, checked as asked.** The marker at `ACC4.ledger.md:101-106`
claims the step:01 command and its `EXIT=1` output were true and only the
inference was wrong. Read against the entry it supersedes (`:79-100`), that
distinction is **accurate**. Step:01 contains two measurements — the verbatim
`npm test` capture (`40 passed (40)`, `Errors 21 errors`, `310.06s`) and
`npm test >/dev/null 2>&1; echo "EXIT=$?"` → `EXIT=1` — and one sentence of
inference, *"The exit code is the one thing that saves it"*. The measurements
are unaffected by F1; the exit code **was** 1. What was wrong is the claim about
what CI does with a 1, which is inference about `checks.yml`, not measurement of
the run. The marker is therefore correctly scoped rather than generous, and this
is the right way to supersede an append-only entry.

**The comparator reading, verified against the script rather than the write-up.**
`scripts/ci/assert_known_failures.py:104-106` computes exactly the three sets
quoted, and `:125-126` returns 1 if any is non-empty. The branch's reasoning
holds end to end:

* Files that never start contribute no `<testcase>` elements — the JUnit
  reporter (`vitest/dist/chunks/index.UpGiHP7g.js`) writes one `testsuite` per
  *reported* file and has no unhandled-error path, and the observed run's 21
  files were counted under `Errors`, not under `Test Files`. So they land in
  neither `failed` nor `ran`. `unexpected = failed - allowed` and
  `repaired = allowed & (ran - failed)` are structurally empty with respect to
  the drop; `missing = allowed - ran` is the only rule the truncation can move.
  Confirmed.
* `checks.yml:492` invokes the script as `--suite frontend`, and
  `suites.frontend.known_failures` holds two ids, both in `registry.test.ts`.
  Confirmed by running the command below.
* Counterfactual: drop 21 files not containing `registry.test.ts` and that file
  runs and fails its allowlisted pair → `failed ⊆ allowed`, `repaired = ∅`,
  `missing = ∅` → **exit 0**. Confirmed.

**Is there any other floor?** No — the branch's account is complete for the
purpose it is quoted for. `if not ran` (`:100`) returns 2 on a report with zero
cases. The only other early exits are `if not report.exists()` (`:93-95`) and an
unknown `--suite` (`:85-87`), both also 2; the first is the same total-collapse
class as `if not ran` (no report at all), the second cannot fire on a
partial run. Nothing anywhere asserts a floor on how much was collected. Noted
for completeness, not as a correction — this is the claim a separate agent is
building a guard against (`feat/suite-size-guard`), and it is sound as stated.

**The corrected command runs.** From the repository root:

```
$ node -e "console.log(require('./scripts/ci/known_test_failures.json').suites.frontend.known_failures.join('\n'))"
src/domains/registry.test.ts::the domain registry > declares exactly the canonical domains
src/domains/registry.test.ts::the domain registry > shares a visibility capability only where that is deliberate
EXIT=0
```

The superseded form reproduces the failure it is recorded as having had
(`.known_failures` at the top level → `undefined`, and `.join` on it throws).
Both halves of the self-correction check out. The audit's allowlist claim is
likewise scoped to `--suite frontend` (`frontend-audit.md`), which matches the
workflow step.

**Non-reproduction, recorded honestly.** Both `frontend-audit.md` and `STATUS.md`
finding 6 carry the 49.86 s / 85.86 s results as **"unreproduced, not refuted"**,
with the trigger threshold stated as unknown and the author's capture left
standing as the observation. And the ordering is right: the corrected FE-DEFECT-1
leads with the workflow quote and the comparator, then names the contingency,
then the remedy, and only then reaches reproduction status — so the gate
consequence rests on reading `checks.yml`, not on inducing the truncation.
`STATUS.md` follows the same order and marks itself a correction rather than
silently swapping the sentence.

The rewrite also does the thing the finding asked for and one thing it did not:
the defect is split into pool behaviour / reporter artifact / gate hole so the
three remedies stop being confused, and the general property is stated —
an allowlist comparator can only notice failures already on its list, which is
the right instrument for "did anything new break" and the wrong one for "did the
suite run". That sentence is what the floor guard has to answer, and it is now
written down where its builder will find it.

## Findings

None.

---

Round 1's finding was that the record made a real gate defect look contained.
The correction makes the defect larger and says so in its own first line. The
branch also caught two errors of its own while verifying the fix — an
unrunnable command in its own ledger, and an unscoped allowlist claim — which is
the failure mode this run has already spent a review round on. Merge permitted.
