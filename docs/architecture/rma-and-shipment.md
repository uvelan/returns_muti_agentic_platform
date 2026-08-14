# RMA persistence and shipment state

**Current as of 2026-08-14, commit `dcbb7dc`.**

## The hierarchy (C3)

```text
Case  →  N Return Records (RMAs)  →  N Return Items
```

Each Return Record owns its **own** RMA number, associated items, tracking
records, labels and return location.

**These values may not be flattened onto the Case and may not be shared between
Return Records.** A case with two RMAs has two labels, two tracking histories and
two return locations, and a screen or an API that shows one of each is lying
about the second.

Support creates zero or more Return Records for one Case. Zero is legal.

## Persistence

Platform-owned SQL, created by:

- `005_case_return_records.sql` — the case → return record → return item tables
- `006_return_shipment_state.sql` — RMA-scoped shipment state

Each Return Record is persisted in **one idempotent transaction**. After commit
the platform case is updated and **only the affected records** are synchronized
into the active graph generation — not the whole case, not the whole graph.

A graph-sync failure at this point parks the case
(`ReturnCaseWorkflow._park_for_graph_sync_failure`) rather than reporting success
over a graph that has never heard of the RMA.

**External source databases remain read-only.** The platform writes its own
return tables in the same SQL Server instance; that is not a violation of the
read-only guarantee, which is a boundary against *source-system objects*. See
[`security-boundaries.md`](security-boundaries.md).

## Shipment state (C4)

### The entry point

```http
POST /api/return-shipments/{return_reference}/updates
```

Required capability: `returns.logistics.act` — the same grant behind confirming a
carrier booking and recording a physical handoff, because a carrier event is a
logistics act.

### The request

| Field | Type | Notes |
|---|---|---|
| `trackingReference` | string, 1–128 | `dbo.return_tracking`'s own column width |
| `shipmentStatus` | string, 1–32 | **Not enumerated.** The carrier's status vocabulary is the carrier's; a platform-side allowlist would silently reject a status a carrier legitimately started emitting. |
| `statusAt` | datetime, **timezone-aware** | The carrier's status timestamp, and the ordering authority for the whole contract. `APPLIED` vs `STALE` is decided against this and nothing else. |
| `trackingType` | string | Validated against `CK_return_tracking_type`. **Required, not defaulted** — a shipment's ship-via is a property of that shipment, and defaulting it would file a BOL freight movement as a parcel with nothing downstream able to tell. |
| `carrierCode` | string, ≤32 | Optional |
| `shipmentDetails` | string, ≤1000 | Optional |

An unzoned `statusAt` is **rejected**, not assumed to be UTC. This field decides
whether an update advances stored truth or is refused as stale; reading a naive
timestamp as UTC would let a submitter in a different zone silently overtake — or
silently lose to — an event it has no relationship to.

Every length above is the destination column's own width, so a payload this model
accepts is a payload the store can hold. Refusing an over-long tracking number is
a 422 naming the field; letting it through is a truncation or a driver error a
caller cannot act on.

### The three outcomes — all HTTP 200

| Outcome | Meaning |
|---|---|
| `APPLIED` | The update advanced stored truth. Synced to the graph. Produces a reading and two case facts. |
| `DUPLICATE` | The same observation, submitted again. Changes nothing, appends no second fact, produces no reading. |
| `STALE` | An observation older than the stored one. Rejected, appends nothing, produces no reading. |

**All three are 200 on purpose.** They are correct outcomes of a well-formed
request, not client errors. A caller replaying a carrier feed must be able to
tell "already knew that" from "your request was wrong", and collapsing
`DUPLICATE` into 4xx destroys that distinction.

### Where the semantics live

RMA scope, idempotency and the verdict are settled **inside the UPDATE's WHERE
clause under `UPDLOCK, HOLDLOCK`** — not by a read-then-write the route could
lose a race on, and not by anything a second concurrent request could reorder.

The route's only jobs: name the RMA, validate the payload against what the store
will accept, hand the update over, report the verdict unchanged.

`statusAt` is converted to naive UTC once, at the route, because `event_at` is
`DATETIME2(3)` and carries no zone. An aware timestamp reaching the driver would
be compared against naive UTC rows by whatever the driver decided to do with the
offset.

### Graph sync, and the one case that fails

A **graph outage does not fail the update**. The fulfilment reading is
best-effort by declared policy and degrades to `UNAVAILABLE`. The authoritative
row is the point; refusing the write over a reporting concern would take the
store down for an outage it does not depend on.

A **graph sync failure does fail the response**. `record_shipment_update` raises
when it cannot project an `APPLIED` update, because a shipment the platform has
accepted and the graph has never heard of reads as `AWAITING_HANDOFF` to every
agent — indistinguishable from a return still on the counter. That becomes a
**502 naming the authoritative row as already committed**. Resubmitting the
identical update is safe and answers `DUPLICATE`.

### Ports and caching

Both graph ports are built from one `TargetedGraphAccess`, so the write
reservation and the read lease are counted against one generation document. Both
are cached on app state — **including the failure to build them**. Retrying a
Neo4j connection on every carrier event turns a best-effort reading into a
per-request timeout.

## Fulfilment reads

Fulfilment reads shipment truth **only through the graph**, after targeted or
scheduled synchronization — never from an inferred prior tracking value.

`workflows/fulfillment_tracking.py` defines `ShipmentObservation`,
`ShipmentObservationPort` and `ShipmentEvidence`. The adapter is
`dynamic_knowledge/integration/shipment_observations.py::GraphShipmentObservations`.
`build_fulfillment_tracking_result` is a pure function.

`ShipmentEvidence` distinguishes three readings and never collapses them:

| Evidence | Meaning |
|---|---|
| `OBSERVED` | The graph holds a shipment for this RMA. |
| `ABSENT` | The graph was read and holds no shipment. |
| `UNAVAILABLE` | The graph could not be read. |

`SHIPMENT_UNAVAILABLE:NOT_ATTEMPTED` is a distinct evidence line from both "the
parcel is not in the graph" and "the lookup failed". An operator staring at a
stalled return needs all three separated.

Readings carry `graph_generation_id`, `sync_request_id` and
`sync_skipped_reason`.

### The reading view

`caseId` is `None` when no case owns the RMA. That is a **real state, not an
error** — `dbo.return_tracking` is keyed on the RMA and needs no `return_record`
row — and reporting it lets a caller tell "nobody was told" from "the reading
failed".

`evidenceReference` is the same string the case fact carries, so what the
associate is shown and what the submitter is told cannot disagree.

## Related

- [`canonical-runtime-flow.md`](canonical-runtime-flow.md) §6, §7
- [`graph-generations.md`](graph-generations.md) — the generation the targeted sync writes into
- [`../screens/support-console.md`](../screens/support-console.md) — where RMAs are created
- [`../api/README.md`](../api/README.md) — per-endpoint contract dimensions
