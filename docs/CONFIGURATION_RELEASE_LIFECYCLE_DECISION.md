# Configuration release lifecycle — decision brief (Wave D3)

Status: **awaiting a decision.** No code changes proposed here; this exists so the choice is
made against measurements rather than the sketch that has been in the execution ledger.

## The short version

The ledger has described this as "two configuration release lifecycles over two stores, and
choosing between them is a data migration". Measured against the running system, **both
halves of that are wrong**:

* There are **four** places configuration lives, not two.
* The one holding real production configuration is **a YAML file on disk**, which has no
  lifecycle at all.
* Neither release lifecycle holds any real data. One holds three test fixtures; the other
  holds nothing.

So this is not a data migration. It is a decision about **which lifecycle owns configuration
promotion**, and the migration attached to it is close to zero.

## What was measured

Against the live dev stack (`return-multi-agent-platform-*`, Docker volumes on this
machine), 2026-08-10.

> **Read this caveat before acting.** These are one developer machine's volumes. Production
> counts may differ, and the first step of any plan below is re-running these queries
> against the real environment. The *code* facts — which classes are constructed where, what
> the startup path does — are machine-independent and hold regardless.

| # | Store | Purpose | Rows here | Wired in production? |
|---|---|---|---|---|
| 1 | Neo4j `ConfigurationRelease` / `ConfigurationHead` / `ConfigurationDomain` | Graph-first runtime configuration | **0** | **Yes** — `main.py:441`, and the integration-outbox worker via `runtime_loader` |
| 2 | Mongo `configuration_releases` | `ReleaseService` / `ActivationService` lifecycle | **3** (all test fixtures: `r1`/`r2`/`r3`, checksums `c1`/`c2`/`c3`) | **No** — both services are constructed in exactly one test each |
| 3 | Mongo `return_configuration_snapshots` | Not a lifecycle. An upsert-on-`sha256` fingerprint log of YAML files loaded from disk | 12 | Yes, as an audit record |
| 4 | `config/returns/production.yaml` and siblings | The actual return configuration | n/a | **Yes** — `load_return_configuration(path)` reads it at startup |

### The startup path, which is the thing that matters

`main.py` builds a `ConfigurationSnapshotBuilder` over the Neo4j repository and calls
`build_snapshot(baseline_return_configuration.configuration, allow_baseline_fallback=(env in
_DEVELOPMENT_ENVIRONMENTS), ...)`.

* **Development** — no active Neo4j release, so it logs
  `graph_configuration_unavailable_using_version_controlled_baseline` and falls back to the
  YAML baseline. That is why the app runs happily against zero rows.
* **Production** — `allow_baseline_fallback=False`, so the same condition raises
  `RuntimeError("Graph-first runtime configuration could not be loaded")` and **startup
  fails**.

So the Neo4j lifecycle is not vestigial. It is load-bearing in production and simply unused
in development, which is precisely why the emptiness went unnoticed.

### The two lifecycles, precisely

| | Lifecycle A | Lifecycle B |
|---|---|---|
| Code | `configuration/application/release_service.py` + `activation.py` | `data_console/api/configuration.py::promote_release_status` |
| Store | Mongo `platform.configuration_releases` | Neo4j, via `Neo4jConfigurationGraphRepository` |
| States | DRAFT → VALIDATED → APPROVED → ACTIVE → SUPERSEDED | DRAFT → VALIDATED → RELEASED → SUPERSEDED → ARCHIVED |
| Integrity | Checksum recomputed and compared on VALIDATED→APPROVED and APPROVED→ACTIVE (Slice 3R); `ConfigurationIntegrityError` on mismatch; activation is a pointer compare-and-swap behind a unique partial index | Required behaviour domains present, and `ReturnPlatformConfiguration` parses. **No checksum recompute** |
| Constructed in production | Nowhere | `main.py:441` |
| Read by the runtime | No | Yes; `RELEASED` is the live state |

The vocabularies overlap only on DRAFT, VALIDATED and SUPERSEDED. `APPROVED`/`ACTIVE` have no
Neo4j counterpart; `RELEASED`/`ARCHIVED` have no Mongo one.

**The hardened lifecycle is the dead one.** Slice 3R's checksum verification, its
compare-and-swap activation and its concurrency tests all protect a code path nothing calls.

## Three things worth noticing before choosing

**The rows in `configuration_releases` are leaks, not data — and there are two sets.**
Corrected after tracing them properly; the first version of this document attributed both to
`test_release_lifecycle.py`, which turned out to use an in-memory client and touch no real
database at all.

* **`platform.configuration_releases`** (3 rows + an active pointer) — definitively
  `tests/configuration/test_concurrent_activation.py`. It cleaned *before* inserting and
  never after, and the residue matches its outcome exactly: `r1` SUPERSEDED, `r3` ACTIVE,
  `r2` the APPROVED loser. Fixed — the setup now runs in a fixture that cleans at both ends.
* **`return_platform.configuration_releases`** (3 rows, all APPROVED, placeholder checksums
  `c1`/`c2`/`c3`) — **origin unknown.** `ActivationService` has targeted the `platform`
  database in every revision back to its introduction, so no version of that test wrote
  these. They are unreachable by current code, which reads only `platform`.

A test suite that leaves rows in a database an operator might inspect is its own small
problem, and this one cost real time: these rows were briefly read as evidence that the
Mongo lifecycle held production data.

**The Neo4j transition table is written out three times.** `data_console/api/configuration.py:345`,
and twice in `configuration/graph_repository.py` (`:154` in-memory, `:388` Neo4j). Whichever
lifecycle wins, that wants to be one table. Deliberately not fixed yet: narrowing the losing
lifecycle's rules before the decision would prejudge it.

**The SystemStore already exists for exactly this, and neither lifecycle uses it.**
`config/platform/system_store.yaml` declares platform structures — physical name, schema
version, indexes, `encrypted: true` — under `provider: MONGODB` with
`allowed_providers: [NEO4J, MONGODB, POSTGRESQL, SQLSERVER]`. `configuration_releases` is not
among the declared structures; `ReleaseService` opens a raw collection in its constructor.

But see the constraint below before treating that as an easy win.

## The constraint on "let config choose the datasource"

`provider` and `allowed_providers` are parsed into `SystemStoreConfig` and **never read** —
zero uses anywhere in `platform/system_store/`. Every concrete implementation is Mongo
(`MongoLeaseStore`, `PymongoStructureGateway`, `MongoVersionLedger`,
`MongoBootstrapStateStore`), and the abstraction seam is at the wrong level: the protocol is
`MongoStructureGateway`, and `IndexDefinition` carries `partial_filter_expression` and
`expire_after_seconds`, which are Mongo concepts.

Two consequences:

1. ~~A config declaring `provider: POSTGRESQL` today validates, logs nothing, and runs on
   Mongo.~~ **Fixed.** It was worse than described: `provider` was not a field on the
   bootstrap loader's payload model at all, so `extra="ignore"` discarded it at parse time —
   the value never reached any code that could have honoured it. `load_system_store_config`
   now refuses a provider no gateway implements, and separately refuses a manifest whose
   `provider` sits outside its own `allowed_providers`. `allowed_providers` still records
   the intended destination; only the active provider has to be serviceable.
2. Declaring the release store as a SystemStore structure would mean **"MONGODB, declared
   properly"** — not "any datasource". Real portability needs a provider-neutral gateway
   contract and at least one non-Mongo implementation, which is Wave G/H-sized and should
   not be smuggled into this decision.

## The options

Each assumes the production counts are re-measured first and match what is described above.
If production Neo4j holds real releases, options B and C grow a genuine migration and should
be re-costed.

### Option A — Promote Neo4j, harden it

Bless what production runs. Port Slice 3R's checksum verification onto `promote_release_status`,
collapse the three transition tables into one, delete `ReleaseService`/`ActivationService`
and their tests, and drop the three fixture rows.

* **Migration:** none. Neo4j keeps its zero rows; Mongo's three are fixtures to delete.
* **Gains:** one lifecycle, matching production. Checksum verification reaches the path that
  actually runs.
* **Costs:** loses the compare-and-swap activation and unique-partial-index concurrency
  guard, which would need rebuilding in Cypher. Loses ~26 adversarial tests, or they need
  rewriting against Neo4j. The release store stays outside the SystemStore.

### Option B — Promote Mongo, repoint the runtime

Wire `ReleaseService`/`ActivationService` in for real, declare `configuration_releases` as a
SystemStore structure, and repoint `ConfigurationSnapshotBuilder` and `runtime_loader` at it.

* **Migration:** none on this machine (Neo4j is empty), but this is the option most exposed
  if production Neo4j turns out to hold releases — `RELEASED` → `ACTIVE` and `ARCHIVED` →
  (no counterpart) would both need deciding.
* **Gains:** keeps the stronger integrity story and its tests. Gets the release store inside
  the SystemStore — declared indexes, drift detection, the fencing guard, migration
  versioning.
* **Costs:** moves the live configuration path onto code that has never run in production.
  The graph-first design in the target doc would need revisiting or explicitly retiring.

### Option C — Declare the structure, defer the store

Declare `configuration_releases` in `system_store.yaml`, route Lifecycle A through the
SystemStore, and leave the runtime reading Neo4j for now. Decide the winner later.

* **Gains:** cheapest step that is not wasted under either A or B; makes the provider a
  config value where it belongs.
* **Costs:** the platform still has two lifecycles afterwards, which is the actual problem.
  This buys time rather than resolving anything.

## Recommendation

**Option A**, with the provider fail-closed check done separately and first.

The deciding argument is that Option B moves the live configuration path — the one whose
failure mode is "production does not start" — onto code with zero production mileage, to gain
integrity guarantees on a path that currently has no data flowing through it. Option A closes
the gap in the other direction, at the cost of reimplementing an activation CAS in Cypher,
which is bounded and testable.

That said, Option A discards real work from Slice 3R, and reasonable people would weigh that
differently. The measurements above are the point of this document; the recommendation is
one reading of them.

## First steps under any option

1. Re-run the store counts against production. Nothing here should be acted on until the
   production numbers are known, because two of the three arguments depend on them.
2. Make `provider` fail closed when it names a backend no gateway implements. Independent of
   the decision, and removes a config value that currently lies.
3. Delete the `r1`/`r2`/`r3` fixtures and isolate the test that created them.
