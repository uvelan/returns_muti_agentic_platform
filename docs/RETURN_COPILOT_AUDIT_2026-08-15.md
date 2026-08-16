# Return Copilot — End-to-End Audit

**Status:** Findings of record. Superseded only by the fixes landing.
**Audit baseline:** `2878be07433e76c9b75f1cc38985248568313f48` plus the unpushed working tree
(5 modified, 17 untracked, 0 staged)
**Branch:** `refactor/unified-return-platform`
**Remediation:** [`RETURN_COPILOT_REMEDIATION_PLAN.md`](RETURN_COPILOT_REMEDIATION_PLAN.md)
**Audited:** 2026-08-15

**The Copilot is wired to real endpoints and driven by invented data.**

This describes the platform as it was at the audit baseline, not as it will be after remediation.
Every finding is marked with how it was established: `RUNTIME CONFIRMED`, `STATICALLY CONFIRMED`,
`NOT REPRODUCED` or `NOT IMPLEMENTED`. Findings were taken against a live compose stack with a
Vite dev server running the unpushed source — the containerised frontend image predates these
changes by two weeks and was excluded.

| | |
|---|---|
| Stack | compose (Mongo, Neo4j, SQL Server, Temporal, Valkey, Vault) — live, unsealed |
| Frontend | Vite from source on `:5273` |
| Date | 2026-08-15 |

---

## 1. Executive verdict

### `BROKEN`

The Copilot cannot complete a single turn against the running backend. It sends `agent_id: "order_discovery"`; the active schema declares the policy under `order-discovery-agent`. Every conversation dies on the first message with `422 · Agent policy is unavailable` — reproduced in the browser against live infrastructure.

Fixing that one string does not make the Copilot work; it makes the rest of the audit visible. Underneath it, five of the eight lifecycle modes render hardcoded literals, policy evaluation does not exist anywhere on this code path, and a Support-issued RMA never advances the screen. With the agent id corrected the honest grade becomes **PARTIALLY CONNECTED**: discovery, cases, RMA records and return history are genuinely wired; item selection, policy, RMA presentation, tracking, warehouse and settlement are not.

| Count | Category |
|---:|---|
| **7** | P0 — blocks workflow |
| **6** | P1 — production risk |
| **3** | P2 — hardening |
| **5 / 8** | modes running on literals |

### The three structural facts everything else follows from

1. **The Copilot creates a *Case*; five of its eight modes read a *ReturnSession*.** These are different aggregates with different workflows. `create_case` writes `sessionId: None` and nothing ever sets it. Runtime: all 8 cases in Mongo have `sessionId: null`, and `return_sessions` holds **0** documents. `session` is permanently `null`, so modes 6–8 are unreachable and mode 5 falls through to fabricated defaults.

2. **No policy evaluation exists on the case path.** `ReturnCaseWorkflow` registers eight activities; `evaluate_return_eligibility` is not one of them. The eligibility gateway that does exist belongs to the session orchestrator, which has no sessions.

3. **Support answers exactly once, forever.** The `support_response` signal is first-wins and the workflow completes immediately after recording it. Delayed tracking, a delayed label, a corrected RMA — none can ever arrive. Runtime-confirmed: a second outcome returned `500 · workflow execution already completed` and the record was untouched.

---

## 2. Copilot lifecycle matrix

Derived from `deriveCopilotMode` in `types.ts`, not from documentation. These are the eight modes the implementation actually has.

| Stage | UI | API | Backend | Persistence | Async | Verdict |
|---|---|---|---|---|---|---|
| `DISCOVERY` | real | `POST /turns` | OrderDiscoveryWorkflow | conversations | n/a | **blocked** |
| `CANDIDATE_ORDER` | real | turn `query_evidence` | graph search | candidate set | n/a | **wired** |
| `ITEM_SELECTION` | literals | none | none | none | none | **fabricated** |
| `RETURN_EVALUATION` | literals | none | not implemented | none | none | **fabricated** |
| `AUTHORIZED_RMA` | mixed | `GET /api/cases/{id}` | ReturnCaseWorkflow | return_records | poll 10s | **unreachable** |
| `CARRIER_TRANSIT` | literals | `GET /api/returns` | session orchestrator | 0 sessions | none | **unreachable** |
| `WAREHOUSE_RECEIVING` | literals | `GET /api/returns` | session orchestrator | 0 sessions | none | **unreachable** |
| `RETURN_SETTLEMENT` | literals | none | no contract exists | none | none | **fabricated** |

### Missing stages

Measured against the canonical lifecycle, the implementation has no equivalent of:

- **Return Facts Collection** — facts are captured by the agent into the case fact log but never read back into the Copilot.
- **Policy Evaluation** — absent end to end.
- **Support Handoff** as an associate action — it is automatic and invisible.
- **Return Instructions** — `shippingInstructionReference` is rendered as an opaque string labelled "Pickup".

### Transition reality

Mode 3 is entered by a purely client-side act: clicking *Select* on a candidate runs `setCandidates([{ ...chosen, items: selectedItems }])`, attaching two hardcoded sample items so that `deriveCopilotMode`'s `Array.isArray(candidates[0].items)` test passes. No backend call is made, no `CONFIRM_ORDER` is issued, and no case is created by that click.

---

## 3. Frontend ↔ backend contract findings

### P0 — Agent id does not match the configured agent policy

`RUNTIME CONFIRMED`

- **Stage:** Conversation Started → every turn
- **Observed:** First message returns `422`; the Copilot renders "Agent policy is unavailable." and the conversation cannot proceed.
- **Expected:** The turn reaches the reasoning graph and returns statements.
- **Root cause:** The frontend hardcodes `agentId: "order_discovery"`. The active schema's `agent_policies` map is keyed `order-discovery-agent`. `runtime.schema.agent_policies.get(request.agent_id)` returns `None` and the activity fails closed.
- **Frontend:** `ReturnCopilotPage.tsx:185` — `send` mutation, `agentId` argument
- **Backend:** `workflows/order_discovery_activities.py:86`; `config/dynamic_knowledge/active-schema.return-order.yaml:5011`
- **API:** `POST /api/v2/order-agent/conversations/{id}/turns`
- **Reproduction:** Copilot at `localhost:5273/returns`, message "I need to return an item from my recent order." → `422`. Same payload with `order-discovery-agent` passes policy resolution and reaches the model gateway.
- **Fix:** Send the configured agent id. It must not stay a frontend literal — expose it on `GET /api/runtime-config` so a schema rename cannot silently break the Copilot again.

### P0 — Lifecycle mode ignores the return records it is given

`RUNTIME CONFIRMED`

- **Stage:** RMA Created → Completion
- **Observed:** Resuming a real case (`aec15726…`, order `CQ363350`, status `AWAITING_SUPPORT`) populates the rail with case and order, polls `GET /api/cases/{id}` every 10s — and leaves the mode at `DISCOVERY`, with the Progress pane reading "Ready" and the business pane showing search tips.
- **Expected:** A case with a confirmed order and, later, an RMA advances the screen.
- **Root cause:** `CopilotStateContext` declares `returnRecords` and `caseDetail`, and `deriveCopilotMode` reads neither. Mode is derived only from `session`, `candidates`, `turn.response.status` and the fabricated `policyEvaluation`.
- **Frontend:** `types.ts:84–150` — `deriveCopilotMode`
- **Backend:** none — the data is already correct on the wire
- **Reproduction:** Previous returns → open an `AWAITING_SUPPORT` case → 5 polls observed over 29s, mode never leaves `DISCOVERY`.
- **Fix:** Derive from the case projection: `returnRecords.length > 0` ⇒ `AUTHORIZED_RMA`; `case.status` and `unassignedItems` drive the earlier modes.

### P0 — Five modes read a ReturnSession that never exists for a Copilot return

`RUNTIME CONFIRMED`

- **Stage:** Authorized RMA, Carrier Transit, Warehouse Receiving, Settlement
- **Observed:** `GET /api/returns` → `[]`. All 8 cases carry `sessionId: null`. `return_sessions` is empty.
- **Expected:** Modes that claim to show carrier, bay and settlement state have an authoritative source.
- **Root cause:** Two parallel aggregates. Sessions are created only by the SYSTEM channel or the frozen `/api/v1/associate-returns` path; the Copilot's `CONFIRM_ORDER` creates a case and starts `ReturnCaseWorkflow` instead. `create_case` writes `sessionId: None` and no writer ever fills it.
- **Frontend:** `ReturnCopilotPage.tsx:133–147`; `types.ts:92–124`; `CarrierTransitMode`, `WarehouseReceivingMode`, `ReturnSettlementMode`
- **Backend:** `operations/repository.py:918` — `create_case`
- **Fix:** Do not link a session. Project shipment, warehouse and settlement state onto the case read model and have the modes read that.

### P1 — Session lookup regressed to the client-side join HEAD had removed

`STATICALLY CONFIRMED`

- **Observed:** The working tree keys the session query on `resolvedOrderReference(candidates)` — the raw search candidate. HEAD keyed it on `confirmedOrderReference` from the case, with a comment explaining that two open orders sharing a reference previously showed the wrong one.
- **Root cause:** Local rewrite. `confirmedOrder` is still computed at line 292 but now feeds only display and `ItemSelectionMode`.
- **Frontend:** `ReturnCopilotPage.tsx:131–147` vs `HEAD:…/ReturnCopilotPage.tsx:419–425`
- **Fix:** Restore the case-anchored lookup, or delete the session query outright once the case projection carries the fields.

### P1 — Wrong-field bindings present unrelated data as carrier and ETA

`STATICALLY CONFIRMED`

- **Observed:** `carrier = session?.orderSource` — the order's source system rendered as "Carrier & Service". `eta = session?.shippingPathExpectation` — a return-method enum rendered as "Est. Delivery".
- **Frontend:** `AuthorizedRmaMode.tsx:25`; `CarrierTransitMode.tsx:48,50`
- **Backend:** No carrier or ETA field exists on `ReturnSessionView` or `ReturnRecordView`.
- **Fix:** Add `carrier` and `estimatedDeliveryAt` to the shipment projection; bind the modes to them.

### Contract checks that passed

- Envelope, paths and verbs for `/api/cases`, `/api/cases/{id}`, `/api/return-history`, `/api/returns`, and all three order-agent routes match the backend routers exactly.
- `ConversationSummary`, `ConversationTranscript`, `CaseSummary`, `CaseDetail`, `CaseReturnRecord` and `ReturnRecordView` field names and nullability agree between TypeScript and the Pydantic models.
- Case reads are correctly scoped by tenant and principal; a foreign case is a 404, verified at runtime.
- Turn submission carries an idempotency key and an optimistic `expected_conversation_version`; the backend answers `409` on conflict.

---

## 4. Policy evaluation

The suspicion in the brief is correct, and worse than incomplete: **there is no policy evaluation on the Copilot path at all**, and the UI presents a fabricated approval in its place.

### P0 — Eligibility is invented in the browser and never computed

`NOT IMPLEMENTED`

- **Stage:** Return Analysis → Decision / Eligibility
- **Observed:** On any turn whose `business_capability` is `POLICY_EVALUATION` or whose `status` is `APPROVED`, the page sets a literal: `{isEligible: true, status: "APPROVED", policyCode: "POL-STD-30D", estimatedRefund: 149.99, restockingFee: 0}`. `ReturnEvaluationMode` is then rendered with a *second* literal object built inline, ignoring even that state, and carries its own `DEFAULT_EVALUATION` with the reason text "Standard 30-day warranty return within policy window."
- **Expected:** An authoritative decision with provenance, persisted, that the workflow honours.
- **Root cause — three independent gaps:**
  1. `POLICY_EVALUATION` is not in `allowed_business_capabilities`. The six permitted values are `entity-resolution`, `order-discovery`, `product-discovery`, `purchase-history`, `candidate-disambiguation`, `return-context-collection` — so `ResponseSafetyGuard` would reject that capability outright.
  2. `status` on `StructuredAgentResponse` is a free-form string produced by the model; keying business state off `"APPROVED"` / `"ISSUED"` is keying off LLM prose.
  3. `ReturnCaseWorkflow` registers `record_case_status`, `resolve_business_deadline`, `request_bay_assignment`, `draft_support_request`, `open_support_work_item`, `send_support_reminder`, `record_support_outcome`, `synchronize_return_records` — and nothing else.
- **Frontend:** `ReturnCopilotPage.tsx:193–204`; `ReturnCopilotPage.tsx:387–400`; `ReturnEvaluationMode.tsx:18–26`
- **Backend:** `workflows/worker.py:41–58`; `workflows/return_case_workflow.py:442–470`; `workflows/eligibility.py` (defined, never registered on this queue)
- **API:** none exists
- **Fix:** Insert an eligibility activity into `ReturnCaseWorkflow` between fact collection and `_open_support`; persist the decision as case facts with source and policy version; expose it on `CaseDetail`; block the support handoff on a rejection.

**The path the brief asked to detect exists.** `ReturnCaseWorkflow.run` goes `GATHERING_INFO → _gather_bay → _open_support → _await_support → _record_support_outcome`. A case is handed to Support and an RMA is issued with no eligibility gate anywhere in between. An out-of-policy return progresses to RMA. Classified **P0**.

### Answers to the specific questions

| Question | Answer |
|---|---|
| Is there a policy evaluation service/agent? | In name only — `EligibilityGatewayService`, task `RETURN_ELIGIBILITY_V1` |
| Is it invoked during the Copilot flow? | No |
| At what lifecycle state? | None |
| What data is passed to it? | None |
| Where do policies come from? | `config/returns/production.yaml` — read for return-method validation only |
| Is the result persisted? | No |
| Is provenance retained? | No |
| Is the decision returned to Copilot? | No |
| Can the associate see eligibility / decision / conditions / exceptions / approval / reason? | Only the invented ones |
| Does the workflow block or advance on evaluation? | No |
| Is the UI presenting fake/static eligibility? | **Yes** |

---

## 5. Support handoff

The Channel B → Channel A bridge is genuinely built and is the strongest part of this path. The defects are at its edges.

The handoff is **automatic, not associate-initiated**: `ReturnCaseWorkflow._open_support` drafts a request and opens a work item as soon as the case exists. The Copilot has no handoff control and no visibility that it happened — `case.status` moves to `AWAITING_SUPPORT` and the Copilot never reads `case.status`. The two buttons that look like handoff controls ("Evaluate Policy & Submit", "View RMA & Shipping Label") send the free-text chat messages `"evaluate policy"` and `"authorize rma"` to the discovery agent, which has no such capability.

Support's reply arrives correctly: `POST /api/v1/return-support/work-items/{id}/return-outcome` signals the case workflow, which writes return records, appends `return_reference` / `tracking_reference` / `label_reference` / `return_location` as case facts, and syncs to the graph before completing. Correlation is sound — the work item knows its case, the case id is derived into the workflow id, and the associate does not need to restart the conversation.

### P0 — Support's RMA is lost with a 500 when the case workflow has closed

`RUNTIME CONFIRMED`

- **Observed:** `POST …/return-outcome` → `500 · An unexpected system error occurred`. Backend log: `temporalio.service.RPCError: workflow execution already completed`. Nothing is persisted; Support has no idea the RMA did not land.
- **Expected:** A meaningful refusal, or a path that still records the outcome.
- **Root cause:** `submit_return_outcome` guards for a missing runtime and a missing case but not for a completed or terminated execution; the raw `RPCError` escapes.
- **Backend:** `api/return_support.py:385–404`
- **Reproduction:** Work item `3a5c8229…` (case `aec15726…`, workflow TERMINATED) and work item `70ddea7d…` (case `d3190045…`, workflow COMPLETED) — both 500.
- **Fix:** Catch the RPC error and answer `409 CASE_WORKFLOW_CLOSED` with the case id. Pair with the reconciliation fix below.

### P1 — Case status is not reconciled with workflow liveness

`RUNTIME CONFIRMED`

- **Observed:** Six cases read `AWAITING_SUPPORT` in Mongo while their `return-case-*` executions are `TERMINATED`. The Copilot polls them forever; Support cannot answer them; the operations console reports them as waiting.
- **Root cause:** Status is written by an activity and never re-derived. `workflows/return_case_recovery.py` exists but does not cover a terminated execution.
- **Backend:** `workflows/return_case_recovery.py`; `operations/repository.py`
- **Fix:** On case read, or on a recovery sweep, reconcile against the execution and surface an `ORPHANED` state the UI can show and an operator can retry.

---

## 6. RMA

The RMA does reach the Copilot — into one small panel in the Progress column, and nowhere else.

`ProgressTruthPane`'s `ReturnRecordsPanel` reads `caseDetail.returnRecords` correctly and honestly: it renders the RMA reference, status, tracking, label, return location and pickup reference, omitting each field that is null rather than substituting anything. This is the only part of the Copilot that tells the truth about a return.

`AuthorizedRmaMode` — the pane that exists to present the RMA — does the opposite, and it is unreachable anyway. Every field falls back to a literal when the real value is absent:

```
rmaNumber      = record.returnReference   ?? session?.returnReference   ?? "RMA-2026-78901"
destination    = record.returnLocation    ?? …                          ?? "Facility East Bay Dock (DC-7)"
carrier        = session?.orderSource                                   ?? "FedEx Freight / Ground Prepaid"
method         = session?.approvedReturnMethod                          ?? "Prepaid Ground Dropoff"
trackingNumber = record.trackingReference ?? session?.trackingReference ?? "TRK-98421049281"
```

A real RMA with no tracking yet therefore displays `TRK-98421049281` as though the carrier had booked it. Approved items, quantities and disposition are not rendered at all, despite `CaseReturnRecord.items` carrying line references, quantities, reasons and conditions. Expiry and support instructions have no field anywhere in the contract.

### Asynchronous ordering scenarios

| Scenario | Backend | Copilot | Result |
|---|---|---|---|
| A · RMA, then tracking, then label | second signal ignored; workflow closed | polling already stopped | **fails** |
| B · RMA + tracking, label later | same | polling already stopped | **fails** |
| C · RMA, pickup info later | same | polling already stopped | **fails** |
| D · partial RMA, updated later | `500`, record unchanged | shows the stale partial forever | **fails** |
| E · duplicate / replayed event | first-wins signal; ids minted by the workflow; unique index on `returnRecordId` | idempotent | **holds** |

Convergence to the latest authoritative state is not achievable in this architecture. One Support reply is the entire vocabulary the case has for Support.

---

## 7. Label and tracking

### P1 — Polling stops on RMA existence, not on business completion

`STATICALLY CONFIRMED`

- **Observed:** `refetchInterval: (query) => (query.state.data?.returnRecords.length ?? 0) > 0 ? false : 10_000`. The first record — with any combination of nulls — stops all refetching permanently. `staleTime` is 30s and `refetchOnWindowFocus` is `false`, so nothing else brings the case back.
- **Expected:** Polling continues until tracking, label, destination and instructions are present, or the case reaches a terminal state.
- **Runtime precondition:** The dangerous state exists in real data today. Record `4e372a39…`:

  ```
  returnReference:              RMA-OPS01-CD4364
  labelReference:               LBL-OPS01
  trackingReference:            null
  returnLocation:               null
  shippingInstructionReference: null
  status:                       ISSUED
  ```

  …with its workflow `COMPLETED`. A Copilot watching that case stops polling with tracking permanently absent.
- **Frontend:** `ReturnCopilotPage.tsx:150–155`; `main.tsx:15–31`
- **Fix:** Stop on a backend-declared completion flag, not on array length. Add `revision` / `updatedAt` to `CaseDetail` and poll until the case says it is done.

### P1 — The label is a bare string and the label button prints the web page

`STATICALLY CONFIRMED`

- **Observed:** `labelReference` is a `str | None` with no artifact behind it. It is rendered as text in one place. "Print Shipping Label & BOL" in `AuthorizedRmaMode` calls `window.print()`, because `ReturnCopilotPage` passes no `onPrintLabel`. The pane also draws a decorative barcode glyph around the RMA number, which is not a scannable barcode.
- **Expected:** A retrievable document with a content type and an authorization boundary.
- **Root cause:** No label artifact endpoint exists on the case surface. `/api/v1/returns/{id}/artifacts` is session-scoped, and Copilot returns have no session.
- **Frontend:** `AuthorizedRmaMode.tsx:110–124`; `ProgressTruthPane.tsx:118`
- **Backend:** `api/return_artifacts.py` (wrong aggregate); `operations/models.py::ReturnRecordView`
- **Fix:** Model the label as a structured artifact — `{documentId, mediaType, fileName, url, expiresAt}` — as a list on the return record, and add `GET /api/cases/{caseId}/returns/{recordId}/label`. One RMA must be able to carry several labels for several packages.

**Tracking** has no independent update path. It is a single nullable string written once by `record_support_outcome`, in the same transaction as the RMA. A reissued tracking number cannot be recorded. The Copilot does not hold a stale snapshot — `caseDetail` is the authoritative read — but it stops asking for it, which produces the same outcome. Expired signed URLs, wrong-RMA association and multiple packages are all **NOT IMPLEMENTED** rather than defective: the model has no place to express them.

---

## 8. Real associate journey

Driven in a browser against the live compose stack, running the local unpushed source via Vite. The containerised frontend image predates these changes by two weeks and was not used.

### Flow 1 · Happy path — **failed at turn 1**

> "I need to return an item from my recent order."

`422`. The chat renders "Agent policy is unavailable." Identification, order selection, product selection, quantity, reason, policy, handoff, RMA and completion were all unreachable.

### Flow 2 · Multiple facts at once — **not reproduced**

> "I'm John Smith, ZIP 75001. I need to return two damaged valves from my order last week."

Blocked by the same 422. Static reading is favourable: `AgentAction.observed_facts` accepts facts on any action specifically so an opening sentence is not re-asked, and the planner ranks the next discriminator by measured selectivity rather than a fixed questionnaire.

### Flow 3 · Partial identity — **not reproduced**

> "The customer is probably John from Ferguson Dallas."

Blocked. The confirmation contract is sound by construction — `CandidateSet.validate_selection` binds a confirmation to a real search, this conversation, principal, tenant and graph generation, and refuses an expired set — so a silent finalisation is not possible through the agent. It *is* possible through the UI: the *Select* button finalises client-side with no confirmation at all.

### Flow 4 · Support delay — **partial**

Resumed a real `AWAITING_SUPPORT` case and held the Copilot open. Polling is live and correct — 5 requests to `/api/cases/{id}` in 29s. Convergence is not: the mode stayed `DISCOVERY`, Progress read "Ready", and the business pane showed discovery tips for a case with a confirmed order.

### Flow 5 · Policy rejection — **not implemented**

No policy gate exists, so no return can fail one. The workflow advances from fact collection straight to Support and RMA.

### Flow 6 · Exception / approval — **not implemented**

`ReturnEvaluationMode` renders supervisor-override controls, but `ReturnCopilotPage` passes neither `onApproveOverride` nor `onRequestException`, so both buttons are unrendered. No approval concept exists in the backend case model.

### Flow 7 · Refresh mid-workflow — **failed**

Reload discards everything. `conversationId` is `useState(() => newConversationId())` with no URL parameter and no storage; `history`, `turn`, `candidates`, `resumedCaseId` and `policyEvaluation` all reset. The case is recoverable only by the associate manually opening "Previous returns" and recognising the row.

### Flow 8 · Duplicate action — **holds**

Send is guarded by `send.isPending`; each submission mints its own turn id used as both `client_turn_id` and `idempotency_key`; the backend enforces `expected_conversation_version`. Case creation is idempotent on `confirmationKey` via a unique partial index, and RMA record ids are minted by the workflow so a replay is a no-op.

---

## 9. Race conditions and stale state

| Condition | Behaviour | Status |
|---|---|---|
| Support update before the frontend starts polling | Safe. The first `caseDetail` fetch reads current state; no event stream to miss. | safe |
| Support creates the RMA while a case fetch is in flight | Safe. React Query resolves the newer request; the next poll converges. | safe |
| Label or tracking arrives after polling stopped | Never delivered — and never sent, because the workflow has closed. | **defect** |
| RMA updated twice | Second update rejected at the API with a 500. | **defect** |
| Support retries the same event | Ignored by the signal handler; ids replayed; unique index holds. | safe |
| Older turn response lands after a newer one | Prevented by the `isPending` guard and version check, not by request ordering. | fragile |
| Candidates clobbered by a later turn | `send.onSuccess` overwrites `candidates` whenever a turn returns any search result, discarding the `items` array the *Select* click injected and dropping the screen out of `ITEM_SELECTION` back to `CANDIDATE_ORDER`. | **defect** |
| Reconnect after a backend outage | Queries retry once on 5xx and stop. No focus refetch, no reconnect refetch. | fragile |
| Conversation resumes after an application restart | Transcript and case restore; conversation version restores; candidates and evidence do not, so the screen falls back to `DISCOVERY`. | fragile |
| Case exists, Support artifact temporarily unavailable | Records render with nulls omitted in the Progress pane; `AuthorizedRmaMode` would substitute literals. | **defect** |

There is no SSE or WebSocket on the Copilot path. A stream endpoint exists at `/api/v1/returns/{session_id}/stream`, but it is session-scoped, not on the canonical `/api/returns` surface, and unused. Copilot updates are polling only.

---

## 10. Backend changes required

### P0 — blocks correct workflow

1. **Publish the agent id.** Add the configured discovery agent id to `GET /api/runtime-config` so the Copilot stops guessing it. `configuration/api`, `api/runtime_config`.
2. **Insert policy evaluation into `ReturnCaseWorkflow`.** A new activity between fact collection and `_open_support`, registered in `create_return_workflow_worker`, reusing `EligibilityGatewayService`. `workflows/return_case_workflow.py`, `workflows/return_case_activities.py`, `workflows/worker.py`.
3. **Persist and expose the decision.** Case facts carrying eligibility, decision, conditions, exceptions, required approval, policy code, policy version and source; surfaced as a `policyEvaluation` block on `CaseDetail`. `api/cases.py`.
4. **Gate the handoff.** A rejection must park the case rather than open a Support work item.
5. **Make Support's reply repeatable.** Replace first-wins with an accumulating `support_response` that upserts records by `returnReference`, keeps the workflow alive until the return is business-complete, and treats a replayed `work_item_id` + payload hash as a no-op. `workflows/return_case_workflow.py:408–418`, `return_case_activities.py:472–560`.
6. **Stop losing RMAs on a closed workflow.** Catch the Temporal `RPCError` and answer `409 CASE_WORKFLOW_CLOSED`. `api/return_support.py:385`.
7. **Project shipment, warehouse and settlement onto the case.** Modes 6–8 need a source that is not `ReturnSession`.

### P1 — required for reliable production behaviour

1. **Completion semantics on the case.** `CaseDetail` gains `revision`, `updatedAt` and an explicit `awaiting` / `businessComplete` flag so the client stops polling on truth rather than on array length.
2. **Label as an artifact.** `{documentId, mediaType, fileName, url, expiresAt}`, a list per record, plus `GET /api/cases/{caseId}/returns/{recordId}/label`.
3. **Carrier and ETA fields** on the shipment projection, so the Copilot stops rendering `orderSource` as a carrier.
4. **Approved items on the RMA.** Quantity approved versus requested, disposition, and per-item status on `CaseReturnItem`.
5. **Reconcile case status with workflow liveness** and expose an orphaned state. `workflows/return_case_recovery.py`.
6. **Return-facts read model.** The agent already writes observed facts to the case; expose the latest-per-name projection on `CaseDetail` so the Copilot can show what the customer said without re-asking.

### P2 — hardening

1. Push updates for the case — SSE on `/api/cases/{id}`, or a revision cursor the client can long-poll.
2. Idempotency on `submit_return_outcome` keyed on work item and payload, so a retry after a timeout is provably safe end to end.
3. Contract tests that assert the frontend's agent id and capability strings against the active schema, so a rename fails CI rather than production.

---

## 11. Frontend integration fixes

Integration, state and data binding only. No layout, styling, component structure or navigation changes are proposed or required.

1. **Send the configured agent id** from runtime config instead of the literal `"order_discovery"`.
2. **Derive the mode from the case projection.** `deriveCopilotMode` must read `caseDetail` and `returnRecords`, which it already receives and ignores. Remove `turn.response.status === "ISSUED"` and the `business_capability` check — both key off free-form model output.
3. **Delete the fabricated policy state.** Remove the `setPolicyEvaluation` literal, remove the inline literal passed to `ReturnEvaluationMode`, remove `DEFAULT_EVALUATION`, and bind the pane to `caseDetail.policyEvaluation`. Render an explicit pending state while the decision is absent.
4. **Delete `DEFAULT_SAMPLE_ITEMS` and `DEFAULT_ITEMS`.** Bind `ItemSelectionMode` to real order lines, and remove the `"CW273354"` fallback.
5. **Make *Select* confirm the order** through the agent's `CONFIRM_ORDER` path rather than mutating local candidate state.
6. **Remove every literal fallback** in `AuthorizedRmaMode`, `CarrierTransitMode`, `WarehouseReceivingMode` and `ReturnSettlementMode`. An absent value must render as absent, the way `ProgressTruthPane` already does.
7. **Poll to business completion** using the backend flag; keep polling while any expected artifact is null.
8. **Restore case anchoring** for the session lookup, or delete the query once the case projection carries the fields.
9. **Survive reload.** Put the conversation and case id in the URL and rehydrate from `/api/cases?conversationId=`.
10. **Stop clobbering candidates** in `send.onSuccess` once an order is confirmed.
11. **Wire the label action** to the artifact endpoint instead of `window.print()`.
12. **Rewrite the MSW handlers to the real contract.** They currently emit `POLICY_EVALUATION`, `APPROVED` and `ISSUED`, and case bodies carrying `customerReference`, which `CaseView` forbids. They are the reason this build looks like it works.

---

## 12. Files that must change

| File | What |
|---|---|
| `frontend/src/domains/returns/ReturnCopilotPage.tsx` | `send` agentId · `setPolicyEvaluation` · `DEFAULT_SAMPLE_ITEMS` · `caseDetail.refetchInterval` · `activeSession` · `onSelectCandidate` · inline evaluation literal · reload rehydration |
| `frontend/src/domains/returns/types.ts` | `deriveCopilotMode` — read `caseDetail` and `returnRecords`; drop model-status keys |
| `frontend/src/domains/returns/modes/ReturnEvaluationMode.tsx` | `DEFAULT_EVALUATION` and every `??` fallback |
| `frontend/src/domains/returns/modes/ItemSelectionMode.tsx` | `DEFAULT_ITEMS`, `orderReference` and `branchName` defaults |
| `frontend/src/domains/returns/modes/AuthorizedRmaMode.tsx` | five literal fallbacks · `carrier` binding · `window.print()` |
| `frontend/src/domains/returns/modes/CarrierTransitMode.tsx` | `DEFAULT_MILESTONES` · `eta` binding |
| `frontend/src/domains/returns/modes/WarehouseReceivingMode.tsx` | facility, bay, scan status and QA routing defaults |
| `frontend/src/domains/returns/modes/ReturnSettlementMode.tsx` | entire settlement ledger is literal; no backend contract exists |
| `frontend/src/api/cases.ts` | add `policyEvaluation`, `revision`, completion flag, label artifacts |
| `frontend/src/api/runtimeConfig.ts` | carry the discovery agent id |
| `frontend/src/mocks/handlers/canonicalHandlers.ts` | align to the real contract; stop emitting capabilities the guard rejects |
| `backend/src/return_platform/workflows/return_case_workflow.py` | `support_response` first-wins · run loop terminates after one outcome · no eligibility step |
| `backend/src/return_platform/workflows/return_case_activities.py` | `record_support_outcome` — upsert rather than create-once |
| `backend/src/return_platform/workflows/worker.py` | register the eligibility activity |
| `backend/src/return_platform/api/return_support.py` | `submit_return_outcome` — handle a closed execution |
| `backend/src/return_platform/api/cases.py` | `CaseDetail` — policy, revision, completion, shipment, label artifacts |
| `backend/src/return_platform/operations/models.py` | `ReturnRecordView` — carrier, ETA, label artifacts, approved quantities |
| `backend/src/return_platform/workflows/return_case_recovery.py` | reconcile status against terminated executions |

---

## 13. Files that must NOT change

The layout, visual system and pane structure are correct and frozen. Nothing in this audit requires touching them.

| File | Why it stays |
|---|---|
| `frontend/src/domains/returns/panes/ReturnCopilotShell.tsx` | the 40fr / 24fr / 36fr grid owner |
| `frontend/src/domains/returns/panes/BusinessObjectPane.tsx` | mode titles and pane chrome |
| `frontend/src/domains/returns/panes/ConversationPane.tsx` | chat, history and quick prompts — presentational and correct |
| `frontend/src/domains/returns/panes/ProgressTruthPane.tsx` | already renders real data honestly; the reference for how the modes should behave |
| `frontend/src/domains/returns/copilotTokens.ts` | layout and header tokens |
| `frontend/src/index.css` | scrollbar and theme utilities |
| `frontend/src/domains/DomainShell.tsx` | shell and rail; unrelated to the integration gaps |
| `frontend/src/domains/returns/modes/DiscoveryMode.tsx` | static guidance by design; no data binding to fix |
| `frontend/src/domains/returns/modes/CandidateOrderMode.tsx` | renders candidate rows generically from real evidence |
| `frontend/src/domains/returns/modes/ReturnHistorySection.tsx` | bound to the real return-history contract |

---

## 14. Final end-to-end verdict

| Question | Answer |
|---|---|
| Is Copilot connected to backend? | **PARTIAL** |
| Is Order Discovery connected? | **PARTIAL** |
| Is Return Analysis connected? | **NO** |
| Is Policy Evaluation implemented and connected? | **NO** |
| Is Support handoff connected? | **PARTIAL** |
| Does Support-created RMA reach Copilot? | **PARTIAL** |
| Does delayed tracking reach Copilot automatically? | **NO** |
| Does delayed label reach Copilot automatically? | **NO** |
| Can Copilot recover after refresh/reconnect? | **NO** |
| Can the full customer return complete through the current Copilot? | **NO** |

### Reading the partials

- **Connected to backend** — the wiring is real for conversations, cases, return records and return history; it is absent for items, policy, shipment, warehouse and settlement.
- **Order Discovery** — the endpoint, workflow, guards, candidate binding and confirmation contract are all built and sound. It is unreachable today because of one wrong string.
- **Support handoff** — the Channel B → Channel A bridge works; the associate has no visibility of it, cannot initiate it, and the two buttons that appear to are sending chat messages.
- **Support-created RMA** — it arrives and renders in the Progress pane. It does not advance the lifecycle, does not populate the RMA pane, and carries no approved items.

---

## Method and evidence

- Audited against the working tree at `2878be0` on `refactor/unified-return-platform`, with 5 modified and 17 untracked files applied.
- Live stack: compose (Mongo, Neo4j, SQL Server, Temporal, Valkey, Vault) with Vault unsealed via `scripts/vault/bootstrap_local_vault.py`; backend and workers restarted to pick up resolved secrets.
- Frontend served by Vite from source on `:5273` — the container image predates these changes by two weeks and was excluded.
- Runtime evidence: live HTTP against the API, browser-driven associate flows, Mongo reads, Temporal execution state, and backend logs.
- **No source file was modified.**
- Reasoning-model routing was unavailable on this host, so LLM-dependent turns are marked `NOT REPRODUCED` rather than asserted.

### Reproducing the environment

Gate 0 of the remediation plan requires reproducing these findings, so the bring-up order matters
— three steps are easy to miss and each produces a misleading failure.

```bash
docker compose up -d                       # datastores + app profile
python scripts/vault/bootstrap_local_vault.py   # Vault starts SEALED; nothing unseals it
docker restart <backend> <workers…>        # started before the unseal hold vault-resolved.invalid
```

1. **Vault starts sealed.** Until `bootstrap_local_vault.py` runs, every secret resolves to the
   `vault-resolved` sentinel and the backend reports `Platform MongoDB is unavailable` — which
   looks like a Mongo outage and is not. `compose.novault.yaml` is the documented alternative.
2. **Restart backend and workers after unsealing.** Processes started before it keep
   `vault-resolved.invalid` as their Mongo host and fail with DNS errors.
3. **The containerised frontend image is stale.** It predates the audited working tree; run Vite
   from source instead, and point `FRONTEND_BACKEND_TARGET` at the compose backend port.
4. **Cold start is slow.** Postgres fsync recovery and SQL Server database recovery can each take
   several minutes on a bind mount; `temporal` restarts until Postgres is ready. Wait for
   `/health/ready` rather than concluding the stack is broken.

