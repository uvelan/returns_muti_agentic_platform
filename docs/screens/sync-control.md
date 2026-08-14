# Source Sync

**Route** `/sync` · **Capability** `config.source.read` ·
**Component** `frontend/src/domains/sync/SyncControlPage.tsx`

## Purpose

Check the graph is current, and rebuild it from the sources when it is not.

`config.source.read` is the right question here — "may this person see how the
platform reads its sources" — so this domain does not borrow a capability the way
Support and Operations currently do.

## UI regions

**Run history** — sync runs, newest first. The mode filter narrows the list; it
does not switch what the screen is.

**Run detail**:

- **mode** and **record scope** — which sources took part, and which records were
  read;
- per-source record counts read and written;
- **watermark** before and after;
- **skipped sources**, each with its reason;
- the graph generation written into;
- start, end, outcome.

**Trigger panel** — request a run.

**Contextual rail** — run facts.

## Mode, record scope and watermark semantics

`GraphSyncRequest` has two **orthogonal** fields, and keeping them separate is a
deliberate design choice the screen mirrors:

| Field | Question | Values |
|---|---|---|
| `mode` | Which **sources** take part | `FULL`, `SOURCE_MONGODB`, `SQLSERVER` |
| `incremental` | Which **records** a run reads | `false` (full scan) / `true` (resume from watermark) |

They are separate so the two questions stay composable: an incremental
Mongo-only pass is `SOURCE_MONGODB` + `incremental`, **not** a fifth enum member
per combination.

`incremental` defaults to `false`. A caller that has not thought about resume
semantics gets the full scan it always got.

`maxRecordsPerAsset` (default 1,000, max 100,000) bounds one run's read per asset.
`applySchema` decides whether the run also applies schema constraints.

### Watermarks

An incremental run resumes from the stored high-watermark per source asset and
advances it on success. The screen shows both values, because "the watermark did
not move" is the signal that a run read nothing — different from a run that failed.

Checkpoint advance is fenced: `MongoSyncCheckpointStore` refuses an advance from a
writer whose fencing token is `$lte` the current one. A superseded writer cannot
move the watermark.

### Skipped-source reporting

A source can be skipped for a reason that is not a failure — not selected by
`mode`, no binding, nothing new since the watermark, or unreachable. Each is
reported with its own reason.

Collapsing these into "skipped" is how a misconfigured binding hides behind a
healthy-looking run. The screen shows the reason per source.

## Manual-trigger authorization

Triggering a run is a **write** and requires write authorization on top of
`config.source.read`. Reading run history does not.

A manual `FULL`, non-incremental run is the heaviest operation available from any
screen: it is the generation-cutover path. The trigger panel states what the
requested combination will do before it is submitted.

## Actions

| Action | API | Side effects | Reversible |
|---|---|---|---|
| List runs | `GET /api/graph-sync/runs` | none | Yes |
| Open a run | `GET /api/graph-sync/runs/{run_id}` | none | Yes |
| **Trigger a run** | `POST /api/graph-sync/runs` | Reads sources; writes a graph generation. A `FULL` non-incremental run performs a **generation cutover**. | No — but the previous generation serves until the new one validates |

## The generation guarantee

A run does **not** mutate the live graph in place. It allocates a generation, syncs
into it, validates, compare-and-swaps the `ActiveRuntimeSnapshot`, drains readers
on the old generation, and retires it.

**The active generation is never dropped before its replacement validates.** If the
candidate fails at any stage it is marked `FAILED`, the swap never runs, and the
current generation keeps serving. So a failed sync degrades freshness, never
availability.

This is what the fencing token protects. It is a **durable monotonic counter**, not
a constant — the old constant `1` fenced nothing, and neither the Neo4j marker's
exact-match check nor the checkpoint store's `$lte` refusal could tell an owner
from a stale writer.

See [`../architecture/graph-generations.md`](../architecture/graph-generations.md).

## Backend APIs consumed

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/graph-sync/runs` | Run history |
| `POST` | `/api/graph-sync/runs` | Trigger |
| `GET` | `/api/graph-sync/runs/{run_id}` | One run's detail |
| `GET` | `/api/config/sources` | Source names for the run view |
| `GET` | `/api/principal` | Capabilities |

## Live-state behaviour

Polled. A run in progress refetches on an interval and renders **server-reported**
counts and stage. No client-side progress estimation.

## Loading, error and empty states

| State | Renders | Distinguished from broken by |
|---|---|---|
| No runs | "No sync runs" — with a note that a graph with no runs is empty | Actionable: loading source collections leaves Neo4j empty, and an empty graph makes the copilot truthfully report finding nothing, which reads as a broken agent rather than a missing build |
| Run in progress | Server stage and counts | Never a timed bar |
| Run failed | The failure and the generation marked `FAILED` — plus the statement that the previous generation is still serving | Otherwise a failed sync looks like an outage |
| Source skipped | The per-source reason | "Skipped" alone would hide a misconfigured binding |
| Watermark unchanged | Stated explicitly | Distinct from a failed run |
| Load failure | Error panel with correlation id | |

The empty state is worth its wording. An empty graph is the single most confusing
state this platform has: everything is up, every health check passes, and the
copilot finds nothing. The screen names the cause and the fix.

## Persistence and data source

- Run records and checkpoints: **Platform MongoDB**
  (`MongoSyncCheckpointStore`).
- Written data: **Neo4j**, into a specific generation.
- Read from: **source** MongoDB and **source** SQL Server, read-only, in bounded
  batches.

Cypher is parameterized and identifiers are allowlisted. SQL reads use bounded
bulk batches (`GRAPH_SYNC_BATCH_SIZE`, 1,000-row default).

## Audit effects

Every run is a durable record: requester, mode, record scope, per-source counts,
watermarks, skipped reasons, generation id, fencing token, outcome. Manual
triggers record the principal.

## Configuration dependencies

| Family | Effect | Restart |
|---|---|---|
| `sync` | Batch size, schedules, per-source enablement | Hot |
| `sources`, `data_assets` | What can be synced | Hot |
| `graph` constraints/indexes | What a run applies when `applySchema` is set | Hot |
| Active schema release | The mapping a run projects through | Applied at activation |
| `GRAPH_SYNC_BATCH_SIZE` (env) | SQL read batch size | Restart |

## Known constraints

- No cancel for an in-progress run.
- No per-asset scheduling from this screen; schedules are configuration.
- No graph browser. Verifying content means querying Neo4j directly.
- Progressive plan fan-out is serial (PERF-01) — see
  [`../optimization/README.md`](../optimization/README.md).
