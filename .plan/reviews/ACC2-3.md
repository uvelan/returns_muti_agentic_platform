# RV review — ACC phase 2, the acceptance slice, round 3

- **Branch:** `feat/acc-scenarios`, head `9af7c74b` — *(ACC) step:14 F5: the Commands block that was written from memory*
- **Base:** trunk merged at `84d5f2e0` / `a56e9af5`. **Complete updated diff re-reviewed.** ACC's own commit since round 2 is `9af7c74b`: **2 files**, `.plan/tracks/ACC.ledger.md` (+61/−5) and `backend/tests/harness/posix_signal_proof.py` (−1).
- **Previous rounds:** `ACC2-1.md` (`cb972fcb`) — four findings; `ACC2-2.md` (`73bd79aa`) — four withdrawn, F5 raised.
- **Reviewer:** RV — Date: 2026-08-31

## Verdict: PASS

**F5 is withdrawn.** All five figures verify against my own runs, the scoping is
honest by a test stronger than plausibility, the removed import was genuinely
dead and the proof still passes without it, and the original text is preserved
verbatim. Zero unresolved findings.

Scope clean. ACC's commit touches one ledger file and deletes one import line.
`.plan/merge.md` and `.plan/reviews/ACC2-2.md` in the range arrived through the
trunk merge `a56e9af5` and are not this slice's.
`scripts/ci/known_test_failures.json` is **byte-identical to trunk** — no diff
against `refactor/unified-return-platform`, one backend entry, the allowlisted
failure. Nothing was smuggled onto the list.

## The five figures, measured

I ran all five rather than the two asked for.

| claim | my run | |
| --- | --- | --- |
| `pytest tests/acceptance -q` | `35 passed, 1 skipped, 2 deselected in 13.31s` | ✓ |
| `-m live_infra` | `2/38 tests collected (36 deselected)` | ✓ |
| `pytest tests -q` | `5230 passed, 3 failed, 11 skipped, 514 deselected` | ✓ |
| `ruff check tests/acceptance tests/harness` | `All checks passed!` | ✓ |
| `ruff format --check` same paths | `19 files already formatted` | ✓ |

The full-suite line reconciles as it has all three rounds: two of my three
failures are the worktree-path-sensitive pair
(`test_runner_default_dotenv_path_is_repository_root`,
`test_no_module_under_src_writes_a_fact_name_as_a_string_literal`) which pass on
the same source in the canonical checkout, so the canonical figure is **5232
passed / 1 failed / 11 skipped / 514 deselected** — 5758 total, unchanged from
round 2, and the one failure is the allowlisted
`test_a_rejected_return_still_opens_no_work_item`. **The import removal cost
nothing.**

## The scoping — honest, and provably so

The interesting question is whether `ruff check tests/acceptance tests/harness`
is a boundary drawn at ownership or a boundary drawn at passing. It is drawn at
ownership, and there is a decisive test for that rather than a judgement call:

**The file that carried the error is inside the claimed scope.**
`tests/harness/posix_signal_proof.py` is in `tests/harness`. Had ACC been
narrowing to a claim it could pass, that path is the first thing it would have
dropped. Instead it kept the path and fixed the error. A dishonest scope excludes
the failure; this one includes it.

Confirmed on the other side too: `ruff check .` over the whole backend still
reports **14 errors**, across `api/shipment_console.py`,
`operations/template_formatters.py`, `tests/operations/test_case_projection.py`,
`tests/operations/test_review_aggregate.py`, `tests/test_shipment_tracking.py`
and `tests/test_stage4_schema_and_seed_contracts.py` — **not one of them a file
this slice owns**, and 15 before ACC removed its own. The entry says so out loud
rather than eliding it (*"`ruff check` over the whole backend is **not** clean on
trunk … this entry says only what it checked"*) and routes it to the orchestrator
as the separate one-level-up finding it is. That is the opposite of hiding, and
the reasoning quoted at dispatch — *"writing an unscoped 'clean' would have been
the same finding a second time"* — is correct.

## The `F401` removal — the "dead import that isn't" shape, excluded empirically

`subprocess` appears three times in that file: the deleted module-level import,
and lines 84-85 **inside the generated `parent.py` string**, whose own first line
is `"import subprocess, sys, time\n"` — the child interpreter imports it for
itself. So the module-level import was dead in the strict sense.

Reasoning was not enough here, and ACC did not stop at it. Neither did I:

```
docker run --rm -v C:\w\a3\backend:/w -w /w python:3.13-slim \
    python tests/harness/posix_signal_proof.py
PASS  start_new_session puts the worker in its own process group
PASS  killpg(getpgid(pid), SIGTERM) reaches the grandchild through the session
PASS  stop() gives the worker its SIGTERM handler (the drain path)
PASS  kill() does not run the worker's SIGTERM handler (the crash path)
all four links proved                                            → exit 0
```

**Check 2 is the one that matters** and it passes: it is the check that spawns
the generated parent, which `subprocess.Popen`s a grandchild that heartbeats to a
file. If the removal had reached the child scripts, that check — and only that
check — would have gone red for want of a grandchild. It did not. The shape is
excluded by observation, not by argument.

## Verbatim quotation, and the arithmetic

**Confirmed verbatim.** The step:14 entry quotes the original block character for
character against my round-2 transcription:

> **Commands:** acceptance default → **34 passed, 2 deselected**; acceptance live
> → **2 passed**; full backend suite → see below. `ruff check` / `ruff format`
> clean.

And the in-place text at step:13 is not a silent overwrite either — it says the
block was corrected at step:14 and that the original is quoted there, so a reader
arriving at step:13 is pointed forward rather than left with a figure whose
history is gone. Same reasoning as the item-18 exchange, applied consistently.

**Arithmetic carried in correctly:** `5197 + 35 = 5232` (Windows) and
`5197 + 36 = 5233` (Linux, where the POSIX gate runs instead of skipping), with
`5198 + 34` recorded as having reached the right total by two compensating
errors, and the skip story (10 → 11 here, 10 on the pipeline) noted as having
been right all along. That matches my derivation in every term.

## Reviewer's read — does the remedy follow from the diagnosis?

Asked for as a reviewer rather than a checker, so: **yes, and in this branch's
own idiom — but the reflection does outrun the change at one point, and it is
worth naming which.**

**Where it follows.** The diagnosis is that a prose claim asserting a green
nobody ran is the same defect as a guard nobody invokes. The remedy that follows
from that is not "be more careful"; it is to make the claim *addressable*. The
entry does exactly that: three unfalsifiable sentences become a table whose left
column is a **literal, pasteable command** and whose right column is that
command's output. Anyone can now falsify the whole block in five pastes — I did.
That is the same move this slice already made in code when it replaced
`"OUTBOUND" not in named` with `assert sites == {…}`: a negative assertion nobody
can aim at, swapped for an exact one that fails loudly. The prose analogy is not
decoration; it produced the same fix shape the tests got.

And three concrete things changed, none of them prose: a dead import deleted from
a shipped file and the deletion re-verified by running it; the ruff claim scoped
to what was executed with the out-of-scope state stated rather than elided; the
superseded text preserved so the correction is auditable. Prose insight is not a
fix, and this is not only prose.

**Where it outruns.** Nothing structurally prevents the *next* Commands block
being written from memory. Taken all the way down, "same defect as a guard
nothing invokes" would imply the same remedy F1 got — build the gate. ACC did not,
and I want to be explicit that **it was right not to**: the only such gate is a
`backend/tests/` module reading `.plan/` and re-running suites, which this slice
already ruled out for a good reason it recorded at item 26 (*"a `backend/tests/`
module parsing `.plan/reviews/` would make the application suite fail on a
planning document"*), and which would be circular besides — a record checking
itself. So the residue is inherent to the domain rather than a shortfall in the
fix. The honest ceiling for a written record is *reproducibility*, and the table
reaches it. Naming that ceiling would have cost one line and would have made the
diagnosis complete rather than slightly ahead of its remedy; that it is unnamed
is an observation, not a finding.

**One stronger option existed, and it is taste, not a finding:** pasting each
command's terminal output verbatim rather than hand-transcribing it into a table
cell. A transcription can drift from what ran, which is a smaller cousin of the
defect being fixed. Every figure in the table is correct, so nothing turns on it
here. Recorded so it is not mistaken for something I overlooked.

## Withdrawn

- **F5** — withdrawn. All three claims measured and correct, the scope drawn at
  ownership rather than at passing, the dead import removed without touching the
  child scripts, and the original text quoted verbatim.

## Closing

Three rounds, five findings, five fixes, and the slice improved on the ask in
four of them. Round 1's findings were about the distance between what the branch
did and what it said it did; the last of them landed on the branch's own ledger,
and it was fixed the same way the first one was — by building something a
stranger can run.

The substance was never in doubt: 26 injections, seven instrument defects found
by ACC in its own work, three misses recorded as limits rather than smoothed
over, and a stale live-infra probe reported rather than repaired, correctly. What
took three rounds was the record catching up to the work. It has.

**Merge permitted.**
