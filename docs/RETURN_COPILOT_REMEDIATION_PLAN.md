# Return Copilot — Remediation Plan

**Status:** Ready for execution
**Planning baseline:** `2878be07433e76c9b75f1cc38985248568313f48` plus the unpushed working tree
(5 modified, 17 untracked files under `frontend/src/domains/returns/`)
**Branch:** `refactor/unified-return-platform`
**Evidence base:** [`RETURN_COPILOT_AUDIT_2026-08-15.md`](RETURN_COPILOT_AUDIT_2026-08-15.md)
**Execution model:** [`RETURN_COPILOT_PARALLEL_EXECUTION.md`](RETURN_COPILOT_PARALLEL_EXECUTION.md)
**Authored:** 2026-08-15

This plan closes the sixteen findings of the Return Copilot audit. Every "current state" claim
was verified against the tree at the planning baseline; file and line references are real. It
consolidates six working documents — an initial plan, three review patches, and two amendment
sets — into one authoritative statement. §14 records what changed across those rounds and why,
because several of the corrections reversed an earlier decision and a plan without its rejected
alternative is one nobody can review.

---

## 1. Objective

Make the existing Copilot complete a real return end to end:

```text
Conversation → Order Discovery → Order Confirmation → Item Selection
→ Return Facts → Policy Evaluation → Support Handoff → RMA
→ Tracking / Label / Pickup → Warehouse → Settlement → Completed
```

### The visual design is frozen

No changes to the Copilot shell, pane layout, navigation, typography, colours, component
placement, conversation layout, progress pane or business-object pane. Where the frontend
expects data that does not exist, the fix is a backend contract — never removal of Copilot
functionality, and never a new control.

The audit's frozen-surface list is authoritative: `ReturnCopilotShell`, `BusinessObjectPane`,
`ConversationPane`, `ProgressTruthPane`, `copilotTokens.ts`, `index.css`, `DomainShell.tsx`,
`DiscoveryMode`, `CandidateOrderMode`, `ReturnHistorySection`.

---

## 2. The four architectural decisions

### 2.1 The Case is the authoritative aggregate — **CLOSED**

The Copilot creates a Case; five of its eight modes read a `ReturnSession`. `create_case` writes
`sessionId: None` and nothing ever sets it — at the baseline, all 8 cases carry `sessionId: null`
and `return_sessions` holds 0 documents. Shipment, warehouse and settlement become projections
**on the case**.

**Do not create a `ReturnSession` to satisfy a pane.** Backfilling one per case would mean
running two workflows over one return and keeping them consistent forever.

### 2.2 Deterministic policy is authoritative; the model is advisory — **CLOSED**

`EligibilityGatewayPort` is documented as an *AI Gateway* contract and its result carries
`model_provider`, `model_name`, `confidence_millionths`. Its only deterministic path is an error
fallback returning `REVIEW_REQUIRED`. Decisively, `EligibilityEvaluationInput` carries no
policy-relevant facts at all — no purchase date, condition, reason, quantity or price — so a
deterministic evaluator handed that payload would have nothing to evaluate.

```text
conversation → LLM extraction → structured facts
             → deterministic evaluator → APPROVE / REJECT / REVIEW_REQUIRED
             → LLM may explain the result
```

The gateway, its redaction, one-attempt policy and fail-safe fallback are retained as an
**advisory** component. The evaluator is the only producer of an executable decision.

### 2.3 Support answers repeatedly; the case declares completion — **CLOSED**

`support_response` is first-wins and the workflow completes immediately after recording it — so
delayed tracking, a delayed label and a corrected RMA can never arrive. It becomes an
accumulating signal delivered through the existing transactional outbox, upserting by
`returnReference`, for which a unique partial index on `(caseId, returnReference)` already
exists.

### 2.4 Every literal dies in the phase that replaces it — **CLOSED**

Deleting `DEFAULT_MILESTONES` before a shipment projection exists trades a lying pane for an
empty one and reads as a regression. Each phase below ends by removing exactly the fallbacks its
own data makes redundant.

---

## 3. Execution spine

```text
0   Baseline & freeze
1   Runtime agent mapping
2   Case projection + CONTRACT FREEZE
      3A deterministic policy
      3B durable Support event / idempotency      (parallel)
      3C order confirmation
4   Cumulative Support / RMA
5   Shipment / tracking / label contracts
6   Case-bound order lines + transactional selection
7   Frontend binding
8   Polling / revision / reload / reconnect
9   Warehouse + settlement semantics
10  Recovery / reconciliation
11  Cross-contract + race regression
12  Real-browser adversarial E2E
```

Phases 2–5 are backend-critical. **Their absence must never be hidden with frontend state.**
Every phase delivers implementation **plus** targeted tests plus validation plus a commit; Phase
11 keeps only what is genuinely cross-cutting.

---

## 4. Phase 0 — Baseline & freeze

The local working tree is the source of truth; the audited changes are unpushed.

Record and keep `git status --short`, `git branch --show-current`, `git rev-parse HEAD`,
`git diff`, `git diff --cached`. Never reset, stash, checkout away, pull/rebase over local work,
discard untracked files, or push before validation.

Capture the current failing behaviour as the baseline artifact:

```text
POST /api/v2/order-agent/conversations/{id}/turns
  agent_id = order_discovery  → 422 ORDER_AGENT_OUT_OF_SCOPE
                                "Agent policy is unavailable."
GET /api/returns              → []          (0 return_sessions; all cases sessionId: null)
record 4e372a39…              → RMA present, trackingReference null, workflow COMPLETED
6 cases AWAITING_SUPPORT      → executions TERMINATED
POST …/return-outcome         → 500 on a closed workflow
```

**Environment prerequisite with an owner, not a code task.** A STANDARD-tier reasoning provider
must be configured, or Gates 1, 6 and most of Phase 12 cannot run through the UI. Record which
provider the acceptance run used. Backend gates can be driven through Temporal and the Support
API without one.

### Gate 0
The baseline reproduces every failure above before implementation starts.

---

## 5. Phase 1 — Runtime agent mapping

The frontend hardcodes `agentId: "order_discovery"`; the active schema keys the policy
`order-discovery-agent`. Every turn 422s.

**5.1** Explicit mapping in return configuration — never inferred from a single registered
policy, which would be a hidden convention:

```yaml
copilot:
  order_discovery_agent_id: order-discovery-agent
```

**5.2** `RuntimeConfig` gains an `agents` block serving the configured value.
`bootstrap/api.py` — `RuntimeConfig`, `get_runtime_config`.

**5.3** `runtimeConfig.ts` gains the field; `ReturnCopilotPage.tsx:185` reads it and the literal
is deleted. A `null` value disables the composer with a specific configuration error — **never a
fallback to another literal.**

**5.4** Validation asserts the reference resolves:

```text
configured id ∈ active agent_policies  → OK
dangling mapping                       → CI FAIL
production                             → startup fails (as the Vault rule already does)
dev / CI                               → /health/ready reports configuration unhealthy;
                                          turn route returns 503 COPILOT_AGENT_CONFIGURATION_INVALID
```

Configuration validity joins the existing `/health/ready` probe set rather than becoming a
parallel mechanism.

### Gate 1
A real first message reaches Order Discovery. Renaming the agent **and** the mapping keeps
working; renaming only the agent fails CI with a dangling-reference error.

---

## 6. Phase 2 — Case projection and contract freeze

Backend only. No frontend work — that is Phase 7, and it cannot start until this freezes.

### 6.1 Extract the case repository — first commit

Move the case band (`create_case` at line 883 through `list_case_return_items` at 1220) out of
the 2695-line `operations/repository.py` into `operations/case_repository.py`. Pure move, no
behaviour change. Nothing else in Phase 2 starts until this lands.

### 6.2 Freeze both enums — second commit

Landing these early unblocks `deriveCopilotStage()`, the migration script and the MSW rewrite,
all of which would otherwise wait for the whole projection.

```text
ReturnCaseStatus   persisted, workflow-owned, authoritative
  GATHERING_INFO · AWAITING_POLICY_REVIEW · AWAITING_SUPPORT
  PROCESSING_RETURN · COMPLETED · COMPLETED_EXTERNAL_SETTLEMENT
  POLICY_REJECTED · CANCELLED · EXPIRED · RECOVERY_REQUIRED

CopilotStage       derived, never stored
  DISCOVERY · ORDER_CONFIRMATION · ITEM_SELECTION · RETURN_FACTS
  POLICY_EVALUATION · APPROVAL_REQUIRED · AWAITING_SUPPORT
  AUTHORIZED_RMA · CARRIER_TRANSIT · WAREHOUSE_RECEIVING
  RETURN_SETTLEMENT · COMPLETED
```

The relationship is **many-to-many**. Never implement it as a lookup table. Declare the full
`ReturnCaseStatus` set here so that policy, completion and recovery work do not each extend it.

Both are declared in Pydantic, exported through OpenAPI, and consumed in TypeScript from
`src/api/generated/return-platform.d.ts`. `npm run contracts:check` already fails on drift — wire
it into CI so this is enforced rather than agreed.

### 6.3 The projection contract

A **read projection contract**, not necessarily one Mongo document.

```text
caseId · conversationId · tenantId · principalId
status · stage · revision · updatedAt
awaiting[] · businessComplete · isTerminal

customer · confirmedOrder · selectedItems[]
facts[]                    latest-value + provenance
policyEvaluation           originalDecision · effectiveDecision · override
support

returnRecords[]
    returnReference · status · returnMethod · returnLocation
    approvedItems[]          quantityApproved · disposition · itemStatus
    artifacts[]              ← AMENDED: artifacts belong to the RECORD, not the shipment
        artifactId · artifactType · shipmentId | None · fileName · mediaType
        expiresAt · createdAt · version · supersededBy
    shipments[]
        shipmentId · carrier · serviceLevel · trackingNumber
        estimatedDeliveryAt · shipmentStatus · createdAt · updatedAt

pickup · warehouse · settlement
```

Every block is nullable and **absent rather than defaulted** when the platform has not computed
it. That distinction is the whole audit.

**Amendment (2026-08-15, during execution).** This sketch originally nested `labelArtifacts[]`
under `shipments[]`. That made a label with no shipment *inexpressible* — and that is the shape of
real record `4e372a39…` (RMA + label + null tracking), the exact stuck state the audit found. The
only ways to serve it were to invent a shipment to carry the label — the `TRK-98421049281`
fabrication in a new costume — or to drop the label. Artifacts therefore belong to the **return
record**, with `shipmentId` optional and carrying the whole of package attribution: `None` means
"this RMA has this document, no package known yet". `ShipmentProjection.labelArtifacts` was
removed rather than kept as a derived view, because two homes for one document is precisely how a
label ends up attributed to the wrong package — which §11 lists as a required test. This also
aligns the contract with §11's authorization rule, which already treats the record as the owner.

`facts` serves the existing `latest_case_facts` projection — the backend already computes it and
`cases.ts` duplicates it client-side in `latestFacts`. Serving it deletes the duplicate.

### 6.4 Completion semantics

```text
completionProfileResolved =
      policyEvaluation.effectiveDecision == APPROVE
  AND returnMethod is resolved
  AND returnMethod != UNKNOWN

awaiting = completionProfileResolved
             ? required(returnMethod, policy) − satisfied(case)
             : [unresolved dimensions]

businessComplete = completionProfileResolved AND awaiting.isEmpty()
isTerminal       = ReturnCaseStatus is terminal
```

Causal order, never the reverse: requirements satisfied → `businessComplete` → workflow
transitions COMPLETED → `isTerminal`. An earlier draft made completion require a terminal
workflow *and* kept the workflow alive until completion, which deadlocks.

It reads `effectiveDecision`, not `originalDecision` — an overridden `REVIEW_REQUIRED → APPROVE`
must resolve the profile.

**`awaiting` is computed, never mutated imperatively.** The method vocabulary already exists in
`return_policy.normalized_return_methods`, and it is why the method must drive completion:
`NO_PHYSICAL_RETURN` and `CUSTOMER_KEEP` need no label, tracking or pickup, so a hardcoded
"needs tracking and label" rule would hang them forever.

```text
PREPAID_PARCEL     → RMA, LABEL, TRACKING
BRANCH_LTL         → RMA, BOL, PICKUP
OFFSITE_LTL        → RMA, BOL, PICKUP, RETURN_LOCATION
CUSTOMER_KEEP      → RMA
NO_PHYSICAL_RETURN → RMA
```

Align the vocabulary with the existing `workflow.completion_dimensions` rather than inventing a
parallel one.

**Never business-complete regardless of an empty requirement set:** `POLICY_REJECTED`,
`APPROVAL_REQUIRED`, `CANCELLED`, `EXPIRED`, `RECOVERY_REQUIRED`, and any case whose method is
unresolved or `UNKNOWN`.

`RECOVERY_REQUIRED` is **non-terminal** — recovery can restart processing, and a terminal marking
would stop polling on a case about to resume.

### 6.5 Revision atomicity

> **Invariant.** Any write that can change the `CaseDetail` projection must, in the same
> transaction, bump `case.revision` and set `case.updatedAt`.

Applies to return records, case return items, case facts, policy decisions, support work-item
state, shipments and artifacts. Where one transaction is impossible, use a projection/outbox
writer with deterministic ordering — never a best-effort second write. Requires a concurrency
test: two writers on different child collections produce two distinct, monotonically increasing
revisions with no lost update.

### 6.6 Stage precedence — frozen

`CopilotStage` is a pure projection over `ReturnCaseStatus` plus policy, RMA, shipment and
warehouse state, computed on read by one function. **No stage logic in API handlers.**

```text
1  terminal            7  policy evaluation / approval required
2  settlement          8  return facts
3  warehouse           9  item selection
4  carrier transit    10  order confirmation
5  authorized RMA     11  discovery
6  awaiting support        (includes warranty and delivery-claim verification)
```

Mixed shipment states resolve to the **furthest-progressed** shipment, so the associate sees the
leading edge of the return rather than its slowest package.

**Monotonicity test required.** Stage must not regress because a secondary package moved.
Regression is permitted only via explicitly enumerated transitions (cancellation, recovery,
replacement shipment).

`isTerminal` derives from persisted `ReturnCaseStatus` only. **The read path never calls
Temporal** — that would make the workflow host a synchronous dependency of every case read.
Divergence is Phase 10's concern.

### 6.7 Migration and backfill

Real cases already exist — 8 at the baseline, 6 orphaned with terminated executions.
Backward-compatible projection, never destructive cleanup:

```text
missing revision        → initialize deterministically
missing stage           → derive from persisted status + records
existing returnRecord   → project into returnRecords[].shipments[]
existing labelReference → project as a single labelArtifact
case active + workflow terminated → RECOVERY_REQUIRED
```

Idempotent and safe to re-run.

### 6.8 Delete the session query

Remove `activeSession` and the `returnsApi.list()` scan entirely
(`ReturnCopilotPage.tsx:131–147`). This closes the regression against HEAD rather than restoring
a lookup this phase makes redundant.

### Gate 2
One `GET /api/cases/{caseId}` carries enough authoritative state to determine the lifecycle
**without any `ReturnSession` read**. Fixtures cover: new case · order confirmed · facts
collected · policy pending · support pending · partial RMA · RMA+tracking · RMA+tracking+label ·
complete. The six known orphans project correctly without manual intervention.

---

## 7. Phase 3A — Deterministic policy evaluation

Authority: [Ferguson Return Policy Baseline](#appendix-a--policy-authority), derived from
Ferguson's public Returns and Cancellations Policy and Terms and Conditions of Sale
(Rev. May 2025).

**Sub-task numbering: §7.N ≡ 3A.N.** The parallel execution plan refers to these tasks as
`3A.1` … `3A.8`; they are the sections below in order.

### 7.1 (3A.1) Source `special_order` / `seller_stocked` — sequence first

The baseline's primary branch is stocked vs special-order. The active schema has **no**
`special_order`, `stocked`, `non_stock` or `product_class`. Per the baseline's own safety rule,
every return routes to `REVIEW_REQUIRED` until this is sourced.

```text
investigate order_line.line_type value domain against the source
  → if it carries the distinction: map it
  → otherwise: add via source binding + schema release + sync
```

A data-platform task, not an evaluator task. It gates 3A **acceptance**, not 3A **code**.

### 7.2 (3A.2) Policy configuration

`ReturnEligibilityPolicy` becomes a versioned section of the return configuration, inheriting the
existing `LoadedReturnConfiguration` validation, release, activation and audit. A malformed or
missing policy **refuses activation** rather than silently deploying an all-`REVIEW_REQUIRED`
rule set.

```text
policy malformed / missing   → refuse activation   (operational failure)
policy valid, facts missing  → REVIEW_REQUIRED     (correct, per case)
```

### 7.3 (3A.3) Policy facts are tri-state

Forbidden — defaulted booleans convert *unknown* into policy evidence and produce hallucinated
approvals through the front door:

```python
used: bool = False          # never
installed: bool = False     # never
special_order: bool = False # never
```

Required — `TRUE | FALSE | UNKNOWN`, or nullable with strict validation. **`known false` ≠ `not
mentioned`.** This binds hardest on AI extraction: a model finding no evidence of installation
must never yield `installed = false`.

Every policy-critical fact carries `value · provenance · acquisitionMethod · validationState ·
capturedAt`. Only facts in an accepted evidence state enter evaluation. The fact log already
carries `acquisitionMethod`, `sourceSystem` and supersession; the evaluator must **enforce** it
as an admission rule.

### 7.4 (3A.4) The evaluator

A pure function with no IO and no model call. Precedence:

```text
1 validate policy release        5 special-order / non-stock path
2 customer/contract override     6 standard stocked 30-day policy
3 delivery-claim detection       7 restocking-fee applicability
4 warranty detection             8 otherwise REVIEW_REQUIRED
```

Safety rule — never infer `APPROVE`:

```text
no rule matched · missing required fact · ambiguous damage cause
· unknown manufacturer acceptance · unknown special-order status
  → REVIEW_REQUIRED
```

Money in `Decimal` or integer minor units, never binary floats. Return-window boundaries use
`session_timezone`, `temporal_grounding.py` and `business_calendar.py` — never naive UTC
subtraction. Test day 30, day 31, 23:50 local, DST, and the 2-business-day claim boundary.

### 7.5 (3A.5) `PolicyOutcome`

`EligibilityDecision` stays exactly three-valued and is shared with the session orchestrator;
routing is expressed by a wrapper so "not applicable" is the **absence** of a decision:

```text
PolicyOutcome
  route    : STANDARD_RETURN | WARRANTY | DELIVERY_CLAIM
  decision : EligibilityDecision | None
  conditions[] · exceptions[] · reasonCodes[] · appliedRules[]
```

Structural invariants enforced at construction by a `model_validator`, matching
`AgentAction.validate_action_payload`:

```text
route = STANDARD_RETURN → decision ∈ {APPROVE, REJECT, REVIEW_REQUIRED}
route = WARRANTY        → decision is null
route = DELIVERY_CLAIM  → decision is null
```

`{ "route": "WARRANTY", "decision": "APPROVE" }` must be unconstructable.

### 7.6 (3A.6) Non-standard routes hand to Support

**Warranty and delivery claims are both verified by Support inside this application. Neither is
terminal.** There are no routed terminal statuses. Only the *eligibility* path differs; the
fulfilment path is the one Support already runs.

```text
PolicyOutcome { route: WARRANTY | DELIVERY_CLAIM, decision: null }
  → ReturnCaseStatus = AWAITING_SUPPORT
  → awaiting = [WARRANTY_VERIFICATION] | [DELIVERY_CLAIM_VERIFICATION]
  → businessComplete = false, isTerminal = false
  → work item on the route's queue, carrying policy outcome and deadline
  → Support verifies, optionally via an external party
  → approved cases continue through the normal RMA lifecycle
```

Both map to the existing `AWAITING_SUPPORT` stage — **no new stage, no UI change.**

`SupportWorkItemView` has no `type` field; it distinguishes by `queue`, `subject` and `priority`.
Route context therefore travels as a configured queue. **Do not add a work-item type field.**

```yaml
support:
  queues: [RETURNS_SUPPORT, WARRANTY_SUPPORT, DELIVERY_CLAIM_SUPPORT]
```

Outcomes map almost entirely onto existing statuses:

| Outcome | Status | Next |
|---|---|---|
| approved | `RETURN_CREATION_PENDING` | normal RMA / replacement / credit |
| rejected | `REJECTED` | terminal rejected outcome |
| more information needed | `CLARIFICATION_REQUIRED` | stays `AWAITING_SUPPORT` |
| external party reviewing | `EXTERNAL_PARTY_REVIEW` *(new)* | stays `AWAITING_SUPPORT` |

**One new status total.** `EXTERNAL_PARTY_REVIEW` is parameterised by route and serves warranty
(manufacturer), delivery claim (carrier) and special-order manufacturer acceptance.

The delivery-claim reporting window (2 business days from delivery) sets `slaDueAt` on the work
item via `business_calendar.py`, so a claim approaching its deadline escalates on the existing
reminder cadence.

**All three paths are one verification hand-off with a route discriminator, not three features.**
The RMA lifecycle downstream is identical.

### 7.7 (3A.7) The workflow gate

Insert between fact collection and `_open_support`. Register `evaluate_case_eligibility` in
`create_return_workflow_worker` beside the existing eight activities — that list having exactly
eight entries is the audit's proof policy is absent.

| Decision | Status | Next |
|---|---|---|
| `APPROVE` | `POLICY_APPROVED` | `_open_support` |
| `REJECT` | `POLICY_REJECTED` | terminal; no work item opened |
| `REVIEW_REQUIRED` | `AWAITING_POLICY_REVIEW` | `policy_override` signal, on the existing timer machinery |

Fail closed: the deterministic fallback yields `REVIEW_REQUIRED` on provider failure. **A policy
error never defaults to approval and never opens a work item.**

### 7.8 (3A.8) Provenance and override

Persist decision, explanation, confidence, evidence references, applied rules, policy id/version,
source document/revision, model provider and name — as case facts, which already carry
`acquisitionMethod`, `sourceSystem` and supersession.

```json
{ "advisory": { "missingFacts": [], "ambiguities": [], "explanation": "…",
                "suggestedHumanReview": false, "modelProvider": "…", "modelName": "…" } }
```

**The advisory carries no decision-shaped field.** No `recommendation`, no `decision`. That makes
`decision = advisory.recommendation` impossible to write later, rather than merely discouraged.

`POST /api/cases/{caseId}/policy-override`, supervisor-gated. Client sends `expectedRevision`,
`overrideDecision`, `reasonCode`, `reason`, `idempotencyKey`. **Server derives** `actor` (from the
authenticated principal), `timestamp`, `originalDecision` and `tenantId` — the same rule
`order_agent.py` already applies to `correlation_id`. The override is append-only; the original
is never overwritten.

Delete in this phase: the `setPolicyEvaluation` literal, the inline evaluation object at
`ReturnCopilotPage.tsx:387–400`, and `DEFAULT_EVALUATION`.

### Gate 3A
Eligible, ineligible and approval-required all work. **The ineligible case proves no Support work
item was opened** — assert on the work-item collection, not the UI. A forced gateway failure
yields `REVIEW_REQUIRED`. Baseline Examples A–G pass as the acceptance suite. No `POL-STD-30D`
literal exists in the frontend.

---

## 8. Phase 3B — Durable Support events

### 8.1 Stable event identity

A stable `supportEventId` (or `Idempotency-Key` header) on every Support mutation, persisted
under a unique constraint on `(caseId, supportEventId)`. A payload digest is retained **only** to
detect same-id/different-payload — never as the identity, because property ordering, timestamps
and new optional fields all change a digest without changing the business event.

```text
same eventId + same payload      → 200, idempotent no-op
same eventId + different payload → 409 IDEMPOTENCY_CONFLICT
new eventId                      → process
```

### 8.2 Delivery through the existing outbox

`operations/integrations/outbox.py` is already a lease-based transactional outbox —
`OutboxCommand` carries topic, aggregate, idempotency key and attempt count, with lease
acquisition and retry, running in the `outbox-publisher` and `integration-outbox-worker`
containers. It dispatches to external HTTP via `TopicDispatcher`. **This needs one new
`TopicDispatcher` that signals Temporal — not a new outbox.**

```text
Support API → Mongo transaction { support event, outbox command } → commit
            → outbox worker → TemporalSignalDispatcher → signal → mark delivered
```

Dedup lives in Mongo under the unique constraint. **Do not keep an unbounded
`applied_support_keys` set in workflow state** — Temporal history is not a deduplication store.

### 8.3 Delivery guarantee

```text
Transport (outbox → Temporal):  AT LEAST ONCE
Business processing:            EFFECTIVELY ONCE, keyed on supportEventId
```

A dispatcher that signals successfully and crashes before acknowledging **will** redeliver.
**Never claim transport-level exactly-once** in code, comments or documentation.

### 8.4 Failure classification

```text
TRANSIENT   Temporal unavailable · timeout · connection failure
            → bounded exponential backoff (OutboxCommand.attempt_count exists)

PERMANENT   workflow completed · cancelled · invalid id
            → DEAD_LETTER / REQUIRES_RECONCILIATION, stop retrying
            → case reconciles to RECOVERY_REQUIRED (Phase 10)
```

### Gate 3B
```text
Mongo commit succeeds, Temporal unavailable
  → durable event + outbox → eventually delivered at least once
  → supportEventId causes exactly one business mutation
Signal succeeds, dispatcher crashes before ACK
  → redelivery → no duplicate RMA/tracking/label, no extra revision
Workflow permanently closed
  → outbox PERMANENT → RECOVERY_REQUIRED → no infinite retry
```

---

## 9. Phase 3C — Order confirmation

Confirmation and selection are two ordered commands. Lines cannot be chosen before they are
fetched, and they are fetched from the case that confirmation creates.

```text
candidates shown → associate selects candidate
  → CONFIRM_ORDER (candidate_set_id, candidate_id, order_reference)
  → Case created, confirmedOrder persisted, workflow started
  → GET /api/cases/{caseId}/order-lines
  → ITEM_SELECTION → POST /api/cases/{caseId}/selected-items
```

`OrderConfirmation.order_line_references` stays empty at confirmation — its contract already
documents "empty means the whole order", so no contract change is needed.

---

## 10. Phase 4 — Cumulative Support / RMA

### 10.1 The signal accumulates

The handler appends every notice; the run loop drains it. Updates must independently express
`RMA_CREATED`, `RMA_UPDATED`, `SHIPMENT_CREATED`, `TRACKING_ASSIGNED`, `TRACKING_UPDATED`,
`LABEL_CREATED`, `LABEL_REPLACED`, `PICKUP_SCHEDULED`, `RETURN_LOCATION_ASSIGNED`,
`RETURN_INSTRUCTIONS_UPDATED`, `CANCELLED`. Equivalent semantics matter; the exact enum does not.

### 10.2 Upsert by business identity

Stable keys: `caseId + returnReference`, `caseId + shipmentId`, `caseId + labelDocumentId`.

`record_support_outcome` currently swallows the duplicate-key error and continues — that is what
makes a second reply a no-op. Look up by `(caseId, returnReference)` under the existing unique
partial index, update at the current `version`, retry once on `ConcurrencyConflictError`. **A
field arriving `null` must not overwrite a present value.**

### 10.3 Workflow lifetime and terminal commands

Continue while `businessComplete` is false and the deadline has not passed, on the existing
reminder cadence. `continue_as_new` is already handled in `_await_support`; add an absolute
lifetime cap and a park state.

Three validated commands replace an unrestricted close signal:

| Command | Validation |
|---|---|
| `COMPLETE` | must satisfy domain completion; otherwise `409` |
| `CANCEL` | server-derived actor and timestamp; client supplies `reasonCode`, `reason`; audited |
| `EXPIRE` | system-initiated on deadline; audited |

### 10.4 Polling stops on `isTerminal`

```typescript
refetchInterval: (query) => query.state.data?.isTerminal ? false : 10_000
```

Not `businessComplete` — a rejected or cancelled case is never business-complete and must still
stop the client.

### Gate 4 — backend only
```text
Support API → RMA      → revision N,   awaiting=[TRACKING,LABEL]
Support API → tracking → revision N+1, awaiting=[LABEL]
Support API → label    → revision N+2, awaiting=[], businessComplete=true
duplicate eventId      → no revision change
```
Assert on **outbox delivery state**, not an HTTP status. The open-Copilot convergence run is
Phase 12 Scenario 6.

---

## 11. Phase 5 — Shipment, tracking, label

Explicit shipment fields — `shipmentId`, `carrier`, `serviceLevel`, `trackingNumber`,
`estimatedDeliveryAt`, `shipmentStatus`, `createdAt`, `updatedAt`. Stop deriving `carrier` from
`orderSource` and ETA from `shippingPathExpectation`.

`returnRecord → shipments[]`. Never assume 1 RMA = 1 package.

```text
ReturnLabelArtifact
  artifactId · artifactType · shipmentId · fileName · mediaType
  expiresAt · createdAt · version · supersededBy
```

Served through an opaque authenticated endpoint, never a storage URL in the client:

```text
GET /api/cases/{caseId}/returns/{returnRecordId}/artifacts/{artifactId}
```

Authorization validates tenant, principal, case access **and** that the artifact belongs to that
return. Never on artifact id alone. A foreign artifact is a 404, matching `get_case`.

Replacements supersede; the old artifact stays auditable.

**Frozen-UI boundary.** The contract carries record-level `artifacts[]` (see §6.3's amendment); the existing single label action
resolves to the backend-declared active artifact — `artifact.active == true AND supersededBy ==
null`, **never `labels[0]`**. Multi-label presentation is out of scope. The decorative barcode
needs no change: the fabrication was the fallback RMA number, removed in Phase 7.

Add `quantityApproved`, `disposition` and per-item `status` to `CaseReturnItem`.

### Gate 5 — API only
Projection correct · artifact download correct · authorization correct · package attribution
correct. Rendering is Gate 7.

---

## 12. Phase 6 — Order lines and transactional selection

### 12.1 Case-bound order lines

No order-lines read surface exists anywhere. A naked `orderReference` is ambiguous across source
systems, tenants, business units and graph generations, and is not an authorization boundary.

```text
after confirmation:   GET /api/cases/{caseId}/order-lines
before confirmation:  resolve via candidateSetId + candidateId
```

The backend resolves the confirmed order identity stored on the case, under the same scoping as
`get_case`.

### 12.2 Returnable quantity

```text
returnableQuantity = orderedQuantity
                   − completedReturnQuantity
                   − openAuthorizedQuantity
                   − activeReservationQuantityFromOtherCases
```

`completedReturnQuantity` and `openAuthorizedQuantity` must be **mutually exclusive** — define
the boundary once, in the projection, and test it. A negative result exposes `0` and raises a
data-inconsistency flag, never a negative or a silent clamp.

The refund base is computable: `order_line.net_price`, `line_net_amount`, `ordered_quantity` and
`shipped_quantity` all exist.

### 12.3 Reservation lifecycle

```text
ACTIVE      selection persisted, quantity held
CONSUMED    RMA authorized the quantity
RELEASED    case cancelled/rejected/expired, or selection changed
EXPIRED     TTL elapsed before authorization
```

Legal transitions only, enforced by conditional update. Never `EXPIRED → CONSUMED`,
`RELEASED → CONSUMED` or `CONSUMED → EXPIRED`.

```sql
UPDATE reservation SET state = CONSUMED
 WHERE reservationId = ? AND state = ACTIVE AND expiresAt > now
```

Atomic with the authorization mutation. Losing the race does **not** authorize:
`409 QUANTITY_RESERVATION_EXPIRED`, recompute, do not create the RMA.

TTL is configuration, beside the other return timings — not a source constant.

**Self-reservation exclusion:** a case editing its own reservation from 1 to 2 must exclude its
existing hold, or the edit rejects itself.

### 12.4 Selection and clobber

Delete `DEFAULT_SAMPLE_ITEMS`, `DEFAULT_ITEMS`, the `"CW273354"` fallback and the `branchName`
default. Reason and condition vocabularies come from return configuration.

`send.onSuccess` overwrites `candidates` on any turn returning a search result, dropping the
screen out of `ITEM_SELECTION`. Ignore incoming candidates once the case has a confirmed order
(`ReturnCopilotPage.tsx:205–206`).

### Gate 6
Real lines render; `EM-9821` and `Emerson 1.5HP Motor` appear nowhere in frontend source.
*Select* creates a case. A subsequent message does not reset the pane. Release-blocking
concurrency tests: two concurrent requests of the full quantity — exactly one succeeds; expiry
worker and RMA authorization simultaneously — exactly one transition wins; abandoned reservation
releases rather than leaks.

---

## 13. Phases 7–12

### Phase 7 — Frontend binding

Mode comes from `caseDetail.stage` via the generated enum. Delete the `session` parameter and
every model-status test — `turn.response.status === "APPROVED" | "ISSUED"`,
`business_capability === "POLICY_EVALUATION"`. These may remain conversational metadata; they are
never business authority.

**Every `??` literal fallback dies here:** `RMA-2026-78901`, `TRK-98421049281`, `FedEx Freight /
Ground Prepaid`, `Prepaid Ground Dropoff`, `Facility East Bay Dock (DC-7)`, `DEFAULT_MILESTONES`,
`Bay 14-B`, `Tier 2 Technical Inspection`, `249.99`, `18.75`, `CM-2026-88192`. A missing value
renders pending, matching `ProgressTruthPane`.

The refund figure renders **pending** until a fee source exists — the same element that fabricated
`$149.99`.

**Gate 7:** for every stage, backend state → reload → same mode. A model response alone cannot
move the workflow forward.

### Phase 8 — Polling, revision, reload, reconnect

Reject stale revisions: client holds 18, response carries 17 → ignore.

Identity in route state — `/returns?conversationId=…&caseId=…`. On init: conversation id →
transcript, case id → `CaseDetail` → derive mode. **IDs only; never persist business state in
localStorage.** Stop clearing `candidates` on resume.

Enable `refetchOnReconnect` with bounded retry. Never rerun AI because the browser reconnected.

SSE on `GET /api/cases/{caseId}/stream` comes last, as an optimisation over the poll and never a
replacement.

**Gate 8:** refresh during discovery · item selection · policy pending · awaiting support · RMA
without label · tracking available · warehouse receiving. A 30s outage recovers and picks up an
RMA issued during it.

### Phase 9 — Warehouse and settlement

Warehouse: `facilityId`, `facilityName`, `bayId`, `receivedAt`, `receivedQuantity`,
`inspectionStatus`, `condition`, `disposition`, `qaStatus`, `warehouseStatus` — **only fields
that exist in current backend flows.** Bay data comes from facts `ReturnCaseWorkflow` already
writes.

Settlement has no producer. Use `NOT_INTEGRATED`, not `NOT_STARTED` — the latter implies a
producer that has not run. It **never enters `awaiting[]`** and never blocks completion. A case
with settlement `NOT_INTEGRATED` reaches `COMPLETED_EXTERNAL_SETTLEMENT`, never plain
`COMPLETED`, so a completed-return count is never misread as a settled-return count.

```text
businessComplete = completion within configured platform responsibility
```

**Gate 9:** every displayed financial number traces to authoritative backend data.

### Phase 10 — Recovery and reconciliation

Six cases read `AWAITING_SUPPORT` with terminated executions. Classify before recovering:

```text
execution unexpectedly unavailable + case expected to accept updates
  → RECOVERY_REQUIRED

case legitimately terminal (COMPLETED · COMPLETED_EXTERNAL_SETTLEMENT
                            · POLICY_REJECTED · CANCELLED · EXPIRED)
+ update incompatible with that state
  → permanent rejection / dead letter; case stays terminal, event retained for audit
```

Workflow ids derive from case ids — **inspect current execution state before restarting
anything.** Never silently create duplicate workflows.

**Gate 10:** no case remains indefinitely `AWAITING_SUPPORT` while its workflow is terminated.

### Phase 11 — Contracts and regression

MSW handlers currently emit `business_capability: "POLICY_EVALUATION"`, statuses `APPROVED` and
`ISSUED`, and case bodies with `customerReference` — a field `CaseView` forbids via
`extra="forbid"`. They were shaped to satisfy the frontend, which is why the fabrication went
unnoticed. Rebuild from the Pydantic models.

`ReturnCopilotPage.test.tsx` stubs the API module wholesale, so the request that 422s in
production is never constructed. Drive it through MSW.

**Gate 11:** requirement-to-test traceability — `requirement id → test id → fails before fix →
passes after fix`. Reintroducing `agent_id: "order_discovery"` fails CI.

### Phase 12 — Adversarial E2E

Real browser, real local infrastructure, no mocks.

| # | Scenario | Must hold |
|---|---|---|
| 1 | Normal eligible return, identity → completion | full path succeeds |
| 2 | Multiple initial facts | captured facts not re-requested |
| 3 | Partial identity | stays candidate evidence until confirmed |
| 4 | Policy rejection | never opens an RMA work item |
| 5 | Approval required | remains in explicit approval state |
| 6 | RMA → +10s tracking → +10s label → +10s pickup | Copilot open, converges |
| 7 | Reverse order: RMA → label → tracking | also converges |
| 8 | 1 RMA, 2 shipments, 2 labels, 2 tracking | API attributes all correctly; UI resolves the active artifact |
| 9 | Replacement tracking / label | old artifact auditable, projection updates |
| 10 | Reload at every lifecycle boundary | resumes correctly |
| 11 | Temporary backend failure | reconnects and recovers from the case |
| 12 | Duplicate Support update | no duplicate records |
| 13 | Concurrent Support updates | no lost updates |
| 14 | Stale HTTP response | lower revision cannot overwrite newer |
| 15 | Warranty route | reaches Support, verifies, rejoins RMA lifecycle |
| 16 | Delivery-claim route | reaches Support with `slaDueAt` set from the reporting window |

---

## 14. Explicitly forbidden

```text
create a ReturnSession to satisfy a Copilot mode
use LLM output as workflow status or policy authority
model warranty or delivery claims as terminal routes
leave the policy decision in the frontend
default policy facts to false
claim transport-level exactly-once delivery
stop polling merely because an RMA exists
represent a label only as a printable page
use orderSource as carrier · shippingPathExpectation as ETA
invent RMA / tracking / settlement / fee values
let a rejected return proceed to Support
complete the workflow after the first Support message
default to approval when the policy engine fails
trust client-supplied actor identity or audit timestamps
resurrect a legitimately terminal case from a late event
solve missing backend data with UI defaults
redesign any Copilot pane
declare completion because tests pass or an RMA exists
```

**Do not delete static instructional text.** `DiscoveryMode`'s search guidance is copy, not data.
Remove only values presented as business data.

---

## 15. Decision log

| Decision | Status |
|---|---|
| Case is the authoritative aggregate | **CLOSED** |
| Settlement representation (`NOT_INTEGRATED`, `COMPLETED_EXTERNAL_SETTLEMENT`) | **CLOSED** |
| Eligibility rule authority (Ferguson public policy baseline) | **CLOSED** |
| Warranty ownership (Support verifies) | **CLOSED** |
| Delivery-claim ownership (Support verifies) | **CLOSED** |
| Seller restocking-fee schedule | **Open** — blocks a displayed figure, not a decision |
| Reasoning provider for dev/CI | **Open** — Gates 1, 6, Phase 12; not policy correctness |

### Expected baseline behaviour

With the rule set adopted but `special_order` unsourced and conversational extraction only
partially covering the fact set, **most returns will initially evaluate to `REVIEW_REQUIRED`.**
That is the system working correctly, and it will not look like it.

```text
before 3A.1  → REVIEW_REQUIRED dominant, expected during development
3A acceptance / production
             → 3A.1 complete: stocked/special-order sourced,
               or proven unavailable and accepted as an explicit decision
+ fact extraction → condition and damage-cause facts resolve
+ fee config      → refund figure becomes displayable
```

An `APPROVE` rate that is low because facts are missing is safe. An `APPROVE` rate that is high
because the evaluator guessed is the failure this programme exists to prevent.

---

## 16. Amendment history

Six working documents preceded this one. Several corrections reversed an earlier decision; those
are recorded because the rejected alternative is what makes the current one reviewable.

| Round | Correction | Why it was wrong |
|---|---|---|
| v2 → v3 | `EligibilityGatewayService` cannot be reused as-is | `build_eligibility_input` derives from session-scoped `ContextSnapshot`s a case never has |
| v2 → v3 | Order lines need a new endpoint | No order-lines read surface exists anywhere |
| v2 → v3 | Reuse `EligibilityDecision`, not a new enum | The three-valued enum already exists in `stage_results.py` |
| v3 → v3.1 | `businessComplete` / workflow terminal were circular | Completion required a terminal workflow that waited on completion |
| v3 → v3.1 | **Deterministic policy authority, model advisory** | The gateway is model-first; its input carries no policy facts. v3 had it backwards |
| v3 → v3.1 | Confirmation and selection are two commands | Lines cannot be chosen before they are fetched |
| v3 → v3.1 | Case-bound order lines | Naked `orderReference` is not an identity or authorization boundary |
| v3 → v3.1 | Stable `supportEventId`, not a payload digest | Serialization changes alter a digest without changing the event |
| v3.1 → v3.2 | Support idempotency via the existing outbox | Dual-write: commit then crash loses the RMA silently |
| v3.1 → v3.2 | Reservation lifecycle pinned before implementation | Expiry races RMA authorization |
| v3.1 → v3.2 | Policy facts tri-state | Defaulted booleans convert unknown into evidence |
| v3.2 → final | At-least-once delivery, effectively-once processing | Transport exactly-once is impossible with any dispatcher |
| v3.2 → final | Server-derived actor, timestamp, original decision | Client-supplied audit fields are not audit |
| v3.2 → final | Advisory carries no decision-shaped field | v3.2 forbade a model decision then stored `recommendation: APPROVE` |
| v3.2 → final | Stage precedence frozen; furthest-progressed shipment wins | Exhaustive enum coverage does not resolve conflicts |
| final → this | **Warranty is not terminal — Support verifies it** | The baseline separates warranty's *eligibility* path, not its fulfilment path |
| final → this | **Delivery claims take the same shape** | Removed both routed terminal statuses; one `EXTERNAL_PARTY_REVIEW` serves three paths |

Speculative content struck along the way: `customerTier`, `priorReturnCount`, product-class
return windows and arbitrary value thresholds were inferred from what a returns policy usually
contains rather than from any Ferguson source.

---

## Appendix A — Policy authority

**Full baseline: [`RETURN_COPILOT_POLICY_BASELINE.md`](RETURN_COPILOT_POLICY_BASELINE.md)** —
the rule set, the deterministic configuration shape, the required evaluation input, the
precedence chain and worked Examples A–G. That document is the authority; this appendix is a
summary and defers to it on any detail.

Baseline: Ferguson's current public **Returns and Cancellations Policy** and **Terms and
Conditions of Sale** (Rev. May 2025). Internal negotiated agreements, manufacturer-specific rules
and authorized Ferguson exceptions remain configurable higher-priority overrides.

```text
precedence:
  1 customer / contract override
  2 special-order manufacturer policy
  3 Ferguson standard return
  4 REVIEW_REQUIRED
```

**Standard stocked return** — 30 days from purchase, product new, suitable for resale, original
undamaged packaging, all original parts, and not used, installed, modified, rebuilt,
reconditioned, repaired, altered or damaged.

**Restocking fee** — applies by default; **no universal percentage is published and none may be
invented.** The engine determines `RESTOCKING_FEE_APPLIES`; the amount comes from
`SELLER_CONFIGURATION`, `SELLER_OVERRIDE` or `MANUFACTURER`.

**Special-order / non-stock** — requires manufacturer acceptance; buyer must accept any required
fee. Unknown acceptance or unknown fee → `REVIEW_REQUIRED`.

**Outside 30 days** → `REVIEW_REQUIRED` with `OUTSIDE_STANDARD_RETURN_WINDOW`. Never automatic
approve, never automatic reject.

**Delivery claims** — shipping damage, shortage, shipment error, improper delivery. Route to
`DELIVERY_CLAIM`; 2 business days from delivery.

**Warranty** — defect after use or installation. Route to `WARRANTY`. Not a failed standard
return.

**Damage-cause routing** — customer/use damage → `REJECT`; shipping damage → `DELIVERY_CLAIM`;
manufacturer defect → `WARRANTY`; unknown cause → `REVIEW_REQUIRED`.

**Not established by public policy and therefore forbidden:** universal restocking-fee
percentage, customer-tier eligibility, prior-return thresholds, product-class-specific windows,
arbitrary value thresholds.

---

## Appendix B — Traceability matrix

Every defect the audit documents, the phase that closes it, and the gate that proves it. The
audit's executive tally is 7 P0 · 6 P1 · 3 P2; this is that tally enumerated, so no finding can
be closed by assertion.

| # | Sev | Finding (audit) | Closed by | Proven at |
|---|---|---|---|---|
| 1 | P0 | Agent id does not match the configured agent policy | §5 Phase 1 | Gate 1 |
| 2 | P0 | `deriveCopilotMode` ignores the return records it is given | §6.6, §13 Phase 7 | Gate 7 |
| 3 | P0 | Five modes read a `ReturnSession` that never exists | §2.1, §6.3, §6.8 | Gate 2 |
| 4 | P0 | Eligibility invented in the browser; no policy evaluation exists | §7 Phase 3A | Gate 3A |
| 5 | P0 | Support answers once — delayed tracking/label/pickup cannot arrive | §10 Phase 4 | Gate 4, E2E 6–7 |
| 6 | P0 | Support's RMA lost with a 500 when the workflow has closed | §8 Phase 3B, §10.3 | Gate 3B |
| 7 | P0 | Item selection fabricated; *Select* does not confirm the order | §9 Phase 3C, §12 Phase 6 | Gate 6 |
| 8 | P1 | Session lookup regressed to the client-side join HEAD removed | §6.8 | Gate 2 |
| 9 | P1 | Wrong-field bindings — `orderSource` as carrier, `shippingPathExpectation` as ETA | §11 Phase 5 | Gate 5 |
| 10 | P1 | Case status not reconciled with workflow liveness | §13 Phase 10 | Gate 10 |
| 11 | P1 | Polling stops on RMA existence, not business completion | §6.4, §10.4 | Gate 4, Gate 8 |
| 12 | P1 | Label is a bare string; the label button prints the web page | §11 Phase 5 | Gate 5 |
| 13 | P1 | Candidate clobber drops the screen out of `ITEM_SELECTION` | §12.4 | Gate 6 |
| 14 | P2 | MSW handlers drift from contract; page test stubs the API module | §13 Phase 11 | Gate 11 |
| 15 | P2 | No push channel — Copilot updates are polling only | §13 Phase 8 | Gate 8 |
| 16 | P2 | Settlement ledger fabricated with no backend contract | §13 Phase 9 | Gate 9 |

### Audit verdict questions → closing phase

The audit's final verdict asks ten questions. Each flips at:

| Question | Phase |
|---|---|
| Is Copilot connected to backend? | 2 |
| Is Order Discovery connected? | 1 |
| Is Return Analysis connected? | 6 |
| Is Policy Evaluation implemented and connected? | 3A |
| Is Support handoff connected? | 3A–4 |
| Does Support-created RMA reach Copilot? | 2 |
| Does delayed tracking reach Copilot automatically? | 4 |
| Does delayed label reach Copilot automatically? | 4–5 |
| Can Copilot recover after refresh/reconnect? | 8 |
| Can the full customer return complete through the current Copilot? | 1–6 |

### Two standing acceptance criteria

**No fabricated value reaches a screen.** A grep of `frontend/src/domains/returns/` for literal
RMA numbers, tracking numbers, policy codes, bay names, credit memos and currency amounts returns
nothing. Absent data renders as absent.

**The associate journey completes end to end.** Audit Flows 1–8 and the sixteen Phase 12
scenarios pass in a browser against a live stack, with Support issuing a partial RMA and
completing it in separate steps while the Copilot stays open.
