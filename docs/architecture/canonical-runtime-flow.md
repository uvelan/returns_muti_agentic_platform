# The canonical runtime flow

**Current as of 2026-08-14, commit `dcbb7dc`.**

One path runs a return from an associate's first sentence to a persisted,
graph-visible RMA. This document is that path. Everything else in
`docs/architecture/` describes one stage of it in depth.

Read this first. Several of the platform's hardest defects were not bugs in a
stage but breaks *between* stages — a case that existed with nothing to advance
it, a shipment written where fulfilment never looked — and those are invisible
if you only ever read one module.

## The path

```text
associate utterance
  │
  ▼
Order Discovery  ── durable, Temporal-hosted, one turn per call
  │   identification fields (runtime configuration)
  │   graph search over the COMPLETE corpus
  │   clarification when ambiguous
  ▼
confirmation  ── CandidateSet.validate_selection re-binds to
  │              conversation + principal + tenant + graph generation
  │
  ├─► Case committed to Mongo
  └─► exactly one ReturnCaseWorkflow started, keyed by
      return_case_workflow_id(case_id)
          │
          ├─► Bay Assignment          (concurrent, best-effort, bounded)
          ├─► Support conversation    (opened, then awaited)
          │      durable business-time wait
          │      numbered reminders on the business calendar
          │      RMA submitted by Support
          ├─► Return records persisted to SQL   Case → N RMAs → N items
          ├─► Targeted graph sync of the affected records
          └─► RMA propagated back into the discovery conversation
                 │
                 ▼
          shipment create/update, RMA-scoped
                 │
                 ▼
          fulfilment reads shipment truth through the graph
```

## Stage by stage

### 1. Discovery

Entered at `POST /api/v1/associate-returns/chat` (natural language) or
`POST /api/v1/associate-returns/conversations` (structured anchor). Each turn
runs as one durable Temporal activity through
`POST /api/v2/order-agent/conversations/{conversation_id}/turns`.

The agent searches on whatever the associate supplied. **What it can search on is
runtime configuration**, not code: `discovery.identification_fields` is the
complete catalogue, and adding a tenth field requires no Python, no TypeScript
and no prompt edit. Colour and ZIP are ordinary configured fields, not special
cases. See [`identification-fields.md`](identification-fields.md).

Misspelled customer names resolve through the Neo4j full-text index
`customer_name_search_v2`. The search covers the **complete** customer set
server-side. See [`order-discovery.md`](order-discovery.md) and
[`../optimization/order-discovery-search.md`](../optimization/order-discovery-search.md).

The server decides which disambiguation slot to ask for. AI may phrase the
approved question. AI never selects a customer, an order or a line.

### 2. Confirmation — the seam that matters

`confirm_order` in `dynamic_knowledge/order_agent/graph_nodes.py` does two things
and they are not independent:

1. commits the case;
2. starts exactly one `ReturnCaseWorkflow`.

The node is idempotent on `(tenant | conversation | order | line-set)`. A
repeated *or simultaneous* confirmation returns the existing case rather than
creating a second one.

**If the workflow cannot be started, the confirmation fails.** A case that exists
without its workflow is unreachable by Support, by Bay, and by every later agent
turn — it is not a degraded case, it is an invisible one. The failure is reported
as retryable and the case is left committed, so the next attempt at the same
confirmation resolves to the same case and starts the same workflow id.

The launcher does not hold a lock. The execution id is *derived* —
`return_case_workflow_id(case_id)` — so two simultaneous confirmations ask
Temporal to start the same id and the loser adopts the winner through
`WorkflowAlreadyStartedError`. Uniqueness is Temporal's, not ours.

`cases.workflowId` is a **link, never a precondition**. Nothing derives the
workflow id from it; `return_support.py` computes the id from the case id. A
failed link write therefore leaves a fully reachable case.

### 3. Recovery — when the seam tears anyway

Committing the case and starting its workflow are two operations against two
systems. There is a window — a Temporal outage, a killed worker, a partition —
in which the case is durable and the execution that owns it is not. The
confirmation fails and the associate is told, but **a failed turn is not a
repair**: nothing about an associate walking away makes the case reachable again.

`workflows/return_case_recovery.py` is the repair. Its queue is the cases
themselves: the case document carries `workflowId`, null on a case whose workflow
never started, with a unique partial index behind it. So the queue cannot drift
out of step with the thing it is repairing, the way a parallel outbox row can.

It calls the same launcher the confirmation node calls, so a case whose workflow
*is* running converges through `WorkflowAlreadyStartedError` and simply has its
link rewritten. A failed pass logs and changes nothing, so the next pass retries.
No lease, no attempt counter, no dead-letter state — the terminal condition is
observable from the case itself.

`grace_seconds` keeps the sweep from racing a confirmation still in flight.

The integration outbox was considered for this and rejected: it is scoped to
*external* dependency commands, and a topic with no registered adapter is marked
non-retryable and abandoned — precisely the outcome a case must never reach.

### 4. Bay Assignment

Started with the workflow and run **concurrently with the support conversation**,
so it no longer waits for a shipment that usually does not exist yet.

Best-effort by contract. `bay_wait_seconds` (default 120s) bounds dead time on
the critical path while an associate waits, and it is deliberately *not* a
business-calendar duration — stretching it across a weekend would leave a live
conversation hanging.

The result is one atomic recommendation: warehouse, bay, return location,
**computed** confidence, reason, explanation. A partial result is not a result.
Failure, timeout or low confidence never blocks the return; the case proceeds
without placement and records why. See [`bay-assignment.md`](bay-assignment.md).

### 5. Support, and business time

The workflow opens the support conversation, then waits. The wait and the
reminders that follow it are **business-calendar durations**, not wall-clock.
Eight hours means eight *working* hours.

This was the SLA defect. The workflow computed `workflow.now() + timedelta(...)`,
so a return raised at 16:30 on a Friday chased Support at 18:30, 20:30 and 22:30
into an empty queue and parked itself at 00:30 on Saturday. The arithmetic now
runs in `resolve_business_deadline` against the calendar named by
`business_calendar_id`. A calendar declaring every day whole restores the old
behaviour exactly, which is what a 24/7 operation should configure.

Reminders are numbered and capped by `max_reminders`. When they run out,
`on_reminders_exhausted` decides: `PARK_FOR_OPERATIONS` or `ESCALATE`. Without a
terminal branch a reminder cap just leaves the case sitting forever with nobody
told.

A workflow reads its timings once at start and keeps them for its lifetime. An
in-flight return must not have its deadline moved underneath it, so a
configuration change applies to new cases.

### 6. RMA persistence

Support creates **zero or more Return Records for one Case**:

```text
Case → N Return Records (RMAs) → N Return Items
```

Each Return Record owns its own RMA number, items, tracking records, label and
return location. These are not flattened onto the Case and not shared between
records. Persisted to platform-owned SQL by migrations `005_case_return_records.sql`
and `006_return_shipment_state.sql`, in one idempotent transaction. After commit
the platform case is updated and **only the affected records** are synchronized
into the active graph generation.

A graph-sync failure here parks the case (`_park_for_graph_sync_failure`) rather
than reporting success over a graph that does not know about the RMA.

See [`rma-and-shipment.md`](rma-and-shipment.md).

### 7. Shipment and fulfilment

`POST /api/return-shipments/{return_reference}/updates` creates or updates
shipment state, scoped to a specific RMA.

Every outcome is HTTP 200 with a verdict in the body: `APPLIED`, `DUPLICATE` or
`STALE`. That is deliberate — a duplicate submission is a *successful* no-op, not
a client error, and an out-of-order carrier callback is a correctly-refused
update rather than a failure the caller should retry.

`APPLIED` updates are pushed to the graph under an RMA-scoped targeted sync.
Fulfilment reads shipment truth **only through the graph**, never from an
inferred prior tracking value. `ShipmentEvidence` distinguishes `OBSERVED`,
`ABSENT` and `UNAVAILABLE`: "the graph says there is no shipment" and "we could
not ask the graph" are different answers and are never collapsed.

## The invariants this flow exists to hold

| # | Invariant |
|---|---|
| C1 | One confirmation identity = one case = one `ReturnCaseWorkflow`, idempotent under retry and under simultaneous confirmation. Never return success while leaving a case with no durable mechanism to start its workflow. |
| C2 | One coherent bay result: warehouse, bay, return location, computed confidence, reason, explanation. No constant confidence. |
| C3 | `Case → N RMAs → N items`. Each record owns its own label, tracking, return location and shipment association. No flattening. |
| C4 | Shipment state is RMA-scoped, idempotent, timestamp/version aware, stale-update safe, persisted, graph-synced and fulfilment-readable. |
| C5 | Every long-running process reports process class, identity, adopted release, epoch, timestamp, heartbeat. `ACTIVATED != LIVE` until every required class adopts. |
| C6 | A discovery field definition is runtime-owned. Adding the tenth identification field requires zero Python edits. |
| C7 | Every external model request: construct → safety → recursive redaction → interception → decision → one final dispatch boundary. `ALLOW_PROVIDER \| HUMAN_RESPONSE \| REJECT`. No business agent holds a raw provider client. |
| C8 | Deadlines and reminders derive from `business_calendar_id`, timezone, configured working periods and holidays. No hardcoded Mon–Fri. |
| C9 | Destructive graph migration: build replacement → catch up → validate → atomic swap → drain old readers → retire. Never mutate the active generation in place. |

## Where each stage lives

| Stage | Module |
|---|---|
| Discovery turn | `dynamic_knowledge/order_agent/graph_nodes.py`, `search_strategy.py`, `identification.py` |
| Confirmation | `dynamic_knowledge/order_agent/graph_nodes.py::confirm_order` |
| Workflow start | `workflows/return_case_launcher.py` |
| Recovery sweep | `workflows/return_case_recovery.py` |
| Case workflow | `workflows/return_case_workflow.py`, `return_case_activities.py` |
| Bay | `workflows/bay_assignment.py`, `operations/warehouse/service.py` |
| Business time | `configuration/return_configuration.py::ReturnCaseTimingConfiguration`, `resolve_business_deadline` |
| RMA persistence | `operations/` SQL return store, migrations `005`/`006` |
| Shipment | `api/return_shipments.py` |
| Fulfilment read | `workflows/fulfillment_tracking.py`, `dynamic_knowledge/integration/shipment_observations.py` |
| Graph sync | `data_platform/graph/sync_service.py` |
| AI dispatch | `ai/gateway/final_dispatch.py` |
