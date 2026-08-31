# RV review — ACC phase 2, the acceptance slice, round 2

- **Branch:** `feat/acc-scenarios`, head `49597554` — *(ACC) step:14 the gating figure, collected rather than remembered*
- **Base:** `cb972fcb` (merge-base with trunk — my round-1 verdict). **Complete updated diff re-reviewed:** `git diff cb972fcb..49597554` — 22 files, **+4975/−0** slice-side. ACC's own two commits (`3a33e074`, `49597554`) are `git diff e0d2174d..49597554` — 5 files, +289/−45.
- **Previous round:** `.plan/reviews/ACC2-1.md` (`cb972fcb`) — CHANGES_REQUIRED, four findings.
- **Reviewer:** RV — Date: 2026-08-31

## Verdict: CHANGES_REQUIRED

**All four round-1 findings are withdrawn.** Each was verified fixed by running
the thing rather than reading the fix, and F1 came back better than I asked for.
Everything the round asked me to judge holds: the gate is real and non-vacuous,
the correction to my own understatement is accurate in both directions and does
not over-correct, the R2/R3 scoping claim is true at the source, and the step:14
self-correction is genuine.

**One new finding, and it is small.** It is in the six lines of the ledger that
step:14's discipline did not reach: the "Commands" block of step:13 states three
results, and all three are wrong or absent. Three commands and one deleted
import resolve it. I raise it rather than waive it because round 1's F2 was the
same defect — a figure the record states about itself that the tool contradicts —
and waiving it here would retire a standard one round after setting it.

Scope clean. ACC's two commits touch `.plan/acceptance/`, `.plan/tracks/` and
`backend/tests/acceptance/` and nothing else. The `src/` and `frontend/` files in
the range arrived through the trunk merge `e0d2174d` (R1/R4, R2/R3 — already
RV-PASSed at `a5610b45`) and are not this slice's. **No production edit crept
in.**

---

## 1. F1's first half — the gate is real, and I proved it non-vacuous myself

`tests/acceptance/test_the_posix_signal_proof_is_gated.py` runs the script as a
subprocess and asserts four things. I ran all three states.

**Clean, in the container the script was written for:**

```
docker run --rm -v C:\w\a2\backend:/w -w /w python:3.13-slim \
    python tests/harness/posix_signal_proof.py
python 3.13.14 on linux, pid 1
PASS  start_new_session puts the worker in its own process group
PASS  killpg(getpgid(pid), SIGTERM) reaches the grandchild through the session
PASS  stop() gives the worker its SIGTERM handler (the drain path)
PASS  kill() does not run the worker's SIGTERM handler (the crash path)
all four links proved                                            → exit 0
```

**Under the injection, applied by me** — `start_new_session: True` → `{}` at
`tests/harness/chaos_restart.py:185`:

```
FAIL  start_new_session puts the worker in its own process group -- getpgid(7) == 1; …
FAIL  killpg(…) reaches the grandchild through the session -- the worker shares
      the runner's process group (1) -- killpg here signals the suite itself, so any
      observed death is collateral rather than the session doing its job
PASS  stop() gives the worker its SIGTERM handler (the drain path)
PASS  kill() does not run the worker's SIGTERM handler (the crash path)
2 FAILED                                                          → exit 1
```

**Every one of the module's assertions reds:** `returncode == 0` fails, the
`PASS  ` count is 2 not 4, and `"all four links proved"` is absent. The gate is
not decoration. Injection reverted; `git status` clean.

Note the asymmetry, which is the second-order evidence: checks 3 and 4 stay
green, which is what removing *only* the session should do and not what a
collapsed import or a broken file would do. That is `merge.md`'s newest failure
shape, satisfied without being asked.

**Is counting lines sufficient?** Yes, and the stated reason is the right one.
`main()` already returns 1 when `_FAILURES` is non-empty, so `returncode == 0`
covers a check that *ran and failed*. What it does not cover is a check that
stopped being run — an early return, a call dropped from `main()`, a refactor
that folds two checks into one. The count is the only assertion that catches
that, and `"all four links proved"` catches a truncated run whose checks all
happened to pass. Three assertions, three distinct failure modes, no overlap.
`returncode != 2` is separately meaningful: the script's own `main()` returns 2
on `os.name == "nt"`, so a POSIX runner reaching that would be a bug in the
proof rather than a passing check, and the module says so.

**Is keeping it a script a sound trade or a rationalisation?** Sound, and I
checked the premise rather than accepting it. `posix_signal_proof.py` imports
`os, signal, subprocess, sys, tempfile, time, pathlib` plus
`tests.harness.chaos_restart`, and `chaos_restart` itself imports **only** the
standard library (`asyncio, os, signal, subprocess, sys, time`, plus `typing`
and friends). `tests/__init__.py` is empty and `tests/harness/__init__.py`
imports nothing. So the bare-container property is real, not asserted — the
`python:3.13-slim` run above is its own proof, with no install step. Porting the
four checks into pytest would pull `tests/conftest.py`'s `return_platform`
import into any container run and lose it. One implementation, two callers, is
the correct resolution.

One detail worth crediting: `test_the_proof_script_is_where_this_module_expects_it`
is **not** skipped on Windows. Without it the module would be one rename away
from a file that silently passes on the dev machine and errors in the pipeline —
and the dev machine is the half a developer sees. That is the failure mode of
the fix, anticipated.

**Verified on Windows,** as claimed: `1 passed, 1 skipped`, with the reason
printed in full and pointing at the container command.

**F1, first half: withdrawn.**

## 2. F1's second half — the correction is accurate, and does not over-correct

I checked this in both directions, because an over-correction here would be the
same defect with the sign flipped.

- `.github/workflows/checks.yml:56-60` — `on: push: branches: ["**"]`, plus
  `pull_request` and `workflow_dispatch`. **Literally every push**, on every
  branch. The phrase "executes on every push" is exact, not rounded up.
- Line 90 — `runs-on: ubuntu-latest`. Line 121 — `pytest tests`.
- `tests/harness/test_chaos_restart.py` carries **no** `live_infra` marker, and
  `test_stop_lets_the_worker_handle_its_signal_and_kill_does_not` is guarded only
  by `skipif(os.name == "nt")`. So it is selected and executed by that job.

The record now says the pin "executes on every push", that the substitution is
"a convenience for dev machines, not a stand-in for absent coverage", and that
the earlier "it has never run" was true of the workstation and false of the
pipeline. **All three are accurate.** Nothing claims the proof is now redundant,
nothing claims it was always gated, and the guard-with-no-gate half is stated
first and plainly (*"That is RV rule 13 exactly, on the branch that made rule 13
its subject"*). Neither direction is overstated.

**The sharper point is correct and I adopt it.** A `skipif(os.name == "nt")` is
not the "skipped on the platform that runs it" shape when the platform that runs
it is Linux; that criticism belongs to a guard whose *only* runner skips it. My
round-1 framing carried the ACC-1-era reading forward into a repository that had
since acquired CI, and did not re-derive it. The distinction between "the
workstation" and "the pipeline" is the one I failed to draw.

**STATUS.md's rule-13 audit now covers every guard the branch adds**, not only
`tests/acceptance/`. The heading changed from "APPLIED TO THE GATE ITSELF" to
"— and to this branch", the scope is stated explicitly (*"every guard added, not
only the ones in `tests/acceptance/`"*), and the POSIX proof is named in the
enumeration. The 512 → 514 deselected figure was corrected in passing and matches
my measurement.

**F1, second half: withdrawn.**

## 3. The R2/R3 interaction — the scoping claim is true at the source

Checked at the source rather than inferred from a green suite, since a wrong
answer here would make the green meaningless.

- `operations/review_aggregate.py:199` —
  `EMPTY_REPLY_BODY_GAP_REASON: Final = "SUPPORT_REPLY_BODY_EMPTY"`.
- `operations/review_aggregate.py:773` — the raise is guarded by
  `if review["reviewKind"] == ReviewKind.SUPPORT_REPLY.value and not _has_reply_body(...)`.
  **The refusal cannot fire for any other review kind.**
- `operations/support_template_gate.py:568` — `SupportTemplateGateService` writes
  `review_kind=ReviewKind.TEMPLATE`, and it is the **only** `ReviewKind` this
  service ever names.

Every ACC review-gate scenario drives that service. The two are orthogonal by
construction, not by coincidence, and the green suite is therefore meaningful.
Confirmed empirically as well: `pytest tests/acceptance` → **35 passed, 1
skipped, 2 deselected**.

## 4. F2, F3, F4

**F2 — withdrawn, and the judgement call is right.** "Rulings owed" is now
"Rulings owed — none. Item 18's was made." The heading resolves it for a reader
who only scans headings, which is where the stale version did its damage.
**Keeping the exchange was the correct call**, and I want to be explicit that I
would have accepted deletion but prefer this: the record's value is that a future
reader can see what was asked, what came back, and that the asking was proper —
*"ACC does not get to pick"* is the sentence that produced AMENDMENT-9, and
deleting it would leave the amendment looking unprompted. The entry also names
why it went stale (step:12 rewrote the rows without reconciling the section),
which is the part that stops it recurring.

**F3 — withdrawn, and it does more than I asked.** The docstring no longer claims
the equality guards a reviewer's window restarting. It now states the limit
*where the next reader meets it*: that the obvious reading is wrong for a worker
kill, that INJ-15a proved it, that a kill is not a `continue_as_new`, that the
resumed field guards continuation, **and what would be needed to guard it** ("a
scenario that wants to guard it must continue-as-new rather than kill"). Then it
states what is genuinely the platform's and what INJ-15b reds. *"Claim that, and
not more."* That is the sentence I was looking for.

**F4 — withdrawn, and the replacement is better than deletion.**
`_GateProbe.reached()` and `self._reached` are gone, along with the `set()` in
`_record`. In their place stands a comment explaining why there is no such
helper — that it fires when an activity *starts*, that everything a caller wants
exists only when it *finishes*, and that it caused both races this module had to
close. *"It is removed rather than left unused: an unused primitive with an
inviting name is one autocomplete away from reintroducing the defect."* The next
author meets the reason instead of the tool, which is strictly better than
meeting nothing. `asyncio` remains used (lines 212, 690, 742, 753), so the
import is not orphaned.

## 5. Arithmetic — the totals verify exactly; the decomposition offered does not

Full default suite, this worktree:

```
3 failed, 5230 passed, 11 skipped, 514 deselected in 258.59s
```

Two of the three failures are the worktree-path-sensitive pair identified in
round 1 (`test_runner_default_dotenv_path_is_repository_root`,
`test_no_module_under_src_writes_a_fact_name_as_a_string_literal`), which pass on
the same source in the canonical checkout; the third is the allowlisted
`test_a_rejected_return_still_opens_no_work_item`. So the canonical figure is
**5232 passed / 1 failed / 11 skipped / 514 deselected** — 5758 total, matching
the claim in every term. Zero new failures.

**Gating, collected rather than remembered** — and I collected it too:

```
pytest tests/acceptance --collect-only -q   → 36/38 tests collected (2 deselected)
pytest tests/acceptance -m live_infra --collect-only
  → test_items_15_16_…::test_a_kill_mid_review_loses_neither_the_draft_nor_the_remaining_timeout
  → test_items_15_16_…::test_an_approval_after_the_restart_sends_exactly_one_message
pytest tests --collect-only -q                        → 5244/5758 (514 deselected)
pytest tests --ignore=tests/acceptance --collect-only  → 5208/5720 (512 deselected)
```

The two deselected are exactly the live review-gate module's, and that module
says so in its own docstring rather than being counted as coverage. The
acceptance directory contributes 36 selected of 38, +2 deselected — confirming
`36/38 (2 deselected)` precisely.

**The step:14 self-correction is real.** Step:13 added the POSIX gate module (2
tests) and left the prose reading `36 collected, 2 deselected` — the round-1
figure, which its own addition had moved. Step:14 corrected it to `36/38 tests
collected (2 deselected)`, which is what the collector prints. The pre-image is
visible in `git show 49597554`. Genuine, and the discipline is the one the four
findings were about.

**One correction to the decomposition, which the branch does not record and
should not adopt.** "trunk's 5198 + 34 acceptance tests" is off by one in both
terms and reaches the right total by two compensating errors. Measured: the
acceptance directory contributes **36 selected**, running as 35 passed + 1
skipped on Windows, so the split is **5197 + 35 = 5232** here — and **5197 + 36 =
5233 passed, 10 skipped, on Linux**, because CI executes the POSIX gate that this
workstation skips. The skip story is exactly right (10 → 11 *is* the new gate
skipping on Windows while CI runs it); the passed/added split is not. Worth
fixing before it is written down, and it is part of F5 that it has not been.

---

## Finding

### F5 — step:13's "Commands" block is the one place step:14's discipline did not reach

**File:** `.plan/tracks/ACC.ledger.md:1199-1201`.
**Rule:** the honesty of the record — this slice's product — and the standard
round-1 F2 set.

Six lines, three results, none of them right:

> **Commands:** acceptance default → **34 passed, 2 deselected**; acceptance live
> → **2 passed**; full backend suite → see below. `ruff check` / `ruff format`
> clean.

**(a) `acceptance default → 34 passed, 2 deselected` is stale.** It is the
round-1 figure. Step:13's own addition moved it: the suite is 38 tests, 36
selected, running as **35 passed + 1 skipped** on Windows and 36 passed on
Linux. This is the identical defect step:14 found and fixed in STATUS.md — a
count carried from memory across an addition that changed it — left standing in
the ledger entry that documents the fixes, one commit before the commit whose
message is *"what the branch says about itself has to be checked like anything
else."* In fairness the earlier line in the same entry (*"Both acceptance suites
re-run after the merge — 34 default, 2 live"*) is **correct**: that run happened
after the trunk merge and before the gate module existed. Only the closing
Commands block is wrong.

**(b) `full backend suite → see below` points at nothing.** Step:14 added no
ledger entry, so there is no "below", and **no full-suite figure for round 2
appears anywhere in `.plan/`** — I grepped. A promised number that is never
delivered is weaker than an absent one, because a reader stops looking. The
figure is `5232 passed, 1 failed, 11 skipped, 514 deselected`, and the
platform-dependent split above belongs with it.

**(c) `ruff check` / `ruff format` clean is false.** With the repository's own
pinned linter — `backend/pyproject.toml:66` and `poetry.lock:1993` both say
`ruff = 0.15.21`, which is what I ran:

```
tests\harness\posix_signal_proof.py:45:8: F401 [*] `subprocess` imported but unused
```

`subprocess` appears twice more in that file (lines 84-85) but **inside a
generated child-script string** — text, not code — so the diagnostic is correct.
It is ACC's file and the **only** ACC-owned lint error in the tree; the other
fourteen are pre-existing in files ACC does not own. `ruff format --check` on
ACC's twelve files reports "12 files already formatted", so that half of the
claim is true.

**The unused import is style and would not block on its own. The claim that
`ruff check` is clean is not style** — it is a statement about a verification
that was not performed, in the record whose reliability is this slice's entire
deliverable, made in the same paragraph as two other unperformed verifications.

**Fix:** re-run the three commands and record what they print; delete
`import subprocess` from `posix_signal_proof.py` (or, if it is wanted as
documentation of what the generated script uses, say so and mark it). Roughly
four lines and one deletion.

---

## Rule 13, one level up — for the orchestrator, not for this slice

While grepping for guards without gates I found one that is neither ACC's to fix
nor in its scope, and it is the reason F5(c) was available to find at all:

**CI runs no backend lint.** `.github/workflows/checks.yml` gates the frontend
with `npm run lint` (line 188) and the backend with `pytest tests` (line 121).
`ruff` is configured in `backend/pyproject.toml:90-94`, pinned in `poetry.lock`,
and **nothing in CI invokes it.** That is why 15 ruff errors and 85
would-be-reformatted files sit on trunk unremarked — `shipment_console.py` alone
carries six `B904`s. The repository defines what "checked" means in its own
tools, and this file's own opening comment says so; for the backend, half of
that definition is not executed.

This belongs to whoever owns `.github/`. **ACC should report it, not repair it** —
`.github/` is neither `backend/tests/` nor `.plan/` — which is how this slice has
correctly handled the SQL-Server port, the stale live-infra probe, and
`pin_routing_decision`. I raise it here because rule 13 obliges me to and because
it is the same pattern the branch has been documenting all round: the correct
mechanism exists and nothing runs it.

---

## Withdrawn

- **F1** (both halves) — withdrawn. Gate built, non-vacuity proved by my own
  injection, correction accurate in both directions, rule-13 audit widened to
  every guard the branch adds.
- **F2** — withdrawn. "Rulings owed — none", with the exchange kept. Right call.
- **F3** — withdrawn. INJ-15a's limit now sits where the next reader meets it.
- **F4** — withdrawn. Primitive deleted, reason left in its place.

## Closing

Four findings, four fixes, and three of them improved on what I asked for: F1
came back with a gate that counts rather than trusts, F3 with the remedy for the
gap as well as the gap, F4 with an explanation standing where the trap was. The
second half of F1 was a correction aimed at me and it was right; I have recorded
the distinction I failed to draw rather than softening it.

What remains is six lines that were written from memory in the entry describing
the fixes for writing things from memory. Correct them and this is a PASS; I
expect round 3 to be short.

Re-review will cover the complete updated diff, not only the changed lines.
