# Retry and backoff

**Current as of 2026-08-14, commit `dcbb7dc`.**

Retry policy was implemented correctly in several places and documented in none as
a single policy. This is that document.

## The problem

Distributed calls fail. Some failures will succeed on a second attempt; most will
not. Retrying the second kind converts a fast error into a slow error and, under
load, into an outage.

## The correctness invariant

> *A retry is only legal when the failed attempt left nothing behind, or when the
> operation is idempotent.*

Every retry in the platform is justified by one of those two, and the
justification is what decides whether a failure is retryable.

## The taxonomy

### Transient — retry

| Failure | Why it is safe to retry |
|---|---|
| SQL Server **deadlock victim** (error `1205`) | The victim's transaction is rolled back **whole** before the error reaches the client. The loser changed nothing and holds nothing. It is not a statement that was wrong; it is a statement that was asked to step aside. |
| Network timeout on an **idempotent** write | The operation converges on the same result — shipment updates answer `DUPLICATE`, workflow starts answer `WorkflowAlreadyStartedError` |
| Provider throttling (AI) | No state changed |
| Graph read failure | Reads change nothing |
| Configuration reconcile failure | The last-good snapshot is kept and the next pass retries |
| Case workflow start failure | The recovery sweep retries; the case is unchanged |

### Permanent — do not retry

| Failure | Why |
|---|---|
| Validation error (422) | The payload is wrong. It will be wrong next time. |
| Authorization failure | The capability is absent |
| Configuration **checksum mismatch** | A security control, not a transient condition. Fail closed. |
| Stale head revision on publish | The caller must re-read and re-decide, not re-submit |
| `STALE` shipment update | The observation is genuinely older. Retrying re-rejects it. |
| Rejected AI key | Open its circuit and rotate. Retrying the same key re-learns the same rejection. |
| Integration topic with no registered adapter | Marked non-retryable and abandoned |
| Vault unavailable **before** client creation | Fail the dependency. Never fall back to `.env`. |

### Special — bounded, non-retryable-by-design

`ShipmentStateSyncFailed` → **502**, not a retry. The authoritative SQL row
committed and the graph projection did not. The *caller* resubmits the identical
update, which is safe and answers `DUPLICATE`. Retrying inside the request would
hold a connection while re-attempting a projection that just failed.

## The deadlock retry, in detail

It is worth spelling out because it is the clearest instance of the taxonomy.

```text
_DEADLOCK_VICTIM_ERROR   = 1205
_DEADLOCK_MAX_ATTEMPTS   = 4
_DEADLOCK_BACKOFF_SECONDS = 0.05   (jittered)
```

**Matched on the driver's error number**, not on the message — the message is
localized and carries the process id.

**Four attempts, bounded rather than generous.** A deadlock is resolved the moment
the winner commits, so the re-run contends with strictly *fewer* writers than the
attempt that lost. A burst of eight concurrent writers resolves well inside four
attempts. A path that still cannot get through after four is **not contending, it
is wedged**, and retrying longer converts a fast error into a slow one.

**Jittered backoff, and the jitter is the point.** The writers that just deadlocked
are, by construction, in lockstep — they arrived together and were released
together. Re-running them all after the same fixed pause **reproduces the collision
that caused the deadlock** instead of resolving it.

## Temporal retries

Activities carry Temporal retry policies. Durable waits and `continue_as_new` are
preserved mechanisms and are not retry — a workflow waiting eight business hours is
not retrying anything.

The workflow layer decides failure handling **per phase, in code**, not from a
configured per-agent policy. There is deliberately no `failure_policy` field on
`AgentConfiguration`, because the directions are different *control flow* rather
than different values:

| Phase | On failure |
|---|---|
| `_gather_bay` | Absorbs a failed bay request into a `REQUEST_FAILED` result and **continues** |
| `_open_support` | Absorbs an unavailable drafter into the deterministic template, and again inside the activity itself |
| `_synchronize_return_records` | **Parks** the case (`_park_for_graph_sync_failure`) |

A configured value could not have produced any of those. It could only have
contradicted them.

## Circuit breakers

Per-key AI circuits open on rejection and recover on a time basis. A circuit is not
a retry — it is a decision to *stop* retrying a route that has told you no.

Configured per route: `circuit_breaker_failure_threshold`, with concurrency and
rate limits alongside.

## Idempotency, which is what makes retry safe

| Operation | Idempotency key / mechanism |
|---|---|
| Order confirmation | `(tenant \| conversation \| order \| line-set)` → returns the existing case |
| Case workflow start | `return_case_workflow_id(case_id)`, **derived** — Temporal answers `WorkflowAlreadyStartedError` and the loser adopts the winner. No lock. |
| Return record persistence | One idempotent transaction per record |
| Shipment update | RMA + `statusAt`, decided inside the UPDATE's `WHERE` under `UPDLOCK, HOLDLOCK` |
| Conversation message | Duplicate returns the prior result rather than applying it twice |
| Graph projection | Merge semantics — re-projecting a record is a no-op |
| Recovery sweep | Calls the same launcher; converges through `WorkflowAlreadyStartedError` |

Note how many of these are **derived rather than stored**. A derived key cannot
drift from the thing it identifies, which is why the workflow id is computed from
the case id and `cases.workflowId` is only a link.

## The limits

- The recovery sweep has **no delivery guarantee of its own**: a failed pass logs
  and leaves the case as it found it, so the next pass retries. No lease, no attempt
  counter, no dead-letter state — the terminal condition is observable from the case
  itself.
- `grace_seconds` prevents the sweep racing a confirmation still in flight.
- The integration outbox terminal state is `BLOCKED_EXTERNAL_DEPENDENCY`. It was
  considered for case recovery and rejected: a topic with no registered adapter is
  marked non-retryable and abandoned, which is precisely the outcome a case must
  never reach.
- Retries do not cross a generation boundary. A fenced writer is refused, not
  retried into.

## Observability

Attempt counts, failure classifications and final outcomes are recorded per
operation. AI attempts are recorded individually with the route tried and why it
failed. Sync runs record per-source outcomes.

## The failure mode

The one this policy exists to prevent: retrying a permanent failure. It costs
latency on every request, multiplies load during an incident, and hides the real
error behind the last retry's.

The taxonomy above is the defence, and it is written down here so a new call site
classifies its failures deliberately rather than by copying whichever neighbour it
was pasted from.

## Related

- [`connection-pooling.md`](connection-pooling.md)
- [`model-routing.md`](model-routing.md)
- [`../architecture/canonical-runtime-flow.md`](../architecture/canonical-runtime-flow.md) §3
