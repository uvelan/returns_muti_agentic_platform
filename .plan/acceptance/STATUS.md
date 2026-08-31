# ACC phase 2 — where the 26-item gate actually stands

Written to be read against the brief's own grouping. The distinction this run
has been strict about is kept here: **an unexecuted scenario is not a green
one**, and a scenario covered by another slice's suite that ACC has not audited
is not an ACC verification either.

Three categories, and nothing is promoted between them by inference.

* **A — verified here, with fault injection.** ACC wrote or ran the scenario,
  injected the fault it exists to catch, and recorded before/after evidence.
* **B — in-slice coverage located, not audited.** Tests exist and were found by
  name; ACC has **not** read their bodies or injected against them, so their
  blindness is unassessed. Every slice on this run shipped at least one
  green-but-blind test, so "a test exists" is not a finding.
* **C — not reached.** No ACC work. Where the reason is capability rather than
  time, it says so.

---

## A — verified here

| item(s) | scenario | evidence |
| --- | --- | --- |
| safety net (a) | the live-infra chaos smoke test, first execution ever, then re-verified from cold | `safety-nets.md` |
| safety net (b) | `killpg(getpgid(pid), SIGTERM)` through the session, proved under Linux | `safety-nets.md` |
| **10** | DEFERRED per AMENDMENT-8, and the unreachability asserted in the three places that must agree | `item-10-deferral.md` |
| **13** | per-case cadence, cap is a case total across N reviews; no duplicate reminders across a wake | `items-13-19-business-time.md` |
| **19** | a wait spanning a closed weekend fires no retroactive burst | `items-13-19-business-time.md` |
| **17** (omc half) | eventually once, **never** atomically — the crash gap observed as a gap, then closed by redelivery | `amendment-4-eventually-once.md` |
| **7** (ingress half) | AMENDMENT-3: all three Support surfaces coexist in the published document | `amendment-3-coexistence.md` |
| **26** | every merged branch has a recorded `PASS`; the calibration bait was caught | below |
| AMENDMENT-5 (partial) | a weekend-spanning close leaves **no review without a legal exit** — the absence of the stranded state asserted | `items-13-19-business-time.md` |

Every scenario above was made to fail. Eleven injections landed; **two were
discarded for being red for the wrong reason** (an invalid released binding that
failed schema validation before any assertion ran; a `$set` upsert the Mongo
double refuses), and **two found defects in ACC's own instruments** (a wake that
could not wake, a document fixture reading two files while reporting four).

## Item 26, audited

`merge.md`'s slice table names a final `PASS` commit for every merged branch.
Checked one by one against `.plan/reviews/`:

| branch | final round | verdict |
| --- | --- | --- |
| S1 | `S1-1.md` | PASS |
| S1 phase 1b | `S1b-1.md` | PASS |
| S2 | `S2-3.md` (after `S2-1`, `S2-2` CR) | PASS |
| V1 phase 1 | `V1p1-2.md` (after `V1p1-1` CR) | PASS |
| V1 phase 2 | `V1p2-3.md` (after two CR) | PASS |
| V2 phase 1 | `V2p1-2.md` (after CR) | PASS |
| V2 phase 1b | `V2p1b-1.md` | PASS |
| V2 phase 2 | `V2p2-2.md` (after CR) | PASS |
| V3 backend | `V3-2.md` (after CR) | PASS |
| V3 frontend | `V3f-1.md` | PASS |
| V3 backend phase 2 | `V3b2-1.md` | PASS |
| ACC phase 1 | `ACC1-2.md` (after CR) | PASS |
| fabrication guard | `GUARD-1.md` | PASS |
| actorId fixtures | `ACTORID-1.md` | PASS |
| RV calibration | `calibration-1.md` | **CHANGES_REQUIRED** — the seeded literal caught as blocking, and this branch never merges |

Twenty-four review documents, fifteen branches, no merged branch without a
`PASS`, and every round count matches `merge.md`. One formatting note, not a
finding: `V1p1-1.md` writes its verdict as a bullet (`- **Verdict: …**`) where
every other file uses a heading. Both are unambiguous; a machine-readable audit
would need to accept both, which is why this one was done by reading.

**Not written as a test, deliberately.** A module under `backend/tests/` that
parsed `.plan/reviews/` would make the backend suite fail on a planning
document, and would run in CI against a directory that has nothing to do with
the application. The gate item asks to *verify against* `.plan/reviews/`, and
that is what this is.

## B — in-slice coverage located, not audited

Named so the next agent can start from the file rather than from a search. **ACC
has not read these bodies and has not injected against them.**

| item(s) | where |
| --- | --- |
| 1–2 config-only rendering | V1's template/renderer suites |
| 3–6 review gate | `tests/test_support_template_review_gate.py` (33 tests), `tests/api/test_case_panel_and_reviews.py` (60) |
| AMENDMENT-5's retry-409 | `test_a_retry_after_the_gate_closed_is_refused_and_changes_nothing`, `test_a_retry_we_cannot_adjudicate_is_503_not_409`, `test_every_state_that_cannot_retry_says_what_can_be_done_instead`, `test_every_state_the_gate_can_close_over_ends_with_a_legal_exit` |
| 7–8 relay + multi-RMA | `tests/operations/test_support_message_classification.py` (22) — including `test_an_unmatched_artifact_never_creates_a_record` (DR-11), `test_an_ambiguous_artifact_asks_rather_than_guesses` |
| 9, 11–12 resolver | `tests/operations/test_support_resolution_ladder.py` (23), `tests/operations/test_support_clarification_roundtrip.py` (18) |
| 17 (relay half) | `test_the_transcript_entry_is_appended_once_across_a_redelivery` |
| 23 (deploy replay) | `tests/test_return_case_workflow_replay_compatibility.py` (15) |

## C — not reached

| item(s) | why |
| --- | --- |
| 1–2 | time. Reachable in the normal suite. |
| 3–6 (beyond AMENDMENT-5's exit assertion) | time. Reachable in the normal suite. |
| 8 (multi-RMA cross-assignment, prompt-injection fixture) | time. |
| 9, 11–12 (resolver, disclosure line, budget, authz) | time. Note item 10's rung is deliberately absent; 9/11/12 concern the fact rung and the clarification path, which **are** reachable. |
| 14–16, 18, 20, 23 (durability, ordered drain, causal ordering, resolver resume) | time — **not** capability. The datastores were up for this run (all six containers healthy) and the live-infra smoke test passed, so these are writable against live infra today. |
| 21–22 (context assembly, compaction, release pinning) | time. |
| 24–25 (frontend) | **out of this dispatch's scope as written** — "backend tests only, no production edits". These need the frontend suite (`npm test`, `contracts:check`, MSW conformance) and a different owner or a widened brief. |

**Nothing in C is claimed as green.** In particular, acceptance 18's condition —
"assert the causation chain, not just the drain" — has **not** been done, and the
brief's dispatch condition 3 remains open.

## Production defects found — reported, not fixed

1. **`scripts/dev/run_real_infra_suite.sh:56` preflights the wrong SQL Server
   port.** It requires `SQL Server:14330`; `compose.yaml:192` publishes
   `${PLATFORM_SQLSERVER_PORT:-11433}` and `.env:94` sets `11433`. The only
   sanctioned entry point for the 512 live-infra tests **refuses to run against
   a stack that is fully up**, and says "start the stack" — the one message
   guaranteed not to lead anyone to the port number. One line, in `scripts/`,
   outside ACC's `backend/tests/`-only scope. Re-verified on this run.

No production defect was found in `backend/src`. Every fault recorded in this
directory was injected by ACC and reverted; `git diff` against the merge base
touches no file outside `backend/tests/` and `.plan/`.
