# Recovery

**Current as of 2026-08-14, commit `dcbb7dc`.**

What repairs itself, what needs an operator, and what needs neither because it was
designed not to break.

## The design principle

> **Never return success while leaving a case with no durable mechanism to advance
> it.**

Every recovery mechanism below exists to make that true under partial failure. Where
a mechanism has no way to be certain, it **fails the caller** rather than reporting
success — a visible error is recoverable, and a silently orphaned case is not.

## Self-healing

### A case whose workflow never started

**The most important one.** Committing the case and starting its workflow are two
operations against two systems. There is a window — a Temporal outage, a killed
worker, a partition — in which the case is durable and the execution that owns it is
not.

The confirmation **fails** in that window and the associate is told. But a failed
turn is not a repair: nothing about an associate walking away makes the case
reachable again, and a case with no workflow is invisible to Support, to Bay and to
every later agent turn.

`workflows/return_case_recovery.py` is the repair, and it is deliberately **not a
second reliability framework**:

- **No new collection and no new record.** The case document already carries
  `workflowId` — a field created for exactly this link, with a unique partial index
  behind it — and it is null on a case whose workflow never started. The queue is
  therefore the cases themselves, and **it cannot drift out of step with them** the
  way a parallel outbox row can.
- **No new idempotency rule.** Recovery calls the same
  `TemporalCaseWorkflowLauncher` the confirmation node calls, so a case whose
  workflow *is* in fact running — the start succeeded and only the link write failed
  — converges through `WorkflowAlreadyStartedError` and simply has its link
  rewritten.
- **No delivery guarantee of its own.** A failed pass logs and leaves the case
  exactly as it found it, so the next pass retries. No lease, no attempt counter, no
  dead-letter state, because **the terminal condition is observable from the case
  itself**.

`grace_seconds` keeps it from racing a confirmation still in flight: a case created a
moment ago is being started right now by the node that created it.

**Why not the integration outbox.** It is scoped to *external* dependency commands:
its topics are enumerated in `IntegrationConfiguration`, its dispatchers are HTTP
adapters, its terminal failure state is `BLOCKED_EXTERNAL_DEPENDENCY`, and a topic
with no registered adapter is marked non-retryable and **abandoned** — precisely the
outcome a case must never reach.

### A failed graph sync

The candidate generation is marked `FAILED`, the compare-and-swap never runs, and
**the previous generation keeps serving**. The next run starts a fresh candidate.

No operator action. Freshness degrades; availability does not.

### An interrupted incremental sync

The watermark did not advance, so the batch is re-read. Projection writes merge, so
re-reading is idempotent.

### A stale writer after a cutover

Fenced at two independent points: the Neo4j generation marker's exact-match check,
and `MongoSyncCheckpointStore`'s `$lte` refusal on watermark advance. A superseded
writer cannot write into a retired generation or move a watermark.

Two checks rather than one, because a writer can be stale against the graph and
against its own checkpoint independently.

### A rejected AI key

Its circuit opens and traffic rotates to the next validated route. Recovery is
time-based. No operator action unless every route is exhausted, in which case callers
already received their deterministic fallback and the business flow continued.

### A worker on the previous configuration release

The reconciler notices the head revision and swaps. `GET /api/config/adoption` shows
`ACTIVATING` until it does.

### An expired adoption record

TTL is three report intervals, not one. A single missed report is a scheduling
hiccup; expiring on it would make a healthy process look dead and every release look
not-live.

### A stale candidate card

Rejected on candidate-set id, expiry, **and** conversation version. The associate
searches again.

### A duplicate message or confirmation

Returns the prior idempotent result rather than applying it twice. Confirmation is
idempotent on `(tenant | conversation | order | line-set)`; a simultaneous
confirmation from a second tab returns the *same* case.

## Needs an operator

### A shipment update whose graph projection failed (502)

The authoritative SQL row **committed**; the graph projection did not. The response
says so.

**Resubmit the identical update.** It is idempotent and answers `DUPLICATE`. Do not
re-enter the data — a second distinct observation is a second observation.

Left unrepaired, the shipment reads as `AWAITING_HANDOFF` to every agent —
indistinguishable from a return still on the counter. That is why this fails loudly
instead of degrading.

### A case parked on graph-sync failure

`_park_for_graph_sync_failure` parks a case whose return records committed to SQL and
did not reach the graph. The case is visible in
[Operations](../screens/case-operations.md) with its park disposition.

Repair: resolve the graph problem, then trigger a targeted or full sync. The SQL rows
are authoritative and intact.

### A case parked with reminders exhausted

`on_reminders_exhausted` decided `PARK_FOR_OPERATIONS` or `ESCALATE`. Support never
answered within `max_reminders × reminder_interval_seconds` of business time.

Repair is a business action, not a technical one: someone must answer.

### Phone or email lookup after HMAC rotation

The lookup key is **intentionally non-recoverable** from graph evidence — that is the
property that makes the evidence safe to store, and it is also why rotation is
expensive.

Existing evidence **cannot be recomputed in place**. Rebuild the customer projection
using the current Vault key, validate graph freshness, and only then re-enable
contact-based lookup.

### A partially populated candidate generation after an interrupted cutover

Marked `FAILED` and not served, so correctness is intact. It occupies storage.
Reclaim it before rebuilding.

### An interrupted SQL migration

Migrations are checksum-tracked in `platform.schema_migrations` and are safe to
rerun, but an interruption mid-statement can leave a partially applied DDL that the
runner then refuses because the recorded checksum does not match. Resolve the DDL
state, then rerun:

```bash
python3.13 scripts/apply_sql_migrations.py
```

### No active configuration release

```bash
./scripts/prepare_runtime_configuration.sh
```

### An empty graph

Every service up, every health check green, discovery finds nothing.

```bash
python backend/scripts/build_knowledge_graph.py
```

See [`reset.md`](reset.md) — this is the platform's most confusing state and it
produces no error anywhere.

## Needs neither

| Concern | Why |
|---|---|
| A `ReturnCaseWorkflow` across a restart | Temporal durable execution. Business-calendar waits and reminder timers survive |
| A long support wait | Durable timer, `continue_as_new` |
| A discovery conversation across a restart | Durable state; reopening by id reloads from the server |
| Undelivered outbox messages | Queued and retried |
| A held interception across a restart | Remains held; `interception_resume` delivers on restart |
| An in-flight case during a configuration publish | Pinned to its own release snapshot. Its deadline is not moved underneath it |

## Diagnosing "this case is not moving"

In order:

```bash
# 1. Does the case have a workflow at all?
curl -fsS http://127.0.0.1:8000/api/cases/{case_id} | jq '.data.workflowId'
#    null  -> the recovery sweep will start it. Check the sweep is running.

# 2. Is it parked, and why?
curl -fsS http://127.0.0.1:8000/api/cases/{case_id} | jq '.data.status, .data.facts'

# 3. Is the worker that owns it on the right release?
curl -fsS http://127.0.0.1:8000/api/config/adoption | jq

# 4. Is something waiting on a human?
curl -fsS http://127.0.0.1:8000/api/ai/interceptions | jq '.data | length'

# 5. Is the graph current?
curl -fsS http://127.0.0.1:8000/api/graph-sync/runs | jq '.data[0]'
```

**What you cannot check, and must not guess at:** whether the workflow is *still
running*. `ReturnCaseWorkflow` has an `execution_state` query carrying status,
reminders sent, and whether bay and support resolved — and **no HTTP route calls it**.
`workflowId` proves an execution was started, not that it is alive. Use the Temporal
UI (`http://localhost:8080`, dev-tools profile) for that.

Also absent: a failure or blocker code on a case or a fact. The Operations screen
states these gaps rather than showing a plausible placeholder, because a fabricated
`HEALTHY` is worse than an admitted gap — the gap is fixable and the fabrication is
trusted.

## Related

- [`../architecture/canonical-runtime-flow.md`](../architecture/canonical-runtime-flow.md) §3
- [`../optimization/retry-and-backoff.md`](../optimization/retry-and-backoff.md)
- [`troubleshooting.md`](troubleshooting.md)
