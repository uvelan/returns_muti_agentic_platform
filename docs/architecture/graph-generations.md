# Graph generations, fencing and cutover

**Current as of 2026-08-14, commit `dcbb7dc`.**

## What a generation is

A graph generation is one complete, independently-addressable projection of the
sources into Neo4j. Exactly one is *active* at a time — named by the
`ActiveRuntimeSnapshot` — and readers bind to a generation for the length of
their work.

Generations are **load-bearing**. They are not a label on a single mutable graph.

## What was wrong

Every sync wrote into a single, permanently-active generation under two literals,
`legacy-live` and a constant fencing token, rather than building a new generation
and swapping. Three consequences, all real:

- A full sync **mutated the live graph in place**, so a reader could observe a
  partially rebuilt graph while it ran.
- A destructive schema change had **no safe cutover path** — the analyzer refused
  non-additive changes instead of migrating them.
- The fencing token was **constant, so it fenced nothing**. Neither the Neo4j
  marker's exact-match check nor `MongoSyncCheckpointStore`'s `$lte` refusal could
  tell an owner from a stale writer when every writer presented `1`.

The blue/green machinery that fixes this already existed and was complete
(`dynamic_knowledge/graph/generation.py`: `GraphGeneration`, fencing tokens,
read/write drain leases, `ProjectionOwnership`; `dynamic_knowledge/lifecycle/`:
the orchestrator, the snapshot compare-and-swap, the drain). It simply was not
used. It is now what `data_platform/graph/sync_service.py` runs on.

## The cutover sequence (C9)

```text
allocate generation N+1
  → sync into it
  → catch up
  → validate
  → compare-and-swap the ActiveRuntimeSnapshot
  → drain readers on generation N
  → retire N
```

**The active generation is never dropped before its replacement validates.** If
the candidate fails at any stage it is marked `FAILED`, the compare-and-swap
never runs, and N keeps serving.

Never mutate the active generation in place.

## Fencing tokens

The fencing token is a **durable monotonic counter**, allocated per generation.
It is not a constant and it is not derived from a timestamp.

A writer that has been superseded still holds the old token. It is refused at two
independent points:

- the Neo4j generation marker's exact-match check;
- `MongoSyncCheckpointStore`'s `$lte` refusal on checkpoint advance.

Two checks rather than one because a writer can be stale against the graph and
against its own checkpoint independently, and either alone would let a partition
survivor write into a retired generation.

## `legacy-live`, and why it still appears

`legacy-live` and the legacy token survive **only as bootstrap values**, never as
what production runs on.

A deployment whose graph predates the protocol has nodes under `legacy-live` and
no `ActiveRuntimeSnapshot`. That generation is **adopted** as activation version
1 by `_resolve_active_generation`, which does two things at once:

1. gives the next rebuild a predecessor to drain and retire, instead of orphaning
   the live data;
2. claims a real allocated token on the marker, so a writer still holding the
   legacy token is fenced off from that moment on.

A brand-new deployment creates the same marker once, for the same reason: readers
that have not yet seen a snapshot fall back to this exact id, so writer and reader
must agree on it until adoption completes.

## Migration classification

Releases are immutable, so migration is generational. Activation used to be a
pointer flip in the dark: an operator could move the runtime from a release whose
`Order` matched on `order_id` to one that matches on `salesInvId` and learn about
it from the first associate who could not find an order.

`dynamic_knowledge/release_migration.py` answers the question the flip has to
answer first — given the running release and the proposed one, what must the
graph do? Three classes, and the strategy each earns:

| Class | Change | Strategy |
|---|---|---|
| `ADDITIVE` | Nothing existing changes; new labels, edges or properties appear | **`BACKFILL`** the affected sources |
| `COMPATIBLE` | Existing identities stand, but their *mapping* moved — a property's path, its type, a cardinality bound | **`AFFECTED_SCOPE_RESYNC`** of those sources |
| `DESTRUCTIVE` | Identity changed, or something is withdrawn that a merge can never take back | **`FULL_REBUILD`** via a generation cutover |

### Why the boundary sits there

The writer **merges**: it matches a node on its key and sets properties. Merging
can add a label, add a property, overwrite a property and add an edge. It
**cannot** unset a property the release stopped projecting, cannot retire an edge
type nobody writes any more, and cannot re-key nodes whose identity changed — it
would insert a second copy beside each one.

Overwriting is the capability `COMPATIBLE` rests on. The other three are why
`DESTRUCTIVE` still means a cutover, which the sync service now actually performs
rather than refusing.

### Why `COMPATIBLE` had to exist

Every mapping change used to be lumped in with the destructive ones, so
correcting a mistyped property cost a complete rebuild of the graph.

And the cheap tier was called `INCREMENTAL`, which was optimistic in the other
direction: an incremental pass only re-reads records whose cursor moved, and a
property that is new or newly-mapped has to be written onto records that did not
change at all. Both cheap tiers are therefore a **bounded full scan of the
affected sources** — which is what `affected_source_asset_ids` is for.
`INCREMENTAL` survives in the enum only so plans recorded before this change
still deserialize; nothing produces it any more.

### Reasons are mandatory

Every non-trivial verdict carries its reasons, including the resync tier.
"Rebuild" without "why" is not reviewable.

## How sync mode maps to strategy

`GraphSyncRequest` has two orthogonal fields, deliberately kept separate:

- `mode` (`FULL`, `SOURCE_MONGODB`, `SQLSERVER`) — which **sources** take part;
- `incremental` (bool) — which **records** a run reads.

They are separate so the two questions stay composable: an incremental
Mongo-only pass is `SOURCE_MONGODB` + `incremental`, not a fifth enum member per
combination. `incremental` defaults to `False` — a caller that has not thought
about resume semantics gets the full scan it always got.

Under migration:

| Plan | Sync |
|---|---|
| `DESTRUCTIVE` | `FULL`, non-incremental — the generation cutover |
| `COMPATIBLE` | Full re-scan of the affected sources into the serving generation |
| `ADDITIVE` | The same scan, filling in what is new |

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/graph-sync/runs` | Sync run history |
| `POST` | `/api/graph-sync/runs` | Trigger a run |
| `GET` | `/api/graph-sync/runs/{run_id}` | One run: what it read and what it wrote |
| `GET` | `/api/schema-releases` | Published schema releases |
| `GET` | `/api/schema-releases/{release_id}/migration-plan` | The classification and its reasons — **read this before activating** |
| `POST` | `/api/schema-releases/{release_id}/activate` | Activate, running the strategy the plan named |

## Preserve

Sync checkpoints and high-watermarks, parameterized Cypher, identifier
allowlisting. `ActiveSchema` is the one compiled form — nothing here is a second
graph-schema representation.

## Related

- [`graph-analyzer.md`](graph-analyzer.md)
- [`../screens/sync-control.md`](../screens/sync-control.md)
- [`../screens/graph-schema-studio.md`](../screens/graph-schema-studio.md)
- [`../optimization/incremental-sync.md`](../optimization/incremental-sync.md)
