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
| W2.4 Return and warehouse entities | **partial** — return entities added, but **by hand-editing the descriptor**, which the step forbids; **no warehouse or bay entity exists**. Blocked on the MSSQL analyzer connector (W4.5), as the step's own Failure condition predicts |
| W2.5 Return on-demand sync | **done** — `ReturnCaseWorkflow` runs a record-scoped `synchronize_return_records` activity after the return record commits, blocking, parking the case as `RETURN_GRAPH_SYNC_FAILED` on failure. Proven against real Mongo and Neo4j: a committed record is queryable through the compiler afterwards, and the pre-fix upstream connector routing is shown writing nothing |
| W2.6 Fulfillment on-demand sync | **done** — a genuine 100-document `shipmentInfo` sample verified the contract; `shipment` is `VERIFIED`/`CONNECTED_SYNC` and the Validation clause holds on the shipped descriptor with no in-test promotion; see below |
| W2.7 Warehouse and bay on-demand sync | **not started** — blocked on W2.4's warehouse entity |
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

## Gate A — not run

The 19 steps are in the plan. Nothing is deleted before it, so Wave 3 is blocked.

## Wave 3 — blocked on Gate A

## Wave 4 — not started (0 of 12)

Confirmed absent: `as_of` on `AgentTurnContext` (W4.7 — zero references).

## Wave 5 — not started (0 of 11)

Confirmed absent: `FUZZY_SEARCH` (W5.1 — zero references). W5.9's stray
`backend/poetry.lock;W/` and `backend/pyproject.toml;W/` are still present.

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
- **`scripts/run_data_job_worker.py` fails `ruff check`** (I001, unsorted imports) at `83321ed`.
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

`::1` port 7687 is Neo4j on IPv6 loopback. Those tests construct their own `Settings()`, which
reads the mounted repository-root `.env` — and that file was rewritten to literal `localhost`
values when Vault was disabled. The Vault placeholders it replaced were host-agnostic; the
literals are not, and inside the container they are wrong. `docker exec -e` overrides do not
help: they reach the process environment, and `Settings()` re-reads the file underneath them.

**Fix:** give the container its own `.env` carrying in-network values — `mongodb:27017`,
`bolt://neo4j:7687`, `temporal:7233`, `valkey`, `sqlserver` — rather than passing hostnames as
`-e` flags. Then re-run `tests/test_order_agent_rest.py`.

Two hypotheses were wrong before this one: that `PLATFORM_AI_PROVIDER_ORDER=MANUAL` starved the
scenarios, and that dropping the synthetic `orders`/`products`/`customers` collections removed
their fixture data. Both were inferred from where the failures clustered instead of from reading
one assertion — which is what execution rule 3 exists to prevent.

**Still open:** 4 errors in `tests/source_connectors/test_sqlserver_connector_docker.py`
(SQL Server is up; likely a missing table or driver in that container). And `ai_replay_mode` is
worth enabling for the next full run so it records itself and later runs cost nothing.

**Also note:** piping a container run through `tail` loses the entire output if the connection
drops -- it happened twice. Write to a file inside the container and tail that afterwards.
