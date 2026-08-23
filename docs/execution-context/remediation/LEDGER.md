# Remediation V4.1 — Execution Ledger

**Authority:** `AGENTS.md` § Authorized remediation overlay · `docs/execution-context/MASTER_EXECUTION_CONTEXT.md`
**Target branch:** `refactor/unified-return-platform`
**Programme baseline:** `04a05fbfa266c689cfce281df6b2b0b83f1121a3`
**Source audit:** deep UI and end-to-end functional audit, 2026-08-22 — 30 findings, verdict **NO-GO**

This file is execution state. It carries task order, status, commits, gates and evidence
pointers — nothing else. It is not a plan and must not grow into one.

## Session preamble

Read control documents at session start and record their hashes below. Reread on session
restart, context compaction, remote update, or hash change — **not per task**.

| Document | SHA-256 at last read |
|---|---|
| `AGENTS.md` | `7344ce7f3ad1e5daeb6f4cbe0d3aa4f44494805238ef26bf6e53f54fac35a7d1` |
| `docs/implementation/MASTER_MULTI_AGENT_PROMPT.md` | `e47a0a4bdc2cbf17b5ea484870653e1ce3ffeb6ca89116ba2189676ab859c3f8` |
| `docs/implementation/ROLE_PROMPTS.md` | `d7c0f8e6859e01cec4ec426e87fb3374d36027554f58d18b8010105a99bd2f26` |
| `docs/execution-context/MASTER_EXECUTION_CONTEXT.md` | `808c102998551360b99d5e55d138581b25e546f162bed77994ffa15c92d6b543` |

Session control-doc read asserted: **YES** (2026-08-22, session start).

## Gate status

| Gate | Covers | Status |
|---|---|---|
| **P** Plan integrity | V4.1 document | **SIGNED** 2026-08-22 |
| **G0** Control truth | T00, T01a | **MET** |
| **G1** Recovery | T01b, T02, T05, T06 | **MET on code**; T01b totals never produced, T02/T04 runtime closure outstanding |
| **G2** Durable control planes | T07–T12 | **MET on code**; T10 live validation blocked with L1 |
| **G3a** Core UI | T13–T15 | **MET** |
| **G3b** Full UI | T16, T17 | **MET** — zero `pending` identities |
| **G4** Operations | T18, T19a–d | **MET**, with T19b applied-run outstanding |
| **L1** Live-route readiness | P00 | BLOCKED — no live model route |
| **L2** Live Discovery outcome | executed inside T20 | BLOCKED behind L1 |
| **G5** Release | T20 + L2 | OPEN |

## Task ledger

Status: `NOT_STARTED` · `IN_PROGRESS` · `IN_REVIEW` · `ACCEPTED` · `BLOCKED`

| Task | Level | Depends | Status | Accepted SHA | Gate | Blocker |
|---|---|---|---|---|---|---|
| P00 live-route readiness | SMALL | — | BLOCKED | | L1 | needs a live model route; the audit found both providers credentialed with none constructed |
| T00 control truth | SMALL | — | ACCEPTED | `9c0a905` | G0 | |
| T01a truthful gates | SMALL | T00 | ACCEPTED | `da04602` | G0 | |
| T01b live-infra runner | SMALL | T00 | **PARTIAL** | `adfb245` | G1 | totals observed at last — 396 passed / 31 failed / 23 errors / 54 skipped; one module hangs and is deselected, and most failures are this session's Temporal port move reaching tests that defaulted to the old port |
| T02 Temporal replay recovery | CRITICAL | T00 | ACCEPTED (code) | `ce660bc` | G1 | runtime closure needs a running worker; see T19a |
| T03 issuance seam | CRITICAL | T01a | ACCEPTED | `893e995` | G1 | |
| T04 exact-once RMA persistence | CRITICAL | T03 | ACCEPTED (code) | `ae7211f` | G1 | closure against fresh identifiers needs the running stack |
| T05 canonical completeness | NORMAL | T02 | ACCEPTED | `a894463` | G1 | |
| T06 status vocabulary | NORMAL | T04 | ACCEPTED | `dbe6364` | G1 | UIAUDIT-020 **reclassified** |
| T07 graph evidence gate | CRITICAL analysis | T01a | ACCEPTED | `3ce4367` | G2 | UIAUDIT-001 **reclassified** |
| T08 graph lifecycle correction | CRITICAL / conditional | T07 | ACCEPTED — outcome `IMPLEMENTED` | `108a982` | G2 | |
| T09 durable sync runs | CRITICAL | T07 + T08 outcome | ACCEPTED | `87e8d91` | G2 | |
| T10 durable manual AI operation | CRITICAL | T01a; live validation via L1 | ACCEPTED (code) | `0ff7ab6` | G2 | live validation blocked with P00 |
| T11 interception history | NORMAL | T01a | ACCEPTED | `f520e6d` | G2 | |
| T12 configuration release contract | NORMAL | T01a | ACCEPTED | `0aeb844` | G2 | |
| T13 real-stack browser harness | NORMAL | T01a | ACCEPTED | `3ff4b90` | G3a | real-stack project skips until `E2E_REAL_BASE_URL` is set |
| T14 shared UI foundation | NORMAL | T13 | ACCEPTED | `05c47d3` | G3a | |
| T15 return-critical UX | NORMAL | T04, T05, T06, T10, T13, T14 | ACCEPTED | `4c05ffc` | G3a | |
| T16 route-wide UX and a11y | NORMAL | T12, T13, T14, T15 | ACCEPTED | `b1b78c1` | G3b | |
| T17 blocked-action and RBAC closure | NORMAL | T09, T11, T12, T13, T16 | ACCEPTED | `c4cee6c` | G3b | |
| T18 runtime portability and observability | NORMAL | T09, T10, T11 | ACCEPTED | `0a1d1e2`, `6aee3d3` | G4 | |
| T19a wedged-history repair | CRITICAL | T02 | **NOT_REQUIRED — verified** | `8fb8893` | G4 | patched worker started and heartbeating; the workflow UIAUDIT-005 named is COMPLETED with zero task failures, and the three still running are awaiting a human |
| T19b return mismatch repair | CRITICAL | T04 | BUILT — dry run only | `8fb8893` | G4 | apply gated on T04 closure against fresh identifiers |
| T19c graph debris repair | CRITICAL | T08 | NOT_REQUIRED | `8fb8893` | G4 | generations empty; exactly one serving snapshot |
| T19d legacy interception repair | CRITICAL | T11 | NOT_REQUIRED | `8fb8893` | G4 | no interception collection exists in any shape |
| T20 audit-equivalent release validation | CRITICAL | G0–G4 complete + L1 | PARTIAL | `f1c10ba` | G5 | live lane needs a current-code backend and L1 |

### T20 progress

| Component | Status |
|---|---|
| Finding closure audit — all 30 re-checked against source | **DONE** — `FINDING_CLOSURE_AUDIT.md` |
| Route / state / viewport / a11y sweep | **DONE** — 117 Playwright tests, 36 routes, 6 viewports |
| Discovery deterministic matrix (§13.2, 15 cases) | **DECLARED + 13/15 EXERCISED** — `discovery_matrix.py`; 2 skip naming the missing model route |
| Datastore and workflow proof | **PARTIAL** — read-only inventory taken; see `T19_STATE_REPAIR_EVIDENCE.md` |
| Clean-environment canonical startup | **BLOCKED** — the backend on port 8000 is pre-T12 |
| Live Discovery smoke (L2) | **BLOCKED** behind L1 |

**Finding closure.** All 30 audit findings closed or formally reclassified,
each re-checked against current source rather than transcribed from a commit
message — `FINDING_CLOSURE_AUDIT.md`. Two closed by reclassification with their
corrected premise on the record: UIAUDIT-001 (the serving generation was unique;
the defect was lifecycle debris) and UIAUDIT-020 (`recordStatus` does not exist;
the real defect was a null status rendering as an empty pill).

**Route and accessibility sweep, standing evidence.** 117 Playwright tests over
36 canonical routes: every route mounts with no console error, uncaught error or
4xx; the first Tab reaches the skip link and Enter moves focus into `<main>`;
zero horizontal overflow at 320, 390, 640 (200% zoom on 1280), 768, 1280 and
1440 measured on the document *and* inside `<main>`; zero critical or serious
axe violations. Identity pending 0 of 36, heading mismatch 0.

**Unit and integration totals.** 4209 backend tests, 552 frontend tests. Ruff,
mypy, tsc and eslint clean. OpenAPI drift PASS.

**What is not done, and why.**

*T20 needs a clean environment it does not have.* A backend is running on port
8000 and serving real data, but it predates this work: `/openapi.json` exposes no
`ConfigurationRelease*` schema, so it is running pre-T12 code -- roughly thirteen
commits behind the branch. T20's scope requires a clean environment and canonical
startup, and validating against that process would produce evidence that does not
correspond to the code under review. Restarting it is an operator decision: it is
not a process this work started.

*P00 needs a live model route.* The audit found both providers credentialed and
reachable with no model route constructed, and the deployment runs
`PLATFORM_AI_PROVIDER_ORDER=MANUAL`. L1 cannot be satisfied by waiting; something
has to change about the provider configuration first.

*T19b is built and deliberately unapplied*, gated on T04 closure against fresh
identifiers.

These are stated here rather than marked complete. A ledger that overstates is
worse than one that is behind.

## T00 evidence

Closure criterion: *no control doc names a stale branch/commit; rule 4 intact; overlay
authorization recorded.*

| Check | Command | Result |
|---|---|---|
| No stale branch/commit in required control docs | `grep -c "feat/v2-order-discovery-integration\|0845d3f\|dcbb7dc"` over the four control docs | `0` in every file |
| Every git command names the target branch | `grep -n "origin/" MASTER_MULTI_AGENT_PROMPT.md` | both hits are `origin/refactor/unified-return-platform` |
| Rule 4 intact | `sed -n '14p' AGENTS.md` | `4. Do not create another implementation plan.` |
| Overlay authorization recorded | `grep -c "Authorized remediation overlay" AGENTS.md` | `1` |
| Vendor/model names removed, ladders retained | `grep -rn "Gemini\|Codex CLI\|Sonnet 4.5\|Antigravity"` over the four docs | `0` hits; SMALL/NORMAL/CRITICAL ladders unchanged |

Files changed: `AGENTS.md`, `docs/implementation/MASTER_MULTI_AGENT_PROMPT.md`,
`docs/implementation/ROLE_PROMPTS.md`, `docs/execution-context/MASTER_EXECUTION_CONTEXT.md`,
plus this ledger (new).

Not done, deliberately: `MASTER_MULTI_AGENT_PROMPT.md` was **not** compacted. V4.1 § 5 permits
compaction only with a semantic rule-diff proving no governance, security or validation rule
was lost; that is out of T00's scope. The file was repointed and de-vendored only.

## T01a evidence

Closure criterion: *lint/typecheck/unit pass; check mode leaves a clean tree; scanner satisfies
the § 11 redaction contract under randomized planted-credential tests.*

| Finding | Fix | Verification |
|---|---|---|
| UIAUDIT-024 (5 lint errors) | `no-dynamic-delete`: object → `Map` + `Object.fromEntries`. `no-unnecessary-type-assertion`: dropped redundant `as string`. `no-unnecessary-condition` ×2: added a `lookup<T>()` helper returning `T \| undefined` so the runtime guards stay reachable. `no-unused-vars`: replaced destructure-omit with spread + static `delete`. **No rule suppressed, no guard deleted.** | `npm run lint` exit 0; `npm run typecheck` exit 0 |
| UIAUDIT-030 (check mode mutates tree) | Receipt write and `EVIDENCE_DIR.mkdir` guarded by `if args.write`. Check mode still prints the receipt. | `check_openapi_drift.py` → `mode: check`, `diffs: []`, `status: PASS`, exit 0; `git status --porcelain` on the receipt path returns empty |
| 8 dead asyncio warnings | Module-level `pytestmark = pytest.mark.asyncio` replaced by per-test markers on the 7 async tests (`asyncio_mode = "strict"`). | `pytest tests/test_replay_provider.py` → 14 passed, **0 warnings** (was 8) |
| Scanner redaction contract | `Finding.redacted()` no longer emits `prefix={text[:4]}`; now `len` + `sha256[:16]` only, computed without decoding the value. | `python scripts/security/test_scan_secrets.py` → 27/27, including 10 new checks |

Redaction contract asserted with a **randomized** credential against stdout and stderr
separately: no full value, no first four characters, no last four characters, no contiguous
eight-character fragment — and the digest and length are still present so allowlisting still
works. The first-four-characters check is a genuine regression test: the previous output
printed `prefix='nvap'` and would have failed it.

Suites, both at baseline parity with the audit:

| Suite | Result | Audit baseline |
|---|---|---|
| frontend `vitest run` | 482 passed / 30 files | 482 passed / 30 files |
| backend `pytest -q` | 4073 passed, 3 skipped, 496 deselected, **1 warning**, exit 0, 217.1s | 4073 passed, 3 skipped, 496 deselected, **8 warnings**, 222.6s |

The warning count fell from 8 to 1 across the whole backend suite. The remaining warning is a
third-party `StarletteDeprecationWarning` raised by `fastapi/testclient.py`, not repository
code — so all eight dead-marker warnings recorded in audit § 11 are closed, verified at suite
scope rather than only in the file that was edited.

**Scope note.** `ReturnCopilotPage.tsx` sits in the AI-operations collision domain (T10), but
V4.1 § 7 assigns all five lint errors to T01a. The change is a behaviour-preserving refactor of
one local accumulator with no API, state or render change, so the boundary is safe. T10 retains
ownership of that file for behavioural work.

## T02 evidence

Closure criteria: *historical replay passes; wedge advances; retry storm stops; no history
rewrite.* Two of four are met in code; two need the live stack. Recorded honestly below rather
than claimed.

### Root cause, confirmed at the converter

`eaed61c` changed `draft_support_request` from `-> str` to `-> SupportRequestDraft`. The
workflow requests the result by type (`result_type=SupportRequestDraft`), so a history holding
a JSON string cannot decode. Reproduced directly from the data converter:

```
decode as str       : 'Hello -- we have a return to raise against CQ800002.'
decode as dataclass : TypeError: Cannot convert to dataclass
                      <class '...SupportRequestDraft'>, value is <class 'str'> not dict
```

Byte-identical to `logs/worker-temporal.log`. Replay is deterministic, so this failed on every
activation forever — five in forty-five minutes, with no alert, metric or terminal state.

### Fix

`workflow.patched(_PATCH_STRUCTURED_SUPPORT_DRAFT)` selects the decode shape. A history
recorded before the marker existed returns `False` and is decoded as the string it holds; a new
execution records the marker and takes the typed path. Both arms produce one
`SupportRequestDraft`, so nothing downstream branches. The legacy arm does **not** synthesise a
structured payload — a pre-`eaed61c` activity composed none, and inventing one would put facts
on a Support message that nothing observed.

Repository-first: `workflow.patched` / `get_version` / `deprecate_patch` had **zero** uses
anywhere before this. The convention starts here.

### Verification

| Check | Result |
|---|---|
| `pytest tests/test_return_case_workflow_replay_compatibility.py` | 9 passed |
| `ruff check` + `ruff format --check` on all changed files | clean |
| Tests fail against pre-patch source (`git show HEAD:...`) | 3 of 9 fail — guard absent, no guarded activities, `draft_support_request` pinned as `{SupportRequestDraft}` only |
| Full backend suite | **4082 passed, 3 skipped, 496 deselected, 1 warning, exit 0** (baseline 4073 + 9 new) |

The third row is the one that matters: the tests are regression tests, not decoration.

**A regression was introduced and fixed inside this task.** The first full-suite run after the
patch failed 21 tests in `tests/policy/` with
`AttributeError: '_Runtime' object has no attribute 'patched'`. Those tests drive the workflow
through a hand-rolled `temporalio.workflow` double that modelled six functions; the patch made
it seven. The double was extended rather than the production code weakened — it now answers
`True` by default, which is what a real `patched` returns for an execution with no history, so
those tests go on exercising the branch they were written against. A `patches=False`
constructor argument makes the legacy arm reachable from a test at all.

That extension is what made the fifth test possible: a runtime answering as a pre-`eaed61c`
history drives the **real `_open_support`** to completion. Before the patch this raised
`TypeError` at the activity-result decode and the workflow task failed permanently. Now the
prose is carried through, `open_support_work_item` receives it with an empty
`business_payload`, and the case reaches `AWAITING_SUPPORT`.

### The forward-looking rule

`ACTIVITY_RESULT_TYPES` in the test pins every activity result type the workflow requests. It is
a lockfile: changing a type fails the test and forces the author to decide whether in-flight
histories can decode the new shape. A companion test rejects any `execute_activity` whose
activity name is computed rather than literal, since the lock cannot see those. This is what
was missing when `eaed61c` landed.

### Residual — needs the live stack, not more code

| Closure criterion | State |
|---|---|
| Historical replay passes | **Partial.** The failing decode step is proven and fixed, and a runtime answering as a pre-`eaed61c` history drives the real `_open_support` to completion. What is *not* done is a `temporalio.worker.Replayer` run against a genuine recorded history — no such fixture exists in the repository, and capturing one requires the live Temporal service. The branch is proven correct and reachable; the specific recorded history is not replayed. |
| Wedge advances (`case 721fb62e`) | **Not verified.** Requires deploying the patched worker and observing the case leave `AWAITING_SUPPORT`. Belongs with T19a. |
| Retry storm stops | **Not verified.** Same prerequisite: watch `worker-temporal.log` for the absence of `Failed activation` over a sustained run. |
| No history rewrite | **Met.** The fix decodes what is recorded; nothing writes to history. |

T02 cannot reach `ACCEPTED` until the three runtime rows are observed against the live stack.

## T03 evidence

Closure criteria: *existing tests pass unchanged; review confirms a pure move and no new
datastore write.* Both met.

New module `operations/return_issuance.py` owns the three things that must not differ between
the two issuance paths: `uuid5` derivation of item ids from (record id, order line), the
mapping onto `CaseReturnRecordsWrite`, and the rule that issuance writes no
`dbo.return_tracking` row. It does **not** own record discovery — the workflow reads a Mongo
case and a merge plan, Support reads its own ticket rows, and forcing one shape on both would
put the workflow's case model into the Support path.

`ReturnCaseActivities._persist_records_to_return_store` now maps its plans into an
`IssuanceIntent` and delegates. Same single call to `persist_case_return_records`, same
transaction, same idempotency — **no new datastore write.**

### The port moved, and that was load-bearing

`test_return_persistence_paths_stay_partitioned` requires `persist_case_return_records` to have
exactly one implementation and **one** declaring port. Adding a second Protocol in the new
module broke it — correctly. Rather than relax the guard, `ReturnRecordStorePort` moved out of
`workflows/return_case_activities.py` into `operations/return_issuance.py`, which is also the
right dependency direction: issuance is an operations concern the workflow calls, not a
workflow concern the operations layer borrows.

`sql_business_state.py:17` imports `pymssql` at module scope, so `return_issuance` imports the
SQL write types under `TYPE_CHECKING` and again inside the one function that constructs them.
That keeps the module — and therefore the port — importable by `workflows` without dragging a
connection pool into a sandboxed package.

| Check | Result |
|---|---|
| Full backend suite | **4094 passed, 3 skipped, 496 deselected, exit 0** (4082 + 12 new) |
| Pre-existing tests changed | none — the 12 additions are all new |
| `ruff check` / `ruff format --check` | clean on all four changed files |
| Partition guard | 5 passed, with the canonical port re-pointed |
| New datastore writes | none — one `persist_case_return_records` call, as before |

## T04 evidence — ADR-001 resolved as option B

**Ruling (owner, 2026-08-22): option B.** A console-issued RMA ticket is a **distinct
artifact** from a case return record. Its authoritative home is
`integration.return_support_ticket` plus `dbo.return_requests` and `dbo.return_items`;
`dbo.return_case` / `dbo.return_record` / `dbo.return_record_item` remain the case workflow's.
The P0 closes by making the product truthful about stores that were already correct, not by
manufacturing case rows to match a sentence.

This supersedes the blocker recorded at `9523628`. The evidence that produced it is kept below,
because it is the justification for the ruling.

### What T03 uncovered

The seam is built and both callers can reach it. Wiring Support to it is blocked on a fact
ADR-001 did not settle: **a console-issued RMA ticket has no `ReturnCase` identity at all.**

| Evidence | Finding |
|---|---|
| `sql_migrations/001_return_business_state.sql:7` | `dbo.return_requests` is `session_id VARCHAR(36) NOT NULL PRIMARY KEY` — **no case column** |
| `sql_business_state.py:236` | `CaseReturnRecordsWrite` requires `case_id`, `tenant_id`, `principal_id` |
| `rma_tickets/service.py:73-84` | `RmaTicketService` holds `sql` plus a Mongo client scoped to the *shipment* collection. No case repository |
| `repository.py:1845,1847` | The only `supportCaseId` written points at a `support_cases` document, not a `ReturnCase`. Different aggregate |

So calling `persist_case_return_records` from the Support transition means **inventing** a
`case_id`, `tenant_id` and `principal_id` for a session that has none, and writing a synthetic
`dbo.return_case` row. That is precisely the second disagreeing source of truth ADR-001 exists
to prevent, so it is not a call to make inside a task.

### The options

| | Ruling | Cost | Risk |
|---|---|---|---|
| **A** | A console ticket **is** a case return record. Establish a case identity for it — resolve one (no link exists) or mint one. | Schema and identity work; a new session→case relationship | Synthetic `dbo.return_case` rows for returns that never had a case |
| **B** *(recommended)* | A console ticket is a **distinct artifact**. `dbo.return_requests` + `dbo.return_items` are its authoritative home; `dbo.return_record` stays the case workflow's. Correct the screen claim and the README ownership table. | Low — no new write | Requires `/operations/cases` to stop presenting the two as one thing |
| **C** | Unify upstream: the console path creates a real case first and issues through the workflow. | Largest | Changes the Support console's whole model |

**Why B is recommended.** The audit's own before/after supports it: `integration.return_support_ticket`
0→1, `dbo.return_requests` 0→1, `dbo.return_items` 0→1 all *did* happen. The Support path does
write its authoritative rows. What is false is the screen's claim that it also writes
`dbo.return_record`, `dbo.return_record_item` and `dbo.return_tracking` — and the README
ownership table that backs the claim. Under B the P0 closes by making the product truthful
about a store that is already correct, rather than by manufacturing case rows to match a
sentence.

B also keeps the tracking rule intact: nothing at issuance fabricates an observation.

### What shipped under B

| Change | Where |
|---|---|
| The false claim corrected | `RmaTicketsPage.tsx:184`. Was *"The ticket, the return record, its items and its tracking are written to the platform tables"*. Now names what this path actually writes — the ticket, the return request and its items — and says tracking is recorded separately, when a carrier files one. |
| The partition made **structural** | `test_the_canonical_writer_is_reached_only_through_the_issuance_seam` pins `persist_case_return_records` to exactly one caller, the issuance seam. A Support-path call fails the test with the reason. |
| The partition made **behavioural** | `test_issuing_the_return_touches_only_the_tickets_own_store` drives `set_status("RETURN_CREATED")` and asserts only the ticket row moves. `FakeSql` has no `persist_case_return_records`, so a service that grew one fails here rather than in production. |

**No README change was needed.** The canonical-flow line (`README.md:40`,
*"Case → N RMAs → N items, persisted to SQL"*) describes the case workflow, which does write
those tables — true under B. The ownership table (`:86`) says the platform owns all of these
tables, also true. Only the screen was wrong.

### Exact-once, and where it already lived

The Support path's own authoritative rows were never the defect — the audit's own before/after
shows `dbo.return_requests` 0→1 and `dbo.return_items` 0→1. Its exact-once property is
pre-existing and tested: `create_rma_ticket` is idempotent on a `request_digest`
(`test_a_repeated_submit_reports_duplicate_rather_than_creating_a_second`), and item ids are
derived so a retry matches (`test_the_return_reference_is_derived_so_a_retry_matches`).

| Check | Result |
|---|---|
| `tests/test_rma_tickets.py` + partition guard | 22 passed |
| Frontend `npm run lint` | exit 0 |
| Frontend `vitest run` | 482 passed / 30 files |
| Full backend suite | see status below |

### Residual

`dbo.return_tracking` is still written only by `record_tracking`, from a real observation.
Nothing at issuance fabricates one, and the seam cannot express one — that rule is unchanged
and now stated on the screen.

**Not verified against live SQL.** All six datastores are up, so a behavioural run against real
SQL is possible and would strengthen this; the structural and service-level pins are what
landed. Recorded so the gap is visible rather than implied.

### What was never blocked

T03's seam stands under every option — it is where the shared rules live either way. T05, T07,
T10, T11, T12 and T13 had no dependency on the ruling.

## T05 evidence

Closure criterion: *selected item plus missing return method reads incomplete on every
surface.* Met.

`required_details_complete` was `bool(selected)` — true the moment any line was picked. So a
case the platform itself recorded as `awaiting: ["RETURN_METHOD"], businessComplete: false`
handed Support a message reading **"Required Return Information: Complete"** and asked them to
issue or decline the RMA on that basis, while the case pane beside it said
*"Waiting on RETURN_METHOD"*. Three surfaces, two answers.

It now comes from `_assess_completion`, the same helper the run loop uses — a set difference
over the requirement table, the policy decision and every child collection:

```
known, _business_complete, awaiting, _revision = await self._assess_completion(case_id)
required_details_complete = known and not awaiting
```

**Why `not awaiting` is the right reading.** At handoff the case has no return records yet, so
the completion profile is unresolved and `awaiting` holds exactly the *unresolved* dimensions —
`POLICY`, `RETURN_METHOD`, the verification pair — the facts nobody has established. The
*required* dimensions (`RMA`, `LABEL`, `TRACKING`…) only enter once a profile resolves, which
is after Support has done the work being asked for. So an empty set is the only honest reading
of complete, and `RETURN_METHOD` outstanding correctly reads incomplete.

`known` is false when the projection cannot be assembled at all. That renders **Incomplete**,
never Complete: "we cannot tell" and "the facts are in" send Support to opposite actions.

| Check | Result |
|---|---|
| Full backend suite | **4098 passed, 3 skipped, 496 deselected, exit 0** (4096 + 2 new) |
| `ruff check` / `format` | clean |
| Prose and payload | both render from one value (`support_handoff.py:367` and `:417`), so they cannot disagree |

Both new tests are genuine regressions: the seeded item carries `returnRecordId: None`, so
`_selected_items` includes it and the old `bool(selected)` would have rendered "Complete"
against an assertion of "Incomplete".

## T01b evidence

`scripts/dev/run_real_infra_suite.sh` created — `backend/pyproject.toml:118` had named it since
the marker was introduced and it did not exist. Collection verified: **496 tests**, matching the
audit's count exactly. Run from the repository root instead of `backend/`, collection fails with
3 `FileNotFound` errors, which is why the script `cd`s first. Preflight checks host **ports**,
not container names — Temporal running healthy with no published port is the exact failure the
audit hit (ENV-ACTION-01).

**Totals, 2026-08-23 — first ever observed.**

| | |
|---|---|
| passed | **396** |
| failed | 31 |
| errors | 23 |
| skipped | 54 |
| duration | 33m 41s |

Run with `test_integration_outbox_index_plans_real_infra.py` deselected, because
that module **hangs** — see below. Most of the 31 failures and 23 errors name one
cause: `tcp connect error 127.0.0.1:7233 ... actively refused`. That is this
session's Temporal port move landing on tests that had not been told about it.
`conftest.py:275` reads `PLATFORM_TEST_TEMPORAL_TARGET` but defaults to
`localhost:7233`, and six real-infra modules do the same — so the default was the
only thing anyone was using. `.env` now carries the override beside the Neo4j one
it already had for the identical WinNAT reason, and the suite is being re-run to
separate genuine failures from that.

**A blocker that is not flakiness.**
`test_integration_outbox_index_plans_real_infra.py::test_the_union_lands_as_six_indexes_on_the_server`
wedges: the process stays alive with **one thread and no CPU**, with the machine
otherwise idle and Mongo answering other queries instantly. Its fixture creates a
throwaway database and builds six indexes on it — six plain `create_index` calls
against an empty collection. The test body is three lines of assertion and cannot
itself hang. Not diagnosed further; recorded so the next run does not rediscover
it as bad luck.

Related, and cheap to fix later: that fixture leaks its throwaway database when a
run aborts. Eleven `*_test_*` / `*_probe_*` databases are currently on the server.

**Earlier history, kept because it is the reason this row was wrong.** This
section said "full run in progress; totals recorded on completion" from the day
it was written, and the totals were never recorded. A background run reported
*exit code 0* while its output read `exit=127` with a pytest crash — the suite had not executed at
all — and a second attempt died to an unrecognised `--timeout` flag (`pytest-timeout` is not
installed). A third reached roughly 91% before the session restarted and produced no summary.

So the closure criterion — *"runner exists and reports known totals"* — is **half met**. The
runner exists and collects; collection is now **511** tests rather than the audit's 496, the
difference being tests added by this programme. No pass/fail total has ever been observed, and
the row above is marked PARTIAL rather than ACCEPTED until one is.

The lesson is worth keeping: a background wrapper's exit code is not the command's exit code, and
a suite reported green on the strength of one is exactly the "dark test" failure mode UIAUDIT-031
recorded against migration 008.

## T06 evidence — UIAUDIT-020 reclassified

**The finding's premise is false.** `recordStatus` appears **0 times** in the repository. The
audit's own probe, `rrprobe.py`, projects `("returnRecordId","caseId",…,"recordStatus",…)`
against Mongo — a field that does not exist under that name — so `d.get("recordStatus")`
returned `None` for all five documents and was read as "stored status is null".

The real field is `status`, and it is honestly persisted, not defaulted:

| Claim | Verified |
|---|---|
| No defaulting line exists | `assembly.py:762` is a bare `.get("status")`; `_text` returns `None` for `None`, never a literal. `ReturnRecordProjection.status` defaults to `None`, not `"ISSUED"`. |
| `"ISSUED"` is genuinely written | `return_case_activities.py:1652`, hardcoded, the only production caller of `create_return_record`. |
| No vocabulary existed | Confirmed — `vocabulary.py` declared nine `StrEnum`s and none for return-record status. |

So there was no defaulting bug to fix. **What was real is the missing vocabulary**, which
`availability.py:28-33` had already flagged in prose, and that is what T06 delivered. Members
taken verbatim from `CK_return_record_status` (`005_case_return_records.sql:85`); enum/constraint
parity asserted `MATCH`.

## PLAN-NEW-002 — RMAs permanently unlinked to their lines

Severity: candidate P2. Traced during T06; **not** the same defect as UIAUDIT-020.

The audit's visible symptom — an RMA rendering "0 lines" beside a shipping label and a tracking
number — is real, and its cause is not status:

- `api/return_support.py:369` — `orderLineReferences: tuple[str, ...] = Field(default=())`
- `return_case_activities.py:1822` — `if not plan.incoming.order_line_references: return`, which
  aborts `_assign_items` before any `assign_return_item_to_record` call
- `assembly.py:740` — `return tuple(projected) or None`, so no linked items serializes as `null`

A Support outcome POST that omits `orderLineReferences` therefore creates an RMA no item is ever
linked to. Label and tracking still render because they travel `RETURN_RECORD_MERGED_FIELDS` onto
the record document and need no item association at all — so "issued, with a label, zero lines"
is a structurally reachable and internally consistent state.

**Decision needed:** should the API reject an outcome naming no lines, or should the projection
surface the inconsistency? The first is a contract change. Recorded rather than chosen.

## T07 evidence — UIAUDIT-001 reclassified from P1

**Classification: LIFECYCLE DEBRIS, not a serving defect.** The P1 as written does not stand.

Verified independently against live Neo4j, not taken from the investigation alone:

```
MATCH (o:SalesOrder {graph_generation_id:'9cf89d56-…'})
RETURN count(o), count(DISTINCT o.sales_order_number)   ->  9999, 9999

MATCH (n0:SalesOrder {graph_generation_id:'9cf89d56-…'})
WHERE n0.sales_order_number='CA064360-1'
RETURN n0.customer_name                                  ->  exactly one row, "LISA BENITEZ"
```

Within the serving generation `sales_order_number` is **exactly unique**, and the order the audit
cited resolves to one row with one customer name. The 800 duplicates decompose with zero residual
into cross-generation copies (`copies == distinct generations` for every group), and reads cannot
reach them: `cypher_compiler.py:131-141` puts the generation predicate **inside the node pattern**
so it binds every alias in a traversal, and `neo4j_gateway.py:276` merges the caller's generation
**last** so a compiled query cannot supply a competing one. `dynamic_knowledge/order_agent/` has
zero raw driver usage.

The serving pointer is one Mongo `ActiveRuntimeSnapshot` document read by both consumers, and
`handle.py:342` fails closed rather than degrading to a legacy id.

**An ACTIVE marker is not a serving pointer.** Ten simultaneous ACTIVE markers are alarming to
look at and operationally inert.

**What is real, and smaller:**

1. **No generation reaper.** Nothing deletes `PREPARING`/`FAILED`/`RETIRED` markers or their
   nodes — 218 PREPARING, 45 FAILED, 1 CATCHING_UP accumulated in 11 days. Unbounded growth.
2. **Activation retires the predecessor outside the CAS.** `orchestrator.py:286` calls `_retire`
   *after* the compare-and-swap, and `_retire` is documented "never raises" (`:390`). A crash
   between the two leaves the predecessor ACTIVE forever with no reconciler. Five more come from
   `sync_service.py:1264-1283`, which mints `legacy-live-*` markers ACTIVE directly, bypassing the
   orchestrator.

Repair targets are enumerated: 9 stale ACTIVE markers (all ≤4 nodes, none referenced by any
snapshot), and 3 data-bearing non-serving generations holding 13,363 nodes. Deleting those three
alone takes SalesOrder from 11,299 to 9,999 and eliminates every "duplicate".

## T11 evidence — root cause found, and it is not where the audit looked

Three candidate causes were tested. **Only one is real, and the visible symptom has a separate
frontend cause.**

| Candidate | Verdict |
|---|---|
| Wrong store injected | **Ruled out.** `main.py:721-725` injects `SystemStoreInterceptionStore`; `runtime_activation.py:662` carries the same object through a release. The only other `list_pending` implementations are test doubles. |
| Legacy field shape | **Ruled out.** One writer (`store.py:167`), one reader (`store.py:333`), both `expires_at`; `store.py:459` would `KeyError` loudly on drift. |
| **Housekeeping worker never runs** | **Confirmed, real.** |

`InterceptionExpirySweep` is correct and wired — and its worker is started nowhere.
`compose.yaml:525` puts it behind `profiles: ["containerized-app"]`, and every host path
hardcodes five workers: `scripts/linux/09_start_workers.sh:8` and `scripts/run_all_host.ps1:54`
both iterate `temporal discovery orchestrator outbox integration-outbox`. The audit's own harness
reproduced it (`start_workers.sh:4`, five worker logs, no housekeeping log). Nothing complains
because `process_adoption.py:62-70` excludes `housekeeping-worker` from `REQUIRED_PROCESS_CLASSES`,
so adoption reaches LIVE and `/health/ready` is green with the reaper dead.

**Consequence:** nothing ever performs `PENDING -> EXPIRED`. The `EXPIRED` tile can never be
non-zero *even after UIAUDIT-009 is fixed*, because no `EXPIRED` documents exist.

**The audit had no database evidence for the expiry claim.** Its `intprobe.py` opens
`c[env["PLATFORM_MONGO_DATABASE"]]` and projects `expiresAt`, while the collection lives in
`database="platform"` (`housekeeping/composition.py:161`) under `expires_at`. The observation was
UI-only — and the UI cause is separate: `main.tsx:34-46` sets `staleTime: 30_000` with
`refetchOnWindowFocus: false`, `AiControlCenterPage.tsx:442-446` has **no `refetchInterval`**, and
`:551` gates the action buttons on `status === "PENDING"` alone, never comparing `expiresAt` to
now. A row fetched at 05:12 keeps offering Respond/Allow/Cancel indefinitely and the presses
return 409/404.

**So T11 needs two independent fixes**, and the ledger should not treat it as one.

Also confirmed for the same track: `ALLOWED` has no tile at all (`AiControlCenterPage.tsx:485-488`
renders four; `records.py:88-92` declares five), no store method returns terminal records, and
`system_store.yaml:51-54` declares only a unique index — a status listing over a never-reaped
collection needs one. UIAUDIT-015's exact line is `AiControlCenterPage.tsx:666`
(`key={route.routeId}`), root-caused to `routes.py:243` omitting tier from the id while `:216`
loops tiers outside it; note `selection.py` keys circuit-breaker health on the same id, so
changing it there merges or splits circuits as a side effect.

## T12 evidence

Closure criteria: *OpenAPI exposes concrete schemas; every displayed field is sourced and
tested; the drift gate catches regressions.* All three met, and the third was demonstrated
rather than asserted.

**The drift gate was proven to work, in both directions.** After typing the endpoint and before
regenerating, `check_openapi_drift.py` reported 5 `DRIFT` entries and `exit_code: 1`. The same
class of change on the *untyped* endpoint had previously produced `diffs: []`. That is
UIAUDIT-007's real cause — not a missing field, but a gate with no shape to compare.

**Two mistakes I made and corrected, both caught by tooling rather than review:**

1. I first typed the routes in `configuration/api/releases.py`. The drift gate then reported no
   change at all — because that router's prefix is `/data-console/v1/configuration` and **no
   `/data-console` path exists in the spec**. It is not mounted. The live routes are
   `configuration/api/router.py:149,160`, which re-declare the reads rather than delegating
   (unlike the mutations, which do delegate). The dead-route edits were reverted; the DTOs stayed
   where they are, and `router.py` imports them.
2. I used Pydantic `alias`, and the regenerated spec came back **snake_case** — FastAPI
   serializes response models with `by_alias=True`, so the wire kept `release_id` while the
   console now read `releaseId`. That is precisely the client/server disagreement this task
   exists to end. Switched to `validation_alias`, which accepts the persisted snake_case in and
   serializes the field name out.

**What the contract now declares** (`openapi.json`, regenerated):

```
ConfigurationReleaseView       -> checksumSha256, createdAt, createdBy, metadata, releaseId, status
ConfigurationReleaseDetailView -> …the same, plus domains
required: checksumSha256, createdAt, createdBy, releaseId, status
```

Six fields removed from the console type — `updated_at`, `validated_at`, `approved_at`,
`approved_by`, `activated_at`, `superseded_by`. None has a writer anywhere in the platform.
`createdBy` was served the whole time and never rendered, so the governance screen now answers
"who released this and when" with data instead of dashes.

**A false-confidence test, fixed.** `ConfigurationPage.test.tsx` mocked `checksum: "abc123"` — a
field the API has never returned — so the test passed while the panel rendered "-" for the one
value that makes a release verifiable. The MSW handler did the same, and passed the contract test
only because an untyped endpoint accepts any shape. Both now carry the wire shape.

| Check | Result |
|---|---|
| Backend suite | 4098 passed, exit 0 |
| Frontend lint / typecheck / vitest | clean · clean · 482 passed |
| Drift gate | detects the change (exit 1) before regen; `PASS` after |
| Check mode non-mutating | receipt sha256 byte-identical before and after a check run |

## T08 terminal outcome

Required before T09 starts. Legal values: `IMPLEMENTED` · `NOT_REQUIRED`.

- Current: **`IMPLEMENTED` required**, scoped to lifecycle debris and a reconciler. The serving
  invariant holds and needs no change — see §T07.
- Evidence: §T07 above, verified independently against live Neo4j.

## Route identity manifest

Owned by T13, resolved by T16. **G3b fails if any canonical route remains `pending`.**
Authority is the audited route manifest, not a repository-wide `<h1>` count.

Nine canonical routes render no `h1` in their normal loaded state at baseline: `/`, `/returns`,
`/support`, `/support/work-queue`, `/support/rma-tickets`, `/approvals`, `/sync`,
`/operations`, `/operations/cases`.

Manifest file: `PENDING — created by T13`

## Audit evidence bundle

Preserved as evidence, never used as a test fixture. T04 closure runs against **fresh isolated
identifiers**; these records are repaired only after the new invariant passes.

| Record | Identifier | Exists? |
|---|---|---|
| RMA ticket | `TCK-1c8a77ec-ed8d-478d-afcb-653009d91689` / `RMA-AUDITRMASESSION001` | not yet inventoried |
| Session | `audit-rma-session-001` | not yet inventoried |
| Discovery conversations | 3 abandoned, plus expired interceptions | not yet inventoried |

Inventory is performed by T19b before any repair.

## Blockers

| Task | Blocker | Evidence | Owner | Required input |
|---|---|---|---|---|

None recorded.

## Completion

- Total tasks: 26 (including P00 and T19a–d)
- Accepted: 0
- In progress: 1
- Blocked: 0
- Not started: 25

Percentage is computed from accepted tasks, never estimated.
