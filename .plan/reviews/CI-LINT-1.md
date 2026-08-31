# RV review — CI-LINT round 1

**Branch** `feat/ci-backend-lint` **head** `a9165b76d41205863cb882d3c0775361459b574c`
**Trunk** `refactor/unified-return-platform` @ `63744f2a`
**Verdict: CHANGES_REQUIRED** — one finding (F-1). Everything else this branch claims
reproduced, including the parts most worth doubting.

---

## 0. Base verification (contracts §3) — PASS

`git merge-base` of branch and trunk is `73bd79aa27d2230ccc42d77f6b3dcd29c799242c`,
which the ledger names as the base. `git merge-base --is-ancestor 73bd79aa
refactor/unified-return-platform` → **true**. Genuine ancestor, not a fabricated sha.

Trunk has advanced **25 commits** since that base (ACC phase 2 and its merge). That is
a stale base, so I checked the consequence rather than assuming it:

- `git merge-tree --write-tree` of trunk × branch → **0 conflicts**.
- On the materialised merge, from `backend/`: `ruff check .` → *All checks passed*;
  `ruff format --check .` → *1159 files already formatted*; root script paths → clean
  on both. **The gate this branch adds is green on the merge, not merely on the branch.**
- Trunk's 25 commits added 12 backend files, all already format-clean, and reintroduced
  none of the 14 errors.

Stale base, harmless here, verified rather than assumed.

---

## 1. The counts, re-measured independently — CONFIRMED, every figure

Measured at `73bd79aa` in a clean detached worktree with the pinned
`ruff 0.15.21` (`backend/.venv/Scripts/ruff.exe`; matches `backend/pyproject.toml`
`[tool.poetry.group.dev.dependencies] ruff = "0.15.21"` and `backend/poetry.lock:1992`).

| Measurement | Ledger | Mine |
| --- | --- | --- |
| `ruff check .` errors | 14 | **14** |
| files carrying them | 6 | **6** |
| auto-fixable | 7 | **7** |
| `ruff format --check .` | 94 (1053 clean) | **94 (1053 clean)** |
| files in **both** sets | 2 | **2** |
| root paths — errors | 0 | **0** |
| root paths — unformatted | 2 (of 3 paths) | **2** |
| **repo totals** | 14 errors / 96 unformatted | **14 / 96** |

Rule breakdown also reproduces exactly: `I001` ×4 files, `B904` ×6 (all
`api/shipment_console.py`), `F401` ×2 (`tests/test_shipment_tracking.py`),
`RUF022` ×1, `RUF059` ×1 = 14.

The two overlapping files are `src/return_platform/api/shipment_console.py` and
`tests/test_shipment_tracking.py`.

### The disjoint-populations claim — CONFIRMED, with one precision

The branch is right and the dispatch was wrong. `ruff check` and `ruff format --check`
are separate commands answering separate questions, and their **violation populations
are near-disjoint**: 6 files vs 94, overlap 2 out of 98. "15 errors" and "85 unformatted
files" were never the same objects, and a reviewer who lets that framing stand repeats
the original error.

One precision the branch's own wording gets right and the dispatch's does not: the two
commands do **not** select different *file sets*. `ruff check . --show-files` enumerates
1148 files; `ruff format --check .` accounts for 1147. They scan the same universe and
disagree about which files in it are wrong. That is a stronger statement than "different
file sets", not a weaker one, and the ledger says "different populations", which is
accurate.

---

## 2. The zero-collision measurement — **FALSIFIED as it now stands** → F-1

### F-1 — the "five unmerged branches, zero collisions" measurement omits a sixth branch, and that branch touches two of the reformatted files

**File** `.plan/tracks/CILINT.ledger.md`, section *"Collision with in-flight work —
measured, not assumed"* (added in `9b665a98`).
**Rule** RV brief — self-description accuracy; a stated measurement that does not
reproduce is a finding. Precedent on this run: `c32371fe` ("ACC CR on four
self-description findings").
**Not one of the twelve blocking rules.** It blocks PASS only because PASS requires
zero *unresolved* findings.

The ledger states:

> Five branches are unmerged into trunk (`feat/acc-scenarios`,
> `feat/teams-bots-windows-first`, `task/teams-gateway`,
> `task/teams-platform-integration`, `task/teams-rma-saga`).
> Files they touch that also appear in the 94 unformatted set: **0**.

`git branch --no-merged 73bd79aa` today returns **six** in-flight branches, not five.
The omitted one is **`feat/live-harness-registration`** (tip `00471116`, worktree
`C:/hn`). Excluding `rv-calibration/seeded-hardcoding` (declared never-merged fixture)
and my own review worktree branch is correct; excluding this one is not.

Measured against the branch's own 100-file affected set:

```
feat/acc-scenarios                overlap = 0
feat/teams-bots-windows-first     overlap = 0
task/teams-gateway                overlap = 0
task/teams-platform-integration   overlap = 0
task/teams-rma-saga               overlap = 0
feat/live-harness-registration    overlap = 2
    backend/tests/test_return_case_policy_gate_real_infra.py
    backend/tests/test_return_case_workflow_real_infra.py
```

The five named branches do check out at zero. The stated *conclusion* — that in-flight
work collides with nothing — does not.

**Why it is a small finding and not a large one.** I pushed on the consequence rather
than stopping at the arithmetic, and the practical impact is nil:

- **Timing.** At `9b665a98` (11:47:17) `feat/live-harness-registration` still pointed at
  `7a898cf9`, which *is* an ancestor of `73bd79aa`, so `git branch --no-merged` would not
  have listed it. Its own commit landed at 11:47:51 — **34 seconds later**. The
  measurement was correct when taken. It went stale before the ledger was closed at
  12:03:18, and was not re-taken.
- **No conflict.** The two branches' hunks in those files are in different regions, and
  `git merge-tree` of the branch into trunk is conflict-free.
- **No red gate.** `ruff format --diff` on the live-harness tree shows the *only*
  unformatted region in both files is the exact `draft_support_request` signature hunk
  CILINT already fixed. That branch's own additions are format-clean and pass
  `ruff check`. When it takes this merge, the hunk resolves and the gate stays green.

**Why it still matters.** This measurement is the load-bearing evidence for choosing
option 1 over option 2, and it is the one claim the ledger explicitly offers as
falsifiable. A record that says "zero" where the answer is now "two, both harmless"
teaches the next reader that the enumeration is complete when it is not — and the
honest correction *strengthens* the option-1 case rather than weakening it.

**Fix.** One paragraph in `.plan/tracks/CILINT.ledger.md`: name the sixth branch, state
the two files, state the 34-second race that made the original count correct at the
time, and state the verified nil impact. No code change. No re-measurement of anything
else is owed.

---

## 3. The gate actually reds, per step — CONFIRMED, both directions

Reproduced on the materialised trunk×branch merge, replicating each step's shell logic
against the pinned ruff.

| Probe | `ruff check .` | `ruff format --check .` |
| --- | --- | --- |
| merged tree, untouched | 0 GREEN | 0 GREEN |
| new file, unused `import os` | **1 RED** | 0 GREEN |
| new file, single-quoted string only | 0 GREEN | **1 RED** |
| after removal | 0 GREEN | 0 GREEN |

**Each violation reds only its own step — confirmed.** This is the decomposition
earning its keep, exactly as claimed: a combined `ruff check . && ruff format --check .`
would have reported the first and hidden the second.

One note on the ledger's probe labelling, offered as precision and **not** as a finding.
Its row reads *"new file, bad spacing only → check GREEN, format RED"*. A file whose
only defect is spacing does not generally stay green under `ruff check`: with
`select = ["E", ...]`, `x  =  1` trips `E221`/`E222` and reds *both* steps — I measured
that. The format-only direction is real, but the cleanest demonstration of it is quote
style or line-splitting, not spacing. The claim is true; that row's example is a poor
witness for it.

---

## 4. Exit-code discrimination — CONFIRMED, and the recorded limitation is conservative

Both halves check out.

**Broken config → 2, reported as "failed to run".** Verified two ways: a TOML syntax
error (duplicate `[tool.ruff.lint]`) and a semantic error (`select = ["ZZZ999"]`, which
prints `Unknown rule selector`). Both give `ruff check` → **2** and
`ruff format --check` → **2**. Every step's `if [ "$status" -gt 1 ]` arm fires and emits
`::error::… failed to run, not the code`, then exits with the real status. Correct.

**Missing path → 1, misreads as a violation.** Verified. `ruff check
../scripts/does_not_exist.py` prints `E902 The system cannot find the file specified`
and exits **1**. The limitation is exactly as recorded, and recording it was right.

**It is better than recorded, in the one place it can occur.** The two `backend`-scoped
steps pass `.`, which cannot be typo'd out of existence. The only step carrying literal
paths is *"ruff over the root script paths"* — and there `ruff format --check` on a
missing file **or a missing directory** exits **2**, not 1. Because that step collects
both codes and trips its loop on any status > 1, a mistyped path fails the step as
`::error::ruff exited 2 -- it failed to run, not the code`. The step's own design
happens to close the gap the ledger honestly admitted to.

The limitation as recorded is real, and the branch understated its own protection. That
is the right direction for a self-assessment to be wrong in.

---

## 5. Rule 13 — the widening genuinely covers it, and nothing new goes ungated

`scripts/linux/03_run_backend_quality.sh` issues **four** ruff invocations. The
`backend-static` job reproduces all four, with the same working directory and the same
paths:

| Script | Workflow |
| --- | --- |
| `cd backend; ruff check .` | step *ruff check*, `working-directory: backend` |
| `cd backend; ruff format --check .` | step *ruff format --check*, `working-directory: backend` |
| `ruff check` ×3 root paths | step *ruff over the root script paths*, after `cd backend` |
| `ruff format --check` ×3 root paths | same step |

The `cd backend` in the third step is **load-bearing, not decoration**, and I verified
it: there is no root `pyproject.toml`, and ruff's per-file config discovery does not
find one walking up from `scripts/`. Run from the repo root, `ruff format --check` over
those paths reports **1** file to reformat (default line-length 88); run from `backend/`,
it reports **2** (line-length 100). The step matches the script because it cds first.
Coverage is genuine, not apparent.

**The script's other five commands are not covered, and the ledger says so** — `poetry
check`, `mypy src tests`, `mypy` over the root scripts, `pytest`, and `pytest
scripts/tests`. The *"Reported, not acted on"* section names mypy as the next instance
of the same pattern and flags `poetry check` and `pytest scripts/tests` alongside it.
Scoping to ruff and naming the remainder is the honest posture; claiming the script was
fully gated would have been a finding. It does not.

**Rule 13 turned on the branch itself.** What it adds: one CI job, three steps, and 100
mechanically-fixed files. The job is in `checks.yml` under the existing
`on: push/pull_request` triggers with no `needs:` gating it out, so it runs. It
introduces no guard, script, assertion, budget, or config that nothing invokes. Nothing
it adds is a comment.

---

## 6. The fixes are honest — CONFIRMED, structurally

- **All six `B904`s chain.** `api/shipment_console.py` lines 140, 150, 190, 195, 204,
  240 (base numbering) now read `raise HTTPException(...) from unconfigured | from
  missing | from rejected | from sync_failed`. The `KeyError` arm was rewritten to
  *bind* its exception (`except KeyError as missing`) specifically so it could chain.
  **Zero occurrences of `from None` anywhere in the diff.**
- **No suppression of any kind.** `git diff 73bd79aa a9165b76 | grep -E
  "^\+.*(noqa|fmt: ?off|fmt: ?skip|type: ?ignore|# pragma)"` → **empty**.
- **The linter's own config is untouched.** No `pyproject.toml`, `ruff.toml`,
  `.ruff.toml`, `setup.cfg` or `.ini` appears in the diff. `select` still carries all
  eight rule families; `ignore` is still `["E501", "RUF100"]`. Nothing was made to pass
  by narrowing the question.
- **Both `F401` removals are genuinely dead.** `datetime.UTC` and `datetime.datetime`
  in `backend/tests/test_shipment_tracking.py:10`. Grepped the base file for `UTC` and
  `datetime` across *all* text, string literals included: the import line is the sole
  occurrence of either. Nothing constructs source from a template naming them.
- **`RUF059` is safe.** `collection` → `_collection` at what is now line 246; that name
  is not read anywhere after the unpack.
- **The 96 formatted files are provably mechanical, not asserted to be.** Two
  independent checks:
  1. **Byte-level.** I checked out the tree at `a4a44a88` (lint fixes applied, nothing
     formatted), ran `ruff format .` from `backend/` and `ruff format <root paths>` from
     `backend/`, and diffed the result against `96c2ec63`. **The only difference is
     `.github/workflows/checks.yml`**, which those commits add. All 96 Python files are
     byte-identical to raw tool output. Nothing was hand-edited under cover of a
     formatting commit.
  2. **AST-level.** Parsed all 96 files before and after the two formatting commits and
     compared `ast.dump`. **96 compared, 0 divergences.** Formatting changed no string
     literal, no argument, no control flow.
- **Import hygiene.** Net import-line changes across the whole branch are the two `F401`
  removals, one `from ... import assembly` folded into an existing parenthesised block
  (semantically identical), and pure reordering. **No new import of any frozen module**
  (`operations/associate_flow`, `agents/order_discovery`, `api/associate_returns`,
  `api/return_agents`) — grepped, empty.
- **Commit hygiene as claimed.** Seven commits, file sets disjoint: `a4a44a88` lint
  fixes (6 files) → `4f8fbbb1` format (94) → `2787be4b` workflow → `96c2ec63` root
  format (2) → `f0574901` workflow widening → ledger. Neither fix commit touches
  `checks.yml`; the wiring commits touch no source.

---

## 7. Suite unchanged and the allowlist untouched — CONFIRMED

**Allowlist.** `scripts/ci/known_test_failures.json` blob sha is
`cb4d565ef4824d4eacc2edd380e296c711d60670` at **base, at branch head, and at current
trunk**. Byte-identical to all three. It still carries exactly one backend entry
(`tests.test_cumulative_support_outcomes::test_a_rejected_return_still_opens_no_work_item`)
and two frontend ones. **Not widened, not touched.**

**Suite.** Rather than take the ledger's figure, I ran the backend suite on the
materialised trunk×branch merge:

```
1 failed, 5232 passed, 11 skipped, 514 deselected in 275.25s
FAILED tests/test_cumulative_support_outcomes.py::test_a_rejected_return_still_opens_no_work_item
```

The absolute counts differ from the ledger's `1 failed, 5197 passed, 10 skipped, 512
deselected` because trunk gained 25 commits of tests since the base the ledger measured
at. **The invariant that matters holds exactly: the set of failing tests is unchanged,
and it is the single allowlisted failure.** No test was weakened, skipped, xfailed or
deleted; the diff contains no assertion changes (96 of 100 files are AST-identical, and
the other four changed only imports, an `__all__` order, a raise-chain and one unused
binding).

---

## Reported, not findings against this branch

**`.env` makes the pre-existing `backend` job unrunnable on a fresh runner.**
`backend/tests/conftest.py:29-30` raises `RuntimeError: Required repository environment
file was not found` at `pytest_configure` when repo-root `.env` is absent. `.env` is
untracked (only `.env.example` is committed), so after `actions/checkout@v4` the
`backend` job's pytest exits **3**, the job's own `status -gt 1` arm fires, and it
reports `::error::pytest exited 3 -- the run failed, not the tests`. I hit this
verbatim on my first suite run in a clean worktree.

This is **not** this branch's defect — the `backend` job is already on trunk, and
`backend-static` needs no `.env` because ruff imports nothing. But it means this
workflow has in all likelihood never completed on a runner, and every "green on this
commit" in the file (this branch's included) rests on local replication of the step
shell rather than on an observed run. Worth its own dispatch, alongside the mypy one
the ledger already raises.

**`.plan/merge.md`'s CI section is stale.** It still describes `vite build` and
`check:bundle` as "gated by nothing until the budget is settled" and the bundle budget
as an open user decision — both settled on trunk by R1/R4, and `checks.yml`'s own
comments now say so. It also does not mention `backend-static`. Pre-existing drift, not
introduced here; noting it so the record gets corrected by whoever owns that file.

---

## Verdict

**CHANGES_REQUIRED.** One finding, **F-1**, and it is a one-paragraph correction to
`.plan/tracks/CILINT.ledger.md` with no code change.

I want the record to be plain about proportion: this is the most thoroughly
self-verified branch I have reviewed on this run. Every count reproduced to the digit.
The formatting commits are byte-identical to raw tool output and AST-identical across
96 files — semantic neutrality proven, not asserted. The `B904` fixes took the harder
correct path over the easier one that would have satisfied the same linter. The
allowlist is untouched. The gate reds in both directions, per step, and stays green on
the merge into a trunk 25 commits ahead of its base. The one limitation the branch
recorded against itself turned out to be *more* protective than it claimed. And the
rule-13 widening is real coverage, verified down to the working directory that makes it
resolve to the right config.

F-1 exists because the branch offered a falsifiable claim, I falsified it, and PASS
requires zero unresolved findings rather than zero blocking ones. The claim was true
when it was measured and went stale 34 seconds later. Correct the paragraph and this is
a PASS.
