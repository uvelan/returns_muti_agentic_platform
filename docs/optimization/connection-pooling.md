# SQL Server connection pooling

**Current as of 2026-08-14, commit `dcbb7dc`.**

## The problem

`SQLBusinessStateRepository` called `pymssql.connect` **per operation**. Nothing
capped how many connections existed at once.

Every other datastore in this platform pools for us: `AsyncMongoClient`,
`neo4j.AsyncDriver` and the Valkey client each hold a pool internally and are
created once per process in `main.py`'s lifespan. `pymssql` has no equivalent, so
this one path had none — and `RuntimeResources` had nothing to close but a
one-worker probe executor.

## The failure mode it produced

A saturated SQL Server answers with:

```text
Login failed for user 'sa'
```

That is a **write failure whose real cause is only visible in the server's own
log**. It reads as a credential problem. Every instinct it triggers — check the
password, check Vault, check the connection string — is wrong, and the actual cause
is that the platform opened more connections than the server would accept.

This is why an unbounded resource is not merely a performance issue. It produces a
misleading error under load, at the moment when diagnosis is hardest.

## The scale assumption

Concurrent returns across an API process and its Temporal worker, each doing several
SQL operations per case. Without a bound, connection count scales with concurrency
and hits the server's limit rather than any limit of ours.

## The correctness invariant

> *A pooled connection handed to a caller must be usable, and a transaction must
> never span two checkouts.*

## Strategy

`operations/sql_connection_pool.py` — a bounded pool, `get_sql_connection_pool()`
per process, with `acquire()` and `transaction()` context managers.

### Synchronous and thread-safe, not async

Deliberately, because the code it serves is.

`pymssql` is a **blocking C extension**, so every caller already runs inside
`asyncio.to_thread`. A connection is therefore checked out **on the worker thread
that is about to use it**, and `threading.Condition` — not `asyncio.Semaphore` — is
what bounds it.

That also means **one pool serves the API process and its Temporal worker alike**,
without either owning an event loop the other can see. An `asyncio`-based pool
would have needed one pool per loop, which is two unbounded pools wearing a bound.

### Connections are opened and validated outside the lock

A `pymssql.connect` against an unreachable server blocks for `login_timeout`.
Holding the pool lock for that long would **convert one slow connect into a stall
for every other caller** — turning a single unreachable-server incident into total
unavailability of the write path.

### Idle validation

A connection that has sat idle past the idle timeout is round-tripped once
(`SELECT 1`) on its way out of the pool. A connection the server closed while it
sat idle is otherwise handed to a caller that discovers it mid-transaction.

## Configuration

| Setting | Default | Range |
|---|---|---|
| `sqlserver_pool_max_size` | 8 | 1–128 |
| `sqlserver_pool_acquire_timeout_seconds` | 5.0 | >0–60 |
| `sqlserver_pool_idle_timeout_seconds` | 300.0 | 1–3,600 |

**Sizing.** `max_size` is a ceiling per process, not per platform. Total connections
are `max_size × (API replicas + worker replicas)` and must stay under the server's
connection limit with headroom for migrations, probes and administrative sessions.

The default of 8 is deliberately modest: the failure mode of too-small is a bounded
wait, and the failure mode of too-large is the misleading login error above.

**Acquire timeout** makes the wait for a free connection a *bounded* wait with a
clear error, instead of an unbounded queue. Five seconds is chosen so a saturated
pool surfaces as saturation rather than as a request that appears to hang.

## Caching and invalidation

Connections are the cache. Invalidated by idle timeout, by failing the `SELECT 1`
validation, or by an error that marks a connection unusable.

## The consistency tradeoff

None. Pooling changes how a connection is obtained, not what a transaction sees.
`transaction()` holds one connection for the whole transaction; a transaction never
spans two checkouts.

## The fallback

| Failure | Behaviour |
|---|---|
| Pool saturated | Wait up to `acquire_timeout`, then fail with a **saturation** error rather than a login error |
| Connection failed validation | Discarded, a fresh one is opened |
| Server unreachable | The connect fails — outside the lock, so other callers are unaffected |
| Connection lost mid-transaction | The transaction fails and the connection is discarded. Callers retry per the retry policy; the shipment write path retries a deadlock victim |

There is no fallback to unpooled connections. That would reintroduce the unbounded
path the pool exists to close.

## The limits

- Per-process ceiling only. Coordinating a platform-wide budget is deployment
  configuration, not code.
- Synchronous — it must be used from a worker thread, and calling it directly on
  the event loop blocks it.
- Only the authoritative business write path uses it. Migrations, probes,
  governance inventory and sampling open their own short-lived connections; those
  are bounded by being one-shot rather than by the pool.

## Observability

Pool acquisition failures and validation discards are logged. A saturation error
names saturation.

The diagnostic worth remembering: **`Login failed for user` under load is a
connection-count symptom, not a credential symptom.** See
[`../operations/troubleshooting.md`](../operations/troubleshooting.md).

## Related

- [`retry-and-backoff.md`](retry-and-backoff.md)
- [`../architecture/rma-and-shipment.md`](../architecture/rma-and-shipment.md)
