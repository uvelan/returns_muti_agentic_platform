# CILINT — backend lint reaches a gate

Integration track. One finding, one decision, one gate.

## The finding

`ruff` is declared in `backend/pyproject.toml` (`[tool.poetry.group.dev.dependencies]`,
pinned `0.15.21`), configured there (`[tool.ruff]`, `[tool.ruff.lint]`), and locked in
`poetry.lock` — and **nothing invoked it**. `.github/workflows/checks.yml` gated the
frontend's `lint` and left the backend's unwritten.

This is RV brief rule 13 exactly: *a guard with no gate is a comment.* Third instance
on this run, after the bundle budget nothing ran and the live-infra suite nothing gated.

## Base verification (contracts §3)

Mandatory, and it mattered. The worktree this track was dispatched into sat at
`0448d32a`, an **ancestor of trunk 847 commits behind**. Branching from the worktree
HEAD would have produced a branch omitting every merged slice — and it would have
failed *silently*, because the tree still compiles and the suite still passes.

`feat/ci-backend-lint` is cut from the **ref** `refactor/unified-return-platform`
(`73bd79aa`), not from the worktree HEAD.

## The measurements

Taken on `73bd79aa` with the pinned `ruff 0.15.21`, from `backend/`.

| Check | Result |
| --- | --- |
| `ruff check .` | **14 errors** across **6 files** (7 auto-fixable) |
| `ruff format --check .` | **94 files** would be reformatted (1053 already clean) |
| Files appearing in **both** sets | **2** |

**The two are different populations, and conflating them would have been a small
dishonesty.** `ruff check` and `ruff format --check` are distinct commands answering
distinct questions. 6 files have lint errors; 94 are unformatted; the overlap is 2.
The "85 unformatted files" of the dispatch are *not* the "15 errors" — and neither
count reproduced exactly at this commit (14 and 94 here), so both were re-measured
rather than inherited.

The 14 errors, in full:

- `I001` import block unsorted — 4 files
- `B904` `raise ... from` inside `except` — 6, all in `api/shipment_console.py`
- `F401` unused import — 2, in `tests/test_shipment_tracking.py`
- `RUF022` `__all__` unsorted — 1
- `RUF059` unused unpacked variable — 1

Every one is mechanical. None encodes a design question.

### Collision with in-flight work — measured, not assumed

**Corrected after RV finding F-1. The first version of this paragraph said
"five unmerged branches touch zero affected files". The count was wrong: there
were six, and one of them does touch files this branch reformatted.**

`git branch --no-merged 73bd79aa` returns **six**: `feat/acc-scenarios`,
`feat/teams-bots-windows-first`, `task/teams-gateway`,
`task/teams-platform-integration`, `task/teams-rma-saga`, and
**`feat/live-harness-registration`** — the one the original count missed.

It touches two files in the 94-file reformatted set:
`backend/tests/test_return_case_policy_gate_real_infra.py` and
`backend/tests/test_return_case_workflow_real_infra.py`.

So the honest statement is: **five of the six touch zero affected files; the
sixth touches two.**

### Why the omission happened, and why option 1 still holds

The branch was not overlooked — at measurement time it did not exist in the
form that would have listed. Its tip `00471116` was committed at
`2026-08-31T11:47:51`; the commit recording the original measurement,
`9b665a98`, landed at `11:47:17`. **Thirty-four seconds.** Until then the branch
pointed at `7a898cf9`, which `git merge-base --is-ancestor 7a898cf9 73bd79aa`
confirms was an ancestor of this branch's base — so `--no-merged` correctly
omitted it.

That explains the number without excusing it. A point-in-time enumeration of
sibling branches is a *perishable* measurement, and this ledger leaned on one as
if it were durable. The conclusion is now carried by the three checks below,
each of which is a property of the content rather than of the moment it was
taken. RV supplied these; they are re-verified here independently, because
another agent's evidence is not automatically this ledger's.

1. **The merge is conflict-free.** `git merge-tree --write-tree HEAD
   feat/live-harness-registration` exits 0 (tree `c05a8b1a`).
2. **The only unformatted region in either file is the hunk this branch already
   fixed.** `ruff format --diff` on the branch's own versions reports exactly one
   hunk per file — the `draft_support_request` signature — and it is
   byte-identical to what `git diff 73bd79aa..HEAD` shows this branch changed
   there. That branch's own additions are format-clean.
3. **The merged tree passes the gate.** The two files extracted from tree
   `c05a8b1a` come back "2 files already formatted".

**Two false positives caught while checking, recorded so the method is not
trusted further than it earns.** Both came from running extracted files in a
scratch directory instead of in the repo:

- `ruff format --diff` there initially reported dozens of wrapped-signature
  hunks. Cause: files outside `backend/` do not pick up
  `backend/pyproject.toml`, so ruff used its **default line-length 88** instead
  of the configured **100**. Re-run with `--config` pointed at the real file, it
  collapses to the single hunk above.
- `ruff check` there reported `I001` on both merged files. Cause: outside the
  repo, ruff cannot infer `return_platform` and `tests` as first-party, so it
  wants a different import order. Controlled for by running the *same isolated
  check* on this branch's own copy of one file, which is known clean in-repo:
  it reports the identical `I001` in isolation and `All checks passed!` in
  place. Artifact of the method, not a finding.

The remaining substance is unchanged: the other five branches intersect zero
affected files, they are overwhelmingly *adding* files, and new files were
already format-clean. Option 1 stands.

## The decision — option 1, fix them all now

Chosen against the dispatch's stated lean toward option 2, because the dispatch's own
escape clause is the condition the measurements met: *"if the 15 errors turn out to be
trivial and the 85 files are pure formatting, option 1 may be plainly better."* Both
halves hold. The errors are trivial and the 94 are pure `ruff format` output.

**Why not option 2 (baseline-and-ratchet).** Baselining is the right instrument when
the debt is *expensive to pay now* — which is why `known_test_failures.json` earns its
keep: its one backend entry is a `temporalio` `workflow.patched` defect that takes real
work to fix, so recording it is cheaper than fixing it and honest about why. Here the
debt costs one `--fix`, six one-line `from err` edits, and one `ruff format`. Paying it
buys a **plain hard gate with no baseline file to maintain, no comparator to keep
honest, and nothing that can rot.** Option 2 would leave permanent machinery in the
repo to manage debt that one commit erases — and, to meet this repo's own Gate 0
standard, that machinery would need its own negative-control self-test alongside
`test_assert_known_failures.py`. That is a lot of apparatus to defer 14 import sorts.
The repo's own allowlist comment warns the list "must not rot into a blanket excuse";
seeding it with trivia is how that starts.

**Why not option 3 (changed files only).** It leaves the 94 permanently unexamined and
creates a second standard — code is clean or it is not, depending on whether someone
happened to touch it. The dispatch is right that this is the worst of the three.

**Honoring the constraint.** "Do not fix lint errors inside files this run's slices own
as a side effect of wiring." The fixes are therefore **two deliberate commits of their
own, before the wiring commit**, each one tool-generated and separately reviewable, and
neither mixed into the workflow change. Semantic neutrality is proven by the suite, not
asserted: same one known failure before and after.

## The surface was wider than `backend/` — found mid-task

`scripts/linux/03_run_backend_quality.sh` is the repository's own definition of
backend quality, and it runs ruff over `backend/` **and** three root paths:
`scripts/probe_configured_ai_models.py`, `scripts/linux/validate_env.py`,
`scripts/tests`. There is no root `pyproject.toml`, so those resolve to
`backend/pyproject.toml`'s config — one lint domain, not two.

**Nothing in CI runs that script.** Gating only `backend/` would have reproduced
the exact defect one level down. Two of the three paths were unformatted, which
is what such a gap reliably produces.

Real totals, therefore: **14 lint errors** and **96 unformatted files** (94 in
`backend/`, 2 under `scripts/`).

## Gate shape

Two steps, not one, following the file's existing decomposition rule — `&&`
short-circuits, and a failing step should name itself on the summary line. `ruff check`
and `ruff format --check` are different events with different fixes.

Exit codes are discriminated as everywhere else in `checks.yml`: ruff exits 1 for
violations and 2 for a run that broke (bad config, unreadable file). Only 1 is a
verdict about the code.

## Log

- Base verified; branch cut from the trunk **ref**, not the 847-behind worktree HEAD.
- Baseline suite: `1 failed, 5197 passed, 10 skipped, 512 deselected` — the one
  allowlisted failure, `test_a_rejected_return_still_opens_no_work_item`.
- Lint fixed (14 → 0). Suite re-run: unchanged.
- Formatted (94 → 0). Suite re-run: unchanged. `known_test_failures.json` needs no
  edit — the set of failing tests did not move.
- Gate wired into `checks.yml` as `backend-static`, then widened to the root
  script paths once the quality script showed the surface was larger.
- Gate proven to fail on a *new* violation, then reverted (below).
- **RV round 1: `CHANGES_REQUIRED`, one finding (F-1), against the record and
  not the code** (`.plan/reviews/CI-LINT-1.md`). The unmerged-branch count was
  five and should have been six; `feat/live-harness-registration` touches two
  reformatted files. Ledger corrected above — the number is stated as six, the
  intersecting branch is named, and the conclusion is re-grounded on three
  content checks rather than on a branch count taken at one instant. No code
  changed; the gate and the suite are untouched by this correction.

## Proof the gate fails on a new violation

Run by replicating each step's shell logic verbatim against the pinned
`ruff 0.15.21`, so the exit codes below are the ones the GitHub steps produce.

| Probe | `ruff check` | `ruff format --check` |
| --- | --- | --- |
| clean merge candidate | 0 GREEN | 0 GREEN |
| new file, unused `os` import | **1 RED** | 0 GREEN |
| new file, bad spacing only | 0 GREEN | **1 RED** |
| after revert | 0 GREEN | 0 GREEN |

The two middle rows are the point: each violation reds **only** its own step.
That is the decomposition earning its keep — a single combined step would have
reported one and hidden the other. The root-script step was probed the same way
(`check=1 format=1` on a file violating both) and, because it collects both
exit codes before failing, it reported both rather than stopping at the first.

Exit-code discrimination checked rather than assumed: `ruff check` with an
unparseable config exits **2**, which the step reports as `::error::` "failed to
run, not the code" instead of as a verdict.

**One honest limitation.** A *missing path* makes ruff exit 1, not 2 — so a
mistyped path in this workflow would read as a violation rather than as a broken
run. The discrimination protects against a broken config, not a typo'd path.
Recorded rather than papered over.

## Reported, not acted on

**`mypy` is the next instance of the same pattern.** It is pinned in
`backend/pyproject.toml`, configured `strict = true`, and run by
`scripts/linux/03_run_backend_quality.sh` and `scripts/dev/run_changed_gate.py`
— neither of which CI invokes. No workflow runs mypy. It was left alone because
this track's scope is lint and a strict-mypy debt is a different size of
question, but by rule 13 it is a guard with no gate and it should get its own
dispatch.

Related: `scripts/linux/03_run_backend_quality.sh` also runs `poetry check`
(lockfile against pyproject) and `pytest scripts/tests`, neither gated. The
script as a whole is a guard nothing runs.
