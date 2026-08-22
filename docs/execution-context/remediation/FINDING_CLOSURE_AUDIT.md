# Finding closure audit — all 30, verified against the branch

**Branch:** `refactor/unified-return-platform` · **Baseline:** `04a05fb`
**Method:** every row below was re-checked against the current source, not
transcribed from a commit message. Where a finding closed by *reclassification*
rather than by a fix, the reclassification and its evidence are named — a
finding closed on a corrected premise is still closed, but only if the
correction is on the record.

**Standing suite evidence:** 4209 backend tests, 552 frontend tests, 117
Playwright tests over 36 canonical routes. Ruff, tsc and eslint clean; OpenAPI
drift PASS. Two mypy errors in `run_lifecycle.py` and `dependency_simulation/ai.py`
**predate the baseline** and are untouched by this work.

---

## P0

### UIAUDIT-010 — the RMA path never writes the platform-owned return record

**Closed by ruling, not by adding a write.** ADR-001 resolved as **option B**: a
console-issued RMA ticket is a *distinct artifact* whose authoritative home is
`dbo.return_requests`, `dbo.return_items` and `integration.return_support_ticket`;
`dbo.return_record` / `dbo.return_record_item` remain the case workflow's.

The audit's own before/after supports this — those three tables all went 0→1,
and re-measured today they hold exactly 1 row each. What was false was the
screen's claim to *also* write the case tables. Wiring Support to them would
have meant inventing a `case_id`, `tenant_id` and `principal_id` for a session
that has none, and writing a synthetic `dbo.return_case` row — the second
disagreeing source of truth ADR-001 exists to prevent.

| Verified | Where |
|---|---|
| One shared issuance seam exists, both callers can reach it | `operations/return_issuance.py:136,182` |
| The workflow path uses it | `return_case_activities.py:2033` |
| The screen names only what it writes | `RmaTicketsPage.tsx:35-40` |
| A guard test pins the seam's callers | `tests/operations/test_return_persistence_paths_stay_partitioned.py` |

Commits `893e995`, `ae7211f`. Full ruling and its evidence in `LEDGER.md` §T04.

---

## P1

| Finding | Verified closed by | Evidence |
|---|---|---|
| **UIAUDIT-004** Support told "Complete" on an incomplete case | Completeness computed from the requirements resolver, not item selection | `return_case_activities.py:1141` — `required_details_complete = known and not awaiting` (`a894463`) |
| **UIAUDIT-001** Ten `ACTIVE` generations, 800 duplicate orders | **Reclassified**, then the real defect fixed | Serving generation was 9999/9999 unique and reads pattern-level pinned; the defect was lifecycle debris. Reaper extended to abandoned/terminal statuses and orphaned actives (`108a982`). Re-measured today: generations collection empty, exactly **1** active runtime snapshot |
| **UIAUDIT-003** A turn blocks ~14 minutes with no signal or cancel | Abort signal, elapsed counter, stop control, and no same-provider tier re-queue | `orderAgent.ts` carries `AbortSignal`; `final_dispatch.py:735` suppresses escalation onto a route that just timed out (`0ff7ab6`) |
| **UIAUDIT-005** Incompatible workflow change wedges histories forever | `workflow.patched()` guard decoding both shapes | 2 occurrences in `return_case_workflow.py`; 9 replay-compatibility tests with an activity-result lockfile (`ce660bc`) |

---

## P2

| Finding | Verified closed by | Evidence |
|---|---|---|
| **UIAUDIT-002** Sync `RUNNING` 15h, no reaper, no concurrency guard | Heartbeat, stall cutoff, 409, reclaimer | `SYNC_STALL_SECONDS = 150`, `GRAPH_SYNC_ALREADY_RUNNING` at `graph_sync.py:164`, `StalledSyncRunReclaimer` (`87e8d91`) |
| **UIAUDIT-006** "Allow model" row vanishes, expiry never runs | Reaper worker actually started; `ALLOWED` tile; lapsed rows not actionable | `InterceptionExpirySweep` composed at `housekeeping/composition.py:139`; worker entrypoint added (`f520e6d`). The audit's probe had read the wrong database |
| **UIAUDIT-007** "Approved by"/"Activated" absent from the contract | Typed release DTO carrying the lifecycle facts | `releases.py:95` and the served routes at `router.py:151,175` (`0aeb844`) |
| **UIAUDIT-008** UI reads `checksum`, API returns `checksum_sha256` | `checksumSha256` typed with `validation_alias="checksum_sha256"` | `releases.py:95` (`0aeb844`) |
| **UIAUDIT-009** Endpoint returns pending only, tiles have no counts | `list_by_status` plus status-filtered listing and server counts | `store.py` `list_by_status`; `canonical_ai.py:159-169` `?status=` (`f520e6d`) |
| **UIAUDIT-011** 14 unlabelled agent fields | Recursive renderer passes a label id to each leaf | `AgentsSection.tsx` `aria-labelledby` ×3 leaf types; 4 tests, 2 of which fail without the fix (`b1b78c1`) |
| **UIAUDIT-020** `/operations/cases` shows ISSUED for a null status | **Reclassified**, and the real case handled | `recordStatus` appears 0 times in the frontend — the audit's `rrprobe.py` projected a field that does not exist. The genuine defect was a *null* status rendering as an empty pill; it reads `UNKNOWN` now, never `ISSUED` (`dbe6364`, `4c05ffc`) |
| **UIAUDIT-021** Contrast token below 4.5:1 across 39 routes | `outline` 4.28→**5.66:1**; new `outline-control` at **3.43:1** for control boundaries | Measured live in the browser; axe reports zero critical/serious on all 36 routes (`05c47d3`) |

---

## P3

| Finding | Verified closed by | Evidence |
|---|---|---|
| **UIAUDIT-012** Scroll regions unreachable by keyboard | Six focusable, labelled scroll regions | `JsonView.tsx`, `AiControlCenterPage.tsx`, `ObjectViews.tsx` ×2, `ProgressTruthPane.tsx` ×2, plus the copilot pane body (`05c47d3`, `3ff4b90`, `4c05ffc`) |
| **UIAUDIT-013** No focus ring on schema entity cards | The node is a native `<button>`; `nodesFocusable={false}` removes React Flow's inert wrapper stop | `SchemaCanvas.tsx:71,200`. The library's own `:focus-visible { outline: none }` out-specified the app ring, so the card was focusable *and* invisible *and* unselectable — Enter now works (`b1b78c1`) |
| **UIAUDIT-014** No customer name or short id on case rows | Customer named on the rail rather than by internal id | `ProgressTruthPane.tsx:118-122`, asserted by `ReturnCopilotModes.test.tsx:403` |
| **UIAUDIT-015** Two AI rail entries render one screen | Route manifest records every canonical route and its identity | `routeManifest.ts`; the sweep covers all 36 (`3ff4b90`) |
| **UIAUDIT-016** e2e never run against a real stack | Real-stack Playwright project, skipped **loudly** | `playwright.config.ts` `real-chromium`; `tests/realStack.ts` `requireRealStack()`. Unknown-route behaviour now follows `App.tsx` (`→ /all`), which the old spec asserted wrongly (`3ff4b90`) |
| **UIAUDIT-017** `infra.sh start` blocked by the `.env` ACL check | Refusal made actionable; **rule unchanged** | `validate_env.py` names the offending principals and prints the `icacls` command. Still only the current user, SYSTEM and Administrators (`6aee3d3`) |
| **UIAUDIT-018** Host scripts need `flock`, absent in Git Bash | Portable `mkdir` mutex with pid, stale reclaim and bounded wait; `flock` still preferred | `prepare_runtime_configuration.sh`; exercised directly — acquires, blocks a live holder, reclaims a dead one. Escape hatch documented in the README (`6aee3d3`) |
| **UIAUDIT-019** Date formatting drift; SLA breach not styled | One formatter module; SLA reads absolute + "breached 2 hours ago" | `format/datetime.ts` (24 tests), `format/sla.ts` (6 tests). Five private `formatWhen` helpers and four inline `toLocaleString()` calls removed (`05c47d3`, `4c05ffc`) |
| **UIAUDIT-022** No skip link | Both frames render one; both `<main>` take `tabindex="-1"` | Asserted on all 36 routes: first Tab reaches it, Enter moves focus into `<main>` (`05c47d3`) |
| **UIAUDIT-023** Viewport range unstated; 1.4.10 undecided | Decided and enforced: 320/390/640/768/1280/1440 | The 1280 floor removed; zero horizontal overflow measured on the document **and inside `<main>`**, which is where `overflow-x-auto` was hiding it (`05c47d3`, `3ff4b90`) |
| **UIAUDIT-024** Lint errors | Five fixed without suppression | `da04602` |
| **UIAUDIT-025** `run_real_infra_suite.sh` missing | Added | `backend/scripts/dev/run_real_infra_suite.sh` (`adfb245`) |

---

## P4

| Finding | Verified closed by | Evidence |
|---|---|---|
| **UIAUDIT-026** Reduced motion honoured nowhere | Global block, spinner slowed rather than frozen | `index.css` — a stopped spinner reads as a hung screen; skeletons go static (`05c47d3`) |
| **UIAUDIT-027** Dead dark-mode config | `darkMode` removed | 0 occurrences in `tailwind.config.js` (`05c47d3`) |
| **UIAUDIT-028/029** RMA form validation | Pasted items validated instead of cast; quantity defaults rather than becoming `NaN` | `RmaTicketsPage.tsx` `asItem`; 2 tests (`4c05ffc`) |
| **UIAUDIT-030** Secret scanner emitted the first four characters | Redaction contract: rule/provider, path, length, `sha256[:16]` only | `scan_secrets.py`; randomized planted credentials asserted against stdout **and** stderr (`da04602`) |
| **Entrypoint/port inconsistencies** (§3, informational) | One entrypoint, one configured port | `asgi:app` everywhere; `BACKEND_PORT` read by both host scripts; 13 tests (`6aee3d3`) |

---

## Findings this work added

Neither was in the audit.

| Finding | Status |
|---|---|
| **PLAN-NEW-001** `outline-variant` measured 1.62:1 | Closed. Panel dividers identify nothing and stay; boundaries that identify a control moved to `outline-control` at 3.43:1 |
| **PLAN-NEW-002** `orderLineReferences` defaults to `()`, leaving RMAs unlinked | Recorded, **not fixed** — outside every audit finding's scope. Named here so it is not lost |

Also surfaced and fixed while working, unprompted by any finding:

- `client_turn_id` was `ui-${conversationId}-${Date.now()}` and is *also* the
  `idempotency_key`. Two tabs submitting in the same millisecond mint the same
  key, and the server is right to answer the second with the first's result —
  the second associate's message is dropped and they read a reply to a question
  they did not ask.
- Six routes rendered an `<h1>` that disagreed with what the rail called them.
  The audit asked whether a heading existed, not whether it agreed.
- Four authorization surfaces were more permissive than the console in front of
  them, and eight `/api/graph-schema` handlers had no guard at all.

---

## Not closed

| Item | Why |
|---|---|
| **T20** audit-equivalent release validation | Needs a clean environment. The backend on port 8000 predates T12 by ~13 commits (`/openapi.json` exposes no `ConfigurationRelease*` schema), and restarting a process this work did not start is an operator decision |
| **P00 / L1** live-route readiness | The audit found both providers credentialed with **no model route constructed**, under `PLATFORM_AI_PROVIDER_ORDER=MANUAL`. Waiting does not fix this |
| **L2** live Discovery smoke | Blocked behind L1 |
| **T19b apply** | Gated on T04 closure against fresh identifiers, which needs the running stack |
| **T19a** worker-driven recovery | 63 workflows RUNNING with no worker alive; recovery is deploying the patched worker |

**Verdict.** All 30 audit findings are closed or formally reclassified with
evidence. The release gate is **not** met: it additionally requires the live
Discovery outcome (L2), which cannot be reached from here.
