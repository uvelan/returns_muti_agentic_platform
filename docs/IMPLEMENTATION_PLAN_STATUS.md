# Implementation plan — execution status

Against [`FERGUSON_RETURNS_IMPLEMENTATION_PLAN_FINAL.md`](FERGUSON_RETURNS_IMPLEMENTATION_PLAN_FINAL.md).
Last verified 2026-08-13 on `refactor/unified-return-platform`, after merging W0.7/W5.9/W5.11
hygiene, W4.5 and W2.6 — three branches cut in parallel from `493c3f3` — and then W2.8, W2.2 and
W2.4/W2.7, cut in parallel from `d9639e4` and `4116915`.

**A step is "done" only when its own Validation clause holds.** Several steps below were
previously reported complete and are recorded here as partial, because theirs does not.

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
| W2.2 Split shape from source binding | **done** — `DOMAIN_SOURCE_COLLECTIONS` is gone; `operations/repository.py` resolves its four upstream datasets through the same catalogue the release compiler and the bindings API already use. The Validation clause is demonstrated against real Mongo, not asserted: editing only `object_ref.name` moves the seed, its indexes and `source_order` to `salesInvV2` and leaves nothing in `salesInv`; a stored rebinding moves the read with the schema file untouched. See below |
| W2.3 Re-analysis and migration | done — three-way diff, proposals as typed mutations, migration plan recorded before the pointer moves |
| W2.4 Return and warehouse entities | **partial** — `warehouse` and `bay` now exist and were **produced by the analyzer**, not typed: `scripts/add_warehouse_bay_entities.py` inspects the real `platform.bay_configuration` through W4.5's `SourceInspectionPort`, turns it into a snapshot, takes `ReanalysisService`'s proposed `AddEntity`/`AddProperty` batch, applies the modelling decisions as typed mutations, compiles through `compile_active_schema` and writes the descriptor. The **return** entities are still the ones hand-added earlier and were not re-derived — see below |
| W2.5 Return on-demand sync | **done** — `ReturnCaseWorkflow` runs a record-scoped `synchronize_return_records` activity after the return record commits, blocking, parking the case as `RETURN_GRAPH_SYNC_FAILED` on failure. Proven against real Mongo and Neo4j: a committed record is queryable through the compiler afterwards, and the pre-fix upstream connector routing is shown writing nothing |
| W2.6 Fulfillment on-demand sync | **done** — a genuine 100-document `shipmentInfo` sample verified the contract; `shipment` is `VERIFIED`/`CONNECTED_SYNC` and the Validation clause holds on the shipped descriptor with no in-test promotion; see below |
| W2.7 Warehouse and bay on-demand sync | **done** — `WarehousePlacementService` takes candidates from a `WarehouseBayObservationPort`; `GraphWarehouseBayObservations` syncs the warehouse on demand and reads `Warehouse` then `Bay` through `CypherCompiler`. `sql.list_bay_candidates` is off the agent path and the fallback is deliberately absent. Validation proven against real Mongo, SQL Server **and Neo4j**: six bays for a real warehouse come back out of the graph after a targeted sync, an unknown id reports `WAREHOUSE_NOT_IN_GRAPH`, and no reference at all reports `NO_WAREHOUSE_REFERENCE` instead of every bay in the estate. One thing the graph cannot carry did not move, and is named below |
| W2.8 Sync control (S6) and incremental sync | **done** against its Validation clause — forced failure, restart, resume from the correct watermark and no duplicate reads are proven against real MongoDB, and the failure is visible in S6. `incremental_sync` and the connector were already correct; what did not exist was a checkpoint store that persists anything and a caller that runs them. Two items from the step's *Change* clause remain unshipped — see below |

### W2.2 closed: the rename is a configuration edit

`operations/repository.py` held the last three references to `DOMAIN_SOURCE_COLLECTIONS`, and
they were not the whole problem — the same four physical names appeared 26 times in that file:
`source_order`'s `find_one`, the readiness counts, and nine `create_index` calls written directly
against `self._source_db["salesInv"]`. All of them now go through
`OperationalRepository.source_dataset`, which resolves `catalogue_from(resolve_active_schema(...),
SourceBindingStore.list())` — the same call the release compiler and `GET /api/source-bindings`
make. The catalogue is built lazily and cached per repository instance, so a rebinding reaches
the next request rather than half of the one in flight.

The stable handle is the **source asset id** (`source_sales`, `source_customers`,
`source_shipments`, `source_products`), not the collection name. Keying on the collection name
would have re-broken the clause: after the rename the catalogue knows the asset as `salesInvV2`,
so `resolve("salesInv")` is correctly `None`. `materialize_domain_seed` was re-keyed to match,
since a manifest keyed by physical name is a second, silent declaration of the binding.

**Proven, not asserted** — `tests/operations/test_source_dataset_binding_real_infra.py`, 7 tests
against real Mongo, same code and two configurations:

- shipped configuration seeds `salesInv` and leaves `salesInvV2` empty;
- with only `object_ref.name` edited, the seed and all nine indexes land on `salesInvV2` and
  **nothing** is left in `salesInv`, and `source_order` returns the order from there;
- a stored rebinding moves the read with the schema file untouched — the 3am route, which needs
  no publish;
- an unplaceable dataset is refused rather than defaulted to a plausible neighbour.

Three things this deliberately does not do, each recorded rather than fixed:

- **Only the collection moves, not the database.** `source_dataset` takes `object_ref["name"]`
  and keeps `settings.source_mongo_database`. The shipped schema declares `return_source`, which
  equals that setting only by default, and `targeted_sync.platform_store_source_ids` already
  treats an operator who renamed the setting as the authority. Honouring a declared database here
  would silently disagree with that.
- **A release that renames the asset ids breaks this.** Something has to be stable between code
  and configuration, and the asset id is it. `ActiveSchema` will not validate a schema missing an
  asset its entities reference, so this is the only unresolvable case — and in `seed_status` it
  degrades to a validation error rather than raising, because that method draws a diagnostics
  card and a 500 there takes down every other card with it.
- **Rebinding now means two different things** in the two halves of the platform, on purpose. A
  release still captures its sources at compile time; direct source reads follow the override on
  the next request. `api/source_bindings.py`'s docstring claimed the first for both and is
  corrected.

`data_platform/ai_studio.py`, `data_platform/operational_generation/relationships.py`,
`data_platform/graph/sandbox.py`, `graph/interim_active_schema.py` and
`scripts/load_reference_dataset.py` still carry the four literal collection names. None is on the
repository's read path and none is in W2.2's scope, but a future rename does not reach them.

### W2.4: what the analyzer produced, and what it still cannot

**The entities exist and nobody typed them.** `bay` carries all 18 columns
`platform.bay_configuration` declares, with the types, the nullability and the
capabilities all derived from the catalogue and the two declared indexes:
`bay_id` is searchable and an anchor because it is the primary key,
`warehouse_id` because it leads the lookup index, `active` and `priority` are
filterable and not searchable because they sit *behind* it, and the eleven
columns no index covers are displayable only. `warehouse` is a dimension
projected out of the same table.

**The warehouse structure is still provisional, and less invented than it was.**
`docs/SEED_DATA_GENERATION.md` documents a `warehouseMaster` collection made up
for this project. It is **not in `return_source`** — the four collections there
are `salesInv`, `customerOutboundCDM`, `lkpSearchProduct` and `shipmentInfo` —
so an entity declared against it would have put paths in the descriptor that
resolve on nothing, which is the defect W2.6 spent its whole verification budget
removing. `platform.bay_configuration.warehouse_id` is real, NOT NULL on every
row and indexed, so `warehouse` is projected off it the way `customer` is
projected off `salesInv`. It carries `warehouse_id` and `branch_id` and nothing
else, because nothing else about a warehouse is stated anywhere this platform can
reach. **It is not a warehouse master**, its own description says so, and a real
one arriving is still a rebinding plus a re-analysis rather than an edit.

**Verified at full breadth, in two halves that do not assume each other.**
`tests/bootstrap/test_analyzer_produced_warehouse_entities.py` re-runs the whole
analyzer path against a written-out observation and requires it to reproduce the
shipped entities exactly; `tests/dynamic_knowledge/test_warehouse_bay_source_
contract_real_infra.py` reads the live SQL Server and requires the catalogue and
the indexes to be that observation, then checks every declared path against
**every row** rather than row zero. A relational source lets that standard be met
exactly rather than approximately — the catalogue *is* the contract.

**What did not go through the analyzer, and why.**

- **The return entities were not re-derived.** W2.4 covers them too and they are
  still the hand-added ones. Re-deriving them means describing four collections
  in the platform's own Mongo store, and a Mongo `describe_object` reports the
  union of keys over a bounded sample, so the exercise would be a *sampled*
  contract check for entities whose paths are already in use — a different and
  much weaker claim than the one just made for `bay`. Worth doing; not done.
- **The authoring form still cannot say several things the descriptor needs**, so
  a whole-descriptor recompile remains lossy and `compile_addition` merges a
  compiled fragment onto the baseline instead. Missing from `GraphSchemaShape`:
  `record_path`, `explode`, `where`, `distinct`, `key_resolution`,
  `ownership_policy`, `deletion_policy`, a description, and any separation of
  entity id from graph label. Six of the eleven existing entities use at least
  one of those, and recompiling the file would silently drop them. **This is
  W2.2's remaining work stated concretely** — the list above is what it has to
  add before the analyzer can own the whole document.
- **`maximum_expected_matches` on a non-unique index.** `bay.warehouse_id` is an
  anchor *field* and defines no anchor: bounding a read to "some number of bays"
  would need a number no observation supplies, and inventing one is how a
  selectivity hint becomes a lie. W2.7 anchors on `warehouse` instead, which the
  node key genuinely bounds to one.

### Three defects found by W2.4/W2.7, all pre-existing

Each was found by running the thing, not by reading it.

1. **A targeted read of any SQL source outside the default schema was
   impossible.** `source_connectors/compilation.py` composed
   `FROM "bay_configuration"` and dropped `object_ref.namespace`, so SQL Server
   answered `Invalid object name`. `SqlServerSourceScanConnector._resolve` has
   always *required* that namespace for a scheduled scan, so the two halves of
   the same connector disagreed. Nothing noticed because every source in the
   descriptor was MongoDB until now.
2. **`maximum_sources_per_target` counted the wrong thing.**
   `graph/projector.py` keyed the counter on the target's *match* key rather than
   its node key. Those coincide only when the match key identifies the node, and
   for the ordinary foreign-key shape it never does: every `ReturnRecord` of one
   case matches on the same `case_id`. So the counter incremented once per edge
   into *any* target sharing the key and tripped `maximum_sources_per_target=1`
   on the second one — refusing a whole projection while reporting "this target
   has two sources" about a target that had one. **`case_raised_return_record`,
   `case_includes_return_item` and `return_record_covers_item` all carry that
   bound**, so a case with two RMAs would have failed its sync. Not observed in
   the wild, because nothing had projected a multi-record case through this path.
3. **Every property a re-analysis proposed compiled to an unusable path.**
   `ReanalysisService` writes `source_field` as `<dataset>.<column>` and strips
   that prefix when comparing; the release compiler did not, so `salesInv.trkNum`
   became the path `('salesInv', 'trkNum')` — which resolves on no document and
   projects null forever. The two halves of one convention now agree.

Two smaller ones, in passing: `nvarchar` was absent from the analyzer's type
table, so every Unicode string column of a SQL Server source was reported as
"could not be typed and is left out"; and `compile_active_schema` marked every
entity it produced `VERIFIED`/`CONNECTED_SYNC` by model default — asserting that
paths nothing had checked were confirmed, and permitting a sync from a source
nothing had read. Both are closed.

### W2.7: what moved to the graph, and the one thing that could not

**The defect the step closes is not performance.** `list_bay_candidates` filtered
with `WHERE (%s IS NULL OR configuration.warehouse_id = %s)`. A return whose
`processingWarehouseReference` was never set passed `None`, the predicate
collapsed to true, and the agent was handed **every bay in every warehouse** to
rank — then staged a parcel into one of them. A missing reference produced a
*longer* candidate list than a present one, and nothing said the warehouse was
unknown. That is the W2.6 defect class in the other direction: the presence or
absence of a reference standing in for an observation.

`BayEvidence` keeps three readings apart, and `WarehouseObservation.evidence_
reference` puts which one applied into the audit trail:
`WAREHOUSE_ABSENT:NO_WAREHOUSE_REFERENCE` (never routed),
`WAREHOUSE_ABSENT:WAREHOUSE_NOT_IN_GRAPH` (an id that resolves to nothing) and
`WAREHOUSE_UNAVAILABLE:*` (we could not look). A warehouse that *is* observed and
has no eligible bay is `OBSERVED` with an empty list — a real state, and not the
same as the other three.

**One anchored read syncs both entities.** `warehouse` and `bay` are bound to the
same object and `GenericSourceRecordExtractor` runs every entity bound to a
source over each document, so a targeted read anchored on `warehouse_id` returns
that warehouse's bay rows and projects one `Warehouse`, its `Bay` nodes and the
`HAS_BAY` edges between them. Proven end to end against the real SQL Server: 6
rows in, 12 node mutations and 6 relationship mutations out, one warehouse node.

**Live reserved capacity did not move and cannot.** The SQL query computed
`max_capacity - SUM(reserved_capacity) WHERE expires_at > SYSUTCDATETIME()` — an
aggregate over unexpired reservations evaluated at the instant of the query. It
changes with the clock rather than with any source write, so no sync however
targeted makes a graph node current for it. `capacityAvailable` is therefore now
the bay's **declared maximum**, and the platform relies on
`reserve_and_assign_handling_unit` to refuse an over-committed bay — which is the
only place holding a lock over that decision anyway. The consequence worth
stating plainly: **the agent can now recommend a bay that is full**, and finds
out at assignment rather than at ranking. Modelling `bay_reservation` as a fourth
entity with a time-filtered read is the way to get it back, and is not in W2.7.

**No SQL fallback, on purpose.** With no observation port configured the service
still uses the SQL path, so a deployment that has not built the targeted-graph
stack keeps working; with one configured, an `ABSENT` or `UNAVAILABLE` reading
yields no candidates and never reaches SQL Server.
`tests/operations/test_bay_candidates_come_from_the_graph.py` injects a
repository that fails the test if `list_bay_candidates` is called at all.

### A defect found and fixed inside W2.5/W2.6's area

The on-demand sync path was complete and wired end to end — guard, planner, connector,
extractor, projector, writer — and **could not put the anchored order into the graph**. The read
projection was built from the anchoring entity's own mapped fields, so it omitted the
discriminator that entity's `where` selector tests, and everything under the exploded line
array. The order the sync was requested for was the one entity never projected; the receipt read
`SUCCEEDED`, and the agent told the associate it had checked the source system directly.

Projection is now derived from every mapped field of every entity bound to the source, plus the
paths its selectors test.

### W2.6 closed: the source contract is verified

A genuine `shipmentInfo` sample (100 documents) was supplied and every declared physical path
checked against all 100, not against document 0. **Six of nine resolved; three did not, and none
of the three was a typo:**

- **`carrier_code` → `shipmentInfoEventData.carrierCode`: does not exist.** That block holds
  exactly `acctId`, `currentStatus`, `shipmentId`, `srcSystem`, `trilOrdNum`, `trkNum`. Carrier
  *is* in the source, but only inside the `shipmentInfo[]` detail array and only for one of the
  two document families: Convey carries `carrierScac`/`carrierName` (28 of 100), DispatchTrack
  carries a truck and a driver because it is own-fleet delivery. **Dropped.** Reaching it would
  mean `explode`ing `shipmentInfo[]`, which makes a shipment's presence in the graph conditional
  on that array being non-empty — and an absent shipment reports `AWAITING_HANDOFF`, the exact
  false negative W2.6 exists to remove. Explode would also have to pick one element per tracking
  number, and on one of the 100 documents element 0 is the one *without* a carrier while its three
  siblings name UPS.
- **`shipped_at` → `shipmentInfoEventData.shippedAt`: does not exist**, and no actual ship *event*
  timestamp exists at any grain. Convey has `shipmentInfo[].shipmentInfo.carrierShipDate` (29 of
  100, same explode problem); DispatchTrack has `reqrdShipDate`, a *required* ship date — a plan,
  and a `MM/DD/YYYY` string, not a `DATETIME`. **Dropped** rather than mapped to something that
  means a different thing.
- **`source_updated_at` → `updatedAt`: absent on all 100.** The change timestamp is
  **`shipmentInfoEventMeta.lastUpdateTs`** (present on all 100), the same meta-block pattern
  `salesInv` cursors on. **Path corrected.**

**No `where` selector.** `shipmentInfoEventMeta.docType` is `disptrck` on 72 and `convey` on 28;
both are genuine shipments sharing one `shipmentInfoEventData` shape, so a selector would discard
a whole family. Two documents also carry `docType: disptrck` over a convey-shaped body, so it is
not a reliable discriminator anyway.

`shipment` is now `VERIFIED` / `CONNECTED_SYNC`, `order_shipped_as` rises to `CONNECTED_SYNC`
(it was `SEED_ONLY` only because the endpoint ceiling forbade more), and the promotion
`test_fulfillment_shipment_sync_real_infra.py` needed is gone — the Validation clause is proven on
the descriptor as shipped. What remains constructed is the *demotion*, for the one test that
asserts the refusal path.

**Known and not fixed:** `trkNum` is **not unique** — 93 distinct values across 100 documents,
with distinct shipments on distinct orders sharing a number. `natural_key: [tracking_number]`
therefore merges them into one node. Only `_id` (`acctId*orderNumber*trkNum`) is unique;
`shipmentId` is worse (89 distinct). Changing the node key is a graph migration touching
constraints, indexes and every edge into `Shipment` — W2.2/W2.3 work, not a W2.6 edit.

The defect the step exists to fix **is** closed regardless: `IN_TRANSIT` is no longer inferred
from a reference existing. Absent an observed shipment the state is `AWAITING_HANDOFF`, and
`evidence_references` distinguishes `SHIPMENT_OBSERVED` / `SHIPMENT_ABSENT` /
`SHIPMENT_UNAVAILABLE`. `_bind_fulfillment_tracking` used to *require* `tracking_reference is
None` for `AWAITING_HANDOFF`, which made "we have a number" and "it is moving" the same statement
by construction; that clause is relaxed.

### W2.8: the incremental sync existed and had never once run

`incremental_sync` was **not** missing and **not** wrong. `GenericSyncCoordinator.incremental_sync`
reads the checkpoint, hands it to the connector as `after`, and advances the checkpoint only after
both the node write and the affected-relationship reconciliation succeed for a page.
`MongoDBSourceScanConnector.scan` honours `after` with a real `$gt` bound. Read in isolation, both
halves are correct.

What did not exist was anything to connect them to:

- **No `CheckpointStore` implementation persisted anything.** The only one in the tree was
  `sync_service._UnusedCheckpointStore`, whose `read` and `write` both raised
  `NotImplementedError`.
- **No caller ran it.** `GraphSyncService._sync_participating_sources` called `full_sync`
  unconditionally, and `GraphSyncScope`'s three values (`FULL`, `SOURCE_MONGODB`, `SQLSERVER`)
  all choose *which sources* participate, never *which records*. Every run S6 has ever
  triggered or listed was a full scan.
- **The existing tests could not have caught it.** `test_generic_sync_coordinator.py` drives
  `incremental_sync` through a fake whose `read` returns `None` on every call — and `after=None`
  is precisely the input under which a resume and a full scan produce identical output. Eight
  tests exercised the method; none of them exercised a resume.

This is the failure mode the step's *Why* names, in a shape the run list cannot show. A
full-scanning "incremental" sync produces the same rows, the same statuses and the same green
ticks; only the cost differs, and only against a real source at real volume.

Now: `MongoSyncCheckpointStore` (Mongo-backed, keyed by generation and source, writes guarded by
fencing token so a fenced-off run raises rather than silently rewinding a live cursor), and
`GraphSyncRequest.incremental` selecting the branch. `incremental` is a separate field rather than
a fourth `GraphSyncScope` value so "which sources" and "which records" stay composable instead of
needing one enum member per combination.

**Proven against real MongoDB**, not around a mock — `test_incremental_sync_real_infra.py`, on the
descriptor as shipped, cursoring on the `shipmentInfoEventMeta.lastUpdateTs` path W2.6 corrected:
a second run reads only the record that changed; a run with nothing to do reads nothing; a run
that fails on page 3 of 4 leaves the checkpoint at page 2 and the restart reads pages 3 and 4 and
neither of the first two; a checkpoint whose record was since deleted still resumes from that
position rather than stalling or restarting. Both halves were then broken deliberately to confirm
the tests fail — a non-persisting `read` fails all eight, and a `scan` that ignores `after` fails
exactly the four resume-dependent ones.

**Single-stage checkpointing, as the step's Failure condition permits.** Two-stage incremental
checkpointing and blue/green catch-up sequencing remain out of scope and are not half-implemented.

**Two operator-facing hazards surfaced rather than left silent.** A source with no
`incremental_cursor_field` is skipped by the coordinator by design; the run now names those in
`skippedSources` and S6 shows them, because otherwise a source quietly stops syncing behind a
COMPLETED run. And S6's "completed without writing anything" warning is suppressed for incremental
runs — writing nothing is the *expected* result there, and an error-toned warning on every quiet
run is how the genuine one stops being read.

**Not shipped, from the step's *Change* clause:**

- **`config/sync/order_{full,partial}.yaml` are still unbound.** Both are `status: DRAFT` and
  nothing reads them. They describe the *order discovery* sync profiles (`FULL_ORDER_SYNC` /
  `PARTIAL_ORDER_SYNC` with strong anchors), which is the targeted on-demand path from W2.5/W2.6,
  not the cursor-based incremental sync closed here.
- **S6 does not show entity, binding version, watermark, or a retry control.** Run id, source,
  schema generation, FULL/PARTIAL, processed, written, skipped, failed, start, finish and failure
  reason are all present. The stored watermark is persisted but has no reader — an operator can
  see that the second run read fewer records, not the cursor value it resumed from.

**A known gap in the resume contract.** A checkpoint is refused when the source's cursor
*strategy* changes (an `OBJECT_ID` cursor against a source now scanning by timestamp is a named
`MongoConnectorError`, not a silent full rescan). It is **not** refused when the cursor's
*physical path* changes underneath it while the type stays the same — the `CheckpointStore`
protocol gives `read` only the source id and the generation, so the binding is not knowable there.
W2.6's own path correction would not have been caught by it. In that case the old connector fails
closed in `capture_high_watermark` rather than resuming wrongly, but that is a property of that
particular change, not a guarantee.

### A pre-existing test that had never run

`tests/data_platform/graph/test_sync_service_pipeline.py` — the only test of
`GraphSyncService.sync()`'s orchestration — died on an `AttributeError` in `refresh_schema` before
reaching the pipeline it exists to exercise. `refresh_schema` was added after the module was
written and its `FakeSettings` never caught up. Both tests in it were failing at `d9639e4`. The
fake was stale, not the service; it is fixed and the tests now run.

Likewise `SyncControlPage.test.tsx`'s "does not call out a healthy run" failed on its own setup
line: `readRun` is mocked to the *targeted* run (5 writes), so the detail pane never rendered the
`300` the test waited for, and its assertion had never once executed.

## Gate A — deferred to a Linux environment

The 19 steps are in the plan. Nothing is deleted before it, so Wave 3 is still blocked.

**Gate A was not attempted-and-failed. It is deferred.** Testing moves to Linux; the run on
Windows was stopped by decision partway through, not by a failure. Everything below the
"verified" line is **untested, not passing**, and must not be read as a partial pass.

Two backend runs happened on 2026-08-13 before stopping. Neither reached its own summary: the
first was killed by `pytest --timeout` at 50%, the second was stopped deliberately at 43%. So
there is still **no complete backend suite tally**, and none should be quoted.

### The "hang" signature is not a hang — and three runs were probably killed while healthy

This is the finding that matters most, because a false belief about it is why Gate A had never
been run at all.

The signature on record — no output for tens of minutes, near-zero CPU — was read as a deadlock
three times. It is not. It is the *expected* shape of an IO-bound real-infra suite. Measured
from the **server** side rather than the client, with MongoDB's `currentOp` sampled three times
at 15s intervals while the run sat at 10 minutes elapsed and 27s of CPU:

* the active namespace advanced on every sample — `case_aggregate_test_…document_artifacts` →
  `…return_configuration_snapshots` → `case_support_test_…case_facts` → `…support_work_items`.
  Different test databases, different collections: forward progress through modules.
* the operations in flight were `createIndexes` against **per-test databases**, seconds each.
  Every real-infra test creates a database, builds the full index set, and drops it.
* zero test databases remained afterwards, so teardown works.

The client is idle because the server is building indexes. Low CPU is therefore evidence of
nothing. **Three earlier runs were almost certainly killed while healthy.**

The "no output for ~50 minutes" is most likely stdout block-buffering — pytest does not
line-buffer when it is not attached to a TTY, so a redirected run shows nothing and then flushes
in a block. **Flagged as strong inference, not proof:** it was not verified against any specific
killed run. Both runs here set `PYTHONUNBUFFERED=1` and produced continuous output, which is
consistent with it but does not establish it. Set `PYTHONUNBUFFERED=1` (or `-s`) on any
redirected run so this cannot recur.

**A separate, genuine wedge did occur, and it is not the same thing.** It must not be folded
into the paragraph above, because the fix for one is not the fix for the other. Run 1 stopped
dead at 50% and `pytest --timeout=300 --timeout-method=thread` dumped the stack, so this one is
captured rather than inferred. Its cause is below. What is *not* claimed: that any of the three
earlier runs hit it. There is no evidence either way, and the currentOp finding above makes the
healthy-kill explanation the more likely one for those.

### The wedge that was real: a crashed SQL Server that keeps its port open

**Mechanism, demonstrated end to end.**

1. `sqlservr` inside `return-multi-agent-platform-sqlserver-1` died mid-run with a fatal
   scheduler assertion:

   ```
   This program has encountered a fatal error and cannot continue running at Thu Aug 13 07:41:41 2026
       Reason: 0x00000004
      Message: ASSERT: Expression=((seenByMonitor) <(NonYieldThreshold))
               File=LibOS\Windows\Kernel\SQLPal\common\dk\sos\src\sosschedmon.cpp Line=202
      Process: 212 - sqlservr
   Capturing a dump of 212
   ```

2. The crash does **not** stop the container. PID 1 is `launch_sqlservr.sh`, which stays alive
   while the dump is written (`ps` showed PID 212 in state `tl` — stopped, multi-threaded, under
   the dump handler, next to a 299 MB `core.sqlservr.8_13_2026_7_41_42.212`). So the container
   never exits, `restart: unless-stopped` never fires, and the compose healthcheck — 12 retries
   at 10s — still reported `healthy` well after the server stopped answering.

3. The listening socket therefore stays open. A client gets a successful TCP connect and then
   silence. `sqlcmd` from inside the container names it exactly:

   ```
   Unable to complete login process due to delay in prelogin response.
   ```

4. `pytest --timeout=300 --timeout-method=thread` caught the stack in the one call that matters:

   ```
   File "backend\tests\source_connectors\conftest.py", line 71, in _sqlserver_database_exists
       request.getfixturevalue("sqlserver_test_database")
   File "backend\tests\source_connectors\conftest.py", line 41, in sqlserver_test_database
       with pymssql.connect(
   +++++++++++++++++++++++++++++++++++ Timeout +++++++++++++++++++++++++++++++++++
   ```

**Why `login_timeout=10` did not save it, proven rather than assumed.** The fixture already
passed `login_timeout=10, timeout=10`. Those bound *reaching* the server and *running a
statement*; neither bounds waiting for the prelogin reply. Reproduced away from SQL Server
entirely, with a socket that accepts and never writes a byte:

```
--- test 1: raw pymssql.connect with login_timeout=10, observed for 40s
RESULT_1=STILL_BLOCKED_after_40.0s
--- test 2: the conftest 30s deadline
RESULT_2=DEADLINE_FIRED_after_30.0s
```

Four times the declared `login_timeout`, still blocked. That is the mechanism: an autouse,
function-scoped fixture that opens a connection before **every** test in
`tests/source_connectors/` blocks forever, on a process consuming no CPU, so the suite stops
dead with no output and no failure. Observed here at 50%, on 58.3s of CPU that then never moved.

Note the trap: this produces *exactly* the same client-side signature as the healthy IO-bound
suite described above. Elapsed time and CPU cannot tell them apart, which is why the earlier
attempts were unreadable and why a per-test `--timeout` is the thing that settles it. Do not use
this section to re-diagnose the earlier runs; use `--timeout` on the next one.

**Fixed** in `tests/source_connectors/conftest.py`: the connect runs on a worker thread bounded
by a 30s wall-clock deadline outside the driver, and the timeout raises a message naming the
real condition. This is not a test relaxation — the tests still require a working SQL Server and
still fail without one. It converts "the entire suite stops" into "this package fails, by name,
and the run reaches its own summary".

**This has happened at least twice.** `/var/opt/mssql/log/` also holds
`core.sqlservr.08_12_2026_06_08_21.43` with the *identical* assertion at `sosschedmon.cpp:202`
on 2026-08-12. That is the same day as earlier suite attempts, which is suggestive and nothing
more — the core file records a crash, not which run was affected by it, and the healthy-kill
explanation above remains the more likely account of those runs.

**What is _not_ diagnosed: why SQL Server crashes.** A non-yielding-scheduler assertion under
`MSSQL_MEMORY_LIMIT_MB=3072` in a 4 GB container is consistent with resource starvation under
suite load, but that is a guess and is written here as one. Two crashes in two days under the
same workload is the fact; the cause is not established, and no attempt was made to reproduce it
(deliberately — repeated-iteration runs are forbidden and would be the obvious way to try).

**Operational note.** `docker restart` on the crashed container left it wedged in startup for
6+ minutes, spinning CPU, never writing an errorlog. `docker stop -t 60` followed by
`docker start` recovered it in 65 seconds. That is recorded because the first thing anyone will
reach for is the one that does not work.

### The Temporal hypothesis is refuted, not merely unconfirmed

The suspicion on record was that `test_return_workflow_concurrency.py` leaves a workflow or
worker wedged. It does not, and the evidence is direct:

* `temporal task-queue describe` on `return-platform-order-discovery-v1` reports
  `ApproximateBacklogCount 0` for both workflow and activity tasks, and no pollers. Nothing
  stale is waiting to be dispatched to a worker a test starts.
* The workflow tests build **per-test** task queues (`test-order-discovery-<hex>`), so they
  cannot pick up anything from the production queue in the first place.
* The hang reproduced in a module that touches neither Temporal nor a workflow.

**A real defect was found while refuting it, though.** The server holds **135 leaked `Running`
workflows** (133 `return-platform-order-discovery-v1`, 2 throwaway reasoning workflows), the
oldest 19 hours old, and the count rose to 137 during a single suite run. They are idle rather
than dangerous — each is parked on a `startToFireTimeout` of `604800s`, seven days, which is why
they generate no task-queue load — but nothing ever terminates them and every run adds more.
`api/order_agent.py` builds the id as `order-discovery-{conversation_id}`; the tests use fresh
UUIDs, so they leak instead of colliding.

Worth knowing before Gate A's business path is attempted: `order_agent.py` does
`start_workflow(...)` and, on `WorkflowAlreadyStartedError`, attaches to the existing handle and
calls `execute_update`. `execute_update` waits for completion indefinitely, and the app's
lifespan creates a Temporal **client only** — never a worker. So driving the agent surface with
no `order-discovery-worker` polling that queue is a second, independent way to hang, and the
`containerized-app` compose profile that provides that worker was not running.

### Acceptance categories from §10

**How far the runs got.** Run 1 covered roughly the first half of the collection in test order —
`agents` through `source_connectors` — before the SQL Server wedge killed it at 50%. Run 2,
with Vault enabled, was stopped at 43%. Neither reached `tests/` root modules or `tests/v2`.
Anything below marked *verified* means "every test in that area that ran, passed"; it does not
mean the category is closed, and **no category should be treated as passing until the Linux
run**.

| Category | Verdict |
|---|---|
| **Business** | **not verified.** The end-to-end path needs the `containerized-app` profile (backend, `order-discovery-worker`, `return-workflow-worker`, frontend) running against real infrastructure. Only the seven infrastructure containers were up; no worker polls the production task queue, so the discovery → `CONFIRM_ORDER` → case → workflow path could not be driven at all. Component-level coverage passing is not the same claim |
| **Graph** | **partly exercised.** Order, return, shipment and warehouse reads through the graph, targeted on-demand sync and sync-failure visibility passed against real MongoDB and Neo4j in both runs. One genuine red remains in this area — see below |
| **Configuration** | **partly exercised.** Approved draft → runtime schema with no file edit, and a source rebinding through configuration alone, both passed against real infrastructure (`tests/operations/test_source_dataset_binding_real_infra.py`). Worker reload without a process restart is **not verified** — no worker was running |
| **Governance** | **not reached.** The modules sit past where both runs stopped |
| **Security** | **partly exercised.** `tests/security`, `tests/configuration` and the tenant-isolation real-infra modules ran clean in run 1. Vault is now genuinely exercised — see below. Source-side DDL/DML refusal ran clean; the provider-boundary PII checks did not all run |
| **Architecture** | **two genuine reds**, both guard trips rather than runtime drift; see below. The W0.6 frozen-runtime import tests and the cross-agent import rules passed |
| **Reliability** | **not verified.** Worker restart losing no waits needs a worker. "No unexplained flaky test remains" is still false: `test_return_workflow_concurrency.py::test_a_second_completion_sees_the_first_ones_state` remains undiagnosed and was deliberately not investigated here, because diagnosing it means repeated runs |
| **Release** | **not verified.** No dependency or image scanner exists in the repository to run; this needs CI |

**Verified independently of the suite, and these do stand:** `ruff format --check`, `ruff check`
and `mypy --strict` are all clean on `src tests scripts` / 507 source files; the frontend is
clean on `npm run lint`, `npm run typecheck` and `npx vitest run` (18 files, 168 tests);
`scripts/check_openapi_drift.py` now exits 0 (it did not at the start — see the commit); and the
W0.7 discovery smoke net's 24 tests passed in both runs.

### Vault: unblocked mid-run, and it changes the result

Vault was sealed when this run started and was unsealed by the user partway through
(`/v1/sys/seal-status` → `sealed=false`, confirmed directly here, not taken on report).

The seal is only half the gate: `.env` carries `PLATFORM_VAULT_ENABLED=false`, which is a
separate switch, so the credential tests still fail with `Vault must be enabled in production`
until the process overrides it. With `PLATFORM_VAULT_ENABLED=true` and `PLATFORM_VAULT_TOKEN_FILE`
pointed at `.vault-local/return-platform.token` as process environment,
`tests/configuration/test_ai_credentials_must_be_vault_references.py` goes from **5 failed to 12
passed**, verified here independently. `.env` was deliberately not edited: the API and worker
read it and the flag's permanent value is not this run's decision.

### Two architecture guards have been red and unseen

Neither is new, and neither had been recorded — because the suite has never before reached the
point of reporting them. Both trip on the same cause: legitimate Wave 1/2 routers were added and
the guards that count them were not updated in the same commit, which is exactly what both
guards' own comments ask for.

```
AssertionError: a new return-domain router appeared; new endpoints belong on the canonical
/api/returns surface: {'return_history.py': '/api/return-history'}

AssertionError: 28 routers are mounted, expected 22; if Wave F deleted one, update this
number in the same commit
```

The six routers added since `944cb82` (F2, the reading that set 22) are
`agent_configuration`, `cases`, `return_history`, `source_bindings`, `schema_releases` and
`graph_sync` — W1.8, W2.1/W2.3, W2.2, W2.5/W2.6 and W2.8 respectively.

**Deliberately left red.** Bumping 22 to 28 and adding `return_history.py` to
`_KNOWN_RETURN_ROUTERS` would take ninety seconds and would erase the question the first guard
exists to force: whether `/api/return-history` should be a surface of its own or belong under
the canonical `/api/returns`. `return_history.py` argues its own case in its module docstring —
it is a graph *traversal* read, built on the same `LogicalQueryPlan`, `SchemaQueryGuard` and
`CypherCompiler` as the agent, so it is not a second graph read path — but that is an
architectural decision, not a test-maintenance chore, and it is not one to make silently during
a gate run. The second guard's number should be updated in whichever commit settles the first.

### One graph red, reproduced and not diagnosed

`tests/dynamic_knowledge/test_return_side_sync_real_infra.py::test_the_case_reaches_the_order_it_was_raised_against`
failed in **both** runs, at the same point, so it is deterministic rather than flaky. Every other
test in that module passed, including `test_the_platform_store_is_actually_read`, so the Stage B
cross-store machinery is reaching the platform database; it is specifically the
`ReturnCase -[:COVERS_ORDER]-> SalesOrder` join that returns the wrong rows.

**Not diagnosed.** The assertion text was never captured: run 1 was killed by `--timeout` before
pytest printed its FAILURES section, and run 2 was stopped for the same reason. The module builds
throwaway uuid-suffixed databases and seeds exactly one `salesInv` header, so the obvious
candidates — leftover state, or the real `return_source` sample data — are both ruled out by
construction. This is the first thing to pick up on Linux; running that one module prints the
assertion in seconds.

### Tooling note

`pytest-timeout` is what made the wedge legible, and it is **not in
`backend/pyproject.toml`** — it was `pip install`ed into the venv for these runs. Whoever sets up
the Linux environment should add it to the dev dependency group; without a per-test timeout the
next unreadable run is unreadable in exactly the same way.

Not done here on purpose: adding it means regenerating `poetry.lock`, which is not a change to
make blind at the end of a session that is being wrapped up.

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
  `config/seed/generation.yaml`. It now emits shipments — one per order that actually shipped a
  line, so "the graph holds no shipment for this number" stays a state the corpus can produce.
  First execution (120 orders into a throwaway database) produced 109 shipments whose every
  declared path resolves and whose order numbers join real `salesInv` documents.

  That first run also found three **pre-existing** generator defects, confirmed by stashing and
  re-running: `contact_point`, `customer_account` and `customer_party` all fail the generator's own
  `_verify` because their `record_path`s (`customer.address`, `party.custAccts.
  additionalCustomerInfo`, `party`) are never built. `_entity_fields` writes only
  `CURRENT_RECORD`-origin fields at the top level and never creates the nesting an exploded entity
  needs, so the customer corpus produces no contact points, accounts or parties. Unrelated to W2.6
  and left open.

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

  **Resolved.** The checksum moved to the normalized text in `4116915`, and the test no longer
  writes at all: it now *asserts* the committed inventory matches what the registry would produce,
  so registry drift fails the gate instead of being silently absorbed. `AIG1_EVIDENCE_WRITE=1`
  regenerates it deliberately, mirroring `scripts/check_openapi_drift.py --write`. Checking rather
  than relocating to `tmp_path` was the choice because the file is referenced review evidence whose
  whole value is that something verifies it.

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

  **Now fixed everywhere, and every claim below that cites this bucket is stale.** Twenty modules
  built the DSN without it, including the shared `test_settings` fixture in `tests/conftest.py`.
  All twenty carry `directConnection=true` now, and **131 tests that could not previously be run
  from the host pass there** — among them `test_generation_lifecycle_e2e` and
  `test_on_demand_sync_production_wiring`, both of which appear in the failure lists below as
  environment noise, and the whole of `tests/operations`.

  This matters beyond the count. "Host Mongo topology" had become the bucket that unread failures
  were sorted into, by me and by three separate agents. It was a real defect every time it was
  cited, but a standing excuse is exactly where a genuine failure hides — and the next run should
  no longer have one to reach for. A `*_real_infra` module that fails on the host now needs a
  reason.

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

## Verification: W2.4 and W2.7

Run on the **host** from a worktree at `d9639e4`, against the running compose
infrastructure. Host runs cannot reach Mongo (see `directConnection` above), so this is
not a substitute for a diagnostics-container run — but SQL Server *is* reachable from the
host, which is what both steps needed.

| Command | Result |
|---|---|
| `ruff format --check src tests scripts` | 809 files already formatted |
| `ruff check src tests scripts` | All checks passed |
| `mypy --strict src` | Success: no issues found in **506** source files |
| `tests/dynamic_knowledge/test_order_discovery_smoke_net.py` (the W0.7 net) | 24 passed |
| the four new modules plus the projector regression | 47 passed |
| `tests/bootstrap tests/graph_schema_analyzer tests/operations tests/source_connectors tests/configuration`, `--ignore-glob="*real_infra*"` | 361 passed, 5 failed, 10 skipped, 6 errors (4m46s) |
| `tests/dynamic_knowledge`, `--ignore-glob="*real_infra*"` | **450 passed**, 9 failed, 4 errors (9m25s) |

**Every failure was read, none inferred.** The 5 configuration failures are the
documented `Vault must be enabled in production` bucket. The 9 failures and 4 errors in
`dynamic_knowledge` are three modules — `test_generation_lifecycle_e2e`,
`test_mongo_graph_state_provider`, `test_on_demand_sync_production_wiring` — all failing
on the documented host-side Mongo topology defect, verbatim
(`ServerSelectionTimeoutError ... ('mongodb', 27017)`), and all three are named in that
bucket already. The 6 errors in `test_mongodb_connector_docker` are the same defect.
None is attributable to this change set.

**One failure was not environmental and is repaired.**
`test_on_demand_sync_reaches_the_record.py::test_every_configured_source_reaches_the_
source_through_one_path` asserted `compiled.statement["projection"]` — the MongoDB
branch's shape. `bay` and `warehouse` are the first SQL-backed anchored entities and
`compile_source_read` hands those a statement *string*, so the assertion raised
`TypeError: string indices must be integers` on a compilation that had succeeded. The
test's own docstring says the plan is built "without any code knowing which source it
belongs to", and the check itself knew. It now asks the same question in each backend's
own vocabulary.

**The SQL-backed real-infra module runs on the host and is green**:
`test_warehouse_bay_source_contract_real_infra.py`, 5 passed — the live catalogue, the
declared indexes, every path against every row, the anchored read, and the projection.

**W2.7 was driven end to end against real Mongo, real SQL Server and real Neo4j**, not
only through stand-ins, because "candidates come from the graph" is not a claim a test
with a fake writer can make. Against the running compose stack, on generation
`legacy-live`:

| `observe(...)` | Result |
|---|---|
| `"WH-CHENNAI-01"` | `OBSERVED`, a sync request id, and **6 bay rows read back out of Neo4j** — `BAY-PPL-01`, `BAY-BOL-01`, `BAY-HOLD-01`, `BAY-CUSTOMER-SHIP-01`, `BAY-DIRECT-VENDOR-01`, `BAY-FIELD-SCRAP-01`, carrying the names and capacities SQL Server holds |
| `"WH-DOES-NOT-EXIST"` | `ABSENT` / `WAREHOUSE_NOT_IN_GRAPH` — the sync ran and found nothing, which is a different statement from not having looked |
| `None` | `ABSENT` / `NO_WAREHOUSE_REFERENCE`, no sync issued and no read attempted |

**The two new labels need their constraints before the first sync writes**, and the run
above applied them:

```
CREATE CONSTRAINT IF NOT EXISTS FOR (n:`Bay`)       REQUIRE (n.`graph_generation_id`, n.`bay_id`) IS UNIQUE
CREATE CONSTRAINT IF NOT EXISTS FOR (n:`Warehouse`) REQUIRE (n.`graph_generation_id`, n.`warehouse_id`) IS UNIQUE
CREATE INDEX      IF NOT EXISTS FOR (n:`Warehouse`) ON (n.`graph_generation_id`, n.`warehouse_id`)
CREATE INDEX      IF NOT EXISTS FOR (n:`Bay`)       ON (n.`graph_generation_id`, n.`warehouse_id`)
```

`required_node_constraints` and `required_relationship_indexes` derive all four from the
schema, so nothing was hand-written — but **any other environment needs the constraint
pass run before its first bay sync**, and that is an operational step, not a code change.

**Not run:** `tests/api`, `tests/test_order_agent_rest.py`, the Temporal workflow modules
and every `*_real_infra*`/`*_docker*` module that needs Mongo from the host.

### What is not verified, and is worth stating

**The `bay-recommendation` route was not exercised against a running backend.** The
service, the observation adapter, the candidate mapping and the graph read are all
covered — the last of them against real infrastructure — but the wiring in
`api/warehouse_placement.py` that builds the port from `app.state` and caches it is not.
That is what a diagnostics-container run of `tests/api` would add.

## Verification: after merging the three parallel branches

The hygiene, W4.5 and W2.6 branches were cut from `493c3f3` independently and merged in that
order. Post-merge on the integrated tree:

| Command | Result |
|---|---|
| `ruff check src tests scripts` | All checks passed |
| `ruff format --check src tests scripts` | 800 files already formatted |
| `mypy --strict src` | Success: no issues found in **502** source files |
| smoke net, connector routing, search strategy, sync adapters, fulfillment observation, credentials, canonical application, source scope | **140 passed** |

Three hand-resolved conflicts, all from branches converging on the same work: both the hygiene
branch and W4.5 independently fixed the connector-routing tripwire and the `run_data_job_worker.py`
I001, and both added `psycopg`.

### One real cross-branch interaction, not a conflict

`git merge` reported no conflict between W2.6 and the smoke net, and the merge was nonetheless
wrong. `test_an_anchor_the_schema_does_not_enable_never_reaches_the_source` used `shipment` as its
example of an entity carrying a well-formed anchor that is nonetheless `SEED_ONLY` — and W2.6's
entire purpose was to stop `shipment` being seed-only. After the merge the guard correctly
permitted the sync, the source was read, and the tripwire failed on `source.reads == 0`, which is
precisely the assertion it exists to make. Neither branch was wrong; the descriptor moved out from
under a test that named a specific entity.

It now demotes a copy of the shipped descriptor, reusing the `_demoted` shape the two fulfillment
modules already use. No other entity substitutes: `customer_account` and `customer_party` are the
only remaining `SEED_ONLY` entities and neither declares a strong anchor, so pointing the test at
one would have exercised the missing-anchor refusal instead — green, and guarding nothing.

## Verification: after closing W2.2

Host run from a worktree at `4116915`, on the shared `backend/.venv`.

| Command | Result |
|---|---|
| `ruff format --check src tests scripts` | 801 files already formatted |
| `ruff check src tests scripts` | All checks passed |
| `mypy --strict src` | Success: no issues found in **502** source files |
| `tests/operations/test_source_dataset_binding_real_infra.py` | **7 passed** |
| `tests/operations` + `test_seed_manifest.py` + `test_stage4_schema_and_seed_contracts.py` + `test_seed_api.py` | **29 passed, 26 errors** — all pre-existing, see below |
| `tests/dynamic_knowledge`, excluding `*_real_infra*`, `*_docker*`, the smoke net and the lifecycle E2E | **416 passed, 5 failed** — all pre-existing, see below |

### The 31 reds are one environment defect, and it is not new

Every one of them is `ServerSelectionTimeoutError: Could not reach any servers in
[('mongodb', 27017)]`. The DSN omits `directConnection=true`, so topology discovery from the host
learns the container hostname the single-node replica set advertises and resolves a name that does
not exist there. **Reproduced identically on an untouched checkout**, both modules and both
symptoms — this was measured, not assumed.

Two origins: the shared `test_settings` fixture in `backend/tests/conftest.py` (the 5 failures, in
`test_mongo_graph_state_provider.py` and `test_on_demand_sync_production_wiring.py`) and the
per-module `_mongo_dsn()` helpers in the three `tests/operations/*_real_infra.py` modules (the 26
errors). The fix is one query parameter, and the working pattern with its rationale is already in
`tests/dynamic_knowledge/test_return_record_sync_real_infra.py`. Left alone deliberately: it is a
separate defect spanning 31 tests it would be wrong to fold into a W2.2 commit.

**Not run:** `tests/dynamic_knowledge`'s `*_real_infra*` and `*_docker*` modules, its discovery
smoke net and its generation lifecycle E2E. A full `tests/dynamic_knowledge` run was started and
wedged — no output after 50 minutes, ~21s of CPU accumulated, so blocked on IO rather than
looping. That is the same symptom recorded above for the whole-suite attempt, and **the next full
run still belongs in the diagnostics container.**

Its `pytest.raises` is tightened in the same change. It accepted
`(OrderAgentFailure, AssertionError)`, and `ScriptedModel` raises `AssertionError` when the graph
asks for an unscripted action — so a turn that sailed past the guard and came back for a second
decision satisfied a `raises` meant to capture a refusal. Only the trailing `source.reads`
assertion caught it. **Any scenario in this module without a comparable trailing assertion has the
same hole**, and that is worth a sweep.
