# Operations — Cases and Return sessions

**Route** `/operations` · **Capability** `config.runtime.read` ·
**Components** `frontend/src/domains/operations/OperationsPage.tsx`,
`CaseOperationsPage.tsx`, `ReturnsOperationsPage.tsx`

## Purpose

Find out why a return is not moving, and who has to act.

## Two sections, because there are two units of work

| Section | Slug | What it is |
|---|---|---|
| **Cases** | `/operations/cases` | The canonical unit: one confirmation identity, N RMAs, its own durable `ReturnCaseWorkflow` |
| **Return sessions** | `/operations/return-sessions` | The older session-scoped record with its own event endpoint |

Neither is a view over the other. Selecting between them changes what the screen
*is*, which is the test for a section rather than an on-screen filter.

Both are backed. The domain used to carry a `NO BACKEND YET` badge; it does not,
because Cases reads `/api/cases`, `/api/config/adoption` and the support surface,
and Return sessions reads `/api/returns`. The platform-health half — graph
generations, workers, outbox — still has no API, so this domain no longer promises
it. A badge saying "no backend" over two working screens would be the same lie in
the other direction.

## Which half is backed, and which is not

Everything on this screen is read from a backend field. **Where the platform
publishes no field, the screen says so in those words** rather than showing a
plausible placeholder. A fabricated `HEALTHY` is worse than an admitted gap,
because the gap is fixable and the fabrication is trusted.

Three questions an operator asks that no API answers, stated as unavailable rather
than invented:

| Question | Why it cannot be answered | What is shown instead |
|---|---|---|
| **Is the workflow still running?** | `ReturnCaseWorkflow` has an `execution_state` query carrying status, reminders sent, and whether bay and support resolved — and **no HTTP route calls it**. | The case's `workflowId` proves an execution was *started*. The screen says exactly that, and does not claim it is running. |
| **What is the workflow's business-calendar deadline?** | `ReturnCaseTimings` is workflow *input*, not published state. | The Channel B work item's `slaDueAt`, labelled as **the support SLA** — not relabelled as the workflow's deadline. |
| **What is blocking this case?** | No case field and no fact carries a failure or blocker code. | Nothing. The absence is stated. |

## UI regions — Cases

**Case list** — from `/api/cases`, with status and creation time.

**Case detail** — from `/api/cases/{case_id}`:

- header and status;
- the **RMA hierarchy**: each return record with its own items, label, tracking
  and return location, nested. Never flattened onto the case;
- the **bay recommendation**: warehouse, bay, return location, computed
  confidence, reason, explanation, capacity evidence. All from one reading;
- the **fact log** — the case-scoped audit trail;
- `workflowId`, presented as proof the workflow was started.

**Adoption panel** — from `/api/config/adoption`. Shows `LIVE` /
`ACTIVATING` / `NO_ACTIVE_RELEASE` and, when activating, **which process classes
have not adopted**. `adoptionGap` computes the difference so an operator sees the
gap rather than reading two lists.

This panel sits on a case screen deliberately: "why is this case behaving like
the old configuration" is answered by "the worker that owns it has not adopted
the release yet", and that is not a question anyone thinks to ask on the
Configuration screen.

**Support conversation** — the Channel B messages for this case, read-only here.

**Contextual rail** — case facts and notes.

## UI regions — Return sessions

Session list from `/api/returns`, and per session: timeline, artifacts, evidence,
support view, and the event endpoint.

## Actions

| Action | API | Side effects | Reversible |
|---|---|---|---|
| Select a case | `GET /api/cases/{case_id}` | none | Yes |
| Refresh adoption | `GET /api/config/adoption` | none | Yes |
| Post a session event | `POST /api/returns/{session_id}/events` | Advances the session-scoped flow | No |

**Permitted interventions are read-mostly.** There is deliberately no "restart
the workflow", "force the bay", or "clear the block" control: none of those have a
backend surface, and a button that calls nothing is worse than an absent one. The
durable recovery sweep (`workflows/return_case_recovery.py`) is what repairs a
case whose workflow never started, and it needs no operator action.

## Backend APIs consumed

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/cases` | Case list |
| `GET` | `/api/cases/{case_id}` | Case, return records, items, facts, bay recommendation, `workflowId` |
| `GET` | `/api/config/adoption` | Release adoption per process class |
| `GET` | `/api/returns` | Session list |
| `GET` | `/api/returns/{session_id}` | Session |
| `GET` | `/api/returns/{session_id}/timeline` | Session timeline |
| `GET` | `/api/returns/{session_id}/artifacts` | Labels, documents |
| `GET` | `/api/returns/{session_id}/evidence` | Evidence records |
| `GET` | `/api/returns/{session_id}/support` | Support view |
| `POST` | `/api/returns/{session_id}/events` | Session event |
| `GET` | `/api/v1/return-support/work-items` | `slaDueAt` for the support SLA |
| `GET` | `/api/return-history` | Historical returns |

## Live-state behaviour

Polled on an interval and on window focus. "Live" means as of the last fetch, and
the screen shows that time.

Adoption is the one panel where staleness is materially misleading, so it is
refetched more aggressively — a release that went `LIVE` thirty seconds ago and
still shows `ACTIVATING` sends an operator chasing a worker that is fine.

## Loading, error and empty states

| State | Renders | Distinguished from broken by |
|---|---|---|
| No cases | "No cases" | Explicit; an error renders the error panel |
| Case has no RMAs | "No RMAs issued" | Distinct from a failed case load |
| Bay pending / not applicable | The recommendation's `reason` and `explanation` | Bay is best-effort. `PENDING` ("not yet") and `NOT_APPLICABLE` ("never") are shown as different things, because they are |
| Bay confidence absent | Explicitly "no recommendation produced" | **Not** rendered as low confidence. `confidence_millionths is None` means nothing was recommended, which is different from a recommendation made with low confidence |
| Capacity evidence `DECLARED` | Stated as such | It means the live reservation aggregate could not be read, so the chosen bay may already be full and the reservation may refuse it |
| No active release | `NO_ACTIVE_RELEASE` | Distinct from a failed adoption read |
| Load failure | Error panel with correlation id | |

## Persistence and data source

| Data | Store | Read path |
|---|---|---|
| Case, facts, `workflowId` | Platform MongoDB | Direct |
| Return records, items, tracking | Platform-owned SQL | Direct |
| Bay recommendation | Computed from a **graph** read | Through the graph, never from source SQL |
| Shipment evidence | **Graph**, after targeted sync | Never from an inferred prior tracking value |
| Adoption | `runtime_process_adoptions` in Platform MongoDB | One document per live process instance |
| Sessions, timeline, artifacts | Platform MongoDB | Direct |

## Audit effects

This screen is mostly a *reader* of audit trails rather than a producer:

- the case fact log is the case-scoped audit trail;
- `/api/config/audit` is the configuration-scoped one — a different question with
  a different scope, and they are not merged;
- posting a session event writes to the session's own history.

## Configuration dependencies

| Family | Effect | Restart |
|---|---|---|
| `workflow` timings | The SLA shown, the reminder count | No; new cases only |
| `business_calendars` | How the SLA deadline was computed | No; new cases |
| Required process classes (`REQUIRED_PROCESS_CLASSES`) | Which classes the adoption panel demands before reporting `LIVE` | Code-level, not configuration — adding a class that can never report would make every release permanently not-live |

## Known constraints

- **No live workflow execution status** (no route over `execution_state`).
- **No workflow business-calendar deadline** published.
- **No failure or blocker code** on a case or a fact.
- No platform-health surface: graph generations, worker health and outbox depth
  have no API, and are therefore absent rather than guessed.
- No operator intervention controls, for the same reason.
