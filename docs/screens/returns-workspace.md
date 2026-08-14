# Return Business Copilot

**Route** `/returns` · **Capability** `returns.session.read` ·
**Component** `frontend/src/domains/returns/ReturnCopilotPage.tsx`

## Purpose

Take an associate from a partial, possibly misspelled description of a purchase
to one confirmed order line, and start the return. This is the screen someone
standing at a counter with a customer uses.

It replaced a queue browser that was never the copilot. Queues, a session list, a
timeline and an event form are returns *operations*, and moved to
[`/operations`](case-operations.md). The copilot's job is the one the backend has
had a durable agent for since Wave C3 and had no UI at all: find the order.

## UI regions

Three panes, from the approved design.

### Left — conversation

The associate's messages and the agent's replies. Free text. Structured
clarification anchors are submitted from candidate cards rather than typed.

### Middle — progress

Six business milestones, each owned by one agent:

| Milestone | Agent | Reached when |
|---|---|---|
| Orders identified | Order Discovery | Candidates exist |
| Order selected | Order Discovery | A confirmation is bound |
| Case raised | Return Case | The case exists |
| Bay recommended | Bay Assignment | A recommendation with a bay reference |
| RMA issued | Support | A return record exists |
| Parcel tracked | Fulfilment | Shipment evidence is `OBSERVED` |

**These are business events, not the platform's internal workflow stages.**
`ProductionReturnStage` has sixteen, most of which mean nothing to an associate
(`PRODUCT_DISPOSITION`, `VENDOR_RECOVERY`). Six are what someone standing at a
counter needs: has the order been found, is the case raised, where is the parcel.

**The middle pane is progress, not a working trace.** No model name, no graph
generation, no note about which stages the API can report on. Those are true and
they are the platform talking about itself; an associate mid-return needs the
stage.

**Progress is derived, never assumed.** A clarifying question means the agent is
still identifying; evidence with candidates means it has searched; a stage the
turn result cannot speak to stays **pending**. `output` is what that agent
produced, read from the return session rather than described — a milestone with
no output yet renders as its label alone.

The temptation here is a bar that advances on a timer. It would look finished and
mean nothing.

### Right — resolved context

The anchors the associate has supplied, **as the agent recorded them** — not as
they were typed. Plus the confirmed customer, order and line once bound, and the
contextual `DomainRail`.

Showing the agent's normalized record rather than the raw input is the point: an
associate who typed `30301` and sees `ZIP 30301` under "Customer ZIP" knows the
agent understood it as a ZIP. Echoing the raw string proves nothing.

## Actions

| Action | API | Side effects | Reversible |
|---|---|---|---|
| Send a message | `POST /api/v2/order-agent/conversations/{id}/turns` | One durable agent turn; graph reads; possibly one AI call | No — the turn is recorded |
| Start a conversation | `POST /api/v1/associate-returns/chat` | Creates a conversation | No |
| Select a candidate card | `POST /api/v1/associate-returns/conversations/{id}/messages` | Submits a structured anchor | No |
| Confirm the order | `POST /api/v1/associate-returns/conversations/{id}/confirm` | **Creates the case and starts its `ReturnCaseWorkflow`.** | **No.** This is the irreversible one. |
| Submit return details | `POST /api/v1/associate-returns/conversations/{id}/details` | Advances the production workflow | No |

**Confirmation is the screen's one irreversible action.** It commits a case and
starts a durable workflow. It is idempotent — a double-click or a simultaneous
confirmation from a second tab returns the *existing* case rather than creating a
second one — but it cannot be undone from here. If the workflow cannot be
started, the confirmation **fails** and the associate is told; it never reports
success over an unreachable case. See
[`../architecture/canonical-runtime-flow.md`](../architecture/canonical-runtime-flow.md) §2.

## Backend APIs consumed

| Method | Path | Shape |
|---|---|---|
| `POST` | `/api/v2/order-agent/conversations/{conversation_id}/turns` | → `AgentTurnResult`: message, candidates, evidence, requested clarification slot |
| `POST` | `/api/v1/associate-returns/chat` | `{message}` → conversation |
| `GET` | `/api/v1/associate-returns/conversations/{id}` | Conversation state |
| `POST` | `/api/v1/associate-returns/conversations/{id}/messages` | Structured anchor |
| `POST` | `/api/v1/associate-returns/conversations/{id}/confirm` | → case id |
| `POST` | `/api/v1/associate-returns/conversations/{id}/details` | Return details |
| `GET` | `/api/returns/{session_id}` | Session for milestone output |
| `GET` | `/api/returns/{session_id}/evidence` | Evidence backing a milestone |
| `GET` | `/api/principal` | Capabilities |

All responses are envelope-wrapped; `frontend/src/api/client.ts` enforces the
envelope rather than trusting the shape.

## Live-state behaviour

**Request/response, not streaming.** A turn is one HTTP call and the screen
renders its result. There is no websocket and no poll on the conversation — a
durable turn is a discrete unit and a poll would show partial agent state that
means nothing to an associate.

RMA and tracking panels populate only once the case's `ReturnCaseWorkflow` has
produced them. Before WF-01 they never populated at all, because no workflow was
running; that is fixed, and the panels are now genuinely "not yet" rather than
"never".

Resume semantics: reopening a conversation by id reloads its full state from the
server. Nothing lives only in browser memory.

## Loading, error and empty states

| State | Renders | How it differs from broken |
|---|---|---|
| Turn in flight | Inline pending indicator on the conversation | Progress pane is untouched — a pending turn is not progress |
| No candidates yet | "No matches yet" in the middle pane | Distinct from the error panel, which names the failure |
| Agent returned zero matches | The agent's own message explaining what it searched | This is a **result**, not an empty state |
| Turn failed | Error panel with the correlation id | Correlation id is what makes a support call actionable |
| Session not found | Explicit not-found, not an empty workspace | |

A zero-result search is the case worth calling out: it is a genuine answer from a
complete-corpus search, not a failure. See
[`../optimization/order-discovery-search.md`](../optimization/order-discovery-search.md).

## Persistence and data source

- Conversation state: **Platform MongoDB**, durable, Temporal-hosted turns.
- Candidates: read from the **Neo4j knowledge graph**, never from source systems
  directly.
- Confirmed case: **Platform MongoDB**, then SQL for return records.
- The screen holds no authoritative state. A refresh loses nothing.

## Audit effects

Every turn is recorded with correlation id, configuration release, head revision
and checksum. Every AI call inside a turn is recorded by `FinalDispatcher` with
provider, model, tokens, latency, cost and outcome — visible in
[AI Control Center](ai-control-center.md).

Confirmation writes the case and its fact log; the fact log is the case-scoped
audit trail, visible in [Case Operations](case-operations.md).

## Configuration dependencies

| Family | Effect | Restart needed |
|---|---|---|
| `discovery.identification_fields` | **What the associate can search on.** Adding a field needs no code. | No — hot-adopted |
| `discovery.strong_anchors` | Which fields match exactly first | No |
| `discovery.ambiguity_gap_millionths` | When candidates are too close to auto-select | No |
| `discovery.conversation` | Greeting patterns and responses | No |
| `discovery.progressive.customer_fulltext_index` | Which Neo4j index fuzzy name search uses | No |
| `agents.order_discovery` | Whether AI assists, whether confirmation is required | No |
| `AI_GATEWAY` routes/tasks | Which model phrases questions | No |

Hot changes apply to **new** conversations. An in-flight conversation keeps its
pinned release.

Confirm adoption with `GET /api/config/adoption` — `ACTIVATING` means some
workers are still on the old catalogue.

## Known constraints

- No canonical *write* surface for the conversation. The associate flow is
  partitioned by channel from `POST /api/returns`;
  `GET /api/returns/{session_id}/conversation` is read-only.
- No live workflow execution status. `ReturnCaseWorkflow` has an
  `execution_state` query and no HTTP route calls it, so the screen can prove a
  workflow was *started* and not that it is still running.
- No streaming turn output.
