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
| **G0** Control truth | T00, T01a | OPEN |
| **G1** Recovery | T01b, T02, T05, T06 | OPEN |
| **G2** Durable control planes | T07–T12 | OPEN |
| **G3a** Core UI | T13–T15 | OPEN |
| **G3b** Full UI | T16, T17 | OPEN |
| **G4** Operations | T18, T19a–d | OPEN |
| **L1** Live-route readiness | P00 | OPEN |
| **L2** Live Discovery outcome | executed inside T20 | OPEN |
| **G5** Release | T20 + L2 | OPEN |

## Task ledger

Status: `NOT_STARTED` · `IN_PROGRESS` · `IN_REVIEW` · `ACCEPTED` · `BLOCKED`

| Task | Level | Depends | Status | Start SHA | Accepted SHA | Gate | Blocker | Evidence |
|---|---|---|---|---|---|---|---|---|
| P00 live-route readiness | SMALL | — | NOT_STARTED | | | L1 | | |
| T00 control truth | SMALL | — | IN_REVIEW | `04a05fb` | | G0 | | §T00 evidence below |
| T01a truthful gates | SMALL | T00 | IN_REVIEW | `04a05fb` | | G0 | | §T01a evidence below |
| T01b live-infra runner | SMALL | T00 | NOT_STARTED | | | G1 | | |
| T02 Temporal replay recovery | CRITICAL | T00 | IN_REVIEW (code) / BLOCKED (runtime) | `da04602` | | G1 | live stack needed for runtime closure | §T02 evidence below |
| T03 issuance seam | CRITICAL | T01a | NOT_STARTED | | | G1 | | |
| T04 exact-once RMA persistence | CRITICAL | T03 | NOT_STARTED | | | G1 | | |
| T05 canonical completeness | NORMAL | T02 | NOT_STARTED | | | G1 | | |
| T06 status vocabulary | NORMAL | T04 | NOT_STARTED | | | G1 | | |
| T07 graph evidence gate | CRITICAL analysis | T01a | NOT_STARTED | | | G2 | | |
| T08 graph lifecycle correction | CRITICAL / conditional | T07 | NOT_STARTED | | | G2 | | outcome: `PENDING` |
| T09 durable sync runs | CRITICAL | T07 accepted + T08 outcome | NOT_STARTED | | | G2 | | |
| T10 durable manual AI operation | CRITICAL | T01a; live validation via L1 | NOT_STARTED | | | G2 | | |
| T11 interception history | NORMAL | T01a | NOT_STARTED | | | G2 | | |
| T12 configuration release contract | NORMAL | T01a | NOT_STARTED | | | G2 | | |
| T13 real-stack browser harness | NORMAL | T01a | NOT_STARTED | | | G3a | | |
| T14 shared UI foundation | NORMAL | T13 | NOT_STARTED | | | G3a | | |
| T15 return-critical UX | NORMAL | T04, T05, T06, T10, T13, T14 | NOT_STARTED | | | G3a | | |
| T16 route-wide UX and a11y | NORMAL | T12, T13, T14, T15 | NOT_STARTED | | | G3b | | |
| T17 blocked-action and RBAC closure | NORMAL | T09, T11, T12, T13, T16 | NOT_STARTED | | | G3b | | |
| T18 runtime portability and observability | NORMAL | T09, T10, T11 | NOT_STARTED | | | G4 | | |
| T19a wedged-history repair | CRITICAL | T02 | NOT_STARTED | | | G4 | | |
| T19b return mismatch repair | CRITICAL | T04 | NOT_STARTED | | | G4 | | |
| T19c graph debris repair | CRITICAL | T08 | NOT_STARTED | | | G4 | | |
| T19d legacy interception repair | CRITICAL | T11 | NOT_STARTED | | | G4 | | |
| T20 audit-equivalent release validation | CRITICAL | G0–G4 complete + L1 | NOT_STARTED | | | G5 | | |

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

## T08 terminal outcome

Required before T09 starts. Legal values: `IMPLEMENTED` · `NOT_REQUIRED`.

- Current: `PENDING`
- Evidence: —

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
