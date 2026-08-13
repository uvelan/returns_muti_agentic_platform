# Implementation plan — execution status

Against [`FERGUSON_RETURNS_IMPLEMENTATION_PLAN_FINAL.md`](FERGUSON_RETURNS_IMPLEMENTATION_PLAN_FINAL.md).
Last verified 2026-08-12 on `refactor/unified-return-platform`.

**A step is "done" only when its own Validation clause holds.** Two steps below were previously
reported complete and are recorded here as partial, because theirs does not.

## Wave 0 — complete

| Step | Status |
|---|---|
| W0.1 Rotate AI credentials | **done** — keys rotated; `_reject_inline_ai_credentials` refuses inline keys outside development/test; duplicate `backend/.env` removed |
| W0.2 Mount `/api/agents` | done — verified against a running backend, not MSW |
| W0.3 Tenant and principal isolation | done — scope in the query filter; cross-tenant and guessed-id both covered |
| W0.4 Serialized PII escape | done — redaction at both `ProviderRequest` sites, recursing into `contextJson` |
| W0.5 Anthropic and OpenAI contracts | done — schema as a forced tool; `max_output_tokens` honoured |
| W0.6 Freeze the duplicate runtime | done — exact importer set pinned; additions *and* removals fail |
| W0.7 Discovery smoke net | done — 19 scenarios; verified by injecting a regression |

## Wave 1 — complete

W1.1 case/facts/return records · W1.2 `CONFIRM_ORDER` · W1.3 multi-return · W1.4
`ReturnCaseWorkflow` (proven by a real worker restart mid-wait, per the step's explicit refusal
of unit-test evidence) · W1.5 Support console · W1.6 outcome into Channel A · W1.7 bay, failure
policy, timings · W1.8 case list and resume.

## Wave 2 — partial

| Step | Status |
|---|---|
| W2.1 Analyzer → runtime schema | **done** — approved draft compiles to an `ActiveSchema` release; runtime prefers it over YAML |
| W2.2 Split shape from source binding | **partial** — binding catalogue, store and API shipped; `DOMAIN_SOURCE_COLLECTIONS` still has 3 references in `operations/repository.py`, so the Validation clause (rename `salesInv → salesInvV2` through configuration only) would fail |
| W2.3 Re-analysis and migration | done — three-way diff, proposals as typed mutations, migration plan recorded before the pointer moves |
| W2.4 Return and warehouse entities | **partial** — return entities added, but **by hand-editing the descriptor**, which the step forbids; **no warehouse or bay entity exists**. No longer blocked: W4.5 landed the MSSQL analyzer connector its Failure condition named |
| W2.5 Return on-demand sync | **done** — `ReturnCaseWorkflow` runs a record-scoped `synchronize_return_records` activity after the return record commits, blocking, parking the case as `RETURN_GRAPH_SYNC_FAILED` on failure. Proven against real Mongo and Neo4j: a committed record is queryable through the compiler afterwards, and the pre-fix upstream connector routing is shown writing nothing |
| W2.6 Fulfillment on-demand sync | **partial** — the code is done and `IN_TRANSIT` now requires an observed shipment, but the Validation clause does not hold on the shipped descriptor; see below |
| W2.7 Warehouse and bay on-demand sync | **not started** — still blocked on W2.4's warehouse entity, which is now producible |
| W2.8 Sync control (S6) and incremental sync | **partial** — S6 ships with run list, filters, detail and manual trigger; `incremental_sync` not confirmed implemented against the cursor contract |

### A defect found and fixed inside W2.5/W2.6's area

The on-demand sync path was complete and wired end to end — guard, planner, connector,
extractor, projector, writer — and **could not put the anchored order into the graph**. The read
projection was built from the anchoring entity's own mapped fields, so it omitted the
discriminator that entity's `where` selector tests, and everything under the exploded line
array. The order the sync was requested for was the one entity never projected; the receipt read
`SUCCEEDED`, and the agent told the associate it had checked the source system directly.

Projection is now derived from every mapped field of every entity bound to the source, plus the
paths its selectors test.

### W2.6 is `partial`, and the reason is configuration rather than code

`shipment` ships as `source_access: SEED_ONLY` with `source_contract_status: UNVERIFIED`,
because **no `shipmentInfo` sample has ever been supplied** — its physical paths are carried over
from the original schema unchecked, and the source store holds no such collection
(`return_source` has `customerOutboundCDM`, `salesInv`, `lkpSearchProduct` and nothing else).
`EntitySourceAccess`'s own matrix forbids on-demand sync at that level, and `ActiveSchema`
refuses to validate an `UNVERIFIED` entity declaring `CONNECTED_SYNC`.

So the fulfillment path **skips the targeted sync and records `SOURCE_ACCESS_SEED_ONLY`**, then
reads the graph anyway — a scheduled sync may have projected the shipment. The step's Validation
clause ("a tracking number not previously in the graph is synced on demand and then read from the
graph") is proven in `test_fulfillment_shipment_sync_real_infra.py` only with the entity promoted
in the test, which that module states in its docstring rather than hiding.

**To close it:** verify `shipmentInfoEventData.{trkNum,trilOrdNum,carrierCode,shipmentId,
currentStatus,srcSystem,shippedAt}` against a genuine `shipmentInfo` document, flip the entity to
`VERIFIED` / `CONNECTED_SYNC`, and add shipments to `scripts/generate_seed_data.py` (it names
`shipmentInfo` in `config/seed/generation.yaml` and emits nothing for it). No code changes.

The defect the step exists to fix **is** closed regardless: `IN_TRANSIT` is no longer inferred
from a reference existing. Absent an observed shipment the state is `AWAITING_HANDOFF`, and
`evidence_references` distinguishes `SHIPMENT_OBSERVED` / `SHIPMENT_ABSENT` /
`SHIPMENT_UNAVAILABLE`. `_bind_fulfillment_tracking` used to *require* `tracking_reference is
None` for `AWAITING_HANDOFF`, which made "we have a number" and "it is moving" the same statement
by construction; that clause is relaxed.

## Gate A — not run

The 19 steps are in the plan. Nothing is deleted before it, so Wave 3 is blocked.

## Wave 3 — blocked on Gate A

## Wave 4 — 1 of 12

| Step | Status |
|---|---|
| W4.5 Complete analyzer connectors | **done** — one read-only `SourceInspectionPort` (`validate`, `list_sources`, `list_objects`, `describe_object`, `sample`, `profile`, `list_indexes`, `list_relationships`) with adapters for MongoDB, SQL Server, PostgreSQL and Neo4j, each proven against a real server. Scope is a hard filter in the tool layer (`ScopedSourceInspection`), refusing inbound *and* filtering outbound. `psycopg` added; dispatch extends the existing `SourceConnectorsByType` rather than adding a second registry. W2.4's blocker — "the analyzer's connector cannot describe the SQL warehouse source" — is closed |

Confirmed absent: `as_of` on `AgentTurnContext` (W4.7 — zero references).

### W4.5's `profile` and what W4.8 inherits

`profile` returns statistics and never a value: approximate row count, per-field
null rate, approximate distinct, identifier candidacy and change-tracking candidacy,
plus the `sampled_rows` those were computed over. The last one matters for W4.8 —
`approximate_distinct` over 50 sampled rows is a different claim from the same
number over a table, and a ranking that cannot tell them apart is a coincidence.
The statistics are computed in one shared module for all four backends for the same
reason: W4.8 compares these numbers *across* sources.

### What W4.5 deliberately did not do

- **No HTTP surface.** The port and the scoped tool layer are bound onto
  `app.state.graph_schema_analyzer_source_inspection`; S4's permitted-object browse and
  S5's profile/diff screens are W4.10, and adding routes here would build them twice.
- **No masking.** `sample` returns values as read. W4.6 masks at the port boundary
  before model invocation, and doing half of it here would leave two places claiming
  responsibility. `profile` needs no masking because it returns no value at all.
- **Nothing consumes `profile` yet.** W4.8 is what puts selectivity into
  `compact_schema`; this step produces the numbers and nothing reads them.
- **The PostgreSQL tests are environment-gated.** This platform runs no PostgreSQL for
  the application (`temporal-postgresql` is Temporal's private store and publishes no
  host port), so `tests/source_connectors/test_source_inspection_postgresql_docker.py`
  skips unless `PLATFORM_TEST_POSTGRES_HOST` is set. They were run green against a
  throwaway `postgres:17.10-alpine`; a CI run that does not provide one will skip them
  rather than fail, which is worth knowing before trusting a green suite.

### PostgreSQL is connected synchronously, on purpose

psycopg 3 refuses to run async on asyncio's `ProactorEventLoop`, which is the
default on Windows and what uvicorn selects there — an `AsyncConnection` works in
the Linux containers and raises `InterfaceError` on every developer machine here.
The connector wraps the synchronous driver in `asyncio.to_thread`, which is what
`SqlServerSourceScanConnector` already does with pymssql. Found by running the
tests, not by reading the docs.

## Wave 5 — not started (0 of 11)

Confirmed absent: `FUZZY_SEARCH` (W5.1 — zero references). W5.9's stray
`backend/poetry.lock;W/` and `backend/pyproject.toml;W/` are still present.

W5.11's first clause — "add a PostgreSQL driver" — is done as part of W4.5
(`psycopg[binary]==3.3.4`, in `pyproject.toml` and `poetry.lock`). The rest of W5.11
(Node/npm alignment, the two Starlette deprecations, the Neo4j 5.26 → 2025.x plan)
is untouched.

W5.9's "`scripts/run_data_job_worker.py` fails `ruff check`" is also closed — it was
the only thing standing between this branch and a clean `ruff check src tests scripts`.

## Work done outside the plan

Not scheduled, and it came at the cost of Wave 2's tail. Recorded so the ledger is honest:

- **Identification signals.** Of the eight signals the flow calls for, the reachable agent
  searched on four. Street, city, state and postal code were declared, validated, then dropped —
  on grounds that stopped being true once the schema grew properties for all of them. Email and
  phone were not on the intent at all, while the clarification policy ranks them at 95 and 90, so
  the agent asked for an email and had nowhere to put the answer. This is the class of defect
  W5.2's evaluation suite exists to catch.
- **Six ineffective `sparse` indexes**, one of them `unique` over a field written as explicit
  `null` — meaning a session-less support case could be created exactly once, ever. `sparse`
  omits a document only when the field is *absent*.
- **Replay provider and manual-handoff selection**, taken from the predecessor platform's
  simulator: recorded answers replayed, a miss calling the real provider and recording it. This
  makes W5.2's evaluation suite affordable to run repeatedly.
- **Seed data generator** (`scripts/generate_seed_data.py`) driven by
  `config/seed/generation.yaml`. Written and committed, **never executed**. It also emits nothing
  for `shipmentInfo` despite naming the collection, which is one of the two reasons W2.6 is
  `partial`.

## Found while landing W2.5 and W2.6

- **`ReturnCaseWorkflow` was registered in no deployed process.**
  `create_return_workflow_worker` takes its activities optionally and
  `scripts/run_return_workflow_worker.py` never supplied them, so a case could be started and
  would then stall on its first activity. W1.4's own Validation was met by a test harness. The
  script now builds `ReturnCaseActivities` with the real support service and the graph-sync port.
- **Four tests were already red at `83321ed`**, verified by stashing and re-running, so they are
  not attributable to Wave 2's tail:
  `test_order_discovery_smoke_net.py::test_declared_address_and_colour_anchors_produce_no_plan`,
  `::test_phone_and_email_cannot_be_expressed_as_search_anchors`,
  `test_search_strategy.py::test_unsupported_signals_do_not_silently_disappear` — all three are
  tripwires the identification-signals work above closed and did not update, and two of them are
  **inside the W0.7 smoke net**, which execution rule 5 requires green at every wave gate — and
  `test_source_connector_routing.py::test_a_type_with_no_connector_is_refused_rather_than_defaulted`,
  which matches on a message `UnreachableSource` no longer emits. **Fixed in W4.5** — and it was
  wrong twice over: the expected *type* was `ValueError` while `UnreachableSource` is a
  `RuntimeError`, so even a corrected message would not have caught it. The behaviour it guards
  (refuse rather than fall back to whichever connector is registered) was correct throughout. The
  three smoke-net tripwires are untouched.
- **`scripts/run_data_job_worker.py` fails `ruff check`** (I001, unsorted imports) at `83321ed`.
  **Fixed in W4.5**; `ruff check src tests scripts` is clean on this branch.
- **Host-side Mongo needs `directConnection=true`.** The single-node replica set advertises its
  container hostname, so topology discovery from the host resolves a name that does not exist and
  every operation times out — which is why the `*_real_infra` modules error on the host. The two
  new real-infra modules use a direct connection and therefore run in both places.

## Verification: first full run

Run 2026-08-12 in the diagnostics container: **2,392 passed, 56 failed, 10 skipped, 4 errors**
(47 min). Waves 0-2, the three merged worktrees, the index migrations and the sync-projection
change all hold against real infrastructure.

**The 56 failures are an environment artefact, not code.** All are in
`tests/test_order_agent_rest.py`, all the same assertion:

```
ConnectionRefusedError: [Errno 111] Connect call failed ('::1', 7687, 0, 0)
  -> dependency_initialization_failed -> 503 -> assert 503 in (200, 201)
```

`::1` port 7687 is Neo4j on IPv6 loopback. The diagnosis recorded here — that those tests build
their own `Settings()` which re-reads the mounted `.env` underneath any `-e` override — was
wrong, and wrong in a way that made the fix look bigger than it is. They use the shared
`test_settings` fixture, and that fixture passed `neo4j_uri="bolt://localhost:7687"` and
`valkey_host="localhost"` as **literal keyword arguments**. A pydantic-settings init keyword
outranks the process environment *and* the dotenv file, so `docker exec -e` set a value nothing
ever consulted — and so would a rewritten container `.env`.

**Fixed in W4.5.** `PLATFORM_TEST_NEO4J_URI`, `PLATFORM_TEST_VALKEY_HOST` and
`PLATFORM_TEST_SQLSERVER_PORT` now override those three, mirroring the `MONGO_HOST`,
`TEMPORAL_TARGET` and `SQLSERVER_HOST` overrides that already existed. Every default is today's
value, so a host run is unchanged. A container run on the compose network needs:

```
PLATFORM_TEST_MONGO_HOST=mongodb  PLATFORM_TEST_NEO4J_URI=bolt://neo4j:7687
PLATFORM_TEST_TEMPORAL_TARGET=temporal:7233  PLATFORM_TEST_VALKEY_HOST=valkey
PLATFORM_TEST_SQLSERVER_HOST=sqlserver  PLATFORM_TEST_SQLSERVER_PORT=1433
```

**Not yet verified:** `tests/test_order_agent_rest.py` has not been re-run in a container with
these set. The change is the mechanism the fix needs; the 56 failures are not yet observed green.

Two hypotheses were wrong before this one: that `PLATFORM_AI_PROVIDER_ORDER=MANUAL` starved the
scenarios, and that dropping the synthetic `orders`/`products`/`customers` collections removed
their fixture data. Both were inferred from where the failures clustered instead of from reading
one assertion — which is what execution rule 3 exists to prevent.

**The 4 errors in `tests/source_connectors/test_sqlserver_connector_docker.py` are closed**, and
the guess recorded here ("a missing table or driver in that container") was wrong on both counts.
pymssql reported only `Login failed for user 'sa'`, which reads as credentials; the server's own
log gives the real reason:

```
Login failed for user 'sa'. Reason: Failed to open the explicitly specified database 'test_db'.
```

`tests/conftest.py::test_settings` has always declared `sqlserver_database="test_db"` and nothing
in the repository ever created it — the SQL migrations run against `return_platform` and compose
has no init script. `test_db` appears as a literal in exactly two test files and nowhere else, so
this module has never been able to run, on the host or in a container. A fixture in
`tests/source_connectors/conftest.py` now creates the database from `master`; all 4 pass.

**Still open:** `ai_replay_mode` is worth enabling for the next full run so it records itself and
later runs cost nothing.

**Also note:** piping a container run through `tail` loses the entire output if the connection
drops -- it happened twice. Write to a file inside the container and tail that afterwards.
