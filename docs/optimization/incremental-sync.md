# Incremental sync, batching, generation swap and targeted sync

**Current as of 2026-08-14, commit `dcbb7dc`.**

## The problem

The graph must be current enough for discovery to find recent orders, without
re-reading every source record on every pass, and without ever presenting a
half-rebuilt graph to a reader.

## The scale assumption

Millions of source rows across MongoDB and SQL Server. A full scan is minutes to
hours; a reader is waiting the whole time.

## Strategies, and the correctness invariant each rests on

### 1. Incremental reads (watermarks)

**Strategy.** Each source asset carries a high-watermark. An incremental run reads
only records past it and advances it on success.

**Correctness invariant.** *A watermark may only advance past a record that has
been durably projected.* If projection fails, the watermark does not move and the
next run re-reads.

**Fencing.** `MongoSyncCheckpointStore` refuses an advance from a writer whose
fencing token is `$lte` the current one. A superseded writer cannot move the
watermark. This is why the token had to become a durable monotonic counter — when
every writer presented `1`, the `$lte` check could not tell an owner from a stale
writer, so it fenced nothing.

**Limit.** Incremental reads only catch records whose cursor moved. **A property
that is new or newly-mapped has to be written onto records that did not change at
all** — which is why both cheap migration tiers are a bounded *full* scan of the
affected sources rather than an incremental pass. `affected_source_asset_ids` is
what bounds them.

This is also why the old `INCREMENTAL` migration strategy was misleadingly
optimistic and was replaced. It survives in the enum only so plans recorded before
the change still deserialize; nothing produces it.

### 2. Batching

**Strategy.** Bounded page reads: `PLATFORM_GRAPH_SYNC_BATCH_SIZE`, default
**250**, range 1–5,000. Applied per source asset scan.

`GraphSyncRequest.maxRecordsPerAsset` (default 1,000, max 100,000) bounds one
run's total read per asset — a separate question from page size.

**Correctness invariant.** *A batch boundary is not a transaction boundary for
correctness purposes.* A run interrupted mid-batch leaves the watermark where it
was, so the batch is re-read. Projection writes merge, so re-reading a batch is
idempotent.

**Why the default is 250 rather than something larger.** Page size trades memory
and Neo4j transaction size against round trips. Raising it raises the cost of a
failed batch, because the whole batch is re-read.

**Limit.** Million-row seed definitions are **lazy**, and source writes use
bounded bulk batches, so configuration loading and validation do not materialize
the entire dataset in memory. An eager expansion of the default manifest would not
fit.

### 3. Generation swap (blue/green)

**Strategy.** Allocate a generation, sync into it, validate, compare-and-swap the
`ActiveRuntimeSnapshot`, drain readers on the old generation, retire it.

**Correctness invariant.** *The active generation is never dropped before its
replacement validates.* A candidate that fails at any stage is marked `FAILED`, the
compare-and-swap never runs, and generation N keeps serving.

This is what makes a failed sync a **freshness** problem rather than an
**availability** problem.

**What this replaced.** Every sync used to write into one permanently-active
generation in place. Three consequences: a reader could observe a partially rebuilt
graph; a destructive schema change had no safe cutover; and the constant fencing
token fenced nothing.

**Migration classification** decides which strategy a schema change earns:

| Class | Strategy | Why |
|---|---|---|
| `ADDITIVE` | `BACKFILL` | Merge can add labels, properties and edges |
| `COMPATIBLE` | `AFFECTED_SCOPE_RESYNC` | Merge can overwrite a property |
| `DESTRUCTIVE` | `FULL_REBUILD` via cutover | Merge **cannot** unset a property, retire an edge type, or re-key a node — it would insert a second copy beside each one |

Overwriting is the capability the `COMPATIBLE` tier rests on. Those three
impossibilities are why `DESTRUCTIVE` still means a cutover.

**Limit.** A cutover costs a full rebuild's worth of time and storage — two
generations exist simultaneously. Plan capacity for that.

### 4. Targeted sync

**Strategy.** After an RMA or shipment write, sync **only the affected records**
into the active generation, under an RMA-scoped generation lease.

**Correctness invariant.** *A targeted sync must not advance any watermark.* It is
an out-of-band projection of specific records, not a scan, and treating it as
scan progress would let a full pass skip records it never read.

**Why it exists.** Fulfilment reads shipment truth only through the graph. Waiting
for the next scheduled sync would mean an accepted shipment reads as
`AWAITING_HANDOFF` to every agent — indistinguishable from a return still on the
counter.

**Operational SLOs.**

| Path | Target | On breach |
|---|---|---|
| Shipment update → graph visible | Within the request. Synchronous. | The request returns **502** naming the row as already committed. Resubmitting the identical update is safe and answers `DUPLICATE`. |
| RMA outcome → graph visible | Within the workflow activity | The case is **parked** (`_park_for_graph_sync_failure`) rather than reported successful |
| Scheduled full/incremental sync | Configuration-driven | Generation marked `FAILED`; previous generation keeps serving |

Note the asymmetry, which is deliberate: a **read** failure (the fulfilment
observation) degrades to `UNAVAILABLE` and does not fail the write, because the
authoritative row is the point. A **projection** failure does fail the response,
because a shipment the graph has never heard of is worse than a visible error.

## Caching and invalidation

Sync holds no read cache. The graph *is* the cache — a projection of the sources,
invalidated by the next run that covers the same assets.

Checkpoints and generation records are in Platform MongoDB and are authoritative,
not cached.

## The consistency tradeoff

**Eventual consistency, bounded by sync cadence**, with two exceptions where
targeted sync makes it synchronous (shipment updates and RMA outcomes).

Discovery can therefore miss an order created seconds ago. That is accepted:
searching sources directly per turn would put per-associate load on production
source systems and break the read-only-through-the-graph design.

## The fallback

| Failure | Fallback |
|---|---|
| Source unreachable | Skipped **with its reason**, run continues over other sources |
| Neo4j unreachable mid-run | Generation `FAILED`; previous generation serves |
| Validation fails on the candidate generation | No swap; previous generation serves |
| Targeted shipment sync fails | 502; the SQL row stands; resubmit |
| Targeted RMA sync fails | Case parked |
| Fulfilment graph read fails | `SHIPMENT_UNAVAILABLE:NOT_ATTEMPTED` — a distinct evidence line from both "not in the graph" and "the lookup failed" |

## The limits

- No cancel for an in-progress run.
- No partial-generation serving. A generation either validates and serves, or does
  not.
- A skipped source is not retried within the same run.
- Two generations coexist during a cutover; storage must accommodate it.
- Cypher is parameterized and identifiers allowlisted — a dynamic identifier
  outside the allowlist is refused rather than interpolated.

## Observability

Per run: requester, `mode`, `incremental`, per-source records read and written,
watermark before and after, skipped sources with reasons, generation id, fencing
token, start, end, outcome. At `GET /api/graph-sync/runs`.

**A watermark that did not move is the signal that a run read nothing** — distinct
from a run that failed, and both are visible.

## The failure mode

**Historically:** in-place mutation, so a failed full sync left a partially
rebuilt live graph with no way back, and a constant fencing token meant a
partitioned writer could keep writing into it.

**Now:** a failed sync leaves the previous generation serving and marks the
candidate `FAILED`. The degradation is staleness, which is visible, rather than
corruption, which is not.

## Related

- [`../architecture/graph-generations.md`](../architecture/graph-generations.md)
- [`../architecture/rma-and-shipment.md`](../architecture/rma-and-shipment.md)
- [`../screens/sync-control.md`](../screens/sync-control.md)
