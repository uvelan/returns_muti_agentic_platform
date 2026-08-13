# Implementation plan — execution status

Against [`FERGUSON_RETURNS_IMPLEMENTATION_PLAN_FINAL.md`](FERGUSON_RETURNS_IMPLEMENTATION_PLAN_FINAL.md).
Last verified 2026-08-13 on `refactor/unified-return-platform` @ `493c3f3`.

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

## Wave 5 — partial (0 done, 2 partial of 11)

Confirmed absent: `FUZZY_SEARCH` (W5.1 — zero references).

| Step | Status |
|---|---|
| W5.9 Test hygiene | **partial** — three of four items done; the concurrency flake is **not diagnosed**, see below |
| W5.11 Currency | **partial** — Postgres driver and both deprecations done; the httpx→httpx2 half is deferred, see below |

### W5.9 — what closed and what did not

- **Provider-free tests no longer demand model credentials.** `test_settings` obtained
  `NVIDIA_API_KEY` and `GOOGLE_API_KEY` through `_required_environment_variable`, which raises, so
  the requirement reached every test taking the fixture — index drift, catalog loading, health
  probes, the system-store bootstrap. Three modules had already responded by *copying* the
  fixture's environment helper rather than using it, which is settings construction growing a
  second implementation inside the test tree. The fixture now falls back to an obvious placeholder
  and a test that genuinely dispatches to a provider takes the new `live_ai_credentials` fixture,
  which skips without real keys. Pinned by
  `tests/platform/test_the_suite_runs_without_model_credentials.py`, asserted against the conftest
  source rather than by running it — running it proves nothing on a machine that has the keys
  exported, which is every machine where the regression would be introduced.
- **The runtime loader refuses `status: DRAFT`.** `application/loader.py` parsed the manifest
  status into `ReleaseStatus` and then only copied it onto the snapshot for display, so the field
  decided nothing and `config/manifest.yaml` shipped as `DRAFT`. The refusal is in
  `LegacyCompatibilityAdapter.build_canonical_snapshot` and not in `ConfigurationLoader`, because
  parsing a draft and *serving* one are different acts: `AgentConfigurationService` reads and edits
  drafts through the same parser and a refusal there would have taken the agent configuration
  screen down with it. `config/manifest.yaml` is now `ACTIVE`.

  **Stated precisely, because the obvious reading is wrong.** Tracing the callers shows nothing in
  `src/` or `scripts/` builds a snapshot from the packaged directory — `LegacyCompatibilityAdapter`
  and `build_snapshot_from_legacy_configs` are reached only from `adapters.py`'s re-export and from
  tests. Production runtime configuration comes from the Mongo `configuration_releases` collection
  via `bootstrap/reconciler.py`, seeded through the Neo4j release path, and **that path already
  refuses anything short of `RELEASED`** (`cli/bootstrap_graph_configuration.py:244-247`). So this
  is **not** a live hole being closed: it is the manifest vocabulary's missing equivalent, put in
  before something wires that translator up, plus a packaged manifest that no longer misdescribes
  itself. Anyone reporting it as "the platform was booting on a draft" is overstating it.
- **The stray `backend/poetry.lock;W/` and `backend/pyproject.toml;W/` are gone.** Both were
  *empty untracked directories*, so their removal cannot appear in a commit — git never held
  them. Recorded here because that is the only durable place it can be recorded.
- **The concurrency flake is NOT diagnosed.** See "The concurrency flake" below. It has not been
  fixed and must not be recorded as fixed.

### W5.11 — what closed and what did not

- **PostgreSQL driver added.** `psycopg[binary]==3.3.4` in `backend/pyproject.toml`, locked with
  Poetry 2.4.1 (the version `backend/Dockerfile` pins), adding only `psycopg` and `psycopg-binary`
  to `poetry.lock` with no churn elsewhere. psycopg 3 rather than psycopg2 or asyncpg because the
  analyzer ports and every existing connector are async and psycopg 3 is the one of the three with
  a native async connection and no second sync path beside it. It was already installed in the
  developer venv and declared nowhere, so the image would never have had it.
- **Node and npm aligned.** `frontend/package.json` declared `node >=24.0.0 <25` and
  `npm >=11.0.0 <12` while `frontend/.nvmrc` pins `24.18.0`, `frontend/Dockerfile` builds on
  `node:24.18.0-bookworm-slim`, and `packageManager` is `npm@11.16.0`. The range admitted versions
  the image never builds with — this machine runs Node 24.14.0 and npm 11.1.0 inside it. `engines`
  is now exactly `24.18.0` / `11.16.0`. `engine-strict` is deliberately **not** enabled: npm then
  reports `EBADENGINE` as a warning rather than refusing to install, which surfaces the drift
  without bricking a developer mid-task. Turning it on is the follow-up once everyone has moved.
- **The `HTTP_422_UNPROCESSABLE_ENTITY` deprecation is gone.** Eight uses across five modules;
  two other modules had already moved, so this was a half-finished migration. The value is 422
  either way, so no response contract changed.
- **The httpx deprecation is deferred.** `Using 'httpx' with 'starlette.testclient' is deprecated;
  install 'httpx2' instead` is emitted from `starlette.testclient`, so it affects the test client
  only, not a production path. Closing it means swapping the runtime HTTP client across eight
  modules — the AI provider transport, Vault, the integration outbox, the support provider and
  `operations/orchestrator.py` — which is a dependency migration, not a deprecation fix. `httpx2`
  exists on PyPI at 2.10.0, so it is available when someone schedules it.

### W5.6 — not started, but the scope is confirmed against the tree

Not attempted. Recorded because the audit's claim was re-verified and the shape of the work is
now concrete:

- **Six services have no healthcheck at all**: `return-workflow-worker`, `order-discovery-worker`,
  `return-orchestrator`, `outbox-publisher`, `data-job-worker`, `integration-outbox-worker`
  (`compose.yaml:435-507`). Only `backend` has one, on `/health/ready`.
- **`return-orchestrator` gates on `service_started`** (`compose.yaml:472-473`) — which is the
  "a PID exists" check the step exists to replace.
- **The frontend's only healthcheck is image-level** (`frontend/Dockerfile:18`) and fetches its own
  nginx index. It proves nginx serves a file, not that the app can reach the backend.
- **Both resume workers exist as libraries and as no process**:
  `src/return_platform/workers/interception_resume.py` and
  `src/return_platform/platform/reasoning/resume_worker.py`. Neither has a runner under
  `backend/scripts/` nor a compose service, so nothing delivers
  `reasoning_resume_commands` as Temporal signals in a deployed environment.

The design question to answer first: these are headless Temporal/Mongo pollers with no HTTP
surface, so "registration and connectivity" needs a probe channel that does not exist yet. Adding
an HTTP endpoint to each is one answer; a worker-written readiness sentinel checked by `CMD` is
another. **A healthcheck that greps a log line or checks the process table is the failure mode the
step names**, so this needs the decision made before any of it is written.

### Neo4j 5.26 → 2025.x: the validation plan (the upgrade itself is not in scope)

`compose.yaml:267` pins `neo4j:5.26.28-community`. The exposure is static Cypher: **176 literal
`MATCH`/`MERGE`/`CREATE`/`CALL db.` occurrences** across `backend/src/return_platform`, plus **5
`.cypher` migration files** shipped in the wheel. Everything else is generated by `CypherCompiler`
from the active schema, which means it is exercised by any query the compiler emits and does not
need separate literal-by-literal review.

The plan, in the order that makes a failure cheap:

1. **Split the surface.** Compiler-generated Cypher is one artifact to validate; the literals are
   individual ones. Run `CypherCompiler` over every entity, relationship and operator combination
   the active schema permits and diff the emitted Cypher against 5.26 — a single compiler
   incompatibility is one fix, and it covers the majority of statements the platform ever runs.
2. **Stand the new version alongside, never in place.** A second Neo4j at 2025.x on a separate
   volume, seeded from the same migrations. **Never upgrade the active graph in place** — D8's
   rule for schema generations applies identically to the engine.
3. **Run the 5 migration files first.** They are DDL-shaped (constraints and indexes) and are the
   most likely place a syntax or semantics change bites. A migration that will not apply stops the
   upgrade before any read is worth checking.
4. **Then the literals, by category rather than one by one.** Group the 176 by the construct they
   use — `CALL db.*` procedures (the likeliest to have moved), `MERGE` with `ON CREATE`/`ON MATCH`,
   fenced writes in `graph/neo4j_writer.py`, full-text index calls. Each group needs one
   representative proven, then the rest read against that result.
5. **Prove behaviour, not parsing.** `EXPLAIN` catching every statement proves syntax and nothing
   about semantics. The generation-fencing writes and the full-text index are the two places where
   a silently changed result is worse than an error, so both need an assertion on returned rows.
6. **The gate is W0.7 plus a full sync.** The smoke net green against the 2025.x instance, and one
   complete graph sync producing an identical node and relationship count to 5.26.

**Do not start this before W4.5.** The analyzer connector work lands a Neo4j read-only connector,
which adds to the surface being validated; validating twice is the avoidable cost.

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
  which matches on a message `UnreachableSource` no longer emits.

  **All four are now green.** Each tripwire was replaced with the positive assertion its own
  failure message asked for, rather than deleted: the address anchors and the two contact anchors
  are asserted to reach `compile_read` through the real `SchemaQueryGuard` against the shipped
  descriptor — a plan that exists and is then refused by the guard is indistinguishable from one
  that was never built, because `order_search` logs the rejection at DEBUG and moves on — and
  colour, which is still genuinely unsupported, is asserted to arrive in the evidence the next
  `decide` reads rather than merely to produce no plan. The connector-routing test was **the test
  being wrong, not the code**: `SourceConnectorsByType.resolve` refuses an unreachable source
  exactly as the test's own docstring requires, and the test asserted on a bare `ValueError` and a
  message from one of the two registries that were consolidated into it. It now asserts
  `UnreachableSource`, which is what `tests/dynamic_knowledge/test_sync_adapters.py:198` already
  asserted on the same code path. W4.5 reached the identical fix independently and in parallel,
  and adds one detail worth keeping: the expected *type* was wrong as well as the message —
  `UnreachableSource` is a `RuntimeError`, so correcting only the `match=` string would still have
  left the test red.

- **A fifth test is red at `493c3f3`** and was not on the list above:
  `tests/api/test_canonical_returns_support_and_conversation.py::test_the_reverse_conversation_lookup_exists_and_is_indexed`.
  Verified pre-existing by stashing to a pristine tree and reproducing. It scans the source of a
  **frozen** module for an index declaration; the assertion text is "the reverse lookup has no
  index". Not diagnosed further — it is not in the W0.7 net and belongs to whoever owns that
  module.

- **A sixth is red and is neither infrastructure nor a tripwire.**
  `tests/data_platform/graph/test_sync_service_pipeline.py` fails both its tests with
  `AttributeError: 'FakeSettings' object has no attribute 'dynamic_knowledge_schema_path'`, raised
  from `data_platform/graph/sync_service.py:346`. The module's own docstring says it needs no live
  infrastructure, so this is a genuine drift between the production settings read and the test's
  stand-in, not an environment artefact. Untouched by the W5.9/W5.11 work above.

- **A test writes into the source tree on every run.**
  `tests/data_platform/test_schema_registry_write_policies.py:85-89` writes
  `docs/evidence/ai_studio_operational_generation/aig1/asset_policy_inventory.json`, whose
  `schemaRegistryChecksum` is `sha256` of `config/schema_registry.yaml` **as bytes**. On a Windows
  checkout those bytes carry CRLF, so every host run produces a spurious one-line diff against a
  file generated on Linux. Harmless until someone commits it. Hash the normalized text, or write
  the evidence to `tmp_path`.

### The concurrency flake: not diagnosed, and not fixed

`test_return_workflow_concurrency.py::test_a_second_completion_sees_the_first_ones_state` remains
**undiagnosed**. It is not fixed and must not be recorded as fixed.

What was ruled out, by reading the source rather than by inference:

- **The mutex in `ReturnWorkflow.complete_stage` is correct.** The `while` re-check around
  `wait_condition` is the fix for the batch-release behaviour, and there is no `await` between the
  loop exiting and `self._transition_in_progress = True`, so two handlers released together cannot
  both pass. The flag is cleared in `finally`, including on the short-circuit return.
- **The idempotent dedupe is correct.** `advance_return_workflow` returns *the identical state
  object* when the command id is already applied (`return state`, not a rebuilt equal value), so
  the loser's `next_state is previous_state` check short-circuits before the activity. Both
  assertions in the test — one applied command, stage advanced once — follow from those two
  mechanisms and are not timing-dependent given them.
- **Test ordering is not a factor.** `pytest-randomly` is not installed; the only plugins are
  `pytest-asyncio` and `pytest-cov`, so collection order is deterministic.
- **Cross-test collision is not a factor.** Workflow ids and task queues are per-test uuids, and
  the probe activities hold all state in-process.

The one load-sensitive element that can be named from the source is
`_ACTIVITY_TIMEOUT = timedelta(seconds=10)` with `maximum_attempts=1` on both activities
(`return_workflow.py:41,386,462`). Under a loaded full-suite run a starved worker could exceed it,
and with one attempt that fails the update rather than retrying. That is a hypothesis, not a
diagnosis — and it does not explain why the ledger recorded only the *second* test as flaky, since
it would fail both. **Do not act on it without evidence.**

15 consecutive isolated runs of the module against real Temporal passed (0 failures). That rules
out an intrinsic in-process race on this machine's timing and says nothing about full-suite
conditions, which is where the original 3-of-13 was observed. The next person should capture the
actual failure output from a full-suite run — the failing assertion, or the exception if the
update itself failed — because which of the two it is decides everything above.

### Also found while landing W2.5 and W2.6

- **`scripts/run_data_job_worker.py` fails `ruff check`** (I001, unsorted imports) at `83321ed`.
  **Fixed** — `ruff check src tests scripts` is clean.
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

## Verification: the W5.9 / W5.11 / smoke-net change set

Run on the **host** against the running compose infrastructure, from a worktree at `493c3f3`.
Host runs cannot reach Mongo (see `directConnection` above), so this is not a substitute for a
diagnostics-container run.

| Command | Result |
|---|---|
| `ruff format --check src tests scripts` | 784 files already formatted |
| `ruff check src tests scripts` | All checks passed (the pre-existing `run_data_job_worker.py` I001 fixed) |
| `mypy --strict src` | Success: no issues found in 493 source files |
| the three repaired modules (smoke net, search strategy, connector routing) | **64 passed** |
| `tests/api tests/configuration tests/dynamic_knowledge tests/graph_schema_analyzer tests/operations tests/agents tests/bootstrap tests/conversation tests/data_platform tests/security tests/v2`, `--ignore-glob="*real_infra*"` | 901 passed, 17 failed, 9 errors, 2 skipped (9m54s) |

**Every one of the 17 failures and 9 errors was read, not inferred**, and falls into three
buckets, none of them this change set:

1. **`pymongo.errors.ServerSelectionTimeoutError` reaching `mongodb:27017`** — the documented
   host-side topology defect. Covers `test_case_detail_multi_rma.py`,
   `test_generation_lifecycle_e2e.py`, `test_mongo_graph_state_provider.py`,
   `test_on_demand_sync_production_wiring.py` and the `test_sync_service_pipeline.py` errors.
2. **`Vault must be enabled in production`** — 5 failures in
   `tests/configuration/test_ai_credentials_must_be_vault_references.py`, because the repository
   `.env` carries `PLATFORM_VAULT_ENABLED=false`. Reproduced on a stashed pristine tree.
3. **The two genuinely pre-existing reds** recorded above:
   `test_the_reverse_conversation_lookup_exists_and_is_indexed` and
   `test_sync_service_pipeline.py`'s `FakeSettings` `AttributeError`.

**Not run:** `tests/test_order_agent_rest.py` (needs the container `.env` fix above),
`*_real_infra*` and `*_docker*` modules, and the Temporal workflow modules. A first attempt at the
whole suite on the host wedged at 42% and was killed — a Temporal test hanging is the likeliest
cause, and `test_return_workflow_concurrency.py`'s own docstring documents that a wedged workflow
produces exactly that. **The next full run belongs in the diagnostics container.**
