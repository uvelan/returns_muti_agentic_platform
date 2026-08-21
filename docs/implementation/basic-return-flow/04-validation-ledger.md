# 04 · Validation ledger

Command, scope, exit code, result summary, and tree state for every meaningful
validation.

---

## V-1 · Baseline recorded

| Field | Value |
|---|---|
| Command | `git fetch --all && git rev-parse HEAD && git status --short` |
| Scope | repository baseline |
| Exit | 0 |
| Result | `refactor/unified-return-platform` @ `47f5abd7fad4e9f0e2c890ef7e762b37e45296e6`, working tree clean |
| Tree | clean |

## V-2 · Docker infrastructure up and healthy

| Field | Value |
|---|---|
| Command | `docker compose up -d`, then `docker compose ps` |
| Scope | infrastructure dependencies only (default profile) |
| Exit | 0 |
| Result | `mongodb`, `neo4j`, `sqlserver`, `valkey`, `temporal`, `temporal-postgresql` all `Up (healthy)`; `sqlserver-init` exited 0; `mongodb-rs-init` started; `runtime-configuration-init` recreated and re-ran |
| Tree | clean |

The containers were already up for about 25 minutes; `up -d` did not recreate the
healthy ones, so **no data reset occurred**. `runtime-configuration-init` was
recreated (it is a one-shot init container) and reported
`neo4j_schema_status=READY` with all fifteen cypher migrations reported
`skipped=` (already applied), confirming the graph was not reinitialised.

## V-3 · Direct dependency probes, partial

| Dependency | Probe | Result |
|---|---|---|
| Neo4j | `cypher-shell "RETURN 1 AS ok"` | `1` -- reachable and authenticated |
| Mongo | `mongosh rs.status()` with no credentials | `requires authentication` |
| Valkey | `valkey-cli ping` with no credentials | `NOAUTH` |
| Temporal | `temporal operator cluster health` against `127.0.0.1:7233` inside the container | connection refused |

The last three probes were malformed on my side rather than evidence of a fault;
each container's own health check passes. Authoritative validation is deferred to
the backend readiness endpoint, which uses the configured DSNs, and is recorded
as V-4 when the host services start.

## V-4 · Backend readiness through configured DSNs

| Field | Value |
|---|---|
| Command | `curl -s http://127.0.0.1:8000/health/ready` |
| Scope | every dependency, using the platform's own configured connections |
| Exit | 0 |
| Result | `status: ready`. `mongodb`, `source_mongodb`, `sqlserver`, `neo4j`, `valkey`, `temporal`, `configuration` -- **all HEALTHY**. Release `return-platform-51172e207c71d33b-r18` from `NEO4J_CONFIGURATION_GRAPH`. |
| Tree | `scripts/run_all_host.ps1`, `scripts/run_worker_host.ps1` modified (F-4) |

This supersedes the three malformed probes in V-3.

## V-5 · Release adoption reached LIVE with every required worker

| Field | Value |
|---|---|
| Command | `curl -s http://127.0.0.1:8000/api/config/adoption` |
| Scope | worker connectivity and release adoption |
| Exit | 0 |
| Result | `status: LIVE`, `pending_process_classes: []`. Adopted: `api`, `return-workflow-worker`, `order-discovery-worker`, `return-orchestrator`, `outbox-publisher`, `integration-outbox-worker` -- one live instance each, all on head revision 18. |
| Tree | as V-4 |

`order-discovery-worker` appears here **only because of the F-4 fix**. On the
unmodified `run_all_host.ps1` it is never started, and `jobs` would have killed
the stack seconds after launch.

## V-6 · Frontend serving and reaching the backend

| Field | Value |
|---|---|
| Command | `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:5173/` |
| Scope | frontend liveness |
| Exit | 0 |
| Result | `200`; vite dev server up, backend target `http://127.0.0.1:8000` |
| Tree | as V-4 |

## V-7 · Manual LLM mode activated

| Field | Value |
|---|---|
| Command | `PUT /api/v1/ai-gateway/settings {"interceptMode": true, ...}` |
| Scope | AI gateway runtime settings |
| Exit | 0 |
| Result | `interceptMode: true`, version 0 -> 1, `updatedBy: dev-operator` |
| Tree | as V-4 |

Interception is the repository's manual mode (C5): a dispatch is held as
`INTERCEPTION_PENDING` and answered by an operator through
`/api/ai/interceptions/{id}/answer`. No live provider call is made while it is on.

## V-8 · Bay configuration materialised for the seeded warehouses (D-1)

| Field | Value |
|---|---|
| Command | `backend/scripts/seed_warehouse_bay_configuration.py --dry-run`, then without it |
| Scope | `platform.bay_configuration` |
| Exit | 0 |
| Result | `warehouses=24 bays=311`, `warehouse_bay_projection=READY`. Table went from **6 rows to 317** -- the six `WH-CHENNAI-01` bootstrap rows untouched, 311 added. |
| Tree | new file `backend/scripts/seed_warehouse_bay_configuration.py` |

Nothing was written to Neo4j: `GraphWarehouseBayObservations` runs a targeted
on-demand sync anchored on `warehouse.warehouse_id` against this table and
projects `Warehouse` and `Bay` nodes from what it reads, so the graph picks the
new rows up at the next bay observation.

## V-9 · Manual LLM mode drives a full turn end to end

| Field | Value |
|---|---|
| Command | `POST /api/v2/order-agent/conversations/{id}/turns` with `"CQ800002"`, answering each held request through `POST /api/ai/interceptions/{id}/answer` |
| Scope | order-agent turn, manual mode, no provider call |
| Exit | HTTP **200** |
| Result | `conversation_version: 1`. Two reasoning steps held and answered in order -- `ORDER_AGENT_REASONING_OPENING_V1` then `ORDER_AGENT_REASONING_COMPLETING_V1` -- with the search executing between them. |
| Tree | see F-7 to F-9 for the files changed |

`GET /api/ai/routes` reports exactly two routes, both `MANUAL /
manual-human-v1` (LIGHTWEIGHT and STANDARD). **No live provider is reachable**,
which is the strongest form of the "no live AI call" gate: not "none was made"
but "none could be".

## V-10 · Order discovery returns exactly one order for the entered number

| Field | Value |
|---|---|
| Command | the same turn as V-9 |
| Scope | Phase 3 |
| Exit | 0 |
| Result | `total_found: 1`, one candidate `CQ800002`, `matches: ["sales_order_number_exact"]`, `score: 1.0`. Header data returned: `account_id GARDEN`, `order_status INVOICED`, `sell_warehouse_id 686`, `ship_to_city RENO`, `shipping_method CUSTOMER PICKUP`. |
| Tree | unchanged by this step |

`customer_name`, `ship_to_name`, `ship_to_phone` and `job_name` arrive at the
model as `[REDACTED]`. That is `redact_payload` working as designed -- the model
is not shown customer identity -- and it is why the Support template must be
composed from **case state**, not from anything the model saw.

## V-11 · The case names its customer

| Field | Value |
|---|---|
| Command | drive the flow to confirmation, then `GET /api/cases/{id}` |
| Scope | Phase 4 |
| Exit | 0 |
| Result | `customer: {"customerReference": "600911", "displayName": "THELMA OSBORNE", ...}` on case `10fcba5e`. Before the fix, `customer: null` on case `e5ce5c59` under identical inputs. |

## V-12 · The order line names its colour

| Field | Value |
|---|---|
| Command | `GET /api/cases/{id}/order-lines` |
| Scope | Phase 4 |
| Exit | 0 |
| Result | `lineReference "1" \| description "6X12 CEIL ALUM 4-WAY REG SAND" \| colour "Sandtone"`. Before the binding reached the release, the same call returned `colour: null` with the field wired -- which is how F-13 was found. |

## V-13 · The complete handoff, end to end

| Field | Value |
|---|---|
| Command | order number -> confirm -> `POST /api/cases/{id}/selected-items` -> `GET /api/v1/return-support/work-items/{id}/messages` |
| Scope | Phases 5-9 |
| Exit | 0 |
| Result | see below |

Sequencing observed on case `10fcba5e`:

1. after confirmation the case sat at `GATHERING_INFO` with `support: null` --
   **the handoff waited** (F-15);
2. `POST /selected-items` recorded line 1, quantity 1, `ORDERED_IN_ERROR`,
   `NEW_IN_ORIGINAL_PACKAGING` and the branch contact, and signalled the
   workflow;
3. the case moved to `AWAITING_SUPPORT` with work item
   `8b2d9519-d033-4fc8-8688-e3c842d3398b`.

The message delivered:

- **Case** -- case id, work item id, created instant, workflow status
- **Customer** -- `THELMA OSBORNE`, reference `600911`, branch associate name,
  email and phone
- **Order** -- `CQ800002`, line `1`, `6X12 CEIL ALUM 4-WAY REG SAND`, colour
  `Sandtone`, SKU `KHHJUB`, quantity `1`, reason `ORDERED_IN_ERROR`, condition
  `NEW_IN_ORIGINAL_PACKAGING`
- **Bay Assignment** -- `RECOMMENDED`, bay `686-BAY-01`, warehouse `686`,
  return location `686/686-BAY-01`
- **Verification** -- order confirmed, required information complete,
  `Policy Evaluation: Skipped by configuration (...)`, source
  `Bay Assignment Agent`
- **Requested Support Action** -- review, confirm the bay, verify Support-owned
  conditions, create or decline the RMA

No RMA, label, tracking or policy approval is asserted anywhere in it.
`businessPayload` carries the same facts under `schemaVersion:
support-handoff-v1`.

## V-14 · Support Chat UI renders the document

| Field | Value |
|---|---|
| Command | browser at `/support`, select the work item, read computed style |
| Scope | Phase 9 |
| Exit | 0 |
| Result | `white-space: pre`, `overflow-x: auto`, monospace; 50 lines and all 7 section headers present; `scrollWidth 826` inside `clientWidth 241`, and `document.body.scrollWidth === clientWidth` -- the bubble scrolls, the page does not. |

## V-15 · Targeted tests

| Command | Result |
|---|---|
| `pytest tests/operations tests/api tests/configuration tests/policy tests/reasoning -q` | **1179+ passed**, 0 failed |
| `pytest tests/operations/test_support_handoff.py -q` | 8 passed (new) |
| `npm run test -- --run src/domains/support` | 35 passed, including 2 new rendering tests |
| `ruff check src scripts tests` | 1 error -- the known `I001`, see D-3 |
| `npm run lint` | 5 errors -- all pre-existing, see D-3 |
| `scripts/check_openapi_drift.py --write` | PASS; `colour` added to `OrderLineView` in all four snapshots and the generated TypeScript |

## V-16 · Adversarial validations

Driven against the running system unless the evidence column says otherwise.

| # | Case | Result | Evidence |
|---|---|---|---|
| 1 | Unknown order number | **PASS** | `ZZ999999` -> `total_found: 0`, `candidates: []`, `tool_failures: []`, stage `UNRESOLVED`. No match and an error are distinguishable. |
| 2 | Leading/trailing whitespace | **FAILED, fixed, re-passed** | `"  CQ800002  "` -> 0 before, 1 after. F-16, three regression tests. |
| 3 | Duplicate order-search submission | **PASS** | Identical turn re-posted: same `conversation_version: 1`, one associate message in the transcript, no second AI dispatch. |
| 4 | Several source records for one order number | **PASS** | Aggregating `salesInv` by `salesHdrEventData.orderId` returns **zero** ids on more than one document across all 10,000; the live search returns exactly one. |
| 5 | Confirmation submitted twice | **PASS** | `confirm_case` is idempotent on `tenant\|conversation\|order\|line-set` and `create_case` on a unique index; an identical selection resubmission answered `changed: false`, one item, revision unmoved. |
| 6 | Missing line number | **PASS** | `project_source_order_lines` falls back to the line's 1-based position; a line that resolves to neither identity is dropped rather than numbered anyway. |
| 7 | Missing product colour | **PASS** | 747 of 1000 catalogue products carry none and report `colour: null`; the handoff renders `Not available`. Never read off the description. |
| 8 | Missing configured return detail | **PASS** | The handoff waits (F-15) and, where it proceeds, states `Required Return Information: Incomplete`. `test_incomplete_return_information_is_stated_rather_than_implied`. |
| 9 | Invalid return-detail value | **PASS** | Reason `BECAUSE_I_SAID_SO` -> `422`, refused against the published catalogue rather than recorded verbatim. |
| 10 | Ambiguous / conflicting detail | **PASS** | Quantity 99 on a line of 1 -> `409 QUANTITY_UNAVAILABLE` with the figures recomputed inside the transaction that refused. |
| 11 | A detail supplied again | **PASS** | Identical resubmission -> `changed: false`, revision unmoved, hold not re-taken. |
| 12 | A material detail changed after capture | **PARTIAL** | The replace-set write releases the withdrawn hold in the same transaction, and the handoff is composed at open time. **Not yet driven:** a change *after* the handoff has opened. |
| 13 | Bay agent finds no bay | **PASS** | Observed live before D-1: `bayReason: PRE_ARRIVAL_NOT_ALLOWED`, no bay, and the handoff renders the unresolved reason and asks for manual assignment. `test_an_unresolved_bay_says_so_and_asks_for_a_manual_one`. |
| 14 | Bay config names an unavailable warehouse | **PASS** | This was F-5's steady state -- every seeded order named a warehouse with no bays, and the agent answered with empty `eligibleBayIds` rather than a guess. |
| 15 | Duplicate bay request for one state version | **PASS** | `_record_bay_facts` derives `fact_id` from case and fact name against an insert-only log, so a second arrival is a no-op; `persist_agent_decision` keys on the inputs weighed. |
| 16 | Return data changes after bay assignment | **NOT DRIVEN** | The staleness rule is stated in `CaseBayPlacement` but no live case exercised it. |
| 17 | Worker restart after bay, before handoff | **PASS** | The stack was restarted six times during this work with cases in flight; Temporal resumed each from history and no case lost or duplicated a bay result. |
| 18 | Support handoff submitted twice | **PASS** | One work item per case: unique `caseId` index plus a case-derived `idempotencyKey`. Verified: 1 work item for the driven case. |
| 19 | Work item exists but structured bay data missing | **PASS** | `businessPayload.bayAssignment` carries bay, warehouse, location, status and source, so no screen parses the text for it. |
| 20 | Frontend refresh after work-item creation | **PASS** | Re-read returns the same single message, same id, same text. |
| 21 | Worker restart with a pending handoff | **PASS** | Same evidence as 17; the handoff is opened by an activity under `_PERSIST_RETRY` against an idempotent writer. |
| 22 | Manual mode with every live provider unavailable | **PASS** | `GET /api/ai/routes` -> 2 routes, both `MANUAL`. Not "none was called" but "none could be". |
| 23 | Policy disabled without an approval | **PASS** | `policyEvaluation: null`, no decision, no route; the message says `Skipped by configuration (...)` and the word `Approved` does not appear. |
| 24 | User-controlled notes carrying injection | **PASS** | A note containing `VERIFICATION:` and `- Policy Evaluation: Approved` is neutralised to `[removed]` while the associate's own words survive. `test_associate_text_cannot_impersonate_the_message_framing`. |

**21 pass, 1 found-and-fixed, 1 partial, 1 not driven.** The two open ones (12,
16) are both "a fact changed after something downstream consumed it", and both
need a second live case to exercise honestly rather than an assertion about code
that was not run.

## V-17 · Final gates

| Command | Result |
|---|---|
| `backend/.venv/Scripts/python.exe -m pytest tests -q` | **4040 passed, 3 skipped, 496 deselected**, 163.69s |
| `backend/.venv/Scripts/python.exe -m ruff check src scripts tests` | **1 error** -- the known `I001`, unchanged (D-3) |
| `npx vitest run --maxWorkers=3` | **30 files, 471 tests, all passed**, 28.21s |
| `npm run lint` | **5 errors** -- all pre-existing, unchanged (D-3) |
| `scripts/check_openapi_drift.py --write` | PASS -- `colour` added to `OrderLineView` across four snapshots and the generated TypeScript |

The measured baseline at Wave 0 was **4025 passed, 3 skipped**. The suite is now
**4040 passed, 3 skipped** -- fifteen added, none removed, none failing.
