# Return Copilot — Parallel Execution Plan

**Status:** Ready for execution
**Planning baseline:** `2878be07433e76c9b75f1cc38985248568313f48` plus the unpushed working tree
**Branch:** `refactor/unified-return-platform`
**Plan:** [`RETURN_COPILOT_REMEDIATION_PLAN.md`](RETURN_COPILOT_REMEDIATION_PLAN.md)
**Authored:** 2026-08-15

Companion to the remediation plan (spine 0→12). Answers one question: **what can run at the same
time, and what cannot.**

File contention is measured against the tree at the planning baseline, not estimated. Line
numbers will drift as the work lands; the ownership boundaries will not.

Task ids `3A.1`…`3A.8` are the plan's §7.1–§7.8 in order; `6.1` is §12.1.

---

## Dependency graph

```text
                    ┌─ 3A.1  source special_order        ─────────────────┐
   start today ─────┼─ deterministic evaluator (pure)    ─────────────────┤
   (no deps)        ├─ ReturnEligibilityPolicy schema    ─────────────────┤
                    └─ business inputs · dev provider    ─────────────────┤
                                                                          │
Phase 0 ──► Phase 1 ──► Phase 2 ══ CONTRACT FREEZE ═══════════════════════╪══►
 baseline    agent id    2.0 case_repository extraction    (commit 1)     │
                         2.1 enum freeze                   (commit 2) ────┤
                         2.2 projection · revision · awaiting · migration │
                                                                          │
                                    ┌── 3A integration ◄──────────────────┘
                                    ├── 3B support events + outbox
                                    ├── 3C order confirmation
                    fan-out ────────┼── 6.1 order-lines endpoint
                                    ├── 10 recovery
                                    ├── migration/backfill
                                    └── MSW rewrite + test scaffolding
                                              │
                    4 ──► 5 ──► 6 ─────────────┘
                                              │
                              7 ──► 8 ──► 9 ──► 11 ──► 12
                            (frontend, then E2E — not usefully splittable)
```

---

## 1. Start today — four tracks, zero dependency

None of these needs the case contract. Starting them on day one is the difference between a fan-out and a queue.

### 1.1 Source `special_order` / `seller_stocked` — **highest priority**

The active schema has no `special_order`, `stocked`, `non_stock` or `product_class`. The Ferguson baseline's primary branch depends on it, and its own safety rule sends unknown special-order status to `REVIEW_REQUIRED` — so without this, the evaluator decides nothing.

```text
investigate order_line.line_type value domain against the source
  → if it carries the distinction: map it
  → if not: add the attribute via source binding + schema release + sync
```

Pure data-platform work. Touches no case code, no workflow, no frontend. **Longest lead time in the programme, and it gates 3A acceptance rather than 3A code** — which is exactly why it must not wait for Phase 2.

### 1.2 Deterministic evaluator, as a pure function

`PolicyEvaluationInput → PolicyOutcome` imports nothing from the case model. New files, no contention, and fully testable now using Ferguson Examples A–G as fixtures.

Buildable immediately: precedence chain, tri-state fact handling, `REVIEW_REQUIRED` fallbacks, route detection, restocking-fee applicability, `PolicyOutcome` invariants, decimal money, business-calendar date boundaries.

Only **integration** — assembling the input from case facts — needs Phase 2. That is a thin adapter over work already finished.

### 1.3 `ReturnEligibilityPolicy` schema + release validation

A new disjoint class in `return_configuration.py`, riding the existing `LoadedReturnConfiguration` activation, checksum and audit machinery. No new release mechanism.

### 1.4 Non-engineering inputs

| Input | Blocks | Owner |
|---|---|---|
| Ferguson rule authoring | 3A acceptance | business policy |
| Seller restocking-fee schedule | a displayed figure only | business policy |
| Reasoning provider for dev/CI | Gates 1, 6, Phase 12 | platform/ops |

None blocks the start. All have lead time.

---

## 2. Phase 0 and Phase 1

**Phase 0** — baseline capture. Sequential, fast, one person. Nothing parallelises inside it.

**Phase 1** — agent id. Small and nearly isolated: `bootstrap/api.py`, `runtimeConfig.ts`, one line in `ReturnCopilotPage.tsx`, two contract tests. One person, and it unblocks every later manual verification.

---

## 3. Phase 2 — internal ordering, and one trick

Phase 2 is the freeze, but it is not one indivisible block.

**Commit 1 — `case_repository.py` extraction.** Move the case band (`create_case` at line 883 through `list_case_return_items` at 1220) out of the 2695-line `repository.py`. Pure move, no behaviour change. **Nothing else in Phase 2 starts until this lands and everyone rebases.**

**Commit 2 — freeze the enums.** `ReturnCaseStatus` and `CopilotStage` are small, and landing them early rather than at the end of the phase unblocks three tracks that would otherwise wait for the entire projection:

```text
deriveCopilotStage()  as a pure function + precedence and monotonicity tests
migration/backfill    needs the target enum to derive missing stage
MSW rewrite           needs the frozen contract shape
```

**Then split.** Projection models, revision invariant, `awaiting` computation and migration divide cleanly across two people.

---

## 4. Fan-out after the freeze

| Track | Depends on | Contention |
|---|---|---|
| 3A integration | Phase 2 + evaluator from §1.2 | shares `run` with 3B |
| 3B Support events + Temporal outbox dispatcher | Phase 2 | shares `run` with 3A |
| 3C order confirmation | Phase 2 | low |
| 6.1 order-lines endpoint | Phase 2 (caseId route) + 3A.1 for data | new file — none |
| 10 recovery / reconciliation | Phase 2 enums | isolated file — none |
| Migration / backfill | Phase 2 enums | isolated — none |
| MSW rewrite + test scaffolding | Phase 2 contract | frontend-side — none |

Seven tracks. Realistically four can be staffed before coordination cost exceeds the gain.

---

## 5. Contention map — measured

| File | Lines | Wanted by | Verdict |
|---|---|---|---|
| `operations/repository.py` | 2695 | A · C · Ph 6 | resolved by §3 commit 1 |
| `configuration/return_configuration.py` | 991 | 3A · Ph 1 · Ph 6 · Ph 3A.6 | **low** — disjoint sections |
| `workflows/return_case_workflow.py` | 833 | 3A · 3B · Ph 10 | **hot** — see §6 |
| `workflows/return_case_activities.py` | 699 | 3A · 3B | low — different methods |
| `operations/models.py` | 508 | A · Ph 5 | low |
| `frontend/…/ReturnCopilotPage.tsx` | 420 | every frontend phase | single owner |
| `api/return_support.py` | 405 | 3B · Ph 3A.6 | low |
| `api/cases.py` | 220 | A · Ph 3 · Ph 5 · Ph 10 | **serialize** — see §6 |
| `frontend/src/api/cases.ts` | 217 | D only | none |
| `workflows/eligibility.py` | 177 | 3A only | none |
| `frontend/…/types.ts` | 150 | D only | none |
| `workflows/worker.py` | 65 | 3A only | none |

**On `return_configuration.py`:** four workstreams add to it, but in disjoint classes — a new `ReturnEligibilityPolicy` (3A.2), `SupportConfiguration` for the verification queues (3A.6), `ReturnCaseTimingConfiguration` for reservation TTL (Phase 6), and a new copilot section (Phase 1). Only the root composition model is shared, one line each. Coordinate on the root; the sections are independent. Same for `config/returns/production.yaml`.

---

## 6. Hard serialization points

### `return_case_workflow.py:run` — the only true bottleneck

3A inserts the policy gate; 3B rewrites the loop for cumulative Support. Same function, same file. Ownership cannot fix this.

```text
either  3A gate lands → 3B rebases → 3B loop
or      one person owns both
```

Prefer 3A first: the gate is an insertion, the loop rewrite is structural, and rebasing an insertion onto a rewrite is easier than the reverse.

### `api/cases.py` — projection first

A's base projection lands before Phase 3's override route, Phase 5's artifact route and Phase 10's orphan state. 220 lines with four claimants; the base is the shared foundation.

### All frontend — one owner

Every phase from 7 onward wants `ReturnCopilotPage.tsx`. Splitting it produces merge conflicts that cost more than the parallelism returns.

### `ReturnCaseStatus` — define once

Policy states, completion states and `RECOVERY_REQUIRED` all extend it. Declare the **full set** in Phase 2 commit 2 rather than letting three tracks each add values.

---

## 7. Crew shapes

### Solo

```text
0 → 1 → 2 → 3A → 3B → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11 → 12
```

Kick off 3A.1 and the business inputs on day one regardless — they run in the background and would otherwise become the critical path.

### Three people

| Person | Sequence |
|---|---|
| **Backend spine** | Phase 2 → 3B → 4 → 5 |
| **Policy** | evaluator now → 3A on freeze → 9 |
| **Frontend** | MSW + `deriveCopilotStage` on enum freeze → 7 → 8 |

Recovery, migration and 6.1 fill the gaps. The backend-spine and policy people must coordinate on `run`.

### Five people

Add:

| Person | Owns |
|---|---|
| **Data platform** | 3A.1 end to end, then 6.1 order-lines |
| **Test / review** | never edits implementation; watches contract drift, race coverage, hardcoded-fallback sweeps, `ReturnSession` dependency sweeps |

The review role is the one that catches the other four disagreeing — which, given the enum, projection and workflow surfaces are shared, is where the real risk sits.

---

## 8. Critical path

```text
Phase 0 → Phase 1 → Phase 2        sequential floor
      ↓
   fan-out                          compressible with people
      ↓
Phase 7 → 8 → 12                    sequential again
```

Frontend binding and browser E2E do not split usefully, so the tail is fixed regardless of crew size. **The only lever on total duration is how early §1 starts** — particularly 3A.1, which is invisible until it blocks 3A acceptance.

---

## 9. Day-one checklist

```text
[ ] Phase 0 baseline captured and recorded
[ ] 3A.1 started — investigate order_line.line_type value domain
[ ] Ferguson rule authoring assigned to a business owner
[ ] Seller fee schedule requested
[ ] Reasoning provider for dev/CI requested
[ ] Deterministic evaluator scaffolded with Examples A–G as fixtures
[ ] ReturnEligibilityPolicy schema drafted
[ ] Owner named for return_case_workflow.py:run sequencing
[ ] Single frontend owner named
```

Everything on this list is startable before Phase 2 exists.
