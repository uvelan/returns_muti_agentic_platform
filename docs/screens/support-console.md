# Returns Support console

**Route** `/support` · **Capability** `returns.session.read` ·
**Component** `frontend/src/domains/support/SupportConsolePage.tsx`

## Purpose

Channel B: where the platform talks to Returns Support, and where a person plays
the Support role while Teams is not connected. Support answers the agent's return
requests and issues the RMA, label and pickup.

Support is a distinct **role** and should eventually get a distinct capability.
It currently shares `returns.session.read` with the copilot, because inventing a
`support.*` capability the backend never grants would hide this screen from
everybody — which reads as a bug rather than as work in progress.

## UI regions

**Three panes, not two.**

### Left — work-item queue

Open return requests from `/api/v1/return-support/work-items`. Each carries an
`slaDueAt`, which is the **support SLA** deadline, computed on the business
calendar.

### Middle — the conversation

The agent's request and Support's replies.

### Right — the case

The third pane is the case itself. An earlier version argued a third pane would
only restate what the agent's request already said. That stopped being true when
the case grew a shape the request cannot carry: contract C3 is
`Case → N return records → N items`, each record owning its own label, tracking
and return location, and **none of that is in a message**.

The pane shows the case header, its bay recommendation, its fact log, and the
**full RMA hierarchy**: each return record with its own items, label, tracking
and return location, nested.

Plus the contextual `DomainRail`.

## The signal-not-write model

Support's reply is a **signal into the case's running `ReturnCaseWorkflow`**, not
a direct write to the case. The workflow owns the case's state machine; this
screen hands it an outcome and the workflow decides what that means — whether it
satisfies the wait, whether reminders stop, whether the case advances or parks.

This is why the WF-01 precondition matters so much here. Before a workflow was
started for confirmed cases, Support's RMA submission signalled
`return-case-<id>` into a namespace where no such execution had ever existed, and
**the reply was lost to a `NOT_FOUND`**. Support could type a perfect answer and
nothing would happen.

The workflow id is computed from the case id (`return_support.py`), never read
from `cases.workflowId` — that field is a link for operators and recovery, not a
precondition.

## The repeatable RMA form

**One reply issues N RMAs.** The form is repeatable: add a record, and each
record collects its own RMA number, item selection, label, tracking and return
location.

It used to collect one flat set of fields and post `records: [record]`, which made
the multi-RMA half of contract C3 unreachable from **the only screen that issues
RMAs**. A return split across two labels going to two locations could be
*described* by the backend and *entered* nowhere.

**`label`, `tracking` and `returnLocation` are never shown on the case header.**
They are columns of `dbo.return_record` and the SQL schema enforces that. A case
header field for "the tracking number" would be a lie the moment a second RMA
existed.

## Actions

| Action | API | Side effects | Reversible |
|---|---|---|---|
| Open a work item | `GET /api/v1/return-support/work-items/{id}` | none | Yes |
| Reply | `POST` support message | Signals the case workflow | No |
| Add / remove an RMA block | *(client-side)* | none until submitted | Yes |
| Submit outcome with N records | Support outcome API | **Persists N return records to SQL in one idempotent transaction, updates the case, targeted-syncs the affected records to the graph, signals the workflow** | No |
| Record a shipment update | `POST /api/return-shipments/{return_reference}/updates` | SQL write, targeted graph sync, fulfilment reading, two case facts | Effectively no — a corrective update is a new observation, not an undo |

A graph-sync failure on the outcome **parks the case** rather than reporting
success over a graph that has never heard of the RMA.

A shipment update answers `APPLIED`, `DUPLICATE` or `STALE`, all HTTP 200.
Resubmitting an identical update is safe and answers `DUPLICATE`. A 502 means the
authoritative row committed and the graph projection did not — resubmit, do not
re-enter.

## Backend APIs consumed

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/return-support/work-items` | The queue |
| `GET`/`POST` | `/api/v1/return-support/...` | Conversation and outcome |
| `GET` | `/api/cases` | Case list |
| `GET` | `/api/cases/{case_id}` | Case detail, return records, items, facts, bay recommendation |
| `GET` | `/api/returns/{session_id}/support` | Support view of a session |
| `POST` | `/api/return-shipments/{return_reference}/updates` | Shipment create/update — requires `returns.logistics.act` |
| `GET` | `/api/principal` | Capabilities |

## Live-state behaviour

Polled. The work-item queue and the open case refetch on an interval and on
window focus (React Query). There is no push channel.

"Live" here means "as of the last successful fetch". The screen shows the fetch
time rather than implying continuous truth, because a support operator deciding
whether an RMA has landed needs to know how old the answer is.

## Loading, error and empty states

| State | Renders | Distinguished from broken by |
|---|---|---|
| Empty queue | "No open return requests" | Explicit wording; an error renders an error panel instead |
| Case has no return records yet | "No RMAs issued" in the case pane | Distinct from "could not load the case" |
| Bay pending | The recommendation's own `reason` and `explanation` | Bay is best-effort; a missing bay is a **state with a reason**, never an error |
| Shipment graph sync failed (502) | An explicit message stating the row is committed and resubmission is safe | Otherwise an operator re-enters the data and creates confusion |
| Load failure | Error panel with correlation id | |

## Persistence and data source

- Work items and conversation: **Platform MongoDB**.
- Return records, items, tracking: **platform-owned SQL** (`dbo.return_record`,
  `dbo.return_record_item`, `dbo.return_tracking`), migrations `005`/`006`.
- Case and fact log: **Platform MongoDB**.
- Bay recommendation and shipment evidence: read **through the graph**.
- The case's authoritative RMA truth is SQL; the graph is the read projection.

## Audit effects

- Every outcome writes case facts, visible in the case's fact log and in
  [Case Operations](case-operations.md).
- Shipment updates append two case facts on `APPLIED` and none on
  `DUPLICATE`/`STALE`.
- The `evidenceReference` on a shipment reading is the same string the case fact
  carries, so what the associate is shown and what the submitter is told cannot
  disagree.
- Administrative configuration changes are audited separately at
  `/api/config/audit`.

## Configuration dependencies

| Family | Effect | Restart |
|---|---|---|
| `workflow` timings — `support_response_wait_seconds`, `reminder_interval_seconds`, `max_reminders`, `on_reminders_exhausted` | The SLA the queue displays and the reminders Support receives | No; applies to **new** cases only — an in-flight return does not have its deadline moved underneath it |
| `business_calendars` | What "eight hours" means. A calendar declaring every day whole restores wall-clock behaviour, which is what a 24/7 operation should configure | No; new cases |
| `integrations.external_support_mirror` | Whether replies mirror to an external system | No |
| Tracking type vocabulary (`CK_return_tracking_type`) | Which `trackingType` values the shipment form accepts | Schema-level; migration |

## Known constraints

- No dedicated `support.*` capability; the screen is gated on
  `returns.session.read`.
- No push channel — the queue is polled.
- Teams integration is not connected; this screen is the human stand-in for
  Channel B.
- `shipmentStatus` is not enumerated by the platform. The carrier's vocabulary is
  the carrier's, and a platform-side allowlist would silently reject a status a
  carrier legitimately started emitting. That means typos are accepted.
