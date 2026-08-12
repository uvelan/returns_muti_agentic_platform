# Ferguson Returns AI Platform — Implementation Plan (final)

**Baseline:** `refactor/unified-return-platform` @ `28b3670` · audit dated 2026-08-11
**Mode:** direct implementation. **Do not generate another plan during execution.**

> Committed to the repository on 2026-08-12 because it previously existed only in
> chat history and was lost to context compaction mid-execution — costing several
> exchanges asking for a step list that had already been given. The plan is the
> contract; it belongs with the code.
>
> Execution status as of that date is tracked in
> [`IMPLEMENTATION_PLAN_STATUS.md`](IMPLEMENTATION_PLAN_STATUS.md).

---

# 1. Decisions

## D1 — Consolidate, never rewrite

**Retain:** LangGraph Order Discovery · Temporal · `ReturnWorkflow` · existing deterministic
domain logic · `ReturnSupportService` · AI Gateway and `AIRoutePool` · `LogicalQueryPlan` →
`CypherCompiler` · Graph Schema Analyzer · graph sync framework · configuration
release/snapshot infrastructure · Vault · the frontend shell.

**Never introduce a second:** Order Discovery implementation · case lifecycle owner · support
backend · approval service · configuration store · graph-schema representation · provider
dispatch path.

Where two implementations of one concern exist, pick the more complete, migrate callers,
delete the other. **Writing a third is the failure mode.**

## D2 — The first breakpoint is order confirmation

```
Discovery → candidate selection → CONFIRM_ORDER ← first missing transition
          → Case → Return workflow → …
```

Bay is the first missing *concurrent downstream activity*, not the first breakpoint. The audit's
executive summary says step 6; sections B1, D4 and D6 say step 3. **Step 3 is correct.**

## D3 — No two-path reconciler

The authoritative return table is platform-written. There is no independent external writer, so
§4.9's reconciliation solves a problem that does not exist. **Keep provenance**
(`source_system`, `source_path`, `external_reference`, `observed_at`); **build no reconciler.**
If a genuinely independent writer appears later, add reconciliation from evidence then.

## D4 — `shipmentInfo` is correct

The spec said `shipinfo`; the code is right. All existing collection names are correct as
implemented. Audit Q3 closed.

## D5 — Case facts are append-only and versioned

**This corrects the earlier plan.** A single nested mutable `facts: {…}` document is a
last-write-wins hazard: Bay, Support, Fulfillment and Channel A all write concurrently.

**`cases`** — `case_id`, `tenant_id`, `principal_id`, `branch_id`, `status`,
`channel_a_conversation_id`, `channel_b_work_item_id`, `confirmed_order_reference`,
`workflow_id`, `configuration_release_id`, `graph_generation_id`, `version` (CAS),
`created_at`, `updated_at`.

**`case_facts`** — `fact_id`, `case_id`, `fact_name`, `value`, `agent_id`, `channel`, `turn_id`,
`source_system`, `source_path`, `acquisition_method`, `observed_at`, `recorded_at`,
`supersedes_fact_id`, `correlation_id`.

Latest state is a **projection**. Provenance is never destroyed.

## D6 — The return record is first-class

**This corrects the earlier plan**, which put RMA, tracking and label on the return *item*. One
RMA covers N items, so item-level artifacts cannot express reality.

```
Case
 ├── ReturnRecord A ── Item 1, Item 2
 └── ReturnRecord B ── Item 3
```

**`return_records`** — `return_record_id`, `case_id`, `return_reference`, `status`,
`return_location`, `tracking_reference`, `label_reference`,
`shipping_instruction_reference`, `source_system`, `version`, timestamps.

**`return_items`** — `return_item_id`, `case_id`, `return_record_id`, `order_line_reference`,
`product_reference`, `quantity`, `reason`, `condition`, `package_reference`.

Supports 1 RMA → N items, N RMAs → 1 case, per-record labels and locations, and partial
returns. `ReturnSessionView` singular fields stay temporarily as compatibility projections.

## D7 — Freeze now, unmount after consumer proof, delete after Gate A

**This corrects the earlier plan**, which unmounted immediately. The audit establishes that 44
mounted routes have no caller *in the frontend or cross-service search* — an undocumented
external consumer would falsify that.

**Immediately:** mark modules frozen with a header naming the canonical replacement; add
architecture tests forbidding new imports and new frontend callers; mark routes deprecated.
**After consumer analysis:** unmount. **After Gate A:** migrate any unique logic, then delete.

This removes ambiguity without risking removal of behaviour nobody has proven is dead.

## D8 — Graph schema releases are immutable; migration is generation-based

Re-analysis never edits the active schema. It produces a new candidate, compared three ways
(active / previous baseline / new candidate), and activation always yields a **complete new
release**. Migration behaviour follows the diff class — see W2.3. **Never drop the active graph
and hope the rebuild succeeds.**

---

# 2. Execution rules

1. **Extend existing code.** Every step names its files and what survives. If a step seems to
   need a new subsystem, re-read its `Retain` line — it usually exists and is not wired.
2. Inspect only the files the current step names. The audit established the endpoint inventory
   (A3.1), env vars (A1.5), routes (A2.1), Cypher counts (B4.3), LLM call sites (C4.3) and
   config loaders (C3.1). Cite them; do not re-derive them.
3. Never read full logs first. Order: exit status → concise error → failed test or exception →
   surrounding context → full log only if unresolved.
4. Targeted tests per step; full suites only at wave gates.
5. **The W0.7 smoke baseline must pass at every wave gate.** A step that breaks it is not done.
6. Real MongoDB, Neo4j, SQL Server, Temporal, Valkey and Vault remain the integration
   infrastructure. **Never add a runtime mock to satisfy a test** — that is exactly how
   `/api/agents` shipped 404 with a green suite.
7. Never mark a step complete while a production path is stubbed.
8. Source systems stay read-only except the already-defined platform-owned return store.
9. LLMs never receive credentials or drivers, and never emit executable DDL or DML.
10. Every side effect idempotent. Every case write via workflow serialization or CAS.
11. YAML is seed and default only. Runtime changes never write packaged YAML.
12. Temporal workflow bodies stay deterministic — no `datetime.now()`, `uuid4()`, driver, HTTP
    or LLM call. Those belong in activities. The three existing workflows are clean (C2.1);
    keep them that way.
13. Configuration stays pinned for an in-flight workflow. New cases pick up new releases.
14. **Known pre-existing failures** — do not attribute to your changes:
    `test_return_workflow_concurrency.py::test_a_second_completion_sees_the_first_ones_state`
    (flaky, 3 of 13, undiagnosed); 58 errors + 1 failure when infra or `NVIDIA_API_KEY` /
    `GOOGLE_API_KEY` are absent.
15. Record one short note per completed step — what changed, what the next step may assume.

---

# 3. Streams and contention

**Critical path (Stream A):**

```
W1.1 case model → W1.2 CONFIRM_ORDER → W1.3 multi-return
  → W1.4 ReturnCaseWorkflow → W1.5 Support console → W1.6 Channel A outcome
```

**Stream B (graph/config)** runs fully in parallel from the moment Wave 0 completes.
**Stream C (security/AI)** is Wave 0 plus W4.7–W4.9, and touches no Stream A or B files.

**Never let two streams write these simultaneously:**
`main.py` · `operations/repository.py` · `operations/models.py` · `workflows/worker.py` ·
`dynamic_knowledge/schema.py` · configuration activation/snapshot code · `frontend/registry.ts`

---

# WAVE 0 — Security, unblocking, regression net

All seven are independent of each other and of everything downstream.

### W0.1 — Rotate AI credentials · **S**

**Change** Revoke the Google and NVIDIA keys; treat committed historical keys as compromised.
Put values in Vault; populate `PLATFORM_*_API_KEY_REFERENCES`; empty the direct key arrays.
Delete the duplicate `backend/.env`.

**Why** `.env` holds apparent live keys; commit `fbfcf05` removed same-provider keys from a
fixture, so they are permanently in git history; two divergent copies exist (A1.7-b, A1.7-c).

**Retain** The `*_SECRET_REFERENCE` mechanism already carries six infrastructure secrets
(`compose.yaml:105-113`).

**Blast radius** `.env`, `backend/.env`, Vault. No source change. **Deps** none.

**Validation** Backend starts with empty key arrays; `GET /api/ai/routes` lists a healthy route;
keys absent from process-visible config, logs and working tree. No live model call needed.

**Failure condition** Route pool empty after the change.
**Skip consequence** Compromised credentials stay live.

### W0.2 — Mount `/api/agents` · **S**

**Change** Import and `include_router` the agent-configuration router in `main.py`.

**Why** `configuration/api/agents.py:36` declares three routes; `grep -rn 'include_router'
backend/src` → 24 hits, none for agents; `frontend/src/api/agentConfig.ts:35,41,49` calls them
(A2.2-a).

**Retain** Router, service and screen are all written.
**Blast radius** `main.py`. **Deps** none.

**Validation** `GET /api/agents` → 200 with 8 modules **against a running backend**; Agents
section works; OpenAPI regenerated. **MSW evidence does not satisfy this gate.**

**Failure condition** Contract-drift check refuses the new paths.

### W0.3 — Tenant and principal isolation on conversations · **S**

**Change** Every conversation carries `tenant_id` and `principal_id`; queries require both.
Modify `mongo_store.py`, `conversation_repository.py`, `api/order_agent.py`.

**Why** `integration/mongo_store.py:40-60` issues `find({}, …)` with no predicate, returning
every associate's transcripts — containing customer names, addresses and phone numbers — to
every caller (B3-a).

**Blast radius** Three files. **Deps** none.

**Validation** Adversarial: tenant A and tenant B each create a conversation; neither can list
or read the other's, **and guessing a conversation id does not bypass authorization**.

**Failure condition** Legacy documents lack the fields — backfill, or filter permissively **and
log**. A `find({})` fallback is never acceptable.

### W0.4 — Close the serialized PII escape · **M**

**Change** Enforce redaction at the final provider boundary, recursing into dicts, lists,
JSON-encoded strings and nested JSON-encoded values.

**Why** `AIGatewayService._validate_scalar` scans payload **keys** (`service.py:142-147`). The
order agent's payload is five keys, one of which — `contextJson` — carries every graph row
retrieved, unredacted, and the interception store persists the same content (C3.5-a).

**Retain** The 12-fragment list, size and depth limits, task allowlist.
**Blast radius** `ai/gateway/service.py`, `structured_invocation.py`. **Deps** none.

**Validation** Assert on the **captured provider request and the interception record**, not a
log line. Cover name, email, phone, address, customer identifiers, a nested customer row, and
`contextJson` itself.

**Failure condition** Masking breaks the agent's ability to name a matched customer to the
associate — mask at the provider boundary only, keep the value on the internal response path,
record the decision.

**Skip consequence** Customer PII leaves the platform on every reasoning call.

### W0.5 — Correct the Anthropic and OpenAI provider contracts · **S**

**Change** Both adapters honour `max_output_tokens` and `response_schema`. Where a provider
lacks native schema support: provider-specific translation, or schema-in-prompt with an explicit
parser. **Never silently ignore the contract.**

**Why** `anthropic.py:42` hardcodes `max_tokens: 512` against a declared 4,096 and sends no
schema; `openai.py:38-43` sends neither. Both fail over silently on every order-agent call
(C4.2-a).

**Blast radius** Two provider files. **Deps** none.

**Validation** Force each route via `force_provider`; complete one turn with no
`RESPONSE_INVALID` attempt recorded.

### W0.6 — Freeze the duplicate runtime · **S**

**Change** Header note on `operations/associate_flow.py`, `api/associate_returns.py`,
`api/return_agents.py`, `agents/order_discovery.py`,
`data_platform/graph/interim_active_schema.py` naming the canonical replacement. Architecture
tests: no new import from retained code, no new frontend caller, no new implementation depending
on frozen modules. Mark routes deprecated. **Do not unmount or delete.**

**Why** Two complete return implementations exist and the UI calls neither of these (A4.2,
A3.1-a). Without the freeze, every Wave 1 and 2 step is built against an ambiguous codebase. Per
D7, unmounting waits for consumer proof.

**Blast radius** Five module headers, one architecture test. **Deps** none.

**Validation** Full backend suite green; frontend unaffected; the architecture test fails on a
deliberate new import.

### W0.7 — Discovery smoke regression net · **M**

**Change** 8–12 deterministic `SIMULATOR` scenarios: exact order, partial order, misspelled
customer, ambiguous customer, no match, clarification, phone, email, SKU/product. Assert the
`AgentAction` sequence, the `LogicalQueryPlan`, the guard result and the candidate outcome.
**Never assert prose.**

**Why** Wave 1 restructures the conversation contract — a new action, new context fields, a case
link — with nothing verifying discovery still works. `golden|eval_set|evalset` → **0 files**
(C4.10).

**Retain** `SIMULATOR` and `MANUAL` providers; the interception store already records real
exchanges to seed from.

**Blast radius** `backend/tests/fixtures/golden/`, one test module. **Deps** none.

**Validation** A deliberate one-line regression in `graph_nodes.py` fails the suite.
**Failure condition** Non-deterministic across runs — pin the simulator seed before proceeding.

> **Smoke net, not the eval suite.** The full evaluation suite is W5.2, after behaviour is
> correct — goldens captured now would encode current bugs as expectations.

---

# WAVE 1 — The return business spine

W1.1 → W1.6 are the critical path. W1.7 and W1.8 run alongside and are **not** on it.

### W1.1 — Case, facts, return records · **L** · *critical path*

**Change** Implement `cases`, `case_facts`, `return_records`, `return_items` per D5 and D6 in
the existing operational repository.

**Indexes** `cases(case_id)` unique · `cases(tenant_id, principal_id, updated_at)` ·
`cases(channel_a_conversation_id)` · `cases(channel_b_work_item_id)` · `cases(workflow_id)`
unique · `case_facts(case_id, fact_name, recorded_at)` · `return_records(case_id,
return_reference)` · `return_items(case_id, order_line_reference)`.

**Why** No case entity exists; Channel B → Channel A is `IMPOSSIBLE IN CURRENT IMPLEMENTATION`,
bridged only by a browser-side `orderReference` match (B3). §6 requires one case owning all facts
with provenance.

**Retain** `OperationalRepository` already owns 20+ collections with `ensure_indexes` and
optimistic-concurrency helpers (`repository.py:107-167`). `ReturnSessionView` stays as the
operational projection.

**Blast radius** `operations/repository.py`, `operations/models.py`. **Deps** none.

**Validation** **Two concurrent fact writers, no last-write-wins loss.** A case survives a
backend restart and is readable by conversation id and by work-item id.

**Failure condition** The provenance shape cannot express an existing `ReturnSessionView` field
— widen the shape; never special-case.

### W1.2 — Explicit `CONFIRM_ORDER` · **M** · *critical path*

**Change** Ninth member on `AgentAction`; a `confirm_order` node in `graph.py`'s dispatch table.
Flow: selected candidate → explicit associate confirmation → validate order and line-set →
idempotently obtain a case → bind the conversation → start `ReturnCaseWorkflow` → return
`case_id`. Idempotency key: `tenant + conversation_id + selected order + selected line-set`.

**Why** The **first end-to-end breakpoint** (B1 step 3). The frontend infers resolution from
`candidates.length === 1` (`ReturnCopilotPage.tsx:220-224`) and must stop doing so.

**Retain** The entire LangGraph agent — registry, capability guard, router table and result
contract take a ninth member exactly as they take the eighth.

**Blast radius** `order_agent/{contracts,graph,graph_nodes}.py`, `AgentTurnResult`,
`ReturnCopilotPage.tsx`. **Deps** W1.1.

**Validation** Confirmation produces a durable case surviving a worker restart. **Two
simultaneous confirmations produce one case.** W0.7 still passes.

**Failure condition** `CapabilityGuard` rejects the action — add it to
`agent_policies.order-discovery-agent.allowed_business_capabilities` and recompute
`configuration_checksum`.

### W1.3 — Multi-return model in use · **L** · *critical path*

**Change** Wire `return_records` and `return_items` through the API and S1. Migrate
compatibility consumers gradually.

**Why** `operations/models.py:130,136,142,143` are singular `str | None`; a multi-item return
produces more than one RMA, label and return location, and packages must not be mixed (§4.11).

**Retain** `ReturnSessionView` as a projection; `physical/service.py`;
`api/return_artifacts.py`.

**Blast radius** `operations/{models,repository}.py`, `physical/service.py`,
`api/return_artifacts.py`, S1 RMA panel. **Deps** W1.1.

**Validation** Three cases — 2 items/1 RMA/1 label; 2 items/2 RMAs/2 labels; 3 items/2
RMAs/different return locations. **No API response mixes artifacts between records.**

**Failure condition** A consumer assumes the scalar — populate the projection from the first
record and **log when N > 1**; never drop data.

### W1.4 — `ReturnCaseWorkflow` · **XL** · *critical path*

**Change** The durable top-level Temporal workflow owning case lifecycle, `ReturnWorkflow`
execution, Bay, Support lifecycle, durable waits, reminders, failure policy and completion.
Signals: `bay_result`, `support_response`, `cancel_case`. Waits via `workflow.wait_condition()`
only. Draft the support template through `StructuredOutputInvoker` with a typed `SupportDraft`
model.

**Why** The bay wait, Support wait and reminder cadence are all absent — `reminder` → **0 hits**
across `backend/src`, `backend/config`, `frontend/src` (A3.2-c). The only implemented Support
wait is `asyncio.sleep` × 12 × 5 s = **60 seconds** against an SLA in business days (C2.2-a).

**Retain** `ReturnWorkflow` **unchanged** as the stage subflow — one case, one workflow owner.
`ReturnSupportService` unchanged. Copy the working patterns from `OrderDiscoveryWorkflow`:
`wait_condition(timeout=, timeout_summary=)` at `:224-228`, the `while` re-check mutex at
`:306-310`, `continue_as_new` at `:241-248`.

**Blast radius** New `workflows/return_case_workflow.py`; `workflows/worker.py`. **Deps** W1.1,
W1.7.

**Validation** **Restart the worker mid-wait against real Temporal.** The timer survives and the
reminder still fires. A passing unit test is not acceptable evidence for this step.

**Failure condition** Determinism violation on replay — nothing in the workflow body may call
`datetime.now()`, `uuid4()` or a driver.

### W1.5 — Support console (S3) · **L** · *critical path*

**Change** A thin frontend over the existing backend: work-item list, status filters, case
reference, return items, message thread, reply composer, and RMA/tracking/label/return-location
capture **associated to a return record**.

**Why** Channel B has a complete backend — `return_support/service.py` (734 lines), 6 mounted
routes, threads with unique idempotency indexes — and **no operator surface** (A2.3 S3). Without
it no human can play Support and no end-to-end return can be exercised.

**Retain** The whole backend. **Backend duplication is prohibited.** Extract a shared
conversation component from S1 **only if it can be done without breaking S1**.

**Blast radius** `frontend/src/domains/support/`, `registry.ts`. **Deps** W1.3.

**Validation** A human completes one Support exchange end to end.

**Failure condition** The shared-component extraction destabilises S1 — ship S3 with a
duplicate, record the debt, never ship a broken copilot.

### W1.6 — Outcome into the original Channel A · **M** · *critical path*

**Change** Support update → case and return-record update → case fact → Channel A conversation
event and context → S1 live view. **No browser-side order-reference joining. No new
conversation.**

**Why** §4.10. The current bridge is `ReturnCopilotPage.tsx:314-320` matching on
`orderReference` client-side — if two open orders share a reference, or the tab closes, the link
is gone (B3).

**Blast radius** `order_agent/conversation_repository.py`, `AgentTurnContext`,
`ReturnCopilotPage.tsx`. **Deps** W1.1, W1.2, W1.5.

**Validation** The next associate turn sees the result in `AgentTurnContext`; S1 updates with no
reload.

**Failure condition** The outcome arrives after the conversation workflow idled out — the
document is durable; write to it directly and let the next turn pick it up.

### W1.7 — Bay concurrency, failure policy, timings · **M** · *parallel*

**Change** Three linked pieces:

- **Bay** — trigger on `CONFIRM_ORDER`, not `IN_TRANSIT`. Remove the three `raise ValueError` at
  `workflows/bay_assignment.py:37,41,45`; return `PENDING` / `NOT_APPLICABLE` / `UNAVAILABLE`.
  Replace the hardcoded `950_000`/`800_000` confidence (`:94`) with a computed margin over the
  runner-up, or delete the field.
- **Failure policy** on `AgentDescriptor`: Order Discovery `blocking`, Return Workflow
  `blocking`, Bay / Fulfillment / Feedback `best_effort`. Blocking failure parks the case,
  exposes the failure and allows resume; best-effort records and continues.
- **Timings** in `config/returns/production.yaml` under `return_case`, with
  `business_calendar_id` and `timezone` on the workflow policy. Defaults in §7 below.

**Why** §4.6 requires concurrent start and best-effort behaviour; the code requires fulfillment
first and raises — the exact inverse (A3.2-b). No failure-policy declaration exists anywhere
(C2.7).

**Retain** `agents/bay_assignment.py` scoring logic in full — correct, just wrongly gated.

**Blast radius** `workflows/bay_assignment.py`, `agents/contracts/descriptor.py`,
`orchestrator.py:660`, `config/returns/production.yaml`. **Deps** W1.1. **Parallel** with W1.2,
W1.3.

**Validation** A case with no bay completes; a bay failure never surfaces as a case failure;
killing the workflow agent parks the case and it resumes.

### W1.8 — Case list and real resume in S1 · **M** · *parallel*

**Change** S1 becomes case-oriented: list, status, search, filters (active / waiting /
completed), resume. Persist the selected case in the route (`/returns/:caseId`). Resume restores
the pending clarification, workflow status, case facts and return records — **not merely
transcript text**.

**Why** `open.mutate(id)` replays a transcript as `role: "restored"` plain text
(`ReturnCopilotPage.tsx:55,262-278`) and cannot resume a paused clarification. A case waits days
for Support, so a chat with no case list serves only single-sitting returns.

**Retain** The three-pane shell, version handshake (`versionRef`, `:242`), milestone rail.

**Blast radius** `api/order_agent.py`, `ReturnCopilotPage.tsx`. **Deps** W1.1, W1.6.

**Validation** Close the browser mid-clarification; reopen from the case list; the agent resumes
at the pending question.

---

# WAVE 2 — Graph runtime, descriptor, sync

Runs in parallel with Wave 1. **W2.6 has no dependencies and should ship first.**

### W2.6 — Fulfillment on-demand sync · **M** · *ship immediately*

**Change** Fulfillment requests targeted sync for a tracking reference, then queries the
`shipment` entity through the existing `CypherCompiler` and updates the fulfillment fact.
`best_effort`.

**Why** The agent named "fulfillment tracking" never reads a tracking source —
`fulfillment_tracking.py:35-46` is a three-branch `if` over the platform's own persisted state
(C1).

**Retain** The `shipmentInfo` → `shipment` → `Shipment` binding **already exists** in the
descriptor (`active-schema.return-order.yaml:35-40,2340,2716`) and the `REQUEST_ON_DEMAND_SYNC`
mechanism already works (`graph_nodes.py:696-788`). **This is the one Wave 2 step needing no
descriptor work.**

**Blast radius** `agents/fulfillment.py`, `workflows/fulfillment_tracking.py`. **Deps** none
(W1.4 to host it in the workflow). **Parallel** with everything.

**Validation** A tracking number not previously in the graph is synced on demand and then read
**from the graph, not SQL**.

### W2.1 — Analyzer output → runtime schema · **XL**

**Change** `APPROVED GraphSchemaShape` + source binding → `ActiveSchema` candidate →
configuration release → activation → runtime snapshot. Change `runtime_factory.py:89` and
`sync_service.py:191` from YAML loading to release loading. YAML becomes bootstrap-once for an
unseeded domain; seeded domains read the release only. **Never write YAML.**

**Why** 5,172 lines of finished analyzer work reach nothing — `GraphSchemaShape` outside the
analyzer returns **one comment** (B4.6-a). The runtime descriptor is hand-authored with typed-in
`approved_by`/`approved_at`. §8 requires DB to be runtime truth. **W2.4 needs a way to add
entities that is not a hand-edit of a 2,830-line file.**

**Retain** The analyzer entire; `ActiveSchema`; `ConfigurationSnapshotBuilder`, which already
does exactly this for four other domains.

**Blast radius** `dynamic_knowledge/config_loader.py`, `runtime_factory.py`, `sync_service.py`,
new converter, `graph_schema_analyzer/application/`. **Deps** W0.6.

**Validation** An approved draft becomes the active schema with no file edit; the order agent
queries against it; W0.7 passes.

**Failure condition** The draft shape cannot express a field the descriptor needs — that is
W2.2; do both together.

### W2.2 — Split graph shape from source binding · **XL**

**Change** Three artifacts. **Graph schema:** entities, graph fields, identities, nodes,
relationships, constraints, indexes. **Source binding:** `source_id`, connector, database,
object, field physical paths, change field, watermark, object filters. **Mapping:** source
`field_id` → graph `field_id`, join rules, relationship mappings. Remove executable business
source names from code, including `repository.py:73`'s `DOMAIN_SOURCE_COLLECTIONS` and the
`find_one`/`create_index` literals.

**Why** One 2,830-line document holds sources, physical paths, graph shape and agent policies
under one checksum (A3.5-a). 41 source-name literals make `salesInv` unrebindable — **8 code
edits across 5 files** (A3.5). R8 is `NO`.

**Retain** `ActiveSchema` already separates `sources` from `graph` internally; compiler and
guards unaffected.

**Blast radius** Descriptor, `dynamic_knowledge/schema.py`, `operations/repository.py`,
`data_platform/`. **Deps** W2.1.

**Validation** Rename `salesInv → salesInvV2` **through configuration only** and complete a
discovery turn.

### W2.3 — Re-analysis and migration policy · **L**

**Change** Implement D8. Three-way diff (active / previous baseline / new candidate),
classified:

| Class | Examples | Behaviour |
|---|---|---|
| **Additive** | new optional property, new entity, new relationship, new index, new binding | activate new generation; incremental backfill; preserve compatible data |
| **Compatible mapping** | physical path changed, graph identity unchanged | activate; resync affected entity/relationship only |
| **Destructive** | identity field, node label, relationship endpoints, entity removal, identifier semantics, incompatible datatype | new generation; stop writes to old; full rebuild; validate; **atomic swap**; retire old afterwards |

**Why** Audit Q5, including the half nobody asked: what happens to graph data already loaded.
The analyzer's graph adapter currently refuses this outright
(`analyzer_graph_target_adapter.py:89-95`) rather than doing something partial — the right
instinct, and the reason this needs deciding.

**Retain** Generation fencing already exists in `graph/neo4j_writer.py`.

**Blast radius** `graph_schema_analyzer/application/`, `data_platform/graph/`. **Deps** W2.1,
W2.2.

**Validation** All three classes exercised; **readers never observe a partially rebuilt
generation**; a failed rebuild leaves the old generation serving.

**Failure condition** Never drop the active generation before the replacement validates.

### W2.4 — Return and warehouse graph entities · **L**

**Change** Add the return-record entity, return-item relationships, warehouse/bay entity,
required indexes and source bindings — **through the analyzer and the approval path**, not by
hand-editing the descriptor.

**Why** The descriptor binds four sources only. Return and warehouse have no entity, node or
binding, so W2.5 and W2.7 have nothing to sync into. Warehouse is SQL-only via
`sql.list_bay_candidates` — the graph bypass R2 forbids. `shipmentInfo` already has its
representation and stays unchanged.

**Deps** W2.1, W2.2, W2.3.

**Validation** Both entities appear in `compact_schema` for permitted agents and return rows.

**Failure condition** The analyzer's connector cannot describe the SQL warehouse source — only
the Mongo adapter exists (A3.3-a). Land the MSSQL adapter from W4.5 first.

### W2.5 — Return on-demand sync · **M**

**Change** After the authoritative return record commits: Return Workflow requests
**record-scoped** sync, waits for the committed graph generation, then agents read.

**Why** Agents must read return state from the graph. **Record-scoped, not collection-scoped** —
three agents firing collection-wide syncs per case would hammer the graph.

**Deps** W2.4, W1.4.

**Validation** A newly created return is queryable on the next agent turn. **The sync activity
returns only when the write commits** — generation-fenced writes mean a read landing mid-write
sees partial data.

**Failure condition** Return Workflow is `blocking`: the case parks and the failure is **loud**.
The return exists in SQL and no agent can see it — never continue silently on stale graph state.

### W2.7 — Warehouse and bay on-demand sync · **M**

**Change** Targeted warehouse sync before scoring, then graph candidate query into the existing
bay scoring. Remove the direct SQL bypass from the agent path.

**Deps** W2.4, W1.7.

**Validation** Bay candidates come from the graph; a stale warehouse row refreshes on demand.

**Failure condition** Bay is `best_effort`: record the reason, mark bay omitted, continue. Never
park the case.

### W2.8 — Sync control (S6) and incremental sync · **L**

**Change** Router over `GraphSyncService.list_runs()` plus run/start/status APIs. Implement
`incremental_sync` against the existing cursor and checkpoint contracts; bind
`config/sync/order_{full,partial}.yaml`. S6 shows run id, source, entity, schema generation,
binding version, FULL/PARTIAL, watermark, processed, written, skipped, failed, retry, start,
finish, failure reason. **Manual triggers capability-gated.**

**Why** Sync failure is invisible end to end (B5-a) — a silently failed sync means Order
Discovery searches stale data and tells associates their order does not exist. The run store,
view model and checkpoint contracts all exist unused (`sync_service.py:108-109,262-270`).

**Retain** `GraphSyncService`, `GraphSyncRunView`, `SourceCursor`, `capture_high_watermark`,
`compare_cursors`.

**Deps** W2.2.

**Validation** Forced failure: sync N rows → fail → restart → resume from the correct watermark
→ no duplicate graph state → **failure visible in the UI**.

**Failure condition** Two-stage checkpointing is genuinely out of scope
(`coordinator.py:107-109`) — implement single-stage and say so.

---

# E2E GATE A — Functional cutover

**Nothing is deleted before this gate.** Run against real infrastructure.

1. Associate opens S1
2. partial and misspelled input
3. candidate found through the graph
4. explicit confirmation
5. durable case created
6. `ReturnCaseWorkflow` starts
7. Bay starts concurrently
8. Bay may succeed or fail without blocking
9. return details captured
10. return record committed
11. return synced into the graph
12. Support work item opens
13. operator uses S3
14. RMA, tracking, label created
15. multiple return records work
16. outcome appears in the original Channel A
17. Fulfillment syncs `shipmentInfo` on demand
18. case resumes after worker restart
19. case reaches a terminal state

**Variants required:** single item/single RMA · multi-item/one RMA · multi-item/multiple RMAs ·
no bay · bay activity failure · worker restart during the Support wait · duplicate
`CONFIRM_ORDER` · duplicate support response · out-of-order signals.

**W0.7 must still pass.**

---

# WAVE 3 — Consolidation

Only after Gate A.

### W3.1 — Migrate unique legacy behaviour · **M**

For each frozen module produce: legacy symbol → business capability → new owner → replacement
test → last caller. **Nothing is deleted merely because it looks duplicated.**

### W3.2 — Delete the losing implementation · **L**

`operations/associate_flow.py`, `api/associate_returns.py`, `api/return_agents.py`,
`agents/order_discovery.py`, `data_platform/graph/interim_active_schema.py`,
`return_support/providers/sandbox.py`, and the 18 unmounted `/data-console/v1/*` routers —
moving the four handler bodies `configuration/api/router.py:31-44` imports into that router
**first**. Unmount quarantined routes; regenerate OpenAPI.

Removes 19 of the 24 hardcoded questions (B2.5) and the hardcoded order-source rule
contradicting the §4.2 deferral (`agents/order_discovery.py:37-50`).

**Architecture gate — exactly one of each remains:** Order Discovery runtime · case lifecycle
owner · support backend · config API · graph runtime schema pipeline · AI dispatch path.

---

# WAVE 4 — Governance, configuration, identity, product surfaces

| ID | Change | Why | Deps | Effort |
|---|---|---|---|---|
| **W4.1** | Worker configuration refresh at loop boundary, activity start and job boundary — **never inside deterministic workflow code**. In-flight cases keep their pinned release; new cases get the new one | `main.py:851` is the only caller and workers serve no HTTP requests, so config applies inconsistently across the deployment (C3.3-a) | — | M |
| **W4.2** | Agent config writes become releases: validate → release → review → approve → activate → audit. Packaged YAML stays seed material | `agent_configuration.py:126-168` writes packaged YAML on disk — §8 forbids it; edits are lost on redeploy, invisible to other replicas, absent from the audit trail. Retain its validate-by-reload discipline; change only the sink | W0.2, W4.1 | M |
| **W4.3** | Shared proposal kernel. Types: `GRAPH_SCHEMA`, `IMPROVEMENT`, `CONFIGURATION`. Lifecycle: DRAFT → VALIDATED → REVIEW_PENDING → APPROVED/REJECTED → ACTIVATED → SUPERSEDED. Every proposal carries before, after, diff, evidence, actor, validation receipt, affected keys, risk. **Activation re-checks the permitted-key policy — never trust the UI.** Capability check on `approve_draft` | §5B S9 requires one inbox. Feedback writes `REVIEW_PENDING` and nothing transitions it (`feedback_service.py:157`). `approve_draft` (`drafts.py:392-406`) accepts **any** actor. **Building a second approval path is the failure** | W2.1 | L |
| **W4.4** | Feedback emits a typed `ImprovementProposal` routed through W4.3 into the existing `RuntimeConfigurationActivator`. Permitted and forbidden key sets per §7 | §4.14. The analysis exists; the governance does not. Restart-free activation is already built | W4.3 | M |
| **W4.5** | Complete analyzer connectors: one read-only interface (`validate`, `list_sources`, `list_objects`, `describe_object`, `sample`, `profile`, `list_indexes`, `list_relationships`) across MongoDB, SQL Server, PostgreSQL, Neo4j. `profile` returns approximate count, null rate, distinct estimate, identifier candidates, change-tracking candidates. **The connector enforces allowed source, objects, fields and sample bounds** | §5A requires 7 tools and 4 connectors; present are 2 tools and 1 adapter (A3.3-b), and no Postgres driver is installed. **Scope must be a hard filter in the tool layer, not a prompt instruction.** Never add a write, DDL or arbitrary-query method | W2.4 | XL |
| **W4.6** | Mask analyzer samples at the port boundary before model invocation. Preserve field names, types, shape, cardinality and distribution metadata; remove sensitive values | `source_port.py:23-27` requires the redactor before any *durable write*; the prompt path is unmasked (C3.5) | W0.4 | M |
| **W4.7** | `as_of` and `session_timezone` on `AgentTurnContext`, set once per turn in `_build_context` (`graph_nodes.py:155`), stated in the system prompt, persisted on the turn. Relative phrases convert to absolute boundaries | R3 requires relative dates; `yesterday\|last week\|relative_date` → **0 hits**; the context has 13 fields, none a date (B4.5). Without a stored as-of, replay of any date-bearing query is not reproducible | — | S |
| **W4.8** | Selectivity into `compact_schema`: `approximate_distinct`, `null_rate`, identifier likelihood, from the analyzer's `profile`. **Never hardcode question order** | Ranked elicitation is a model guess over a catalog with no statistics (B2.4) | W4.5 | M |
| **W4.9** | Vendor-neutral OIDC/JWT `PrincipalProvider`: issuer, audience, JWKS discovery, tenant/subject/groups/branch claims, claim → capability mapping. **No development fallback outside development and test** | `main.py:768-771` raises outside dev/test and nothing supplies a provider. Making it OIDC-generic means deployment values, not architecture, remain outstanding | W4.3 | L |
| **W4.10** | Complete S1–S9 per §5B. S2 gains agent states, workflow state, waits, deadlines, reminders, failures, retries, signals and authorized control actions. S4 gains add/edit/disable/validate, connection test, permitted-object browse, credential **reference** (secrets write-only from the UI). S5 gains source/object/field selection, free-text context, profile, diff, index plan, sync plan. S7 gains release diff, rollback, field catalog, tone, access-control view | S3 and S6 land in Waves 1–2; the rest are extensions of screens that already exist (A2.3) | W4.3, W4.9 | XL |
| **W4.11** | AI pricing as versioned runtime config: provider, model, `effective_from`, currency, input / cached-input / output per million tokens, source. **Cost computed at the pricing version effective at request time and stored on the record.** Absent pricing → `estimated_cost = null`, `pricing_status = UNKNOWN` — **never 0** | `estimatedCostMicrousd` is hardcoded to 0 (C4.4-a). Deriving cost at read time silently rewrites history whenever a provider changes price | W4.1 | M |
| **W4.12** | AI correlation: `trace_id`, `correlation_id`, `case_id`, `conversation_id`, `agent_id`, provider, model, `prompt_version`, input / cached-input / output tokens, latency, `estimated_cost`, `pricing_version`, `fallback_reason`, status. Wire the existing replay and compare endpoints into S8 | Metrics are rich on the provider dimension and blind on the business one; replay is mounted and unused (C4.4-a, C4.5) | W4.11 | M |

---

# WAVE 5 — Search, evaluation, efficiency, reliability, hygiene

Independent of Wave 4 except where noted; run any of these as capacity allows.

| ID | Change | Why | Deps | Effort |
|---|---|---|---|---|
| **W5.1** | Typed `FUZZY_SEARCH` operation on `LogicalQueryPlan`, compiled to the existing Neo4j full-text query. The model still never writes Cypher; `SchemaQueryGuard`, field allowlists and capability guards stay active | The index is created and **verified ONLINE at startup** and used only by dead code (`associate_flow.py:1362`). The live path scans 100 rows and scores with `difflib` — a misspelled customer outside the first 100 is unfindable (B2.2-a) | W3.2 | M |
| **W5.2** | Full evaluation suite, **extending** W0.7: exact, partial, misspelling, phone, email, SKU, tracking, relative date, absolute date, multiple candidates, no result, strong-anchor on-demand sync, weak-anchor refusal, prompt injection, invalid graph field, forbidden capability, multi-turn clarification, explicit confirmation. Assert action sequence, query plan, allowed fields, clarification, guard decision. Simulator CI must be deterministic | `promptVersion` is at **v11** — eleven revisions with no way to tell whether any helped (C4.10) | W4.7 | M |
| **W5.3** | Stabilise prompt layout — system instructions, stable policy, stable compact schema, stable tool contract, then variable case context, transcript, evidence, current request — build `compact_schema` once per turn, then enable provider caching. **No claimed optimization without measurement** of tokens/request, cached tokens, latency, cost, hit rate | ~6,400 stable tokens per call (systemPrompt 2,284 + compact_schema 4,083) across up to 8 reasoning steps, uncached, with the largest static segment buried mid-blob (C4.6-a); `compact_schema` is rebuilt per node entry (`graph_nodes.py:175`) | W5.2 | M |
| **W5.4** | One production AI dispatch path: retire the `ai_gateway/` shim, consolidate `service.py`'s bespoke contract onto `structured_invocation.py`. Architecture test: exactly one production provider dispatch site; no agent calls a provider directly | Three `generate()` sites already share one `AIRoutePool`; only post-processing differs (C4.3) | W3.2 | M |
| **W5.5** | Temporal retry policies differentiating transient infrastructure failure, permanent validation failure, authorization failure and business rejection. **Never retry all exceptions indiscriminately** | All three activities are `maximum_attempts=1` — a transient Neo4j blip fails the turn (C2.7) | — | S |
| **W5.6** | Meaningful readiness for all workers and the frontend; verify **registration and connectivity**, not that a PID exists. Register the interception-resume and reasoning-resume workers as managed services | Six workers and the frontend have no healthcheck; `return-orchestrator` gates on `service_started` only; two resume workers have no service at all (A1.3, A1.2-a) | — | M |
| **W5.7** | Bootstrap as Python orchestration replacing the `sh -ec` chain. Each step records step, version, started_at, completed_at, status, error. Every step idempotent; restart from partial failure safe | `runtime-configuration-init` is the single readiness gate for the whole application tier and a half-failure leaves Neo4j migrated, SQL unmigrated and config unbootstrapped (A1.4-a) | — | M |
| **W5.8** | Replace per-request config refresh with an epoch, ETag or bounded TTL; fast path when the version is unchanged | A Neo4j round trip on **every** API call, including every S1 poll (`main.py:851`) | W4.1 | S |
| **W5.9** | Test hygiene: provider-free tests must not require `GOOGLE_API_KEY`/`NVIDIA_API_KEY` (tests that call providers opt in explicitly); reproduce and fix the concurrency flake under stress — **not accepted as instability**; runtime loader rejects `status: DRAFT` outside a design context; delete `backend/poetry.lock;W/` and `backend/pyproject.toml;W/` | A1.9-a, A1.9-b, A1.6-b, A4.3 | — | M |
| **W5.10** | Documentation cutover: delete obsolete architecture docs rather than leaving contradictory ones. Keep README, runtime architecture, case lifecycle, Order Discovery, `ReturnCaseWorkflow`, graph schema lifecycle, source binding, sync lifecycle, support channel, AI Gateway, configuration precedence, security and PII, failure policies, operations, bootstrap and recovery | ~40 ORPHANED and ~24 STALE of 137 files; the shipped LangGraph agent has module docstrings and nothing else (C5). R18 is `NO` | W3.2 | L |
| **W5.11** | Add a PostgreSQL driver; align Node to `.nvmrc` 24.18.0 and npm to 11.16.0; address the two Starlette deprecations; plan Neo4j 5.26 → 2025.x validation against the 234 static Cypher literals | §5A requires a Postgres connector and none is installed; everything else is current (D8 of the audit) | W4.5 | M |

---

# 6. Isolated validation environment

Required for Gate A and the final gate; unblocked from audit Q6.

Per commit or run: unique Compose project name · dedicated named volumes · dedicated temporary
secrets · **no reuse of any production or developer database** · automatic teardown after
evidence capture. Prefer CI on Linux for the final cold-start gate.

Sequence: empty volumes → bootstrap → migrations → seed → application start → E2E → restart the
application tier → verify persistence → destroy.

---

# 7. Resolved questions and settings

## Timing defaults (audit Q1)

```yaml
return_case:
  bay_wait_seconds: 120
  support_response_wait_seconds: 28800     # 8 business hours
  reminder_interval_seconds: 7200          # 2 business hours
  max_reminders: 3
  business_calendar_id: <deployment>
  timezone: <deployment>
```

Support durations use the business calendar, not 24×7 wall time. Defaults, not constants —
editable through configuration releases; in-flight workflows keep their starting policy.

**Two open sub-questions, flagged rather than assumed:**

1. **Is one Support wait enough?** The source process document describes chat confirmation in
   3–5 minutes and the RGA, label or pickup instructions taking **1–5 business days**. Moving the
   label from email onto the same chat changed the transport, not necessarily that timeline. If
   the label still takes days, one 8-hour deadline is wrong and two waits are needed —
   confirmation and fulfilment — with separate reminder cadences. **Confirm before W1.7.**
2. **Is 120 s the right bay wait?** It sits on the front of **every** return, and bay is
   advisory. That is two minutes of dead time per return before the Support handoff begins.
   30 s, or sending immediately and following up with the bay detail as a later message, may
   serve associates better. **Measure once Gate A is reachable.**

**Reminder exhaustion needs a defined outcome.** `max_reminders: 3` with nothing after it means
the case sits forever with nobody told. Define: park with an operations alert surfaced in S2,
or escalate to a named role.

## Permitted proposal keys (audit Q2)

**Allowed** — `returns.conversation.tone.*` · `returns.reminders.*` ·
`returns.elicitation.field_priority.*` · `returns.discovery.scoring.*` ·
`returns.discovery.clarification.*` · `returns.support.template.*` ·
`returns.fulfillment.polling.*`. **Every numeric field additionally carries server-side
min/max validation** — a ranking weight of one billion is a valid number that silently breaks
elicitation.

**Forbidden** — `secrets.*` · `credentials.*` · `vault.*` · `auth.*` · `principal.*` ·
`capabilities.*` · `permissions.*` · `sources.*.credentials` · `sources.*.connection` ·
`graph.schema.*` · `graph.identities.*` · `graph.source_bindings.*` · `agent.failure_policy.*` ·
`ai.provider_credentials.*` · `ai.allowed_hosts.*` · `ai.safety.*` · `ai.guardrails.*` ·
`workflow.idempotency.*` · `workflow.authorization.*`.

Graph and schema changes go through schema approval. Security and access changes go through
platform administration. **Feedback proposals never activate anything directly.**

## Identity (Q3), pricing (Q4), re-analysis (Q5), environment (Q6)

Resolved as architecture in W4.9, W4.11, W2.3/D8 and §6. Deployment-specific values — actual
issuer, enterprise groups, negotiated rates — remain external and do not block engineering.

## Advisories (Q7)

**Not an architecture question.** A mandatory automated release gate scanning container images,
Python lock, Node lock and OS packages/SBOM. Policy: critical exploitable → **block**; high with
an available fix → **block**; high without a fix → documented risk acceptance plus compensating
control; medium and low → tracked. Upgrade decisions come from the scanner output for the target
commit, never from a snapshot encoded in this plan.

---

# 8. Adversarial matrix

Every one deterministic and explicitly asserted:

two simultaneous confirmations · duplicate Temporal activity · duplicate signal · duplicate
support response · bay signal arriving before the wait · bay never returns · bay process crash ·
worker restart during the Support wait · two agents writing case facts concurrently ·
configuration activated mid-case · graph generation changing mid-case · cross-tenant guessed
conversation id · one RMA covering two items · two RMAs for one order · label assigned to the
wrong RMA · return created but graph sync fails · warehouse sync fails · shipment appearing after
fulfillment starts · relative-date replay one day later · misspelled customer outside the old
100-row window · analyzer attempting an unselected table · analyzer attempting a source mutation ·
DRAFT configuration activation · unauthorized proposal approval · nested PII in `contextJson` ·
model pricing absent.

---

# 9. Validation strategy

**Per step** — changed-module tests, static checks on touched code, affected contract test.

**Per wave** — relevant backend and frontend suites, OpenAPI drift, architecture rules, **and
W0.7**.

**Gate A** — real Mongo, Neo4j, SQL Server, Temporal, Valkey, Vault, backend, workers, frontend.

**Final** — all backend tests · `mypy --strict` · backend lint and format · frontend lint,
typecheck and unit · Playwright · OpenAPI drift · configuration validation · cold start · worker
restart · sync recovery · security isolation · dependency and image scans.

---

# 10. Acceptance

**Business** — discovery → `CONFIRM_ORDER` → case → return workflow → concurrent bay → support →
N return records → fulfillment → original associate conversation → terminal case, against real
infrastructure.

**Graph** — order, return, shipment and warehouse reads all occur through the graph; targeted
on-demand sync works; sync failure is visible.

**Configuration** — an approved analyzer draft becomes the runtime schema with no file edit; a
source rebinds through configuration alone; worker reload works without a process restart.

**Governance** — feedback creates proposals; only whitelisted keys activate; unauthorized
approval fails.

**Security** — no cross-tenant history access; no credential reaches an agent; no customer PII
crosses the provider boundary unintentionally; source-side DDL and DML remain impossible.

**Architecture** — exactly one Order Discovery runtime, case lifecycle owner, support backend,
approval kernel, runtime configuration authority, graph runtime schema pipeline and AI dispatch
path.

**Reliability** — worker restart loses no waits; duplicate inputs cause no duplicate business
effects; cold start succeeds from empty isolated state; no unexplained flaky test remains.

**Release** — dependency and image scanners satisfy policy.
