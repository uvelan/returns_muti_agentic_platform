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

Five branches are unmerged into trunk (`feat/acc-scenarios`,
`feat/teams-bots-windows-first`, `task/teams-gateway`,
`task/teams-platform-integration`, `task/teams-rma-saga`).

Files they touch that also appear in the 94 unformatted set: **0**.
Files they touch that also appear in the 6 lint-error set: **0**.

They are overwhelmingly *adding* files, and new files are already format-clean.

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
