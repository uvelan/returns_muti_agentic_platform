# Configuration snapshot caching and adoption

**Current as of 2026-08-14, commit `dcbb7dc`.**

## The problem

Every request needs the active configuration. The configuration lives in a Neo4j
graph. Traversing that graph per request would put control-plane load on the
business path.

## The scale assumption

Configuration changes are rare — a handful of releases a day at most. Reads are
per-request. The ratio is enormously read-heavy, which is what makes a snapshot the
right shape.

## The correctness invariant

> *No request may observe two modules on two different releases.*

And its corollary:

> *A release must not be reported live until every required process class is
> actually running it.*

## Strategy

### Immutable process snapshot

At startup, each process loads the active `ConfigurationHead` release, verifies its
checksum, validates the complete configuration model, resolves graph-declared Vault
references, and builds an **immutable snapshot**.

Business requests read the snapshot. They do not traverse the configuration graph.

### Head-revision comparison, not graph traversal

Runtime processes periodically compare the graph **head revision** with their
last-good snapshot's. One cheap read decides whether anything changed; a full
traversal happens only when it did.

Other API processes detect a new head revision within about five seconds and
activate the same validated domains without a restart.

### Epoch-keyed atomic swap

Reconfiguration is two-phase, coordinated by `EpochAdmission` and
`ReconfigurationCoordinator`. A single replica-scoped epoch swap is what makes a
change visible atomically — which is how the "no request sees two releases"
invariant is held rather than hoped for.

Every request holds a **uniquely-identified `EpochLease`**, not a bare count, so
releasing one request's lease can never be mistaken for releasing another's. A
reference count would let an early release from one request permit a swap while
another was still mid-flight.

Activation is a four-pass sequence: construct → publish native capabilities →
publish adapter bindings → resolve.

### Case and conversation pinning

Every conversation and case pins release id, head revision, checksum and source.

New work uses the new active release. **Existing cases continue on their pinned
release** unless a family is explicitly documented as non-case-pinned. A workflow
reads its timings once at start and keeps them for its lifetime — an in-flight
return must not have its deadline moved underneath it.

## What was wrong

Two defects, and they compounded.

**CFG-01 — workers were excluded.** The reconciler ran in the API process. Workers
were **startup-bound**: they loaded configuration once and never reconciled. So
publishing a release changed API behaviour and left every worker on the old one,
indefinitely, with no error anywhere.

**CFG-02 — adoption was unreported.** Nothing published which processes were on
which release, so the split above was undetectable. An operator who published a
release had no way to learn it was half-applied.

The documentation asserted hot configuration, which was true of the API and false
of the workers. That is DOC-16: a hot-config claim conflicting with runtime
composition.

## What exists now

All five deployed worker classes hot-adopt, and adoption is reported.

```http
GET /api/config/adoption
```

| Status | Meaning |
|---|---|
| `LIVE` | Every required class has at least one live instance, and **all** of them report the activated release id **and** head revision |
| `ACTIVATING` | Activated, and at least one required class has not adopted — still on the previous release, or not reporting at all |
| `NO_ACTIVE_RELEASE` | Nothing activated |

**Both halves of the identity are checked.** A process reporting the right release
id at an older head revision has not adopted.

### The six required classes

`api`, `return-workflow-worker`, `order-discovery-worker`, `return-orchestrator`,
`outbox-publisher`, `integration-outbox-worker`.

These are the identifiers the processes already publish — the same strings their
heartbeats use.

The API is in the set because it holds its own snapshot and serves reads from it. A
release adopted by every worker and not by the API is exactly as split as the
reverse.

### Adoption records are per instance, not per class

`runtime_process_adoptions`, one document per live process instance,
`_id = "<class>:<instance>"`.

Deliberately **not** the `worker_heartbeats` document, which is keyed by class
alone and therefore cannot hold two replicas. Readiness asks "is this class up",
which one row per class answers. Adoption asks "is *every instance* on the new
release", which it cannot.

`adopted_at` and `reported_at` are distinct: the gap is how long the process has
been quietly serving the release, and a process that adopted an hour ago and
reported a second ago is healthy, not stale.

`source` uses the same vocabulary as `PinnedConfigurationSnapshot.source`, so a
process running the version-controlled baseline is **not silently counted** as
having adopted a graph release.

## Caching and invalidation

| Cache | Invalidation |
|---|---|
| Process configuration snapshot | Head-revision change → validate → atomic epoch swap |
| AI route pool | Rebuilt at the **same** activation boundary |
| Resolved Vault references | On client creation or refresh, not per query |
| Case/conversation pinned snapshot | Never — pinning is the point |
| Adoption records | TTL-expiring, `expiresAt = reported_at + 3 × report interval` |

The adoption TTL is three report intervals rather than one: a single missed report
is a scheduling hiccup, and expiring on it would make a healthy process look dead
and every release look not-live.

## The consistency tradeoff

**Bounded staleness, reported.** Between publication and adoption, some processes
run the previous release. The window is roughly one reconcile interval per process
and is visible as `ACTIVATING`.

This is the right trade: the alternative is a synchronous distributed
reconfiguration barrier, which would make a configuration publish able to stall
every process at once.

Infrastructure endpoint changes are **restart-required and fail closed**. A running
process holds live client pools against the old endpoint; swapping under them
would leave half the process talking to each.

## The fallback

| Failure | Behaviour |
|---|---|
| Checksum mismatch | **Refuse** startup or activation |
| New release fails validation | Keep the last-good snapshot. The swap does not run |
| Stale head revision on publish | Configuration revision conflict |
| Graph unreachable at reconcile | Keep the current snapshot and retry |
| Graph unreachable at startup | Fail startup — there is no last-good snapshot to keep |
| No active release | `NO_ACTIVE_RELEASE`; run `scripts/prepare_runtime_configuration.sh` |
| Vault unavailable before client creation | Fail that dependency. **Never** fall back to `.env` credentials |
| Vault unavailable with pools initialized | Continue bounded use of established clients |
| `AI_GATEWAY`/`DEPENDENCY_SIMULATION` missing (prod/staging) | Fail closed |

Note the startup/runtime asymmetry: at runtime a last-good snapshot exists and is
better than failing; at startup none does, and serving unknown configuration is
worse than not serving.

## The limits

- Packaged YAML is bootstrap/default input only and is **never rewritten at
  runtime**.
- MongoDB holds the digest-addressed snapshot as **audit evidence** only, not as an
  editable authority.
- Rollback is forward-only: promote an earlier release.
- Adoption cannot report a class that never starts. Adding a class that can never
  report would make every release permanently not-live and turn a real signal into
  one operators learn to ignore.

## Observability

`GET /api/config/runtime` — what *this* process serves.
`GET /api/config/adoption` — whether every class has adopted.
`GET /api/config/audit` — who changed what.

The per-class gap is surfaced on the [Operations](../screens/case-operations.md)
screen, where the operator asking "why is this case behaving like the old
configuration" actually is.

## The failure mode

**Historically:** silent half-application. The API on the new release, every worker
on the old one, no error and no way to tell.

**Now:** the split is either impossible (workers reconcile) or visible
(`ACTIVATING` names the classes that have not adopted).

## Related

- [`../architecture/configuration-adoption.md`](../architecture/configuration-adoption.md)
- [`../configuration/families.md`](../configuration/families.md)
