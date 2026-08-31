# HARNESS-2 — RV review

**Branch** `feat/live-harness-registration`
**Head** `2f1c0e50` — *"(harness) step:23 inconclusive, and step:21 misread its own headline number"*
**Base** `7a898cf9` (merge-base with trunk, unchanged from round 1)
**Trunk at review** `c8eac86d`
**Round 1** `.plan/reviews/HARNESS-1.md` — `CHANGES_REQUIRED`, three ledger findings

## Verdict: `PASS`

Zero unresolved findings. Round 1's F1, F2 and F3 are answered in the form
contracts.md §3 requires — original wording kept, forward pointers inserted, the
corrected record appended — and the four things this round was asked to check
hardest all hold under measurement rather than under reading.

Two observations and two recommendations are recorded at the end. **None is a
finding**, and I say so explicitly for each, because silence is not withdrawal
and neither is a recommendation.

---

## What actually changed since the round-1 verified head

Worth stating first, because it makes this review tractable and because the
ledger's size (3,233 lines) suggests otherwise:

    git diff --stat 00471116 2f1c0e50 -- backend/tests/ scripts/
     tests/operations/test_integration_outbox_index_plans_real_infra.py |  38 +-
     tests/test_return_case_workflow_real_infra.py                      |  12 +
     scripts/dev/run_real_infra_suite.sh                                | 311 +-

**`backend/tests/activity_probe.py` and
`backend/tests/test_return_case_workflow_replay_compatibility.py` are
byte-identical to the state I verified in round 1** — empty diff, not "no
material change". The whole of step:16's `LIVENESS_CEILING_SECONDS` derivation
and its helper are gone from `activity_probe.py`; the file is the one that
survived round 1's two mutations. So round 1's §1 (structural derivation), §2
(the stated limit) and §3 (rule 13 on the guard) carry forward without
re-derivation, and I re-ran only the gate:

    pytest tests/test_return_case_workflow_replay_compatibility.py -> 17 passed in 4.87s

Everything else in the 3,233-line diff is `.plan/tracks/HARNESS.ledger.md`.

## Scope and ownership

Seven files, all inside the slice, **no production file touched**:

    .plan/tracks/HARNESS.ledger.md
    backend/tests/activity_probe.py                                    (new, round 1)
    backend/tests/operations/test_integration_outbox_index_plans_real_infra.py
    backend/tests/test_return_case_policy_gate_real_infra.py
    backend/tests/test_return_case_workflow_real_infra.py
    backend/tests/test_return_case_workflow_replay_compatibility.py    (new, round 1)
    scripts/dev/run_real_infra_suite.sh

Rule 11 clean. Standing greps clean: no new imports of `operations/associate_flow`,
`agents/order_discovery`, `api/associate_returns` or `api/return_agents`; no
fact-name string literals added anywhere in the diff. Rule 1 does not engage —
the diff is test-side and script-side only.

---

# 1. The reversion is comments-only — checked, not accepted

The claim, checked with the branch's own command and independently against base.

    git diff fad67539 2f1c0e50 -U0 -- backend/tests/

    +5 lines at test_return_case_workflow_real_infra.py:553   all `#` comments
    +4 lines at test_return_case_workflow_real_infra.py:598   all `#` comments
    +3 lines at test_return_case_workflow_real_infra.py:637   all `#` comments
    ------------------------------------------------------------------------
    12 added lines, 12 comments, 0 non-comment additions, 0 deletions

Zero hunks in `activity_probe.py`, zero in the policy-gate module. The claim is
exact.

**And the stronger check the claim does not make.** A comments-only diff against
`fad67539` proves the revert is complete relative to *step:15*, not that step:15
was itself clean. So the net diff against the merge-base:

    git diff 7a898cf9...2f1c0e50 -- <both probe modules>

Every added line is one of: an import, a probe method for a gate/clarification
activity, the `all()` docstring, or one of the twelve comments. **No
`within_seconds` value anywhere in the branch differs from base.** Both probes:

    policy_gate_real_infra.py:240   within_seconds: float = 30.0
    workflow_real_infra.py:320      within_seconds: float = 30.0

and the five explicit call sites (558, 602, 640, 784, 820) all read
`within_seconds=20`, which is what they read at base. Fourteen budgets raised
and fourteen restored, with nothing left behind.

# 2. The three kept budgets are assertions; site 784 is correctly excluded

The claim is that a ceiling *below* `bay_wait_seconds` is the evidence the wait
was short-circuited. That only holds if the awaited activity sits **after** the
wait in the workflow. It does:

`return_case_workflow.py:1892` executes `request_bay_assignment`; `:1915-1920`
then `wait_condition(..., timeout=bay_wait_seconds)`; `open_support_work_item`
is at `:2299`, downstream of both. So a case that sat out a 30 s wait cannot
reach `open_support_work_item` inside 20 s.

| line | test | `bay_wait_seconds` | awaits | verdict |
|---|---|---|---|---|
| 558 | `..._arriving_before_the_wait_is_kept` | 30 | `open_support_work_item` | **assertion** |
| 602 | `..._activity_answers_and_no_signal_is_needed` | 30 | `open_support_work_item` | **assertion** |
| 640 | `..._won_the_race_is_not_overwritten...` | 30 | `open_support_work_item` | **assertion** |
| 784 | `test_cancelling_stops_the_case_without_asking_support` | 30 | `request_bay_assignment` | liveness net |
| 820 | `test_the_wait_counts_business_time_not_wall_clock` | **0** | `open_support_work_item` | liveness net |

All three commented sites check out, and the docstring at line 580 —
*"`bay_wait_seconds` is 30 here and the test does not take 30 seconds: that is
the assertion"* — is accurate for its site.

**Site 784 was classified correctly, and for the right reason.** It pattern-matches
to the group perfectly — same 30 s bay wait, same 20 s ceiling — but it awaits
`request_bay_assignment`, which runs *before* the wait at `:1915`. Twenty seconds
there asserts nothing about the wait; it is a liveness net and raising it would
weaken nothing. That distinction is not visible from a grep and requires reading
the workflow, and the author read it. Site 820 is a second correct exclusion the
brief did not name: `bay_wait_seconds=0` takes the `if timings.bay_wait_seconds
<= 0: return` branch, so there is no wait to be below.

Both exclusions are uncommented while the three inclusions carry twelve lines of
comment. That asymmetry is the right way round — the comment exists to stop a
future editor "finishing the job", and the sites that need protecting are the
ones that are load-bearing.

# 3. The outbox pin is a strengthening, and production is the correct side

**No assertion removed.** The full delta on
`test_integration_outbox_index_plans_real_infra.py`:

    - test_the_union_lands_as_six_indexes_on_the_server        (renamed)
    + test_the_union_lands_on_the_server_exactly_as_declared
    + 2 entries in the `keys ==` equality dict
    + 4 assertions (unique + partialFilterExpression on each new index)
    - assert len(after) == 7
    + assert len(after) == 9

The `keys ==` comparison is an **equality against the full
`index_information()`**, `_id_` included — so it is a two-sided pin: an index
that vanished fails, and so does an unexpected extra one. Going 7→9 does not
loosen it.

**Production is right and the test was stale, not the reverse.** Three
independent confirmations:

1. **The contract mandates it.** contracts.md §7, Ordering: *"per-case streams
   `inbound | outbound | review_commands | omc`; unique `(case_id, stream,
   sequence)`, CAS allocation; every event carries `event_id` …"*. Both indexes
   are that sentence.
2. **Production implements exactly what the test now asserts.**
   `outbox.py:304-317` creates `case_stream_sequence_unique` on
   `(aggregateId, stream, streamSequence)` with `unique=True` and
   `partialFilterExpression={"stream": {"$type": "string"}}`, and
   `case_stream_event_id_unique` on `eventId` with `unique=True` and
   `partialFilterExpression={"eventId": {"$type": "string"}}`. The four added
   assertions match field-for-field.
3. **The "second lock" reading is correct.** `outbox.py:327`, inside
   `allocate_case_stream_sequence`: *"the unique `(aggregateId, stream,
   streamSequence)` index on the outbox is the second lock on the same door"*,
   behind the `$inc` CAS counter. Losing `unique` would leave the counter as the
   only defence and the key-pattern assertion would still pass — which is
   precisely what the added assertion covers.

**The staleness window is provable rather than asserted.** The commit that added
the two indexes is `1a1b6c81` *(S2) step:02 per-case streams on the outbox*, and
it is an ancestor of this branch's base — `git log -S case_stream_sequence_unique
7a898cf9` returns it. So the test has been failing 9-against-7 since that merge
and nothing surfaced it, exactly as the new docstring says.

`backend/src/return_platform/operations/integrations/outbox.py` is **byte-identical
at base, branch head, trunk and the current main worktree** (blob
`5d9e3122`) — so this is not a production change smuggled in behind a test edit.

**Executed, not inferred.** Against the live stack, through the branch's own
runner:

    tests/operations/test_integration_outbox_index_plans_real_infra.py -> 7 passed in 25.45s

The strengthened equality holds against a real server. It follows deductively
that the base version — asserting 7 where the server has 9 — fails
deterministically.

# 4. Rule 10 on the net diff — clean

| check | result |
|---|---|
| skips / xfails added | **none** (every `skip` in the diff is prose or a shell variable) |
| assertions removed | one, `len(after) == 7`, replaced by `== 9` |
| test definitions removed | one, renamed with a strengthened body |
| tests deleted | **none** — 4,512 → 4,514, net **+2** (round 1's two guard tests) |
| `scripts/ci/known_test_failures.json` | **byte-identical** at base, head and trunk: `cb4d565ef4824d4eacc2edd380e296c711d60670` |
| weakened budgets | none; every `within_seconds` matches base |

A raised-then-reverted budget leaves no residue, and I checked that rather than
assuming it (§1). The one behavioural change in the runner — one process per
module instead of one for all 512 — removes no test and was mandated by round
1's own ruling; the script rejects the quarantine alternative at lines 41-45
explicitly *because* it would remove coverage. That is the right instinct and
the right call.

# 5. Rule 13 on the runner — and the unexercised-runner question, answered by running it

**The branch's admission is accurate.** Step:19 §5 states plainly that the runner
as it stands — author A's step:12 fixes plus author B's step:17 marker — has never
been run end to end, because step:18's repetitions invoked `pytest` directly.
Step:23's closing "Open" item 5 repeats it. Recording that rather than letting a
reader assume otherwise is the correct behaviour and I want it noted as such.

**Asked whether an unexercised runner is acceptable to merge: it no longer has to
be, because I ran it.** The stack was up (all five ports), so I exercised the
real script, unmodified, in its own worktree, over a genuine two-module fan-out.
(`--ignore=` arguments narrow collection without tripping the script's `narrowed`
short-circuit, so the fan-out path is the one under test.)

**Green path — the whole pipeline:**

    source tree: .../agent-af79f912fcfd95e05/backend/src
    live-infrastructure suite: all five datastores reachable
    collection: 15/5212 tests collected (5197 deselected) in 6.59s
    running 2 modules, one process each (ceiling 900s per module)
    === [ 1/ 2] tests/operations/test_integration_outbox_index_plans_real_infra.py  started 2026-08-31T21:06:40+05:30
      7 passed in 25.45s
    === [ 2/ 2] tests/test_return_case_policy_gate_real_infra.py  started 2026-08-31T21:07:08+05:30
      8 passed in 13.02s
    modules run 2 / passed 2 / failed 0 / tests passed 15 / tests failed 0
    the live-infrastructure suite PASSED: 2 modules, 15 tests (0 skipped).

Preflight, single collection read twice, module discovery from pytest's own
collection, a fresh process per module, the per-module timestamps, the summary
parse, the aggregate and the verdict — all correct, and `7 + 8 = 15` reconciles
to the collected total.

**Red path — forced with `LIVE_MODULE_TIMEOUT=5`:**

    !!! tests/...outbox... TIMED OUT after 5s -- no result; the module did not finish
    !!! tests/...policy_gate... TIMED OUT after 5s -- no result; the module did not finish
    counts could not be read for 2 module(s); the totals above are
    incomplete and must not be quoted as the suite's result:
    FAILED modules:  x ... exit 124 -- TIMED OUT after 5s ...
    the live-infrastructure suite FAILED (2 of 2 modules).
    -> exit 1

The ceiling fires, 124 is classified as a timeout rather than folded into the
generic failure line, unreadable counts are reported as unreadable and count
against the run, the verdict is sticky across modules, and the exit code is 1.
**The aggregate does not lie in either direction.**

**The `TIMEOUT_CMD` coreutils check is real and it is the right check.** Line 232
asks the binary what it is (`--version | grep -qi coreutils`) rather than
checking for a non-empty `command -v`. On this machine `/usr/bin/timeout` is
`GNU coreutils 8.32` and it was accepted; a name-only check would have handed
every module to `C:\WINDOWS\system32\timeout.exe`, which takes `/T` and exits 1
without running pytest. This is the one guard on the branch most likely to be
written as a comment, and it is not one.

**The `PYTHONPATH` export works, including the Windows case I expected it to
fail.** `export PYTHONPATH="$ROOT/backend/src:..."` emits an MSYS path
(`/k/Projects/...`), which I expected native Python to ignore. Git Bash mangles
it to a Windows path on the exec boundary, and the interpreter resolves the
branch's `src` first:

    ['K:\\...\\agent-af79f912fcfd95e05\\backend\\src', 'K:\\...\\backend\\src']

I record that I raised this and withdrew it on measurement.

**The one branch I could not reach by execution** is line 347 — the
non-timeout "produced no readable summary" marker (step:17), which needs a
module stopped by something that is not the gate. I exercised its parser
directly against six real summary shapes:

    'tests/foo.py .....'                -> UNPARSED   (the step:17 case)
    '7 passed in 25.45s'                -> passed=7
    '1 failed, 12 passed in 88.11s'     -> passed=12 failed=1
    '10 skipped in 6.37s'               -> skipped=10 (the n_skipped fix)
    '1 error in 0.55s'                  -> errors=1
    '=== 8 passed in 13.02s ==='        -> passed=8

All six correct, including the leading-space fix that the branch found via its
own deliberate-failure proof. The marker's sibling branch (rc 124/137) was
exercised live and shares the reporting path.

**Rule 13 on the runner: satisfied.** The gate that runs it is the manual
acceptance run — CI does not invoke it, correctly, since `addopts` deselects
`live_infra`. Nothing about this script is load-bearing for a CI gate, its
failure modes are loud, and it is now exercised.

# 6. `ruff format --check` — it does **not** block, and the premise that it would is false

The pre-existing defect is real and exactly as described: `draft_support_request`
is wrapped across three lines in both probe files at the merge-base `7a898cf9`,
still wrapped at head, and collapsed to one line on trunk `c8eac86d`. The branch
did not cause it and did not fix it.

**The conclusion drawn from it is wrong, and I checked rather than reasoned.**
Merging cannot carry the wrap onto trunk: the merge-base has the wrapped form,
the branch left that region untouched, and trunk changed it — so a three-way
merge takes trunk's side. Confirmed on the actual merge result:

    git merge-tree --write-tree c8eac86d 2f1c0e50
    -> c69aa9a9  (clean, no conflicts)

    c69aa9a9:backend/tests/test_return_case_workflow_real_infra.py:203
      async def draft_support_request(self, request: DraftSupportRequestInput) -> SupportRequestDraft:
    c69aa9a9:backend/tests/test_return_case_policy_gate_real_infra.py:135
      async def draft_support_request(self, request: DraftSupportRequestInput) -> SupportRequestDraft:

Both collapsed. And on the whole merged backend tree, with the repository's own
ruff:

    ruff format --check .  ->  1160 files already formatted
    ruff check .           ->  All checks passed!

**Clean.** `checks.yml:323-327` runs `ruff format --check .` and
`scripts/linux/03_run_backend_quality.sh:11` runs the same; neither goes red on
the merge result.

For completeness, the branch *in isolation* reports **94 files would be
reformatted** and 13 `ruff check` errors — two orders of magnitude more than the
two probe files. That is not this branch's doing either: it is a base that is
~100 commits behind a trunk which has since run a formatting and import-sort
pass. It disappears on merge, as the tree above shows.

**Ruling: not blocking, and no fix is asked for.** Fixing it on-branch would
have made the reversion diff unverifiable for the sake of a defect the merge
resolves. Leaving it deliberately, and saying so in the commit message, was the
correct trade. The one thing I would ask is that the merge be taken as a merge —
a rebase or squash-onto-base would re-open the question, and the check above is
only valid for the three-way merge.

# 7. The ledger's final state reflects the corrections

Two headline claims were withdrawn by their own author. Both withdrawals are in
the file's **last** entry, step:23, and the conclusions there assert the
corrected version:

- **The 175 s checkpoint.** §3: *"`checkpoint_completion_target=0.9` means
  Postgres deliberately spreads a timed checkpoint's write phase across 270
  seconds … the phase that actually touches the disk synchronously is `sync=`,
  and it is 3.0 seconds … the headline number in step:21 was normal behaviour
  misread as pathology."* Corroborated independently in the same entry by the
  manual checkpoints — 820 buffers in 0.076 s.
- **The wrong `pg_test_fsync` row.** §3: *"step:21 quoted `open_datasync` at
  30 ms … this server uses `fdatasync`, at 126 ms. I quoted the wrong row of my
  own measurement."*

§4 then partitions the record explicitly, and the partition is the honest one:

> **Established:** ~126 ms per WAL flush; ~2× faster with `fsync=off` on every
> Temporal-dependent module with a Mongo-only control unmoved; Temporal
> persistence errors to zero.
> **Not established:** that it removes the test flakiness — both A/B arms were
> clean, the forced trigger was mis-built, *"the link … is **unproven**, and
> nothing in this ledger should be read as proving it."*

And §2 records the failed experiment as failed, with the reason in the
instrument's own log — 48 cheap checkpoints instead of one heavy stall, so the
experiment never built its condition. §1 records that the 5/5 prediction bar was
set without checking whether n=5 could clear it: *"a prediction that could only
ever confirm."* That is a branch marking its own experiment inconclusive when a
weaker reading was available, which is the behaviour this run exists to produce.

**The gate conclusion is unchanged and correct**: with the link unproven, this
suite cannot be a hard gate on this hardware and its failures must be recorded as
flakes, never re-run into passes. That is round 1's ruling, sustained on better
evidence than round 1 had.

**Round 1's three findings are resolved in the required form.** Forward pointers
at ledger lines 75, 211 and 224 sit beneath the original wording; step:07 carries
the corrected record; the diff for that step is insertions only, and the ledger
says so and shows the `--stat` as the check. F1 is answered *and generalised* —
that a flake counted into a defect total and a flake absorbed into a green are
the same error with the sign flipped is a better sentence than the finding asked
for. F3's relabelling is the one I proposed, kept in its stronger form. **F1, F2
and F3: sustained as resolved.**

**The step:19 reconciliation is sound.** The duplicate `step:11`/`12`/`13`
headings are indexed to commits rather than renumbered, the index is verified
against `git log` rather than transcribed, and I spot-checked three rows against
the repository and they match. Not renumbering was right: it is the one edit an
append-only format exists to prevent. The entry also declines to preserve either
author's initial "intrusion" framing, symmetrically. Nothing here is a review
matter — no code is affected — but the record is in better shape than the
incident that produced it.

---

# Observations and recommendations — none is a finding

**O1 — step:20 exists only as a commit message.** The reversion is recorded in
`c82cee66`'s message, thoroughly, but there is no `## step:20` entry in the
ledger, while step:21 and step:23 both cite "(step:20)" in their Open lists. A
reader following that citation inside the file finds nothing. **Not a finding**:
the commit message is part of the record, it is more detailed than most entries,
and I verified every claim in it.

**O2 — step:21 has no back-inserted forward pointer.** Step:07 established the
branch's own correction discipline (original wording kept, block-quoted pointer
beneath) and applied it three times; step:11 applied it again. Step:23's
corrections to step:21 are not marked at step:21. A reader landing there sees
*"the root cause is fsync latency"* and *"174 seconds to write"* with no local
signal that both were revised. **Not a finding**, and I considered making it one:
step:23's own heading — *"…and a correction to step:21's mechanism"* — names its
target in the most visible place in the file, and the brief's bar is that the
*conclusions* not assert the withdrawn version. They do not. Recommend the
pointer anyway, if this ledger is touched again.

**O3 — 81 mojibake sequences (`â€"`) in the ledger.** Author A's `Add-Content`
encoding defect, self-recorded at step:13 and left unrepaired. Affects several
`## step:` headings. **Not a finding** — cosmetic, disclosed, and repairing it
would mean rewriting another author's append-only entries.

**R1 — the aggregate should reconcile against its own collection.** The runner
reports `collection: 512 tests collected` and separately sums per-module counts,
and never asserts the two agree. It reconciled in my run (15 = 7 + 8) and the
ledger reconciles 421/3/54 to 512 by hand in step:12. Three lines of arithmetic
at the summary would make that a machine check rather than a reader's. For a
script whose stated purpose is "an aggregate that cannot lie", this is the one
remaining way it can be quietly incomplete. **Not a finding** — the unparsed-counts
path already catches the realistic cases, and I exercised it.

**R2 — the interpreter fallback in a worktree.** With no `backend/.venv` in a
worktree the script falls back to `command -v python`, which on this machine is
the Windows Store Python and fails with `ModuleNotFoundError: No module named
'pydantic'`. Loud, not silent, and the PYTHONPATH pin still points at the right
tree — so it is a usability edge, not a correctness one. Worth a line in the
interpreter comment. **Not a finding.**

---

# What I ran

Live infrastructure was up throughout (Mongo 27017, Neo4j 17687, SQL Server
11433, Valkey 6379, Temporal 17233 — all reachable). Everything reported as a
run result above was executed by me, in the branch's own detached worktree at
`2f1c0e50`, with `PYTHONPATH` pinned to that worktree's `backend/src` on every
Python invocation.

    run_real_infra_suite.sh (2-module fan-out, live)  -> 2 modules, 15 tests, PASSED, exit 0
    run_real_infra_suite.sh (LIVE_MODULE_TIMEOUT=5)   -> 2 modules TIMED OUT, FAILED, exit 1
    pytest tests/test_return_case_workflow_replay_compatibility.py -> 17 passed in 4.87s
    ruff format --check / ruff check on the merged tree c69aa9a9 -> clean, clean
    git merge-tree --write-tree c8eac86d 2f1c0e50 -> clean merge

**Not run, and not claimed.** The full 71-module live suite. The ledger's
wall-clock investigation — the A/B arms, the forced-checkpoint experiment,
`pg_test_fsync` — is read and assessed for internal consistency and for whether
its conclusions match its evidence; it is **not independently reproduced**, and
nothing in this review should be read as confirming or disputing its
measurements. The compose change it describes lives outside this repository, is
uncommitted by design, and is correctly out of scope for this branch. Round 1's
mutation testing of `activity_probe.py` was not repeated because the file is
byte-identical to the state those mutations were run against.

---

# Summary

The reversion is genuinely comments-only and I checked both halves of it — the
diff the branch offers, and the net diff against base that it does not. The three
kept budgets are assertions, provable from the workflow's own ordering, and the
two sites that pattern-match into that group were excluded for the correct
reason. The outbox pin is a strengthening on both sides of an equality, backed by
contracts.md §7, matching production field-for-field, with the staleness window
provable from `git log` — and it passes against a live server. Rule 10 is clean
on the net diff, with the allowlist byte-identical. The format failure resolves
itself on merge, and I confirmed that on the merge tree rather than arguing it.

The runner is no longer unexercised: it ran green, it ran red, the ceiling fired,
the unreadable-counts refusal fired, and the exit codes were right in both
directions.

Two headline claims were withdrawn by the author before I arrived, and the
withdrawals are in the last entry with the evidence for each. An investigation
that ends inconclusive and says so — naming the reason its own experiment
failed, and marking its own prediction as one that could only confirm — is worth
more to this repository than one that ends with a finding it cannot support.

`PASS`. Merge permitted, as a three-way merge.
