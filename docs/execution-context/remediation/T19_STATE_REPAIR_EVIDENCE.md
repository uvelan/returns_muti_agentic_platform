# T19 — targeted state repair: inventory, outcomes, and what is still gated

**Taken:** 2026-08-22, against the live stack (all six containers healthy).
**Method:** read-only queries against Mongo `return_platform`, SQL Server
`dbo.*`, and Temporal `localhost:7233`. No writes were made by the inventory.

Two of the four sub-runs turned out to have nothing to repair, one is built and
deliberately unapplied, and one is not a data repair at all. Each is recorded
with the query that decided it, so a later reader can re-take the same reading
rather than trusting this file.

---

## T19a — wedged Temporal histories · **NOT A DATA REPAIR**

| Reading | Value |
|---|---|
| Workflows RUNNING | 63 |
| Workflows TERMINATED | 29 |
| Workflows COMPLETED | 9 |
| Running types (sample of 20) | `order-discovery-v1` ×17, `return-case-v1` ×3 |
| Oldest sampled start | 2026-08-21 09:06 UTC |
| `worker_heartbeats` documents | 0 |

**Outcome: recovery is a deployment, not an edit.** T02 added
`workflow.patched()` handling so a compatible worker decodes both the legacy
`str` and the typed `SupportRequestDraft`. The plan is explicit that Temporal
repair "uses compatible deployed code and supported reset/replay mechanics; it
does not edit history" — so the action for these 63 is to run the fixed worker
and let it advance them.

### Executed 2026-08-23 — and there was no wedge left to recover

The patched worker was started and is heartbeating (`worker_heartbeats` holds
`return-workflow-worker`, which is T18's fix observed live; its log is 0 bytes,
which is T18's *other* finding — these workers emit nothing).

What the workflows actually show:

| Workflow | Status | Waiting on |
|---|---|---|
| `return-case-721fb62e-…` — **the one UIAUDIT-005 named** | **COMPLETED** | — |
| `return-case-7b216e58-…` | RUNNING | case `AWAITING_SUPPORT` |
| `return-case-2328a586-…` | RUNNING | case `AWAITING_SUPPORT` |
| `return-case-e5ce5c59-…` | RUNNING | case `AWAITING_POLICY_REVIEW` |

None of the four has a pending activity in a failing state, and the audit's
workflow has **zero** workflow-task failures and zero `SupportRequestDraft`
decode failures across its whole history. The three still running are waiting on
a human, which is what `RUNNING` correctly means for those two statuses — a case
awaiting Support is not a wedged case.

**Attribution, stated honestly.** `721fb62e` closed at **2026-08-22 10:37 UTC**,
which is *before* the worker started here. So it cannot be credited to this run;
something during the T02 work advanced it. What this run establishes is the
present state — no wedge remains, and a compatible worker is now live, so any
future replay is served by patched code.

Sixty-one `order-discovery-v1` workflows are also RUNNING. They belong to a
different worker and are outside UIAUDIT-005, which named `ReturnCaseWorkflow`.

---

## T19b — return records that claim ISSUED over nothing · **BUILT, DRY-RUN, NOT APPLIED**

| Store | Reading |
|---|---|
| Mongo `return_records` | 5 documents, all `status: "ISSUED"` |
| ...of those, `approvedItems` empty | **5** |
| SQL `dbo.return_record` | **0 rows** |
| SQL `dbo.return_record_item` | **0 rows** |

SQL is authoritative for `dbo.return_record` and `dbo.return_record_item`; Mongo
is a derived projection. The authoritative store holds nothing, so these are not
projections that lost their items and can be rebuilt — they are the only trace
of returns that were never durably written. That is UIAUDIT-010 seen from the
other end.

**Which tables count, and why it is not obvious.** ADR-001 resolved as option B:
a console-issued RMA ticket is a *distinct artifact* whose authoritative home is
`dbo.return_requests`, `dbo.return_items` and `integration.return_support_ticket`.
Those three each hold exactly one row, which is the audit's own 0→1 and means the
console path works. The Mongo collection repaired here is written by
`case_repository.py` and keyed by `caseId`, so it projects the *case* aggregate,
whose store is `dbo.return_record` / `dbo.return_record_item` — both empty, as is
`dbo.return_case`. Comparing against the console path's tables instead would have
concluded, wrongly, that nothing was missing.

| SQL table | Rows | Whose |
|---|---|---|
| `integration.return_support_ticket` | 1 | console RMA ticket (option B) |
| `dbo.return_requests` | 1 | console RMA ticket (option B) |
| `dbo.return_items` | 1 | console RMA ticket (option B) |
| `dbo.return_case` | **0** | case workflow |
| `dbo.return_record` | **0** | case workflow — the authority for these five |
| `dbo.return_record_item` | **0** | case workflow |
| `dbo.return_tracking` | **0** | written only from a real tracking observation |

### Targets

| `returnRecordId` | `returnReference` | `caseId` | created |
|---|---|---|---|
| `4e372a39-882a-4617-b2c8-60c14e094c64` | `RMA-OPS01-CD4364` | `d3190045-3baa-4895-8ad0-461d080eb750` | 2026-08-14 08:49 |
| `d4322f4a-6dfa-49d4-a798-00bf69b6c418` | `P12-RMA-1786809322` | `8c7c0741-f919-488b-9004-a8f10715a48d` | 2026-08-15 15:56 |
| `b960d1a6-edaa-4410-bd9d-a09ec5ae461d` | `P12-WTY-1786809694` | `6900a93c-6f63-44d3-8bc2-2b73fd0c321a` | 2026-08-15 16:01 |
| `54eba26d-df86-40cb-84b6-749d4f0963f1` | `P12-DLV-1786809831-2` | `fddb2752-45ed-44c0-aedc-61eccd111c83` | 2026-08-15 16:03 |
| `ab94b8d6-0e40-4183-8f6e-36177bff44cc` | `P12-DLV-1786809831-1` | `fddb2752-45ed-44c0-aedc-61eccd111c83` | 2026-08-15 16:03 |

### What the repair does, and why not the alternatives

*Not deletion.* These documents are the sole surviving evidence that someone was
told a return had been issued. The rules forbid deleting return or financial
truth, and this is the only copy of it.

*Not fabrication.* Writing the missing SQL rows would mean inventing a
`return_record_item` per document — quantities, reason codes, order lines —
that nothing observed. An invented durable record is worse than an honest gap,
because the next reader cannot tell it from a real one.

*The reclassification.* `ISSUED` is a claim about the authoritative store and the
authoritative store says nothing, so the status becomes `UNKNOWN` — the frozen
vocabulary's answer for "the platform does not know", and specifically never
`ISSUED`. Identifiers, case, reference and timestamps are untouched, and each
document gains a marker naming the repair and its manifest digest.

### The mechanism that made them is now closed

Repairing these would have been treating a symptom. Support could submit an
outcome with no lines ticked; the activity builds a record's items by iterating
`order_line_references`; an empty list produced a record covering nothing. The
invariant that forbids this was declared in the frozen decisions and enforced in
no layer — not the submit gate, not the wire contract, not the seam, not SQL.

`ReturnOutcomeRecord.orderLineReferences` now requires at least one, and the
console names the RMAs missing lines. Committed separately at `36a82d0`.

### Applied 2026-08-23, after T04 closed

**T04 closed first, live, which is what the rules gate this on.** One Support
answer against case `7b216e58` produced exactly one `dbo.return_record`
(`RMA-T04-C0EB81B235`), exactly one `dbo.return_record_item` (`LINE-1`), and
**zero** `dbo.return_tracking` rows — from *four* requests: one `RECORDED` and
three `DUPLICATE`, all sharing one `outboxCommandId`. `dbo.return_record` went
from having never held a row to holding one. The case advanced
`AWAITING_SUPPORT → RMA_RECEIVED`.

Then the repair ran: **5 attempted, 5 reclassified, 0 skipped, complete**, digest
`09d980e757…`. Each document now reads `UNKNOWN` and carries
`repairedBy: T19b-return-projection-status`. Identifiers, references, cases and
timestamps are untouched. The one genuinely issued record — the one T04 had just
written — is **still `ISSUED`**, which is the repair being as selective as it
claims.

### Two corrections the live run forced

Running this against real data found two defects in the repair itself, both of
which would have made it wrong:

1. **The detection matched everything.** It looked for an empty `approvedItems`
   on the Mongo document. That field is not stored there at all — items live in
   their own collection and the field is assembled at read time — so the query
   flagged healthy records as well as damaged ones. Detection is now per record:
   *does the authoritative store hold a row for this id?*

2. **The refusal was too coarse.** It refused whenever SQL held anything, on the
   reasoning that a non-empty store means these are stale rather than baseless.
   The first correctly written record — T04's — immediately made the repair
   unavailable for five projections that still had no row of their own. The
   question is asked per record now.

Both were caught only because the repair was run against a store that had just
gained one real row. A dry run against an empty store would have looked correct.

### Original gating rationale, kept

The repair rules sequence this explicitly: preserve the audit bundle → inventory
whether the records still exist → capture pre-repair snapshots → **run T04
closure against fresh isolated identifiers** → repair historical records only
after the new invariant passes.

T04's exact-once invariant has not been proven against fresh identifiers on this
stack. That needs the API and the workflow worker running end to end, which has
not been done. Applying before that would repair the symptom while the cause is
still unverified — and would consume the only evidence of it.

### Evidence retained

- Dry-run manifest and rollback manifest: `.runtime/repair/T19b/`
- Plan digest (stable across runs over unchanged data):
  `315e82dc7d497f37f38fc2fb185fba263b05aed406bfb86e75f696d48c1ae61c`
- Command: `python scripts/repair_return_projections.py` (dry run is the default)
- Apply requires quoting the digest; a plan whose data has moved is refused.

---

## T19c — graph lifecycle debris · **NOT_REQUIRED**

| Reading | Value |
|---|---|
| `dynamic_graph_generations` grouped by status | `{}` — the collection is empty |
| `dynamic_graph_active_snapshots` | **1** |
| Serving snapshot | `ORDER_DISCOVERY`, generation `9cf89d56-4320-4dbc-8ab3-0102e64b6661`, activation version 4, activated 2026-08-16 11:40 UTC |

The serving invariant holds: exactly one active runtime snapshot, and no
generation documents at all. The debris T08 was built to reclaim (stamped
`ACTIVE` 9, `FAILED` 20, `PREPARING` 20) is not present in this deployment's
current state.

**No repair is run.** T08's reclaimer remains the mechanism if debris
reappears; there is nothing for a one-off repair to target.

---

## T19d — legacy interception records · **NOT_REQUIRED**

Searched for `ai_interceptions`, `interceptions` and `ai_interception_records`.
**None of the three exists** in the `return_platform` database.

There are no interception documents in any shape, legacy or current, so there is
nothing to migrate. T11's fix — starting the reaper worker and adding
status-filtered listing — stands on its own; this sub-run has no target.

---

## Operational readings taken alongside

| Reading | Value | Note |
|---|---|---|
| `integration_outbox` unpublished | 9 of 9 | Nothing has ever been published; the publisher is not running |
| `graph_sync_runs` | COMPLETED 12, FAILED 4, RUNNING 1 | The RUNNING row is what `StalledSyncRunReclaimer` terminalizes |
| `worker_heartbeats` | 0 | No worker process is running |
| `cases` | 20 — AWAITING_SUPPORT 10, AWAITING_POLICY_REVIEW 5, RMA_RECEIVED 3, GATHERING_INFO 1, CLOSED 1 | |

These are consistent with a stack whose infrastructure is up and whose
application processes are not. They are readings, not findings.

---

## Audit evidence preservation

The identifiers the rules require preserving —
`TCK-1c8a77ec-ed8d-478d-afcb-653009d91689`, `RMA-AUDITRMASESSION001`, session
`audit-rma-session-001` — were searched for:

- `rma_tickets`: the collection does not exist in this database.
- `return_records` matching `RMA-AUDITRMASESSION001`: **0**.

The audit's own records are **not present** in this deployment. Nothing repaired
here touches them, and the five T19b targets are unrelated identifiers.
